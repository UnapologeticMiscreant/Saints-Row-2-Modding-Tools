
from __future__ import annotations

import json
import os
import re
import tkinter as tk
import wave

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import List, Optional, Tuple, Union


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class WAVInfo:
    channels: int
    sample_rate: int
    bits_per_sample: int
    duration_ms: int
    file_size: int
    valid: bool
    error: Optional[str] = None


@dataclass
class BNKInfo:
    signature: bytes
    file_size: int
    warnings: List[str] = field(default_factory=list)
    sound_count: int = 0
    sounds: List[dict] = field(default_factory=list)


@dataclass
class MBNKInfo:
    signature: bytes
    file_size: int
    warnings: List[str] = field(default_factory=list)
    station_data: Optional[dict] = None


@dataclass
class XTBLSongEntry:
    name: str
    duration_ms: int
    path: str
    hash: Optional[str] = None


@dataclass
class XtblInfo:
    station_name: str
    songs: List[XTBLSongEntry]
    raw_content: str


# ============================================================================
# LOGGING
# ============================================================================

class ConsoleLogger:
    def __init__(
        self,
        text_widget: tk.Text,
        font_provider=None,
    ) -> None:
        self.text_widget = text_widget
        self.font_provider = font_provider

    def _write(self, message: str, level: str = "INFO") -> None:
        timestamp = datetime.now().strftime("[%H:%M:%S]")
        line = f"{timestamp} {level}: {message}\n"

        self.text_widget.configure(state="normal")
        self.text_widget.insert("end", line)
        self.text_widget.see("end")
        self.text_widget.configure(state="disabled")

    def info(self, message: str) -> None:
        self._write(message, "INFO")

    def warning(self, message: str) -> None:
        self._write(message, "WARNING")

    def error(self, message: str) -> None:
        self._write(message, "ERROR")

    def debug(self, message: str) -> None:
        self._write(message, "DEBUG")

    def exception(
        self,
        message: str,
        error: Optional[BaseException] = None,
    ) -> None:
        suffix = f" {error}" if error else ""
        self._write(f"{message}{suffix}", "EXCEPTION")

    def update_font(self, font) -> None:
        self.text_widget.configure(font=font)


# ============================================================================
# AUDIO FILE READERS
# ============================================================================

class WavReader:
    @staticmethod
    def read_wav(path: Path) -> WAVInfo:
        file_size = 0

        try:
            if not path.exists():
                return WAVInfo(
                    0, 0, 0, 0, 0, False,
                    "File does not exist",
                )

            file_size = path.stat().st_size

            with path.open("rb") as file:
                header = file.read(4)

            if header != b"RIFF":
                return WAVInfo(
                    0, 0, 0, 0, file_size, False,
                    "File does not start with RIFF id",
                )

            with wave.open(str(path), "rb") as wav_file:
                channels = wav_file.getnchannels()
                sample_rate = wav_file.getframerate()
                bits_per_sample = wav_file.getsampwidth() * 8
                frames = wav_file.getnframes()

                if sample_rate <= 0:
                    return WAVInfo(
                        0, 0, 0, 0, file_size, False,
                        "Invalid sample rate",
                    )

                duration_ms = int(frames / sample_rate * 1000)

                return WAVInfo(
                    channels=channels,
                    sample_rate=sample_rate,
                    bits_per_sample=bits_per_sample,
                    duration_ms=duration_ms,
                    file_size=file_size,
                    valid=True,
                )

        except wave.Error as error:
            return WAVInfo(
                0, 0, 0, 0, file_size, False, str(error)
            )

        except Exception as error:
            return WAVInfo(
                0, 0, 0, 0, file_size, False, str(error)
            )

    @staticmethod
    def format_duration(duration_ms: int) -> str:
        total_seconds, milliseconds = divmod(duration_ms, 1000)
        minutes, seconds = divmod(total_seconds, 60)
        return f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


class BankInspector:
    KNOWN_SIGNATURES = {
        b"WSND": "WSND (Wwise Sound Bank)",
        b"SNDH": "SNDH (Sound Header)",
        b"BANK": "BANK (Generic Bank)",
    }

    @staticmethod
    def inspect_bank(bnk_path: Path) -> BNKInfo:
        try:
            file_size = bnk_path.stat().st_size

            with bnk_path.open("rb") as file:
                content = file.read()

            signature = content[:4]
            warnings: List[str] = []
            sound_count = 0

            sig_label = BankInspector.KNOWN_SIGNATURES.get(
                signature,
                f"Unknown ({signature.hex(' ')})",
            )

            if signature in (b"WSND", b"SNDH"):
                sound_count = content.count(b"SNDH")
            else:
                warnings.append(f"Unknown BNK signature: {sig_label}")

            return BNKInfo(
                signature=signature,
                file_size=file_size,
                warnings=warnings,
                sound_count=sound_count,
            )

        except Exception as error:
            return BNKInfo(
                signature=b"",
                file_size=0,
                warnings=[f"Failed to read BNK: {error}"],
            )

    @staticmethod
    def inspect_mbnk(mbnk_path: Path) -> MBNKInfo:
        try:
            file_size = mbnk_path.stat().st_size

            with mbnk_path.open("rb") as file:
                header = file.read(32)

            signature = header[:4]
            warnings: List[str] = []

            sig_label = BankInspector.KNOWN_SIGNATURES.get(
                signature,
                f"Unknown ({signature.hex(' ')})",
            )

            if signature not in BankInspector.KNOWN_SIGNATURES:
                warnings.append(f"Unknown MBNK signature: {sig_label}")

            return MBNKInfo(
                signature=signature,
                file_size=file_size,
                warnings=warnings,
            )

        except Exception as error:
            return MBNKInfo(
                signature=b"",
                file_size=0,
                warnings=[f"Failed to read MBNK: {error}"],
            )

    @staticmethod
    def validate_bank_basic(
        bank_info: Union[BNKInfo, MBNKInfo],
    ) -> List[str]:
        errors: List[str] = []

        if not bank_info.signature:
            errors.append("Empty signature")

        if bank_info.file_size < 100:
            errors.append(
                f"Unusually small file size: {bank_info.file_size}"
            )

        return errors


class XtblReader:
    @staticmethod
    def read_xtbl(xtbl_path: Path) -> XtblInfo:
        try:
            content = xtbl_path.read_text(
                encoding="utf-8",
                errors="ignore",
            )

            station_name = xtbl_path.stem.replace("_media", "")
            songs: List[XTBLSongEntry] = []

            for line in content.splitlines():
                if "duration" not in line.lower():
                    continue

                duration_match = re.search(r"(\d+)", line)

                if duration_match:
                    songs.append(
                        XTBLSongEntry(
                            name=f"track_{len(songs) + 1}",
                            duration_ms=int(duration_match.group(1)),
                            path="",
                        )
                    )

            return XtblInfo(
                station_name=station_name,
                songs=songs,
                raw_content=content,
            )

        except Exception as error:
            return XtblInfo(
                station_name="",
                songs=[],
                raw_content=f"Error: {error}",
            )


# ============================================================================
# PROJECT SCANNER
# ============================================================================

class ProjectScanner:
    @dataclass
    class ScanResult:
        vpp_files: List[Path] = field(default_factory=list)
        bnk_files: List[Path] = field(default_factory=list)
        mbnk_files: List[Path] = field(default_factory=list)
        wem_files: List[Path] = field(default_factory=list)
        xtbl_files: List[Path] = field(default_factory=list)

    @staticmethod
    def scan_project(project_path: Path) -> "ProjectScanner.ScanResult":
        result = ProjectScanner.ScanResult()

        extensions = {
            ".vpp": result.vpp_files,
            ".bnk": result.bnk_files,
            ".mbnk": result.mbnk_files,
            ".wem": result.wem_files,
            ".xtbl": result.xtbl_files,
        }

        try:
            for root, _, files in os.walk(project_path):
                for filename in files:
                    file_path = Path(root) / filename
                    extension = file_path.suffix.lower()

                    if extension in extensions:
                        extensions[extension].append(file_path)

        except OSError as error:
            print(f"Scan error: {error}")

        for file_list in extensions.values():
            file_list.sort(key=lambda path: str(path).lower())

        return result


# ============================================================================
# AUDIO MODDING TOOLS
# ============================================================================

class AudioModdingTools:
    def __init__(self, logger: ConsoleLogger) -> None:
        self.logger = logger

    def validate_wav_batch(
        self,
        wav_paths: List[Path],
    ) -> Tuple[int, int, List[str]]:
        valid_count = 0
        invalid_count = 0
        errors: List[str] = []

        for path in wav_paths:
            info = WavReader.read_wav(path)

            if info.valid:
                valid_count += 1
                self.logger.info(
                    f"Valid: {path.name} | "
                    f"{info.channels}ch, "
                    f"{info.sample_rate}Hz, "
                    f"{info.bits_per_sample}-bit | "
                    f"{WavReader.format_duration(info.duration_ms)}"
                )
            else:
                invalid_count += 1
                error_message = f"{path.name}: {info.error}"
                errors.append(error_message)
                self.logger.error(f"Invalid: {error_message}")

        return valid_count, invalid_count, errors

    def audit_station_files(self, project_path: Path) -> dict:
        result = ProjectScanner.scan_project(project_path)

        stats = {
            "vpp_count": len(result.vpp_files),
            "bnk_count": len(result.bnk_files),
            "mbnk_count": len(result.mbnk_files),
            "wem_count": len(result.wem_files),
            "xtbl_count": len(result.xtbl_files),
        }

        self.logger.info(f"Audit complete: {stats}")
        return stats


# ============================================================================
# THEME AND CONFIGURATION
# ============================================================================

class Theme:
    def __init__(
        self,
        root: tk.Tk,
        text_size: int = 11,
    ) -> None:
        self.root = root
        self.base_size = text_size
        self.current_size = text_size
        self._update_fonts()

    def _update_fonts(self) -> None:
        self.title_font = (
            "Arial",
            self.current_size + 4,
            "bold",
        )
        self.normal_font = (
            "Arial",
            self.current_size,
        )
        self.mono_font = (
            "Consolas",
            self.current_size,
        )

    def change_size(self, amount: int) -> int:
        self.current_size = max(
            8,
            min(24, self.current_size + amount),
        )
        self._update_fonts()
        return self.current_size

    def reset_size(self) -> int:
        self.current_size = self.base_size
        self._update_fonts()
        return self.current_size


class Config:
    def __init__(
        self,
        path: Path = Path("music_tool_config.json"),
    ) -> None:
        self.path = path
        self.data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"text_size": 11}

        try:
            with self.path.open("r", encoding="utf-8") as file:
                data = json.load(file)

            if not isinstance(data, dict):
                return {"text_size": 11}

            return data

        except (OSError, json.JSONDecodeError):
            return {"text_size": 11}

    def save(self) -> None:
        try:
            with self.path.open("w", encoding="utf-8") as file:
                json.dump(self.data, file, indent=2)
        except OSError:
            pass

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        self.data[key] = value
        self.save()


# ============================================================================
# MAIN APPLICATION
# ============================================================================

class MusicToolApp:
    BACKGROUND = "#1a1a2e"
    FOREGROUND = "#eaeaea"
    PANEL = "#2d2d44"
    BUTTON = "#6d4aff"

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Saints Row Re-Elected Music Tool")
        self.root.minsize(950, 700)
        self.root.configure(bg=self.BACKGROUND)

        self.config = Config()

        try:
            text_size = int(self.config.get("text_size", 11))
        except (TypeError, ValueError):
            text_size = 11

        self.theme = Theme(root, text_size=text_size)

        self.project_path = tk.StringVar()
        self.output_path = tk.StringVar()
        self.status_text = tk.StringVar(value="Ready.")

        self.wav_files: List[Path] = []

        self.logger: Optional[ConsoleLogger] = None
        self.tools: Optional[AudioModdingTools] = None

        self._configure_ttk_style()
        self._build_interface()

        self.logger = ConsoleLogger(
            self.console,
            lambda: self.theme.mono_font,
        )
        self.tools = AudioModdingTools(self.logger)

        self.logger.info("Application started.")
        self.logger.info("Milestone 1 - Modding Tools Ready.")

    # ------------------------------------------------------------------
    # Styling
    # ------------------------------------------------------------------

    def _configure_ttk_style(self) -> None:
        style = ttk.Style(self.root)

        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            "Dark.Treeview",
            background=self.BACKGROUND,
            foreground=self.FOREGROUND,
            fieldbackground=self.PANEL,
            borderwidth=0,
            rowheight=24,
            font=self.theme.normal_font,
        )

        style.map(
            "Dark.Treeview",
            background=[
                ("selected", "#30105C"),
                ("!selected", self.BACKGROUND),
            ],
            foreground=[
                ("selected", self.FOREGROUND),
                ("!selected", self.FOREGROUND),
            ],
        )

        style.configure(
            "Dark.Treeview.Heading",
            background=self.PANEL,
            foreground=self.FOREGROUND,
            relief="flat",
            font=(
                "Arial",
                self.theme.current_size,
                "bold",
            ),
        )

        style.map(
            "Dark.Treeview.Heading",
            background=[("active", self.BUTTON)],
        )

    # ------------------------------------------------------------------
    # Interface construction
    # ------------------------------------------------------------------

    def _build_interface(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        self._build_toolbar()
        self._build_main_content()
        self._build_status_bar()

    def _build_toolbar(self) -> None:
        toolbar = tk.Frame(
            self.root,
            bg=self.BACKGROUND,
        )
        toolbar.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=10,
            pady=10,
        )
        toolbar.columnconfigure(0, weight=1)

        title = tk.Label(
            toolbar,
            text="Saints Row Re-Elected Music Tool",
            bg=self.BACKGROUND,
            fg=self.FOREGROUND,
            font=self.theme.title_font,
        )
        title.grid(row=0, column=0, sticky="w")

        controls = tk.Frame(
            toolbar,
            bg=self.BACKGROUND,
        )
        controls.grid(row=0, column=1, sticky="e")

        self._button(
            controls,
            "A−",
            lambda: self._change_text_size(-1),
        ).grid(row=0, column=0, padx=2)

        self._button(
            controls,
            "A+",
            lambda: self._change_text_size(1),
        ).grid(row=0, column=1, padx=2)

        self._button(
            controls,
            "Reset",
            self._reset_text_size,
        ).grid(row=0, column=2, padx=2)

    def _build_main_content(self) -> None:
        content = tk.Frame(
            self.root,
            bg=self.BACKGROUND,
        )
        content.grid(
            row=1,
            column=0,
            sticky="nsew",
            padx=10,
            pady=(0, 10),
        )

        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=0)
        content.rowconfigure(1, weight=1)
        content.rowconfigure(2, weight=0)
        content.rowconfigure(3, weight=1)
        content.rowconfigure(4, weight=2)

        self._build_project_section(content)
        self._build_wav_section(content)
        self._build_operation_section(content)
        self._build_discovery_section(content)
        self._build_console_section(content)

    def _build_project_section(self, parent) -> None:
        frame = tk.LabelFrame(
            parent,
            text="Project and Output",
            bg=self.PANEL,
            fg=self.FOREGROUND,
            relief="flat",
            bd=0,
        )
        frame.grid(
            row=0,
            column=0,
            sticky="ew",
            pady=(0, 8),
            padx=1,
        )
        frame.columnconfigure(1, weight=1)

        tk.Label(
            frame,
            text="Project folder:",
            bg=self.PANEL,
            fg=self.FOREGROUND,
            font=self.theme.normal_font,
        ).grid(
            row=0,
            column=0,
            sticky="w",
            padx=8,
            pady=6,
        )

        tk.Entry(
            frame,
            textvariable=self.project_path,
            bg="#202035",
            fg=self.FOREGROUND,
            insertbackground=self.FOREGROUND,
            font=self.theme.normal_font,
        ).grid(
            row=0,
            column=1,
            sticky="ew",
            padx=8,
            pady=6,
        )

        self._button(
            frame,
            "Browse",
            self._select_project_folder,
        ).grid(
            row=0,
            column=2,
            padx=8,
            pady=6,
        )

        tk.Label(
            frame,
            text="Output folder:",
            bg=self.PANEL,
            fg=self.FOREGROUND,
            font=self.theme.normal_font,
        ).grid(
            row=1,
            column=0,
            sticky="w",
            padx=8,
            pady=6,
        )

        tk.Entry(
            frame,
            textvariable=self.output_path,
            bg="#202035",
            fg=self.FOREGROUND,
            insertbackground=self.FOREGROUND,
            font=self.theme.normal_font,
        ).grid(
            row=1,
            column=1,
            sticky="ew",
            padx=8,
            pady=6,
        )

        self._button(
            frame,
            "Browse",
            self._select_output_folder,
        ).grid(
            row=1,
            column=2,
            padx=8,
            pady=6,
        )

    def _build_wav_section(self, parent) -> None:
        frame = tk.LabelFrame(
            parent,
            text="WAV Files for Modding",
            bg=self.PANEL,
            fg=self.FOREGROUND,
            relief="flat",
            bd=0,
        )
        frame.grid(
            row=1,
            column=0,
            sticky="nsew",
            pady=(0, 8),
            padx=1,
        )
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self.wav_list = tk.Listbox(
            frame,
            selectmode=tk.EXTENDED,
            bg="#14000C",
            fg=self.FOREGROUND,
            selectbackground="#30105C",
            selectforeground=self.FOREGROUND,
            font=self.theme.normal_font,
            relief="flat",
        )
        self.wav_list.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=8,
            pady=8,
        )

        buttons = tk.Frame(frame, bg=self.PANEL)
        buttons.grid(
            row=0,
            column=1,
            sticky="ns",
            padx=8,
            pady=8,
        )

        self._button(
            buttons,
            "Add WAV Files",
            self._select_wav_files,
        ).pack(fill="x", pady=(0, 6))

        self._button(
            buttons,
            "Clear WAV List",
            self._clear_wav_files,
        ).pack(fill="x")

    def _build_operation_section(self, parent) -> None:
        frame = tk.LabelFrame(
            parent,
            text="Modding Operations",
            bg=self.PANEL,
            fg=self.FOREGROUND,
            relief="flat",
            bd=0,
        )
        frame.grid(
            row=2,
            column=0,
            sticky="ew",
            pady=(0, 8),
            padx=1,
        )

        operations = [
            ("Scan Project", self._scan_project),
            ("Validate WAV", self._validate_wav),
            ("Inspect Banks", self._inspect_banks),
            ("Match Songs", self._match_songs),
            ("XTBL Sync Check", self._check_xtbl_sync),
            ("Generate Report", self._generate_report),
        ]

        for column, (text, command) in enumerate(operations):
            self._button(
                frame,
                text,
                command,
            ).grid(
                row=0,
                column=column,
                padx=8,
                pady=8,
            )

    def _build_discovery_section(self, parent) -> None:
        frame = tk.LabelFrame(
            parent,
            text="Discovered Project Files",
            bg=self.PANEL,
            fg=self.FOREGROUND,
            relief="flat",
            bd=0,
        )
        frame.grid(
            row=3,
            column=0,
            sticky="nsew",
            pady=(0, 8),
            padx=1,
        )
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self.discovery_tree = ttk.Treeview(
            frame,
            columns=("path",),
            show="tree headings",
            style="Dark.Treeview",
        )

        self.discovery_tree.heading(
            "#0",
            text="Type / File",
        )
        self.discovery_tree.heading(
            "path",
            text="Path or Count",
        )

        self.discovery_tree.column(
            "#0",
            width=220,
            anchor="w",
        )
        self.discovery_tree.column(
            "path",
            width=700,
            anchor="w",
        )

        self.discovery_tree.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=(8, 0),
            pady=8,
        )

        scrollbar = tk.Scrollbar(
            frame,
            orient="vertical",
            command=self.discovery_tree.yview,
            bg=self.PANEL,
        )
        scrollbar.grid(
            row=0,
            column=1,
            sticky="ns",
            padx=(0, 8),
            pady=8,
        )

        self.discovery_tree.configure(
            yscrollcommand=scrollbar.set,
        )

    def _build_console_section(self, parent) -> None:
        frame = tk.LabelFrame(
            parent,
            text="Console Log",
            bg=self.PANEL,
            fg=self.FOREGROUND,
            relief="flat",
            bd=0,
        )
        frame.grid(
            row=4,
            column=0,
            sticky="nsew",
            padx=1,
        )
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self.console = tk.Text(
            frame,
            wrap="word",
            state="disabled",
            undo=False,
            bg="#000000",
            fg=self.FOREGROUND,
            insertbackground=self.FOREGROUND,
            selectbackground="#30105C",
            selectforeground=self.FOREGROUND,
            font=self.theme.mono_font,
            relief="flat",
            padx=8,
            pady=8,
        )
        self.console.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=(8, 0),
            pady=8,
        )

        scrollbar = tk.Scrollbar(
            frame,
            orient="vertical",
            command=self.console.yview,
            bg=self.PANEL,
        )
        scrollbar.grid(
            row=0,
            column=1,
            sticky="ns",
            padx=(0, 8),
            pady=8,
        )

        self.console.configure(
            yscrollcommand=scrollbar.set,
        )

    def _build_status_bar(self) -> None:
        status = tk.Label(
            self.root,
            textvariable=self.status_text,
            anchor="w",
            bg=self.PANEL,
            fg=self.FOREGROUND,
            font=self.theme.normal_font,
            padx=8,
            pady=4,
        )
        status.grid(
            row=2,
            column=0,
            sticky="ew",
        )

    def _button(self, parent, text: str, command) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=self.BUTTON,
            fg=self.FOREGROUND,
            activebackground="#183C80",
            activeforeground=self.FOREGROUND,
            font=self.theme.normal_font,
            relief="flat",
            padx=8,
            pady=5,
            cursor="hand2",
        )

    # ------------------------------------------------------------------
    # Font controls
    # ------------------------------------------------------------------

    def _change_text_size(self, amount: int) -> None:
        size = self.theme.change_size(amount)
        self.config.set("text_size", size)
        self._refresh_fonts()

        if self.logger:
            self.logger.info(f"Text size changed to {size}.")

    def _reset_text_size(self) -> None:
        size = self.theme.reset_size()
        self.config.set("text_size", size)
        self._refresh_fonts()

        if self.logger:
            self.logger.info("Text size reset to default.")

    def _refresh_fonts(self) -> None:
        self.root.option_add("*Font", self.theme.normal_font)

        self.wav_list.configure(
            font=self.theme.normal_font,
        )
        self.console.configure(
            font=self.theme.mono_font,
        )

        if self.logger:
            self.logger.update_font(self.theme.mono_font)

        self._configure_ttk_style()

    # ------------------------------------------------------------------
    # File selection
    # ------------------------------------------------------------------

    def _select_project_folder(self) -> None:
        selected = filedialog.askdirectory(
            title="Select Saints Row project folder",
        )

        if selected:
            self.project_path.set(selected)

            if self.logger:
                self.logger.info(f"Project folder: {selected}")

            self.status_text.set("Project folder selected.")

    def _select_output_folder(self) -> None:
        selected = filedialog.askdirectory(
            title="Select output folder",
        )

        if selected:
            self.output_path.set(selected)

            if self.logger:
                self.logger.info(f"Output folder: {selected}")

            self.status_text.set("Output folder selected.")

    def _select_wav_files(self) -> None:
        selected = filedialog.askopenfilenames(
            title="Select WAV files",
            filetypes=[
                ("WAV files", "*.wav"),
                ("All files", "*.*"),
            ],
        )

        if not selected:
            return

        added = 0

        for filename in selected:
            path = Path(filename)

            if path not in self.wav_files:
                self.wav_files.append(path)
                self.wav_list.insert("end", str(path))
                added += 1

        if self.logger:
            self.logger.info(f"Added {added} WAV file(s).")

        self.status_text.set(f"Added {added} WAV file(s).")

    def _clear_wav_files(self) -> None:
        self.wav_files.clear()
        self.wav_list.delete(0, "end")

        if self.logger:
            self.logger.info("WAV list cleared.")

        self.status_text.set("WAV list cleared.")

    # ------------------------------------------------------------------
    # Project operations
    # ------------------------------------------------------------------

    def _clear_discovery_tree(self) -> None:
        for item in self.discovery_tree.get_children():
            self.discovery_tree.delete(item)

    def _get_project_path(self) -> Path:
        project = Path(self.project_path.get().strip())

        if not project.is_dir():
            raise ValueError(
                "Select a valid project folder first."
            )

        return project

    def _scan_project(self) -> None:
        try:
            project = self._get_project_path()

            if self.logger:
                self.logger.info("Scanning project...")

            result = ProjectScanner.scan_project(project)
            self._clear_discovery_tree()

            categories = {
                "VPP files": result.vpp_files,
                "BNK files": result.bnk_files,
                "MBNK files": result.mbnk_files,
                "WEM files": result.wem_files,
                "XTBL files": result.xtbl_files,
            }

            for category, files in categories.items():
                category_id = self.discovery_tree.insert(
                    "",
                    "end",
                    text=category,
                    values=(f"{len(files)} file(s)",),
                    open=True,
                )

                for path in files:
                    try:
                        relative_path = path.relative_to(project)
                    except ValueError:
                        relative_path = path

                    self.discovery_tree.insert(
                        category_id,
                        "end",
                        text=path.name,
                        values=(str(relative_path),),
                    )

            if self.logger:
                self.logger.info(
                    f"Found {len(result.vpp_files)} VPP, "
                    f"{len(result.bnk_files)} BNK, "
                    f"{len(result.mbnk_files)} MBNK, "
                    f"{len(result.wem_files)} WEM, "
                    f"{len(result.xtbl_files)} XTBL file(s)."
                )

            self.status_text.set("Scan complete.")

        except Exception as error:
            self._handle_error("Scan failed.", error)

    def _validate_wav(self) -> None:
        try:
            if not self.wav_files:
                raise ValueError("No WAV files selected.")

            if not self.tools:
                raise RuntimeError("Audio tools are not initialized.")

            valid, invalid, _ = self.tools.validate_wav_batch(
                self.wav_files,
            )

            if self.logger:
                self.logger.info(
                    f"Validation complete: "
                    f"{valid} valid, {invalid} invalid."
                )

            self.status_text.set("Validation complete.")

        except Exception as error:
            self._handle_error("Validation failed.", error)

    def _inspect_banks(self) -> None:
        try:
            project = self._get_project_path()
            result = ProjectScanner.scan_project(project)

            if not result.bnk_files and not result.mbnk_files:
                if self.logger:
                    self.logger.warning(
                        "No BNK or MBNK files found."
                    )

                self.status_text.set("No bank files found.")
                return

            for bnk_path in result.bnk_files:
                if self.logger:
                    self.logger.info(
                        f"Inspecting BNK: {bnk_path.name}"
                    )

                bnk = BankInspector.inspect_bank(bnk_path)

                for warning in bnk.warnings:
                    self.logger.warning(
                        f"{bnk_path.name}: {warning}"
                    )

                for error in BankInspector.validate_bank_basic(bnk):
                    self.logger.error(
                        f"{bnk_path.name}: {error}"
                    )

                self.logger.info(
                    f"{bnk_path.name}: "
                    f"{bnk.file_size} bytes, "
                    f"{bnk.sound_count} sound marker(s)"
                )

            for mbnk_path in result.mbnk_files:
                if self.logger:
                    self.logger.info(
                        f"Inspecting MBNK: {mbnk_path.name}"
                    )

                mbnk = BankInspector.inspect_mbnk(mbnk_path)

                for warning in mbnk.warnings:
                    self.logger.warning(
                        f"{mbnk_path.name}: {warning}"
                    )

                for error in BankInspector.validate_bank_basic(mbnk):
                    self.logger.error(
                        f"{mbnk_path.name}: {error}"
                    )

                self.logger.info(
                    f"{mbnk_path.name}: "
                    f"{mbnk.file_size} bytes"
                )

            if self.logger:
                self.logger.info("Bank inspection complete.")

            self.status_text.set("Bank inspection complete.")

        except Exception as error:
            self._handle_error("Inspection failed.", error)

    def _match_songs(self) -> None:
        if self.logger:
            self.logger.info(
                "Song matching is not implemented yet."
            )

        self.status_text.set("Song matching is not implemented.")

    def _check_xtbl_sync(self) -> None:
        if self.logger:
            self.logger.info(
                "XTBL synchronization checking is not implemented yet."
            )

        self.status_text.set(
            "XTBL sync checking is not implemented."
        )

    def _generate_report(self) -> None:
        try:
            project = self._get_project_path()
            result = ProjectScanner.scan_project(project)

            report_lines = [
                "Saints Row Re-Elected Music Tool Report",
                "=" * 45,
                f"Project: {project}",
                f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}",
                "",
                f"VPP files: {len(result.vpp_files)}",
                f"BNK files: {len(result.bnk_files)}",
                f"MBNK files: {len(result.mbnk_files)}",
                f"WEM files: {len(result.wem_files)}",
                f"XTBL files: {len(result.xtbl_files)}",
                f"Selected WAV files: {len(self.wav_files)}",
            ]

            report = "\n".join(report_lines)

            self.console.configure(state="normal")
            self.console.insert("end", f"\n{report}\n")
            self.console.see("end")
            self.console.configure(state="disabled")

            self.status_text.set("Report generated.")

            if self.logger:
                self.logger.info("Report generated.")

        except Exception as error:
            self._handle_error("Report generation failed.", error)

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    def _handle_error(
        self,
        message: str,
        error: BaseException,
    ) -> None:
        if self.logger:
            self.logger.exception(message, error)

        self.status_text.set("Operation failed.")

        messagebox.showerror(
            "Operation Failed",
            f"{message}\n\n{error}",
            parent=self.root,
        )


# ============================================================================
# APPLICATION ENTRY POINT
# ============================================================================

def run() -> None:
    root = tk.Tk()
    MusicToolApp(root)
    root.mainloop()


if __name__ == "__main__":
    run()

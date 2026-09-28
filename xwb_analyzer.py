#!/usr/bin/env python3
"""
XWB Analyzer
Python 3.10+
Standard-library only.

This program is read-only. It does not modify XWB files.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import struct
import threading
import traceback
import tkinter as tk

from dataclasses import asdict, dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any, Optional


# ----------------------------------------------------------------------
# Appearance
# ----------------------------------------------------------------------

BLACK = "#000000"
GREEN = "#00FF90"
DARK_PURPLE = "#35105C"
DARKER_PURPLE = "#210A3A"
GRAY = "#AAAAAA"
DARK_GRAY = "#181818"
WHITE = "#FFFFFF"


# ----------------------------------------------------------------------
# Data structures
# ----------------------------------------------------------------------

@dataclass
class Diagnostic:
    severity: str
    code: str
    message: str
    offset: Optional[int] = None
    entry_index: Optional[int] = None


@dataclass
class Segment:
    index: int
    offset: Optional[int]
    length: Optional[int]
    end: Optional[int]
    valid: bool
    raw: str = ""


@dataclass
class AnalysisResult:
    path: str
    file_size: int
    sha256: str
    signature: str
    endianness: str
    version_candidates: list[int] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    printable_strings: list[str] = field(default_factory=list)
    hex_preview: str = ""
    raw_header_values: list[dict[str, Any]] = field(default_factory=list)


# ----------------------------------------------------------------------
# Binary helpers
# ----------------------------------------------------------------------

def read_u16(data: bytes, offset: int, endian: str = "<") -> Optional[int]:
    if offset < 0 or offset + 2 > len(data):
        return None
    return struct.unpack_from(endian + "H", data, offset)[0]


def read_u32(data: bytes, offset: int, endian: str = "<") -> Optional[int]:
    if offset < 0 or offset + 4 > len(data):
        return None
    return struct.unpack_from(endian + "I", data, offset)[0]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def printable_strings(data: bytes, minimum: int = 4) -> list[str]:
    result: list[str] = []
    current: bytearray = bytearray()

    for value in data:
        if 32 <= value <= 126:
            current.append(value)
        else:
            if len(current) >= minimum:
                result.append(current.decode("ascii", errors="replace"))
            current.clear()

    if len(current) >= minimum:
        result.append(current.decode("ascii", errors="replace"))

    return result


def format_hex_dump(data: bytes, limit: int = 512) -> str:
    data = data[:limit]
    lines: list[str] = []

    for offset in range(0, len(data), 16):
        chunk = data[offset:offset + 16]
        hex_part = " ".join(f"{byte:02X}" for byte in chunk)
        ascii_part = "".join(
            chr(byte) if 32 <= byte <= 126 else "."
            for byte in chunk
        )
        lines.append(
            f"{offset:08X}  {hex_part:<47}  {ascii_part}"
        )

    if len(data) < limit:
        lines.append(
            f"\nHex preview limited to {limit} bytes."
        )

    return "\n".join(lines)


def entropy(data: bytes) -> float:
    if not data:
        return 0.0

    counts = [0] * 256

    for value in data:
        counts[value] += 1

    length = len(data)
    total = 0.0

    for count in counts:
        if count:
            probability = count / length
            total -= probability * math.log2(probability)

    return total


# ----------------------------------------------------------------------
# Analyzer
# ----------------------------------------------------------------------

class XWBAnalyzer:
    """
    Conservative analyzer.

    It records raw values and flags probable structures. It does not
    silently assume that every version 42/44 bank uses one identical
    layout.
    """

    def analyze(self, path: Path) -> AnalysisResult:
        data = path.read_bytes()

        signature_bytes = data[:4]
        signature = signature_bytes.decode("ascii", errors="replace")

        result = AnalysisResult(
            path=str(path),
            file_size=len(data),
            sha256=sha256_bytes(data),
            signature=signature,
            endianness="little-endian",
            hex_preview=format_hex_dump(data),
        )

        if signature not in {"WBND", "DNBW"}:
            result.diagnostics.append(
                Diagnostic(
                    "ERROR",
                    "BAD_SIGNATURE",
                    f"Expected WBND or DNBW signature, found {signature!r}.",
                    0,
                )
            )

        if signature == "DNBW":
            result.endianness = "big-endian"
            endian = ">"
        else:
            endian = "<"

        self._scan_header_values(data, result, endian)
        self._find_version_candidates(data, result, endian)
        self._guess_segments(data, result, endian)
        self._validate_segments(result)
        self._analyze_entropy(data, result)
        self._find_possible_audio_signatures(data, result)

        strings = printable_strings(data)
        result.printable_strings = strings[:500]

        return result

    def _scan_header_values(
        self,
        data: bytes,
        result: AnalysisResult,
        endian: str,
    ) -> None:
        """
        Stores the first 128 bytes as 16-bit and 32-bit raw values.

        These values are retained for comparison and manual verification
        against the XACT3/XWB specifications.
        """

        limit = min(len(data), 128)

        for offset in range(0, limit - 3, 4):
            value = read_u32(data, offset, endian)

            if value is not None:
                result.raw_header_values.append(
                    {
                        "offset": f"0x{offset:08X}",
                        "u32": value,
                        "u32_hex": f"0x{value:08X}",
                    }
                )

    def _find_version_candidates(
        self,
        data: bytes,
        result: AnalysisResult,
        endian: str,
    ) -> None:
        candidates: set[int] = set()

        for offset in range(0, min(len(data), 512) - 3, 4):
            value = read_u32(data, offset, endian)

            if value in {42, 44}:
                candidates.add(value)

            value16 = read_u16(data, offset, endian)

            if value16 in {42, 44}:
                candidates.add(value16)

        result.version_candidates = sorted(candidates)

        if not result.version_candidates:
            result.diagnostics.append(
                Diagnostic(
                    "WARNING",
                    "VERSION_NOT_FOUND",
                    "No obvious version value of 42 or 44 was found in the first 512 bytes.",
                )
            )
        else:
            result.diagnostics.append(
                Diagnostic(
                    "INFO",
                    "VERSION_CANDIDATE",
                    "Possible XWB header version(s): "
                    + ", ".join(map(str, result.version_candidates)),
                )
            )

    def _guess_segments(
        self,
        data: bytes,
        result: AnalysisResult,
        endian: str,
    ) -> None:
        """
        Searches the first 512 bytes for plausible offset/length pairs.

        This is deliberately reported as a candidate rather than asserted
        to be the final segment table. It avoids corrupting the report when
        a bank uses a different header variant.
        """

        size = len(data)
        candidates: list[Segment] = []

        for offset in range(0, min(size, 512) - 7, 4):
            segment_offset = read_u32(data, offset, endian)
            segment_length = read_u32(data, offset + 4, endian)

            if segment_offset is None or segment_length is None:
                continue

            end = segment_offset + segment_length

            if (
                segment_offset >= 0
                and segment_length > 0
                and segment_offset < size
                and end <= size
            ):
                candidates.append(
                    Segment(
                        index=len(candidates),
                        offset=segment_offset,
                        length=segment_length,
                        end=end,
                        valid=True,
                        raw=(
                            f"header=0x{offset:08X}; "
                            f"offset=0x{segment_offset:08X}; "
                            f"length=0x{segment_length:08X}"
                        ),
                    )
                )

        # Remove exact duplicates.
        unique: list[Segment] = []
        seen: set[tuple[int, int]] = set()

        for segment in candidates:
            key = (segment.offset or 0, segment.length or 0)

            if key not in seen:
                seen.add(key)
                segment.index = len(unique)
                unique.append(segment)

        result.segments = unique[:32]

        if not result.segments:
            result.diagnostics.append(
                Diagnostic(
                    "WARNING",
                    "NO_SEGMENT_CANDIDATES",
                    "No plausible offset/length segment pairs were detected.",
                )
            )

    def _validate_segments(self, result: AnalysisResult) -> None:
        segments = [
            segment for segment in result.segments
            if segment.offset is not None
            and segment.length is not None
            and segment.end is not None
        ]

        for left_index, left in enumerate(segments):
            if left.offset % 4 != 0:
                result.diagnostics.append(
                    Diagnostic(
                        "WARNING",
                        "UNALIGNED_SEGMENT",
                        f"Candidate segment {left.index} begins at a non-4-byte boundary.",
                        left.offset,
                    )
                )

            for right in segments[left_index + 1:]:
                if (
                    left.offset < right.end
                    and right.offset < left.end
                    and left.offset != right.offset
                ):
                    result.diagnostics.append(
                        Diagnostic(
                            "WARNING",
                            "SEGMENT_OVERLAP",
                            f"Candidate segments {left.index} and {right.index} overlap.",
                            left.offset,
                        )
                    )

    def _analyze_entropy(
        self,
        data: bytes,
        result: AnalysisResult,
    ) -> None:
        value = entropy(data)

        result.diagnostics.append(
            Diagnostic(
                "INFO",
                "FILE_ENTROPY",
                f"Whole-file byte entropy: {value:.4f} bits per byte.",
            )
        )

        if value > 7.8:
            result.diagnostics.append(
                Diagnostic(
                    "INFO",
                    "HIGH_ENTROPY",
                    "The file contains highly packed or compressed-looking data.",
                )
            )

    def _find_possible_audio_signatures(
        self,
        data: bytes,
        result: AnalysisResult,
    ) -> None:
        signatures = {
            b"RIFF": "RIFF/WAV",
            b"XWMA": "xWMA",
            b"DPDS": "possible XMA seek table",
            b"XMA2": "possible XMA signature",
        }

        for signature, description in signatures.items():
            position = data.find(signature)

            if position >= 0:
                result.diagnostics.append(
                    Diagnostic(
                        "INFO",
                        "AUDIO_SIGNATURE",
                        f"Found {description} signature at 0x{position:08X}.",
                        position,
                    )
                )


# ----------------------------------------------------------------------
# Comparison
# ----------------------------------------------------------------------

def compare_files(
    original_path: Path,
    modified_path: Path,
) -> str:
    original = original_path.read_bytes()
    modified = modified_path.read_bytes()

    lines: list[str] = [
        "XWB FILE COMPARISON",
        "====================",
        "",
        f"Original:  {original_path}",
        f"Modified:  {modified_path}",
        "",
        f"Original size:  {len(original):,} bytes",
        f"Modified size:  {len(modified):,} bytes",
        f"Size delta:     {len(modified) - len(original):+,} bytes",
        "",
        f"Original SHA-256: {sha256_bytes(original)}",
        f"Modified SHA-256: {sha256_bytes(modified)}",
        "",
    ]

    if original == modified:
        lines.append("Files are byte-for-byte identical.")
        return "\n".join(lines)

    first_difference = None

    for index, (left, right) in enumerate(zip(original, modified)):
        if left != right:
            first_difference = index
            break

    if first_difference is None:
        first_difference = min(len(original), len(modified))

    lines.extend(
        [
            f"First differing offset: 0x{first_difference:08X}",
            f"Original byte: "
            f"{original[first_difference]:02X}"
            if first_difference < len(original)
            else "Original byte: <none>",
            f"Modified byte: "
            f"{modified[first_difference]:02X}"
            if first_difference < len(modified)
            else "Modified byte: <none>",
            "",
        ]
    )

    changed_bytes = sum(
        1
        for left, right in zip(original, modified)
        if left != right
    )

    lines.append(f"Changed bytes within shared range: {changed_bytes:,}")

    return "\n".join(lines)


# ----------------------------------------------------------------------
# Formatting
# ----------------------------------------------------------------------

def result_to_text(result: AnalysisResult) -> str:
    lines: list[str] = [
        "XWB FORENSIC ANALYSIS",
        "=====================",
        "",
        f"File:       {result.path}",
        f"Size:       {result.file_size:,} bytes",
        f"SHA-256:    {result.sha256}",
        f"Signature:  {result.signature!r}",
        f"Endianness: {result.endianness}",
        "",
        "VERSION CANDIDATES",
        "------------------",
    ]

    if result.version_candidates:
        lines.append(
            ", ".join(str(value) for value in result.version_candidates)
        )
    else:
        lines.append("None detected")

    lines.extend(
        [
            "",
            "SEGMENT CANDIDATES",
            "------------------",
        ]
    )

    if result.segments:
        for segment in result.segments:
            lines.append(
                f"{segment.index:02d}: "
                f"offset=0x{segment.offset:08X} "
                f"length=0x{segment.length:08X} "
                f"end=0x{segment.end:08X}"
            )
    else:
        lines.append("None detected")

    lines.extend(
        [
            "",
            "DIAGNOSTICS",
            "------------",
        ]
    )

    for diagnostic in result.diagnostics:
        location = ""

        if diagnostic.offset is not None:
            location += f" offset=0x{diagnostic.offset:08X}"

        if diagnostic.entry_index is not None:
            location += f" entry={diagnostic.entry_index}"

        lines.append(
            f"[{diagnostic.severity}] "
            f"{diagnostic.code}:{location} "
            f"{diagnostic.message}"
        )

    lines.extend(
        [
            "",
            "PRINTABLE STRINGS",
            "-----------------",
        ]
    )

    if result.printable_strings:
        lines.extend(result.printable_strings)
    else:
        lines.append("None found")

    lines.extend(
        [
            "",
            "HEX PREVIEW",
            "-----------",
            result.hex_preview,
            "",
            "RAW HEADER VALUES",
            "-----------------",
        ]
    )

    for value in result.raw_header_values:
        lines.append(
            f"{value['offset']}: "
            f"{value['u32']} "
            f"({value['u32_hex']})"
        )

    return "\n".join(lines)


def result_to_json(result: AnalysisResult) -> str:
    return json.dumps(asdict(result), indent=2)


# ----------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------

class Application(tk.Tk):
    def __init__(self) -> None:
        super().__init__()

        self.title("XWB Forensic Analyzer")
        self.geometry("1200x800")
        self.minsize(850, 600)
        self.configure(bg=BLACK)

        self.analyzer = XWBAnalyzer()
        self.current_result: Optional[AnalysisResult] = None
        self.log_lines: list[str] = []

        self.text_size = tk.IntVar(value=11)
        self.status_text = tk.StringVar(value="Ready.")
        self.original_path = tk.StringVar()
        self.modified_path = tk.StringVar()

        self._configure_styles()
        self._build_interface()

    def _configure_styles(self) -> None:
        style = ttk.Style(self)

        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            "TFrame",
            background=BLACK,
        )

        style.configure(
            "TLabel",
            background=BLACK,
            foreground=GREEN,
        )

        style.configure(
            "TButton",
            background=DARKER_PURPLE,
            foreground=GREEN,
            padding=5,
        )

        style.map(
            "TButton",
            background=[
                ("active", DARK_PURPLE),
                ("pressed", DARK_PURPLE),
            ],
            foreground=[
                ("active", WHITE),
                ("pressed", WHITE),
            ],
        )

        style.configure(
            "TEntry",
            fieldbackground=BLACK,
            foreground=GREEN,
            insertcolor=GREEN,
        )

        style.configure(
            "TCheckbutton",
            background=BLACK,
            foreground=GREEN,
        )

        style.configure(
            "TSpinbox",
            fieldbackground=BLACK,
            foreground=GREEN,
            insertcolor=GREEN,
        )

    def _build_interface(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill=tk.X, padx=8, pady=8)

        ttk.Label(top, text="Original XWB:").grid(
            row=0, column=0, sticky="w", padx=4, pady=3
        )

        ttk.Entry(
            top,
            textvariable=self.original_path,
            width=80,
        ).grid(row=0, column=1, sticky="ew", padx=4, pady=3)

        ttk.Button(
            top,
            text="Browse",
            command=self.choose_original,
        ).grid(row=0, column=2, padx=4, pady=3)

        ttk.Label(top, text="Modified XWB:").grid(
            row=1, column=0, sticky="w", padx=4, pady=3
        )

        ttk.Entry(
            top,
            textvariable=self.modified_path,
            width=80,
        ).grid(row=1, column=1, sticky="ew", padx=4, pady=3)

        ttk.Button(
            top,
            text="Browse",
            command=self.choose_modified,
        ).grid(row=1, column=2, padx=4, pady=3)

        top.columnconfigure(1, weight=1)

        buttons = ttk.Frame(self)
        buttons.pack(fill=tk.X, padx=8, pady=(0, 8))

        ttk.Button(
            buttons,
            text="Analyze Original",
            command=self.analyze_original,
        ).pack(side=tk.LEFT, padx=3)

        ttk.Button(
            buttons,
            text="Compare Files",
            command=self.compare,
        ).pack(side=tk.LEFT, padx=3)

        ttk.Button(
            buttons,
            text="Save Output",
            command=self.save_output,
        ).pack(side=tk.LEFT, padx=3)

        ttk.Button(
            buttons,
            text="Save Log",
            command=self.save_log,
        ).pack(side=tk.LEFT, padx=3)

        ttk.Button(
            buttons,
            text="Clear",
            command=self.clear_output,
        ).pack(side=tk.LEFT, padx=3)

        ttk.Label(buttons, text="Text size:").pack(
            side=tk.LEFT, padx=(25, 3)
        )

        ttk.Spinbox(
            buttons,
            from_=7,
            to=32,
            textvariable=self.text_size,
            width=5,
            command=self.change_text_size,
        ).pack(side=tk.LEFT, padx=3)

        ttk.Button(
            buttons,
            text="Apply",
            command=self.change_text_size,
        ).pack(side=tk.LEFT, padx=3)

        self.output = ScrolledText(
            self,
            wrap=tk.NONE,
            bg=BLACK,
            fg=GREEN,
            insertbackground=GREEN,
            selectbackground=DARK_PURPLE,
            selectforeground=GREEN,
            undo=False,
            font=("Consolas", self.text_size.get()),
        )

        self.output.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        self.output.bind("<Control-a>", self.select_all)
        self.output.bind("<Control-A>", self.select_all)

        self.context_menu = tk.Menu(
            self,
            tearoff=False,
            bg=BLACK,
            fg=GREEN,
            activebackground=DARK_PURPLE,
            activeforeground=GREEN,
        )

        self.context_menu.add_command(
            label="Copy",
            command=self.copy_selection,
        )

        self.context_menu.add_command(
            label="Select All",
            command=self.select_all,
        )

        self.context_menu.add_separator()

        self.context_menu.add_command(
            label="Clear",
            command=self.clear_output,
        )

        self.output.bind("<Button-3>", self.show_context_menu)

        status = ttk.Label(
            self,
            textvariable=self.status_text,
            anchor="w",
        )
        status.pack(fill=tk.X, padx=8, pady=4)

    # --------------------------------------------------------------
    # GUI actions
    # --------------------------------------------------------------

    def choose_original(self) -> None:
        path = filedialog.askopenfilename(
            title="Select original XWB file",
            filetypes=[
                ("XWB files", "*.xwb"),
                ("All files", "*.*"),
            ],
        )

        if path:
            self.original_path.set(path)

    def choose_modified(self) -> None:
        path = filedialog.askopenfilename(
            title="Select modified XWB file",
            filetypes=[
                ("XWB files", "*.xwb"),
                ("All files", "*.*"),
            ],
        )

        if path:
            self.modified_path.set(path)

    def analyze_original(self) -> None:
        path_text = self.original_path.get().strip()

        if not path_text:
            messagebox.showerror("Missing file", "Select an XWB file first.")
            return

        self._run_background(
            lambda: self._analyze_path(Path(path_text))
        )

    def compare(self) -> None:
        original_text = self.original_path.get().strip()
        modified_text = self.modified_path.get().strip()

        if not original_text or not modified_text:
            messagebox.showerror(
                "Missing files",
                "Select both an original and modified XWB file.",
            )
            return

        self._run_background(
            lambda: self._compare_paths(
                Path(original_text),
                Path(modified_text),
            )
        )

    def _run_background(self, function) -> None:
        self.status_text.set("Working...")

        thread = threading.Thread(
            target=self._background_wrapper,
            args=(function,),
            daemon=True,
        )

        thread.start()

    def _background_wrapper(self, function) -> None:
        try:
            output = function()
            self.after(0, lambda: self._show_output(output))
        except Exception as exc:
            error_text = (
                f"{type(exc).__name__}: {exc}\n\n"
                f"{traceback.format_exc()}"
            )

            self._write_log(error_text)

            self.after(
                0,
                lambda: self._show_output(
                    "ANALYSIS ERROR\n\n" + error_text
                ),
            )

    def _analyze_path(self, path: Path) -> str:
        if not path.is_file():
            raise FileNotFoundError(path)

        result = self.analyzer.analyze(path)
        self.current_result = result

        output = result_to_text(result)

        self._write_log(
            f"Analyzed {path}\n"
            f"Size: {result.file_size}\n"
            f"SHA-256: {result.sha256}\n"
        )

        return output

    def _compare_paths(
        self,
        original: Path,
        modified: Path,
    ) -> str:
        if not original.is_file():
            raise FileNotFoundError(original)

        if not modified.is_file():
            raise FileNotFoundError(modified)

        output = compare_files(original, modified)

        self._write_log(
            f"Compared {original} against {modified}\n"
        )

        return output

    def _show_output(self, output: str) -> None:
        self.output.delete("1.0", tk.END)
        self.output.insert("1.0", output)
        self.status_text.set("Finished.")

    def clear_output(self) -> None:
        self.output.delete("1.0", tk.END)
        self.status_text.set("Cleared.")

    def change_text_size(self) -> None:
        try:
            size = max(7, min(32, int(self.text_size.get())))
        except (TypeError, ValueError):
            size = 11
            self.text_size.set(size)

        self.output.configure(font=("Consolas", size))
        self.status_text.set(f"Text size changed to {size}.")

    def select_all(self, event=None):
        self.output.tag_add(tk.SEL, "1.0", tk.END)
        self.output.mark_set(tk.INSERT, "1.0")
        self.output.see(tk.INSERT)
        return "break"

    def copy_selection(self) -> None:
        try:
            selected = self.output.get(tk.SEL_FIRST, tk.SEL_LAST)
        except tk.TclError:
            return

        self.clipboard_clear()
        self.clipboard_append(selected)
        self.update()

    def show_context_menu(self, event) -> None:
        self.context_menu.tk_popup(event.x_root, event.y_root)

    # --------------------------------------------------------------
    # Output and logging
    # --------------------------------------------------------------

    def save_output(self) -> None:
        contents = self.output.get("1.0", tk.END).rstrip()

        if not contents:
            messagebox.showinfo("Nothing to save", "The output window is empty.")
            return

        path = filedialog.asksaveasfilename(
            title="Save analysis output",
            defaultextension=".txt",
            filetypes=[
                ("Text files", "*.txt"),
                ("JSON files", "*.json"),
                ("All files", "*.*"),
            ],
        )

        if not path:
            return

        output_path = Path(path)

        if output_path.suffix.lower() == ".json":
            if self.current_result is None:
                payload = {
                    "type": "text-output",
                    "output": contents,
                }
            else:
                payload = asdict(self.current_result)

            output_path.write_text(
                json.dumps(payload, indent=2),
                encoding="utf-8",
            )
        else:
            output_path.write_text(contents, encoding="utf-8")

        self._write_log(f"Saved output to {output_path}")
        self.status_text.set(f"Saved output to {output_path}")

    def save_log(self) -> None:
        if not self.log_lines:
            messagebox.showinfo("No log data", "There is no log data to save.")
            return

        path = filedialog.asksaveasfilename(
            title="Save program log",
            defaultextension=".log",
            filetypes=[
                ("Log files", "*.log"),
                ("Text files", "*.txt"),
                ("All files", "*.*"),
            ],
        )

        if not path:
            return

        Path(path).write_text(
            "\n".join(self.log_lines),
            encoding="utf-8",
        )

        self.status_text.set(f"Saved log to {path}")

    def _write_log(self, text: str) -> None:
        self.log_lines.append(text)


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main() -> None:
    application = Application()
    application.mainloop()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3

import os
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter import font as tkfont

import xwb_build


# ----------------------------------------------------------------------
# Theme
# ----------------------------------------------------------------------

BG = "#000000"
FG = "#00FF90"
PURPLE = "#4B0082"
PURPLE_DARK = "#260040"
ENTRY_BG = "#06130D"
DISABLED_FG = "#397A5D"


class XWBBuilderUI(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("XWB Wave Bank Builder")
        self.geometry("1000x700")
        self.minsize(800, 560)
        self.configure(bg=BG)

        self.base_font_size = 11
        self.wav_files = []

        self.output_var = tk.StringVar()
        self.version_var = tk.IntVar(value=44)
        self.bank_name_var = tk.StringVar(value="bank")
        self.alignment_var = tk.StringVar(value="4")
        self.friendly_names_var = tk.BooleanVar(value=False)
        self.compact_var = tk.BooleanVar(value=False)
        self.streaming_var = tk.BooleanVar(value=False)
        self.font_size_var = tk.IntVar(value=self.base_font_size)

        self._configure_styles()
        self._create_fonts()
        self._build_ui()
        self._apply_fonts()

    # ------------------------------------------------------------------
    # Styling
    # ------------------------------------------------------------------

    def _configure_styles(self):
        style = ttk.Style(self)

        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            ".",
            background=BG,
            foreground=FG,
            fieldbackground=ENTRY_BG,
            bordercolor=FG,
            lightcolor=FG,
            darkcolor=FG,
            troughcolor=PURPLE_DARK,
            selectbackground=PURPLE,
            selectforeground=FG,
        )

        style.configure(
            "TFrame",
            background=BG,
        )

        style.configure(
            "TLabel",
            background=BG,
            foreground=FG,
        )

        style.configure(
            "TButton",
            background=BG,
            foreground=FG,
            borderwidth=1,
            padding=(8, 5),
        )

        style.map(
            "TButton",
            background=[
                ("active", PURPLE),
                ("pressed", PURPLE_DARK),
            ],
            foreground=[
                ("disabled", DISABLED_FG),
                ("active", FG),
            ],
        )

        style.configure(
            "TCheckbutton",
            background=BG,
            foreground=FG,
        )

        style.map(
            "TCheckbutton",
            background=[("active", BG)],
            foreground=[("disabled", DISABLED_FG)],
        )

        style.configure(
            "TRadiobutton",
            background=BG,
            foreground=FG,
        )

        style.map(
            "TRadiobutton",
            background=[("active", BG)],
            foreground=[("disabled", DISABLED_FG)],
        )

        style.configure(
            "TEntry",
            fieldbackground=ENTRY_BG,
            foreground=FG,
            insertcolor=FG,
            bordercolor=FG,
        )

        style.configure(
            "TCombobox",
            fieldbackground=ENTRY_BG,
            foreground=FG,
            background=BG,
            selectbackground=PURPLE,
            selectforeground=FG,
        )

        style.configure(
            "TScale",
            background=BG,
            troughcolor=PURPLE_DARK,
        )

        style.configure(
            "TLabelframe",
            background=BG,
            foreground=FG,
            bordercolor=FG,
        )

        style.configure(
            "TLabelframe.Label",
            background=BG,
            foreground=FG,
        )

    def _create_fonts(self):
        self.normal_font = tkfont.Font(
            family="DejaVu Sans",
            size=self.base_font_size,
        )

        self.heading_font = tkfont.Font(
            family="DejaVu Sans",
            size=self.base_font_size + 2,
            weight="bold",
        )

        self.monospace_font = tkfont.Font(
            family="DejaVu Sans Mono",
            size=self.base_font_size,
        )

    def _apply_fonts(self):
        size = self.font_size_var.get()

        self.normal_font.configure(size=size)
        self.heading_font.configure(size=size + 2)
        self.monospace_font.configure(size=size)

        self.option_add("*Font", self.normal_font)
        self.option_add("*Entry.font", self.normal_font)
        self.option_add("*Listbox.font", self.normal_font)
        self.option_add("*Text.font", self.monospace_font)

        if hasattr(self, "title_label"):
            self.title_label.configure(font=self.heading_font)

    def change_text_size(self, value=None):
        self.base_font_size = self.font_size_var.get()
        self._apply_fonts()

    # ------------------------------------------------------------------
    # User interface
    # ------------------------------------------------------------------

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.rowconfigure(4, weight=1)

        self.title_label = tk.Label(
            self,
            text="XWB Wave Bank Builder",
            bg=BG,
            fg=FG,
            font=self.heading_font,
        )
        self.title_label.grid(
            row=0,
            column=0,
            sticky="w",
            padx=16,
            pady=(14, 8),
        )

        self._build_options_panel()
        self._build_file_panel()
        self._build_log_panel()
        self._build_bottom_buttons()

    def _build_options_panel(self):
        frame = ttk.LabelFrame(self, text="Wave Bank Options")
        frame.grid(
            row=1,
            column=0,
            sticky="ew",
            padx=16,
            pady=8,
        )

        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(4, weight=1)

        ttk.Label(frame, text="Output file:").grid(
            row=0, column=0, sticky="w", padx=8, pady=7
        )

        self.output_entry = ttk.Entry(
            frame,
            textvariable=self.output_var,
        )
        self.output_entry.grid(
            row=0,
            column=1,
            columnspan=3,
            sticky="ew",
            padx=8,
            pady=7,
        )

        ttk.Button(
            frame,
            text="Browse...",
            command=self.choose_output,
        ).grid(
            row=0,
            column=4,
            sticky="e",
            padx=8,
            pady=7,
        )

        ttk.Label(frame, text="Format version:").grid(
            row=1, column=0, sticky="w", padx=8, pady=7
        )

        version_frame = ttk.Frame(frame)
        version_frame.grid(
            row=1,
            column=1,
            sticky="w",
            padx=8,
            pady=7,
        )

        ttk.Radiobutton(
            version_frame,
            text="42",
            variable=self.version_var,
            value=42,
        ).pack(side="left", padx=(0, 16))

        ttk.Radiobutton(
            version_frame,
            text="44",
            variable=self.version_var,
            value=44,
        ).pack(side="left")

        ttk.Label(frame, text="Bank name:").grid(
            row=1, column=2, sticky="e", padx=8, pady=7
        )

        ttk.Entry(
            frame,
            textvariable=self.bank_name_var,
            width=20,
        ).grid(
            row=1,
            column=3,
            sticky="ew",
            padx=8,
            pady=7,
        )

        ttk.Label(frame, text="Entry alignment:").grid(
            row=2, column=0, sticky="w", padx=8, pady=7
        )

        ttk.Combobox(
            frame,
            textvariable=self.alignment_var,
            values=("1", "2", "4", "8", "16", "32", "64", "128"),
            state="readonly",
            width=12,
        ).grid(
            row=2,
            column=1,
            sticky="w",
            padx=8,
            pady=7,
        )

        ttk.Checkbutton(
            frame,
            text="Friendly names",
            variable=self.friendly_names_var,
        ).grid(
            row=2,
            column=2,
            sticky="w",
            padx=8,
            pady=7,
        )

        ttk.Checkbutton(
            frame,
            text="Compact metadata",
            variable=self.compact_var,
        ).grid(
            row=2,
            column=3,
            sticky="w",
            padx=8,
            pady=7,
        )

        ttk.Checkbutton(
            frame,
            text="Streaming bank",
            variable=self.streaming_var,
        ).grid(
            row=3,
            column=3,
            sticky="w",
            padx=8,
            pady=7,
        )

        ttk.Label(frame, text="Text size:").grid(
            row=4, column=0, sticky="w", padx=8, pady=7
        )

        self.font_scale = ttk.Scale(
            frame,
            from_=8,
            to=24,
            variable=self.font_size_var,
            command=self.change_text_size,
        )
        self.font_scale.grid(
            row=3,
            column=1,
            sticky="ew",
            padx=8,
            pady=7,
        )

        self.font_size_label = ttk.Label(
            frame,
            textvariable=self.font_size_var,
        )
        self.font_size_label.grid(
            row=3,
            column=2,
            sticky="w",
            padx=8,
            pady=7,
        )

    def _build_file_panel(self):
        frame = ttk.LabelFrame(self, text="Input WAV Files")
        frame.grid(
            row=2,
            column=0,
            sticky="nsew",
            padx=16,
            pady=8,
        )

        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        list_frame = ttk.Frame(frame)
        list_frame.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=8,
            pady=8,
        )

        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        self.file_list = tk.Listbox(
            list_frame,
            bg=ENTRY_BG,
            fg=FG,
            selectbackground=PURPLE,
            selectforeground=FG,
            activestyle="none",
            exportselection=False,
            relief="solid",
            borderwidth=1,
        )
        self.file_list.grid(
            row=0,
            column=0,
            sticky="nsew",
        )

        scrollbar = ttk.Scrollbar(
            list_frame,
            orient="vertical",
            command=self.file_list.yview,
        )
        scrollbar.grid(
            row=0,
            column=1,
            sticky="ns",
        )

        self.file_list.configure(
            yscrollcommand=scrollbar.set
        )

        buttons = ttk.Frame(frame)
        buttons.grid(
            row=0,
            column=1,
            sticky="ns",
            padx=(0, 8),
            pady=8,
        )

        ttk.Button(
            buttons,
            text="Add WAV files",
            command=self.add_files,
        ).pack(fill="x", pady=3)

        ttk.Button(
            buttons,
            text="Remove selected",
            command=self.remove_selected,
        ).pack(fill="x", pady=3)

        ttk.Button(
            buttons,
            text="Move up",
            command=self.move_up,
        ).pack(fill="x", pady=3)

        ttk.Button(
            buttons,
            text="Move down",
            command=self.move_down,
        ).pack(fill="x", pady=3)

        ttk.Button(
            buttons,
            text="Clear list",
            command=self.clear_files,
        ).pack(fill="x", pady=3)

    def _build_log_panel(self):
        frame = ttk.LabelFrame(self, text="Build Log")
        frame.grid(
            row=4,
            column=0,
            sticky="nsew",
            padx=16,
            pady=8,
        )

        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(
            frame,
            bg=ENTRY_BG,
            fg=FG,
            insertbackground=FG,
            selectbackground=PURPLE,
            selectforeground=FG,
            relief="solid",
            borderwidth=1,
            wrap="word",
            state="disabled",
        )
        self.log_text.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=8,
            pady=8,
        )

        scrollbar = ttk.Scrollbar(
            frame,
            orient="vertical",
            command=self.log_text.yview,
        )
        scrollbar.grid(
            row=0,
            column=1,
            sticky="ns",
            padx=(0, 8),
            pady=8,
        )

        self.log_text.configure(
            yscrollcommand=scrollbar.set
        )

    def _build_bottom_buttons(self):
        frame = ttk.Frame(self)
        frame.grid(
            row=5,
            column=0,
            sticky="e",
            padx=16,
            pady=(0, 14),
        )

        ttk.Button(
            frame,
            text="Build XWB",
            command=self.build_bank,
        ).pack(side="right", padx=(8, 0))

        ttk.Button(
            frame,
            text="Quit",
            command=self.destroy,
        ).pack(side="right")

    # ------------------------------------------------------------------
    # File management
    # ------------------------------------------------------------------

    def choose_output(self):
        filename = filedialog.asksaveasfilename(
            title="Save XWB file",
            defaultextension=".xwb",
            filetypes=[
                ("XACT Wave Bank", "*.xwb"),
                ("All files", "*.*"),
            ],
        )

        if filename:
            self.output_var.set(filename)

    def add_files(self):
        files = filedialog.askopenfilenames(
            title="Select PCM WAV files",
            filetypes=[
                ("WAV files", "*.wav"),
                ("All files", "*.*"),
            ],
        )

        for filepath in files:
            if filepath not in self.wav_files:
                self.wav_files.append(filepath)
                self.file_list.insert(
                    tk.END,
                    filepath,
                )

    def remove_selected(self):
        selected = list(self.file_list.curselection())

        for index in reversed(selected):
            self.file_list.delete(index)
            del self.wav_files[index]

    def clear_files(self):
        self.wav_files.clear()
        self.file_list.delete(0, tk.END)

    def move_up(self):
        selected = self.file_list.curselection()

        if not selected:
            return

        index = selected[0]

        if index == 0:
            return

        self.wav_files[index - 1], self.wav_files[index] = (
            self.wav_files[index],
            self.wav_files[index - 1],
        )

        self.refresh_file_list()
        self.file_list.selection_set(index - 1)

    def move_down(self):
        selected = self.file_list.curselection()

        if not selected:
            return

        index = selected[0]

        if index >= len(self.wav_files) - 1:
            return

        self.wav_files[index + 1], self.wav_files[index] = (
            self.wav_files[index],
            self.wav_files[index + 1],
        )

        self.refresh_file_list()
        self.file_list.selection_set(index + 1)

    def refresh_file_list(self):
        self.file_list.delete(0, tk.END)

        for filepath in self.wav_files:
            self.file_list.insert(tk.END, filepath)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def log(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build_bank(self):
        self.clear_log()

        output_path = self.output_var.get().strip()
        bank_name = self.bank_name_var.get().strip()

        if not output_path:
            messagebox.showerror(
                "Missing output file",
                "Choose an output .xwb file.",
            )
            return

        if not bank_name:
            messagebox.showerror(
                "Missing bank name",
                "Enter a bank name.",
            )
            return

        if not self.wav_files:
            messagebox.showerror(
                "No input files",
                "Add at least one WAV file.",
            )
            return

        try:
            alignment = int(self.alignment_var.get())
        except ValueError:
            messagebox.showerror(
                "Invalid alignment",
                "Alignment must be an integer.",
            )
            return

        if alignment < 1 or alignment & (alignment - 1):
            messagebox.showerror(
                "Invalid alignment",
                "Alignment must be a positive power of two.",
            )
            return

        output_directory = os.path.dirname(
            os.path.abspath(output_path)
        )

        if not os.path.isdir(output_directory):
            messagebox.showerror(
                "Invalid output directory",
                "The selected output directory does not exist.",
            )
            return

        self.log("Starting build...")
        self.log(f"Version: {self.version_var.get()}")
        self.log(f"Output: {output_path}")
        self.log(f"Bank name: {bank_name}")
        self.log(f"Alignment: {alignment}")
        self.log(f"Friendly names: {self.friendly_names_var.get()}")
        self.log(f"Compact metadata: {self.compact_var.get()}")
        self.log(f"Entries: {len(self.wav_files)}")
        self.log("")

        try:
            xwb_build.build_xwb(
                output_path=output_path,
                wav_files=self.wav_files,
                version=self.version_var.get(),
                friendly_names=self.friendly_names_var.get(),
                bank_name=bank_name,
                alignment=alignment,
                compact=self.compact_var.get(),
                streaming=self.streaming_var.get(),
            )

        except Exception as exc:
            self.log(f"ERROR: {exc}")
            messagebox.showerror(
                "Build failed",
                str(exc),
            )
            return

        self.log("")
        self.log("Build completed successfully.")

        messagebox.showinfo(
            "Build complete",
            f"Created:\n{output_path}",
        )


def main():
    app = XWBBuilderUI()
    app.mainloop()


if __name__ == "__main__":
    main()

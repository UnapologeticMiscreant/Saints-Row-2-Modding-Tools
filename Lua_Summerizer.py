#!/usr/bin/env python3
"""
Saints Row 2 Lua Script Analyzer

Analyzes Lua files and displays:
- Functions
- Global variables
- Tables
- Navpoint references
- UI elements
- External API calls
- Triggers
- Comments
"""

import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QFontDialog,
    QApplication,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QCheckBox,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


# =============================================================================
# COLORS
# =============================================================================

COLORS = {
    "background": "#000000",
    "foreground_dark_pink": "#C71585",
    "text_green": "#00FF90",
    "button_purple": "#4B0082",
    "button_hover": "#6A0DAD",
    "border_gray": "#333333",
}

TEXT_SIZE = 13


# =============================================================================
# LUA PARSER
# =============================================================================

class LuaFileParser:
    def __init__(self, filepath: str):
        self.filepath = Path(filepath)
        self.content = ""

        self.functions: List[Dict] = []
        self.global_vars: Dict[str, str] = {}
        self.tables: Dict[str, List[str]] = {}
        self.navpoint_refs: List[str] = []
        self.ui_elements: List[str] = []
        self.external_calls: List[str] = []
        self.triggers: List[str] = []
        self.comments: List[str] = []
        self.file_type = "Unknown"

    def parse(self) -> bool:
        try:
            with self.filepath.open(
                "r",
                encoding="utf-8",
                errors="ignore",
            ) as file:
                self.content = file.read()

            self._extract_functions()
            self._extract_global_variables()
            self._extract_tables()
            self._extract_navpoint_references()
            self._extract_ui_elements()
            self._extract_external_calls()
            self._extract_triggers()
            self._extract_comments()
            self._detect_file_type()

            return True

        except Exception as error:
            print(f"Error parsing {self.filepath}: {error}")
            return False

    def _extract_functions(self) -> None:
        pattern = (
            r"function\s+"
            r"(?:([a-zA-Z_][\w.]*)::)?"
            r"([a-zA-Z_][\w]*)"
            r"\((.*?)\)"
        )

        for match in re.finditer(pattern, self.content):
            module = match.group(1) or ""
            function_name = match.group(2)
            parameters = match.group(3) or ""

            full_name = f"{module}{function_name}" if module else function_name

            self.functions.append(
                {
                    "name": full_name,
                    "params": parameters,
                    "line": self._get_line_number(match.start()),
                }
            )

    def _extract_global_variables(self) -> None:
        # Saints Row-style global variables, for example:
        # $Mission_Name: "value"
        dollar_pattern = r'\$([A-Za-z_][A-Za-z0-9_]+):\s*"([^"]*)"'

        for match in re.finditer(dollar_pattern, self.content):
            self.global_vars[match.group(1)] = match.group(2)

        # Standard uppercase Lua globals, for example:
        # MAX_PLAYERS = 4
        lua_pattern = r"^([A-Z_][A-Z0-9_]*)\s*=\s*(.+)$"

        for match in re.finditer(lua_pattern, self.content, re.MULTILINE):
            self.global_vars[match.group(1)] = match.group(2)[:100]

    def _extract_tables(self) -> None:
        pattern = r"(\w+)\s*=\s*{\s*([^}]*)}"

        for match in re.finditer(pattern, self.content, re.DOTALL):
            table_name = match.group(1)
            table_content = match.group(2)
            keys = re.findall(r"(\w+)\s*[=:]", table_content)

            self.tables[table_name] = keys[:10]

    def _extract_navpoint_references(self) -> None:
        pattern = r'(?:teleport|move_to|turn_to)\s*\(\s*"([^"]*)"'

        for match in re.finditer(pattern, self.content):
            self.navpoint_refs.append(match.group(1))

    def _extract_ui_elements(self) -> None:
        pattern = r'vint_object_find\s*\(\s*"([^"]*)"'

        for match in re.finditer(pattern, self.content):
            self.ui_elements.append(match.group(1))

    def _extract_external_calls(self) -> None:
        patterns = [
            r"(vint_\w+)\s*\(",
            r"(audio_\w+)\s*\(",
            r"(group_\w+)\s*\(",
            r"(vehicle_\w+)\s*\(",
            r"(delay|message|thread_new)\s*\(",
        ]

        for pattern in patterns:
            for match in re.finditer(pattern, self.content):
                call_name = match.group(1)

                if call_name not in self.external_calls:
                    self.external_calls.append(call_name)

    def _extract_triggers(self) -> None:
        pattern = r'\$Trigger:\s*"([^"]*)"'

        for match in re.finditer(pattern, self.content):
            self.triggers.append(match.group(1))

    def _extract_comments(self) -> None:
        pattern = r"--\s*(.*)$"
        matches = re.finditer(pattern, self.content, re.MULTILINE)

        self.comments = [
            match.group(1).strip()
            for match in list(matches)[:5]
        ]

    def _detect_file_type(self) -> None:
        content_lower = self.content.lower()

        if "hud_" in content_lower:
            self.file_type = "HUD/UI Script"
        elif "activity" in content_lower:
            self.file_type = "Activity/Mission Script"
        elif "ai_" in content_lower:
            self.file_type = "AI/Test Script"
        elif "menu_" in content_lower:
            self.file_type = "Menu System Script"
        else:
            self.file_type = "General Game Logic Script"

    def _get_line_number(self, position: int) -> int:
        return self.content[:position].count("\n") + 1

    def generate_summary(self) -> str:
        lines = [
            "=" * 70,
            f"FILE ANALYSIS: {self.filepath.name}",
            "=" * 70,
            f"File Type: {self.file_type}",
            f"Total Lines: {len(self.content.splitlines())}",
            "",
        ]

        if self.comments:
            lines.append("--- COMMENTS ---")

            for comment in self.comments:
                lines.append(f"  # {comment}")

            lines.append("")

        lines.append(f"--- FUNCTIONS ({len(self.functions)}) ---")

        if self.functions:
            for function in self.functions[:20]:
                parameters = (
                    f"({function['params']})"
                    if function["params"]
                    else "()"
                )

                lines.append(
                    f"  • {function['name']}"
                    f"{parameters}"
                    f" @ Line {function['line']}"
                )
        else:
            lines.append("  None found")

        lines.append("")

        lines.append(
            f"--- GLOBAL VARIABLES ({len(self.global_vars)}) ---"
        )

        if self.global_vars:
            for name, value in list(self.global_vars.items())[:15]:
                lines.append(f"  • ${name} = {value}")
        else:
            lines.append("  None found")

        lines.append("")

        unique_navpoints = sorted(set(self.navpoint_refs))

        lines.append(
            f"--- NAVPOINT REFERENCES ({len(unique_navpoints)}) ---"
        )

        if unique_navpoints:
            for navpoint in unique_navpoints:
                lines.append(f"  • {navpoint}")
        else:
            lines.append("  None found")

        lines.append("")

        lines.append(
            f"--- UI ELEMENTS ({len(self.ui_elements)}) ---"
        )

        if self.ui_elements:
            for element in sorted(set(self.ui_elements)):
                lines.append(f"  • {element}")
        else:
            lines.append("  None found")

        lines.append("")

        lines.append(
            f"--- EXTERNAL API CALLS ({len(self.external_calls)}) ---"
        )

        if self.external_calls:
            for call in sorted(self.external_calls):
                lines.append(f"  • {call}(...)")
        else:
            lines.append("  None found")

        lines.append("")

        lines.append(f"--- TRIGGERS ({len(self.triggers)}) ---")

        if self.triggers:
            for trigger in sorted(set(self.triggers)):
                lines.append(f"  • {trigger}")
        else:
            lines.append("  None found")

        lines.append("")

        lines.append(f"--- TABLES ({len(self.tables)}) ---")

        if self.tables:
            for table_name, keys in self.tables.items():
                key_text = ", ".join(keys) if keys else "No named keys"
                lines.append(f"  • {table_name}: {key_text}")
        else:
            lines.append("  None found")

        lines.append("")
        lines.append("=" * 70)

        return "\n".join(lines)


# =============================================================================
# MAIN WINDOW
# =============================================================================

class LuaAnalyzerWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.current_file: Optional[Path] = None
        self.text_sizes = [10, 12, 13, 15, 18, 22]
        self.current_text_size_index = 2
        self.bold_enabled = False

        self.setup_ui()
        self.apply_dark_theme()

    def setup_ui(self) -> None:
        self.setWindowTitle("SR2 Lua Script Analyzer")

        # Enlarged window
        self.setMinimumSize(1200, 850)
        self.resize(1600, 1000)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        # Application title
        title_label = QLabel("🎮 SR2 Lua Script Analyzer")

        title_font = QFont("Segoe UI", 24, QFont.Bold)
        title_label.setFont(title_font)
        title_label.setStyleSheet(
            f"color: {COLORS['foreground_dark_pink']};"
            "padding: 10px;"
        )

        main_layout.addWidget(title_label)

        # Toolbar
        toolbar_layout = QHBoxLayout()

        self.btn_open = QPushButton("📂 Open Lua File")
        self.btn_open.clicked.connect(self.open_file)
        self.btn_open.setStyleSheet(self.get_button_style())
        toolbar_layout.addWidget(self.btn_open)

        self.btn_batch = QPushButton("📁 Batch Process Folder")
        self.btn_batch.clicked.connect(self.open_folder)
        self.btn_batch.setStyleSheet(self.get_button_style())
        toolbar_layout.addWidget(self.btn_batch)

        self.btn_copy = QPushButton("📋 Copy Analysis")
        self.btn_copy.clicked.connect(self.copy_summary)
        self.btn_copy.setStyleSheet(self.get_button_style())
        toolbar_layout.addWidget(self.btn_copy)

        self.btn_save = QPushButton("💾 Save Analysis")
        self.btn_save.clicked.connect(self.save_analysis)
        self.btn_save.setStyleSheet(self.get_button_style())
        toolbar_layout.addWidget(self.btn_save)

        self.btn_size = QPushButton("🔤 Text Size: 13")
        self.btn_size.clicked.connect(self.change_text_size)
        self.btn_size.setStyleSheet(self.get_button_style())
        toolbar_layout.addWidget(self.btn_size)

        self.btn_bold = QPushButton("B Bold: Off")
        self.btn_bold.setCheckable(True)
        self.btn_bold.clicked.connect(self.toggle_bold)
        self.btn_bold.setStyleSheet(self.get_button_style())
        toolbar_layout.addWidget(self.btn_bold)

        toolbar_layout.addStretch()
        main_layout.addLayout(toolbar_layout)

        # Options
        options_layout = QHBoxLayout()

        self.checkbox_word_wrap = QCheckBox("Word Wrap")
        self.checkbox_word_wrap.setChecked(False)
        self.checkbox_word_wrap.setStyleSheet(
            f"color: {COLORS['text_green']};"
            f"font-size: {TEXT_SIZE}px;"
        )

        self.checkbox_word_wrap.toggled.connect(
            self.toggle_word_wrap
        )

        options_layout.addWidget(self.checkbox_word_wrap)
        options_layout.addStretch()

        main_layout.addLayout(options_layout)

        # Main splitter
        splitter = QSplitter(Qt.Horizontal)

        # Processing status panel
        left_group = QGroupBox("Processing Status")
        left_group.setStyleSheet(self.get_group_style())

        left_layout = QVBoxLayout()
        self.left_text = QTextEdit()
        self.left_text.setReadOnly(False)
        self.left_text.setStyleSheet(self.get_text_style())

        left_layout.addWidget(self.left_text)
        left_group.setLayout(left_layout)
        splitter.addWidget(left_group)

        # Analysis output panel
        right_group = QGroupBox("Script Analysis")
        right_group.setStyleSheet(self.get_group_style())

        right_layout = QVBoxLayout()
        self.summary_output = QTextEdit()
        self.summary_output.setReadOnly(False)
        self.summary_output.setStyleSheet(self.get_text_style())

        right_layout.addWidget(self.summary_output)
        right_group.setLayout(right_layout)
        splitter.addWidget(right_group)

        # Give the analysis panel more space
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([450, 950])

        main_layout.addWidget(splitter, 1)

        # Status bar
        self.status_bar = QLabel("Ready - Select a Lua file to begin")
        self.status_bar.setStyleSheet(
            f"background-color: {COLORS['button_purple']};"
            f"color: {COLORS['text_green']};"
            "padding: 8px;"
        )

        main_layout.addWidget(self.status_bar)

    def apply_dark_theme(self) -> None:
        palette = QPalette()

        palette.setColor(
            QPalette.Window,
            QColor(COLORS["background"]),
        )
        palette.setColor(
            QPalette.WindowText,
            QColor(COLORS["text_green"]),
        )
        palette.setColor(
            QPalette.Base,
            QColor(COLORS["background"]),
        )
        palette.setColor(
            QPalette.Text,
            QColor(COLORS["text_green"]),
        )
        palette.setColor(
            QPalette.Button,
            QColor(COLORS["button_purple"]),
        )
        palette.setColor(
            QPalette.ButtonText,
            QColor("#FFFFFF"),
        )

        self.setPalette(palette)

    def get_button_style(self) -> str:
        return f"""
            QPushButton {{
                background-color: {COLORS["button_purple"]};
                color: #FFFFFF;
                border: none;
                border-radius: 5px;
                padding: 10px 18px;
                font-size: {TEXT_SIZE}px;
                font-weight: bold;
            }}

            QPushButton:hover {{
                background-color: {COLORS["button_hover"]};
            }}

            QPushButton:pressed {{
                background-color: {COLORS["foreground_dark_pink"]};
            }}
        """

    def get_group_style(self) -> str:
        return f"""
            QGroupBox {{
                color: {COLORS["foreground_dark_pink"]};
                font-size: 15px;
                font-weight: bold;
                border: 1px solid {COLORS["border_gray"]};
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }}

            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }}
        """

    def get_text_style(self) -> str:
        return f"""
            QTextEdit {{
                color: {COLORS["text_green"]};
                background-color: #000000;
                border: 1px solid {COLORS["border_gray"]};
                font-family: Consolas, "Courier New", monospace;
                font-size: {TEXT_SIZE}px;
                padding: 6px;
            }}
        """

    def toggle_word_wrap(self, enabled: bool) -> None:
        mode = (
            QTextEdit.WidgetWidth
            if enabled
            else QTextEdit.NoWrap
        )

        self.summary_output.setWordWrapMode(mode)

    def open_file(self) -> None:
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "Open Lua File",
            "",
            "Lua Files (*.lua);;All Files (*)",
        )

        if filepath:
            self.analyze_file(filepath)

    def open_folder(self) -> None:
        folder_path = QFileDialog.getExistingDirectory(
            self,
            "Select Folder",
        )

        if not folder_path:
            return

        folder = Path(folder_path)
        lua_files = sorted(folder.glob("**/*.lua"))

        if lua_files:
            self.batch_analyze(lua_files)
        else:
            QMessageBox.warning(
                self,
                "No Files",
                "No .lua files were found in the selected folder.",
            )

    def analyze_file(self, filepath: str) -> None:
        file_path = Path(filepath)

        self.current_file = file_path
        self.summary_output.clear()
        self.status_bar.setText(
            f"Analyzing: {file_path.name}..."
        )

        parser = LuaFileParser(filepath)

        if parser.parse():
            self.display_summary(parser.generate_summary())

            self.left_text.append(
                f"✓ {file_path.name} - "
                f"{len(parser.functions)} functions"
            )

            self.status_bar.setText(
                f"Complete: {file_path.name}"
            )
        else:
            self.status_bar.setText(
                "Error: Failed to analyze the selected file."
            )

    def batch_analyze(self, files: List[Path]) -> None:
        total = len(files)

        self.left_text.clear()
        self.summary_output.clear()
        self.status_bar.setText(
            f"Processing {total} Lua files..."
        )

        for index, file_path in enumerate(files, start=1):
            self.status_bar.setText(
                f"Processing: {file_path.name} "
                f"({index}/{total})"
            )

            QApplication.processEvents()

            parser = LuaFileParser(str(file_path))

            if parser.parse():
                self.left_text.append(
                    f"✓ {file_path.name} - "
                    f"{len(parser.functions)} functions"
                )
            else:
                self.left_text.append(
                    f"✗ {file_path.name} - Failed to analyze"
                )

        self.status_bar.setText(
            f"Complete: {total} files processed"
        )

    def display_summary(self, summary: str) -> None:
        self.summary_output.clear()

        document = self.summary_output.document()
        cursor = QTextCursor(document)

        header_format = QTextCharFormat()
        header_format.setForeground(
            QColor(COLORS["foreground_dark_pink"])
        )
        header_format.setFontWeight(QFont.Bold)

        normal_format = QTextCharFormat()
        normal_format.setForeground(
            QColor(COLORS["text_green"])
        )

        for line in summary.splitlines():
            if line.startswith("=") or line.startswith("---"):
                cursor.setCharFormat(header_format)
            else:
                cursor.setCharFormat(normal_format)

            cursor.insertText(line + "\n")

        self.summary_output.moveCursor(QTextCursor.Start)


    def change_text_size(self) -> None:
        self.current_text_size_index = (
            self.current_text_size_index + 1
        ) % len(self.text_sizes)

        size = self.text_sizes[self.current_text_size_index]

        self.btn_size.setText(f"🔤 Text Size: {size}")

        for editor in (self.left_text, self.summary_output):
            font = editor.font()
            font.setPointSize(size)
            editor.setFont(font)

    def toggle_bold(self, enabled: bool) -> None:
        self.bold_enabled = enabled

        self.btn_bold.setText(
            "B Bold: On" if enabled else "B Bold: Off"
        )

        for editor in (self.left_text, self.summary_output):
            font = editor.font()
            font.setBold(enabled)
            editor.setFont(font)

    def save_analysis(self) -> None:
        text = self.summary_output.toPlainText()

        if not text.strip():
            self.status_bar.setText(
                "There is no analysis to save."
            )
            return

        default_name = "analysis.txt"

        if self.current_file is not None:
            default_name = (
                f"{self.current_file.stem}_analysis.txt"
            )

        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "Save Analysis",
            default_name,
            "Text Files (*.txt);;All Files (*)",
        )

        if not filepath:
            return

        try:
            Path(filepath).write_text(
                text,
                encoding="utf-8",
            )

            self.status_bar.setText(
                f"Saved analysis: {Path(filepath).name}"
            )

        except OSError as error:
            QMessageBox.critical(
                self,
                "Save Error",
                f"Could not save the analysis:\n{error}",
            )

    def copy_summary(self) -> None:
        summary = self.summary_output.toPlainText()

        if not summary.strip():
            self.status_bar.setText(
                "There is no analysis to copy."
            )
            return

        QApplication.clipboard().setText(summary)
        self.status_bar.setText(
            "Analysis copied to clipboard."
        )


# =============================================================================
# APPLICATION START
# =============================================================================

def main() -> None:
    app = QApplication(sys.argv)

    window = LuaAnalyzerWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

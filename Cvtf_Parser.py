#!/usr/bin/env python3
"""
CVTF Binary Analyzer v2.0
Enhanced parser with component record extraction and PyQt6 compatibility.
"""

import sys
import struct
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTextEdit,
    QSplitter,
    QStatusBar,
    QToolBar,
    QFileDialog,
    QDialog,
    QLabel,
    QPushButton,
    QSlider,
    QGroupBox,
    QCheckBox,
)
from PyQt6.QtGui import QFont, QAction, QTextOption
from PyQt6.QtCore import Qt, QSize


# ============================================================================
# COLORS
# ============================================================================

COLORS = {
    "background": "#000000",
    "text": "#00FF90",
    "foreground_objects": "#FF1493",
    "highlight_bg": "#6A0DAD",
    "button": "#1E3A8A",
    "button_hover": "#2563EB",
    "warning": "#F59E0B",
    "string": "#8B5CF6",
    "hex_bytes": "#00FF90",
    "address": "#6B7280",
}


# ============================================================================
# CVTF PARSER - CORRECTED VERSION
# ============================================================================

class CVTFParser:
    """
    CVTF parser with proper component record extraction.
    
    Known structure:
    - Header: 0x30 bytes
    - Character strings: ~0x3C-0x60
    - Component records: 0x60-0x700 (16 bytes each)
    - Texture section: 0x700+
    """

    EXPECTED_VERSION = 1
    EXPECTED_MAGIC1 = 0x5A2B28CF
    EXPECTED_MAGIC2 = 0xC99C7CAD

    COMPONENT_STRINGS = {
        "body",
        "teeth",
        "head hair",
        "eyebrows",
        "overshirt",
        "bottoms",
        "shoes",
        "eyes",
        "coat",
        "back pants pocket",
        "eyewear",
        "gloves",
    }

    RECORD_MARKER_1 = 0x00000101
    RECORD_MARKER_ALT = 0x00000100
    RECORD_MARKER_INVALID = 0xFFFFFFFF

    # Float constants that appear as noise in variant fields
    FLOAT_CONSTANTS = {
        0x3F800000,  # 1.0f
        0x42C80000,  # 100.0f
    }

    def __init__(self, filepath):
        self.filepath = Path(filepath)
        self.data = b""
        self.size = 0
        self.errors = []
        self.warnings = []

    def load(self):
        try:
            self.data = self.filepath.read_bytes()
            self.size = len(self.data)
            return True
        except Exception as exc:
            self.errors.append(f"Failed to load file: {exc}")
            return False

    def read_dword(self, offset):
        if offset < 0 or offset + 4 > self.size:
            return None
        return struct.unpack_from("<I", self.data, offset)[0]

    def read_float(self, offset):
        if offset < 0 or offset + 4 > self.size:
            return None
        return struct.unpack_from("<f", self.data, offset)[0]

    def read_string(self, offset):
        if offset is None or offset < 0 or offset >= self.size:
            return ""

        end = self.data.find(b"\x00", offset)
        if end == -1:
            end = self.size

        raw = self.data[offset:end]

        if not self.is_printable_ascii(raw):
            return ""

        return raw.decode("ascii")

    @staticmethod
    def is_printable_ascii(raw):
        return bool(raw) and all(32 <= byte < 127 for byte in raw)

    def extract_strings(self, minimum_length=3):
        """Return printable null-terminated ASCII strings and file offsets."""
        results = []
        pos = 0

        while pos < self.size:
            end = self.data.find(b"\x00", pos)
            if end == -1:
                end = self.size

            raw = self.data[pos:end]

            if len(raw) >= minimum_length:
                try:
                    decoded = raw.decode("ascii")
                    results.append({
                        "offset": pos,
                        "text": decoded,
                    })
                except UnicodeDecodeError:
                    try:
                        decoded = raw.decode("ascii", errors="ignore")
                        if decoded:
                            results.append({
                                "offset": pos,
                                "text": decoded,
                            })
                    except:
                        pass

            pos = end + 1

        return results

    def parse_header(self):
        header = {}

        if self.size < 0x30:
            self.errors.append("File too small for the known header")
            return header

        header["version"] = self.read_dword(0x00)
        header["magic1"] = self.read_dword(0x04)
        header["constant_0x08"] = self.read_dword(0x08)
        header["padding_0x0C"] = self.read_dword(0x0C)
        header["string_table_offset"] = self.read_dword(0x10)
        header["magic2"] = self.read_dword(0x14)
        header["float_const"] = self.read_float(0x18)
        header["section_a_ptr"] = self.read_dword(0x1C)
        header["count_flags"] = self.read_dword(0x20)
        header["section_b_ptr"] = self.read_dword(0x24)
        header["field_0x28"] = self.read_dword(0x28)
        header["field_0x2C"] = self.read_dword(0x2C)

        if header["version"] != self.EXPECTED_VERSION:
            self.warnings.append(f"Unknown version: {header['version']}")

        if header["constant_0x08"] != 0x10:
            self.warnings.append(
                f"Unexpected value at 0x08: 0x{header['constant_0x08']:08X}"
            )

        return header

    def parse_character_info(self):
        """Parse character name and class from string table."""
        info = {}
        strings = self.extract_strings()

        relevant = [item for item in strings if item["offset"] >= 0x3C]

        if relevant:
            info["character_name"] = relevant[0]["text"]

        if len(relevant) > 1:
            info["class_name"] = relevant[1]["text"]

        return info

    def parse_component_strings(self):
        """Find known component-category strings."""
        results = []
        strings = self.extract_strings()

        for item in strings:
            normalized = item["text"].lower()

            if normalized in self.COMPONENT_STRINGS:
                results.append({
                    "id": len(results) + 1,
                    "category": item["text"],
                    "offset": item["offset"],
                    "confidence": "category string only",
                })

        return results

    def parse_component_records(self):
        """
        Parse the binary component records - FIXED VERSION.
        
        Each record is 16 bytes (4 DWORDs):
        [0x00-0x03]: Pointer to category string
        [0x04-0x07]: Marker (typically 0x00000101)
        [0x08-0x0B]: Pointer to description/next category string
        [0x0C-0x0F]: Additional data pointer (often 1.0f or other constants)
        
        Records start at 0x60, after header and character strings.
        """
        records = []
        all_strings = self.extract_strings()
        strings_cache = {s["offset"]: s["text"] for s in all_strings}
        
        # Start at 0x60, not 0x50
        offset = 0x60
        
        # Scan until we hit the texture section (~0x700)
        while offset < 0x6FC and offset < self.size - 16:
            cat_ptr = self.read_dword(offset)
            marker1 = self.read_dword(offset + 4)
            desc_ptr = self.read_dword(offset + 8)
            variant_ptr = self.read_dword(offset + 12)
            
            # Skip if category pointer is invalid
            if cat_ptr is None or cat_ptr >= self.size:
                offset += 16  # Use 16-byte stride
                continue
            
            # Check marker validity - skip 0xFFFFFFFF (padding)
            if marker1 == self.RECORD_MARKER_INVALID:
                offset += 16
                continue
            
            # Read category string
            category = strings_cache.get(cat_ptr, self.read_string(cat_ptr))
            
            # Skip if no valid category found
            if not category or category == "(none)":
                offset += 16
                continue
            
            # Find description
            description = "(none)"
            if desc_ptr and desc_ptr < self.size and desc_ptr not in self.FLOAT_CONSTANTS:
                candidate = self.read_string(desc_ptr)
                if candidate and len(candidate) > 5:
                    if "'" in candidate or candidate.count(" ") >= 2:
                        description = candidate
            
            records.append({
                "record_offset": offset,
                "category": category,
                "marker": hex(marker1),
                "description_ptr": desc_ptr,
                "description": description,
                "variant_ptr": variant_ptr,
                "valid": marker1 in (self.RECORD_MARKER_1, self.RECORD_MARKER_ALT),
            })
            
            # Move to next record (16-byte stride)
            offset += 16
        
        return records

    def parse_texture_refs(self):
        textures = []
        seen = set()

        for item in self.extract_strings():
            text = item["text"]

            if (
                text.startswith("LOG_")
                or text.lower().endswith((".tga", ".png", ".dds", ".jpg"))
            ):
                if text not in seen:
                    textures.append({
                        "name": text,
                        "offset": item["offset"],
                    })
                    seen.add(text)

        return textures

    def count_float_constants(self):
        """Count raw byte-pattern occurrences at every byte offset."""
        unity_count = 0
        hundred_count = 0

        for offset in range(0, self.size - 3):
            value = self.read_float(offset)

            if value is None:
                continue

            if abs(value - 1.0) < 0.0001:
                unity_count += 1
            elif abs(value - 100.0) < 0.0001:
                hundred_count += 1

        return {
            "unity_1.0": unity_count,
            "hundred_100.0": hundred_count,
        }

    def validate_file(self, header):
        checks = []
        valid = True

        magic1 = header.get("magic1")
        magic2 = header.get("magic2")
        version = header.get("version")

        if magic1 == self.EXPECTED_MAGIC1:
            checks.append(("Header magic1", "PASS"))
        else:
            checks.append((
                "Header magic1",
                f"UNEXPECTED: {self.format_dword(magic1)}",
            ))
            valid = False

        if magic2 == self.EXPECTED_MAGIC2:
            checks.append(("Header magic2", "PASS"))
        else:
            checks.append((
                "Header magic2",
                f"UNEXPECTED: {self.format_dword(magic2)}",
            ))
            valid = False

        if version == self.EXPECTED_VERSION:
            checks.append(("Version", "PASS"))
        else:
            checks.append(("Version", f"UNEXPECTED: {version}"))
            valid = False

        if self.size >= 0x100:
            checks.append(("Minimum size", "PASS"))
        else:
            checks.append(("Minimum size", f"SMALL: {self.size} bytes"))
            valid = False

        checks.append((
            "Footer",
            "NOT CHECKED — footer layout is not established",
        ))

        return checks, valid

    @staticmethod
    def format_dword(value):
        if value is None:
            return "MISSING"
        return f"0x{value:08X}"

    def generate_summary(self):
        if not self.load():
            return (
                f"ERROR: Failed to load {self.filepath.name}\n"
                + "\n".join(self.errors)
            )

        header = self.parse_header()
        char_info = self.parse_character_info()
        components = self.parse_component_strings()
        component_records = self.parse_component_records()
        textures = self.parse_texture_refs()
        floats = self.count_float_constants()
        checks, valid = self.validate_file(header)

        lines = []

        lines.append(
            f"## {self.filepath.name} ({self.size:,} bytes)"
        )
        lines.append("")

        lines.append("Header:")
        lines.append(
            f"  Version={header.get('version', '?')} "
            f"Magic1={self.format_dword(header.get('magic1'))}"
        )
        lines.append(
            f"  Magic2={self.format_dword(header.get('magic2'))} "
            f"FloatConst={header.get('float_const', 'N/A')}"
        )
        lines.append(
            f"  StringTableOffset={self.format_dword(header.get('string_table_offset'))}"
        )
        lines.append(
            f"  SectionAPtr={self.format_dword(header.get('section_a_ptr'))}"
        )
        lines.append("")

        lines.append("Character:")
        lines.append(
            f"  Name=\"{char_info.get('character_name', 'N/A')}\""
        )
        lines.append(
            f"  Class=\"{char_info.get('class_name', 'N/A')}\""
        )
        lines.append("")

        lines.append("Component Category Strings:")
        lines.append(f"  Total: {len(components)} found")

        for component in components:
            lines.append(
                f"  [{component['id']:02}] "
                f"{component['category']} "
                f"(offset=0x{component['offset']:04X})"
            )

        lines.append("")

        # Clean summary output with proper record iteration
        lines.append("Component Binary Records:")
        valid_records = [r for r in component_records if r.get('category') != '(none)']
        lines.append(f"  Total parsed: {len(component_records)} records")
        lines.append(f"  Valid records: {len(valid_records)}")
        lines.append("")

        for record in component_records:
            marker_status = "✓" if record.get('valid') else "⚠"
            
            category = record.get('category', '(none)')
            description = record.get('description', '(none)')
            variant_ptr = record.get('variant_ptr', None)
            
            record_line = f"  {marker_status} Cat:\"{category}\" | Desc:\"{description}\""
            if variant_ptr and variant_ptr not in self.FLOAT_CONSTANTS:
                record_line += f' | Var:"{hex(variant_ptr)}"'
            
            lines.append(record_line)

        lines.append("")

        lines.append(f"Textures: {len(textures)} references")

        for texture in textures:
            lines.append(
                f"  - {texture['name']} "
                f"(offset=0x{texture['offset']:04X})"
            )

        lines.append("")

        lines.append("Float-pattern statistics:")
        lines.append(f"  1.0f byte patterns: {floats['unity_1.0']:,}")
        lines.append(f"  100.0f byte patterns: {floats['hundred_100.0']:,}")

        lines.append("")
        lines.append("Validation:")

        for check, status in checks:
            if status == "PASS":
                marker = "✓"
            elif status.startswith("NOT CHECKED"):
                marker = "–"
            else:
                marker = "⚠"

            lines.append(f"  {marker} {check}: {status}")

        lines.append("")
        lines.append(f"Status: {'STRUCTURALLY PARSEABLE' if valid else 'WARNING'}")

        if self.warnings:
            lines.append("")
            lines.append("Parser warnings:")
            for warning in self.warnings:
                lines.append(f"  - {warning}")

        return "\n".join(lines)


# ============================================================================
# CLI MODE (for testing without GUI)
# ============================================================================

def run_cli(filepath):
    """Run parser in command-line mode."""
    parser = CVTFParser(filepath)
    summary = parser.generate_summary()
    print(summary)


# ============================================================================
# HEX VIEWER
# ============================================================================

class HexViewer(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)

        font = QFont("Monospace")
        font.setPointSize(12)

        self.setFont(font)
        self.setReadOnly(True)
        self.setWordWrapMode(QTextOption.WrapMode.NoWrap)

        self.setStyleSheet(f"""
            QTextEdit {{
                background-color: {COLORS["background"]};
                color: {COLORS["text"]};
                border: none;
            }}
        """)

    def set_word_wrap(self, enabled):
        self.setWordWrapMode(
            QTextOption.WrapMode.WordWrap if enabled else QTextOption.WrapMode.NoWrap
        )

    def display_hex(self, data, max_lines=256):
        html = []

        html.append(
            f"<span style='color:{COLORS['address']}'>"
            "Address  "
            "00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F"
            "</span><br>"
        )

        html.append(
            f"<span style='color:{COLORS['address']}'>"
            + "-" * 75
            + "</span><br>"
        )

        maximum = min(len(data), max_lines * 16)

        for offset in range(0, maximum, 16):
            chunk = data[offset:offset + 16]

            hex_part = []
            ascii_part = []

            for byte in chunk:
                hex_part.append(
                    f"<span style='color:{COLORS['hex_bytes']}'>"
                    f"{byte:02X}</span>"
                )

                if 32 <= byte < 127:
                    ascii_part.append(chr(byte))
                else:
                    ascii_part.append(".")

            while len(hex_part) < 16:
                hex_part.append("  ")

            address = (
                f"<span style='color:{COLORS['address']}'>"
                f"{offset:08X}</span>"
            )

            text = (
                f"<span style='color:{COLORS['string']}'>"
                f"{''.join(ascii_part)}"
                "</span>"
            )

            html.append(
                f"{address}  "
                f"{' '.join(hex_part)}  |{text}|<br>"
            )

        if len(data) > maximum:
            html.append(
                f"<br><span style='color:{COLORS['warning']}'>"
                "... truncated"
                "</span>"
            )

        self.setHtml("".join(html))


# ============================================================================
# SETTINGS DIALOG
# ============================================================================

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.parent_window = parent
        self.setWindowTitle("Settings")
        self.setMinimumSize(400, 250)

        layout = QVBoxLayout(self)

        display_group = QGroupBox("Display Options")
        display_layout = QVBoxLayout(display_group)

        self.wrap_checkbox = QCheckBox("Enable Word Wrap")
        self.wrap_checkbox.stateChanged.connect(self.on_wrap_changed)
        display_layout.addWidget(self.wrap_checkbox)

        self.bold_checkbox = QCheckBox("Bold Text")
        self.bold_checkbox.setChecked(True)
        self.bold_checkbox.stateChanged.connect(self.on_bold_changed)
        display_layout.addWidget(self.bold_checkbox)

        layout.addWidget(display_group)

        font_group = QGroupBox("Text Size")
        font_layout = QVBoxLayout(font_group)

        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(8, 24)
        self.size_slider.setValue(12)
        self.size_slider.valueChanged.connect(self.on_size_changed)

        self.size_label = QLabel("Current: 12pt")

        font_layout.addWidget(self.size_slider)
        font_layout.addWidget(self.size_label)
        layout.addWidget(font_group)

        buttons = QHBoxLayout()
        buttons.addStretch()

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        buttons.addWidget(close_button)

        layout.addLayout(buttons)

    def on_wrap_changed(self, state):
        enabled = state == Qt.CheckState.Checked

        if self.parent_window:
            self.parent_window.hex_viewer.set_word_wrap(enabled)
            self.parent_window.summary_view.setWordWrapMode(
                QTextOption.WrapMode.WordWrap if enabled else QTextOption.WrapMode.NoWrap
            )

    def on_size_changed(self, value):
        self.size_label.setText(f"Current: {value}pt")

        if not self.parent_window:
            return

        for widget in (
            self.parent_window.hex_viewer,
            self.parent_window.summary_view,
        ):
            font = widget.font()
            font.setPointSize(value)
            font.setBold(self.bold_checkbox.isChecked())
            widget.setFont(font)

    def on_bold_changed(self, state):
        bold = state == Qt.CheckState.Checked

        if not self.parent_window:
            return

        for widget in (
            self.parent_window.hex_viewer,
            self.parent_window.summary_view,
        ):
            font = widget.font()
            font.setBold(bold)
            widget.setFont(font)


# ============================================================================
# MAIN WINDOW
# ============================================================================

class CVTFAnalyzerWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.current_file = None
        self.parser = None

        self.setWindowTitle("CVTF Binary Analyzer v2.0")
        self.setGeometry(100, 100, 1400, 800)

        self.build_ui()
        self.apply_theme()

    def build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)

        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(24, 24))
        main_layout.addWidget(toolbar)

        open_action = QAction("Open .cvtf", self)
        open_action.triggered.connect(self.open_file)
        toolbar.addAction(open_action)

        settings_action = QAction("Settings", self)
        settings_action.triggered.connect(self.show_settings)
        toolbar.addAction(settings_action)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        hex_group = QGroupBox("Hex Dump")
        hex_layout = QVBoxLayout(hex_group)

        self.hex_viewer = HexViewer()
        hex_layout.addWidget(self.hex_viewer)

        splitter.addWidget(hex_group)

        summary_group = QGroupBox("Parsed Analysis")
        summary_layout = QVBoxLayout(summary_group)

        self.summary_view = QTextEdit()
        self.summary_view.setReadOnly(True)
        self.summary_view.setFont(
            QFont("Monospace", 12, QFont.Weight.Normal)
        )

        summary_layout.addWidget(self.summary_view)
        splitter.addWidget(summary_group)

        splitter.setSizes([650, 750])
        main_layout.addWidget(splitter)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage(
            "Ready - Select a .cvtf file to analyze"
        )

    def apply_theme(self):
        self.setStyleSheet(f"""
            QMainWindow {{
                background-color: {COLORS["background"]};
            }}

            QGroupBox {{
                color: {COLORS["text"]};
                border: 1px solid {COLORS["highlight_bg"]};
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
                font-weight: bold;
            }}

            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: {COLORS["foreground_objects"]};
            }}

            QToolBar {{
                background-color: {COLORS["button"]};
                spacing: 10px;
                padding: 5px;
            }}

            QToolButton {{
                color: {COLORS["text"]};
                background-color: transparent;
                padding: 5px 10px;
                border-radius: 3px;
            }}

            QToolButton:hover {{
                background-color: {COLORS["button_hover"]};
            }}

            QStatusBar {{
                color: {COLORS["text"]};
                border-top: 1px solid {COLORS["highlight_bg"]};
            }}
        """)

    def open_file(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "Open CVTF File",
            "",
            "CVTF Files (*.cvtf);;All Files (*)",
        )

        if not filepath:
            return

        self.current_file = Path(filepath)
        self.status_bar.showMessage(
            f"Analyzing: {self.current_file.name}"
        )

        self.parser = CVTFParser(filepath)
        summary = self.parser.generate_summary()

        self.summary_view.setPlainText(summary)
        self.hex_viewer.display_hex(
            self.parser.data,
            max_lines=256,
        )

        self.status_bar.showMessage(
            f"Loaded: {self.current_file.name} "
            f"({self.parser.size:,} bytes)"
        )

    def show_settings(self):
        dialog = SettingsDialog(self)
        dialog.exec()


# ============================================================================
# ENTRY POINT
# ============================================================================

def main():
    # Check if running in CLI mode
    if len(sys.argv) > 1 and (sys.argv[1] == "-c" or sys.argv[1] == "--cli"):
        if len(sys.argv) < 3:
            print("Usage: python Parser.py --cli <filepath.cvtf>")
            sys.exit(1)
        run_cli(sys.argv[2])
        return

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = CVTFAnalyzerWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

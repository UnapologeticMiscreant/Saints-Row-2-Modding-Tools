import os
import re
import shutil
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import (
    QAction,
    QColor,
    QFont,
    QKeySequence,
    QShortcut,
    QTextCharFormat,
    QTextCursor,
    QSyntaxHighlighter,
)
from PyQt6.QtWidgets import (
    QApplication,
    QColorDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------

BG_COLOR = "#000000"
TEXT_COLOR = "#00FF90"
HIGHLIGHT_COLOR = "#00008B"   # dark blue
OBJECT_COLOR = "#8B008B"      # dark purple
ERROR_COLOR = "#450000"
ERROR_TEXT_COLOR = "#FF4444"
FONT_SIZE = 15

BACKUP_EXTS = {
    ".cts",
    ".xtbl",
    ".lua",
    ".txt",
    ".vintxdoc",
    ".hex",
    ".ini",
    ".xml",
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(SCRIPT_DIR, "scripts", "backups")


# ----------------------------------------------------------------------
# Syntax highlighting
# ----------------------------------------------------------------------

class CodeHighlighter(QSyntaxHighlighter):
    """Basic syntax highlighter for Lua, XML, INI, text, and similar files."""

    def __init__(self, document):
        super().__init__(document)

        self.rules = []

        # Comments
        comment_format = QTextCharFormat()
        comment_format.setForeground(QColor("#666666"))
        self.rules.append((
            r"//[^\n]*|#[^\n]*|--[^\n]*",
            comment_format,
        ))

        # Strings
        string_format = QTextCharFormat()
        string_format.setForeground(QColor("#FFD700"))
        self.rules.append((
            r'"[^"\n]*"|\'[^\'\n]*\'',
            string_format,
        ))

        # Numbers
        number_format = QTextCharFormat()
        number_format.setForeground(QColor("#00BFFF"))
        self.rules.append((
            r"\b\d+(\.\d+)?\b",
            number_format,
        ))

        # XML tags and attributes
        xml_format = QTextCharFormat()
        xml_format.setForeground(QColor("#FF69B4"))
        self.rules.append((
            r"</?[A-Za-z_][^>]*>|[A-Za-z_][A-Za-z0-9_-]*(?=\s*=)",
            xml_format,
        ))

        # Common programming keywords
        keyword_format = QTextCharFormat()
        keyword_format.setForeground(QColor("#FF69B4"))
        keyword_format.setFontWeight(QFont.Weight.Bold)

        keywords = (
            r"\b(and|break|case|class|continue|def|do|else|elseif|end|"
            r"false|for|from|function|if|import|in|local|nil|not|or|"
            r"print|return|then|true|var|while|with)\b"
        )
        self.rules.append((keywords, keyword_format))

        # Brackets
        bracket_format = QTextCharFormat()
        bracket_format.setForeground(QColor("#FF1493"))
        bracket_format.setFontWeight(QFont.Weight.Bold)
        self.rules.append((r"[\{\}\(\)\[\]]", bracket_format))

    def highlightBlock(self, text):
        for pattern, text_format in self.rules:
            for match in re.finditer(pattern, text):
                self.setFormat(
                    match.start(),
                    match.end() - match.start(),
                    text_format,
                )


# ----------------------------------------------------------------------
# Incorrect-line detection
# ----------------------------------------------------------------------

def find_incorrect_lines(text):
    """
    Detect unmatched (), {}, and [] brackets.

    Returns a set of zero-based line numbers that contain errors.
    Strings and comments are ignored approximately.
    """
    incorrect_lines = set()
    stack = []

    opening = "([{"
    closing = ")]}"
    pairs = {
        ")": "(",
        "]": "[",
        "}": "{",
    }

    in_string = None

    for line_number, original_line in enumerate(text.splitlines()):
        line = original_line
        cleaned = []
        i = 0

        while i < len(line):
            character = line[i]

            # Handle quoted strings
            if in_string:
                if character == in_string and (
                    i == 0 or line[i - 1] != "\\"
                ):
                    in_string = None
                cleaned.append(" ")
                i += 1
                continue

            if character in ("'", '"'):
                in_string = character
                cleaned.append(" ")
                i += 1
                continue

            # Handle comments
            if line.startswith("//", i) or line.startswith("--", i):
                break

            if character == "#":
                break

            cleaned.append(character)
            i += 1

        for character in cleaned:
            if character in opening:
                stack.append((character, line_number))

            elif character in closing:
                if not stack or stack[-1][0] != pairs[character]:
                    incorrect_lines.add(line_number)
                else:
                    stack.pop()

    # Unclosed opening brackets
    for _, line_number in stack:
        incorrect_lines.add(line_number)

    return incorrect_lines


# ----------------------------------------------------------------------
# Editor pane
# ----------------------------------------------------------------------

class EditorPane(QWidget):
    def __init__(self, side, parent=None):
        super().__init__(parent)

        self.side = side
        self.file_path = None
        self.word_wrap = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # Header
        header = QHBoxLayout()

        self.max_button = QPushButton(f"Maximize {side}")
        self.max_button.clicked.connect(self.toggle_maximize)

        self.save_button = QPushButton(f"Quick Save {side}")
        self.save_button.clicked.connect(self.quick_save)

        header.addWidget(self.max_button)
        header.addWidget(self.save_button)
        header.addStretch(1)

        layout.addLayout(header)

        # Editor
        self.editor = QPlainTextEdit()
        self.editor.setFont(self._default_font())
        self.editor.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.NoWrap
        )
        self.editor.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.editor.customContextMenuRequested.connect(
            self.show_context_menu
        )
        self.editor.cursorPositionChanged.connect(self.update_status)
        self.editor.textChanged.connect(self.mark_incorrect_lines)

        self.highlighter = CodeHighlighter(self.editor.document())

        layout.addWidget(self.editor)

        # Status bar
        self.status = QLabel("No file open")
        layout.addWidget(self.status)

        self.maximized = False
        self.other_pane = None

    @staticmethod
    def _default_font():
        font = QFont("Monospace", FONT_SIZE)
        font.setStyleHint(QFont.StyleHint.TypeWriter)
        return font

    # ------------------------------------------------------------------
    # Editor features
    # ------------------------------------------------------------------

    def toggle_bold(self):
        cursor = self.editor.textCursor()

        if not cursor.hasSelection():
            QMessageBox.information(
                self,
                "Bold Text",
                "Select some text first.",
            )
            return

        current_format = cursor.charFormat()
        new_format = QTextCharFormat(current_format)

        if current_format.fontWeight() == QFont.Weight.Bold:
            new_format.setFontWeight(QFont.Weight.Normal)
        else:
            new_format.setFontWeight(QFont.Weight.Bold)

        cursor.mergeCharFormat(new_format)
        self.editor.setTextCursor(cursor)

    def toggle_wrap(self):
        self.word_wrap = not self.word_wrap

        if self.word_wrap:
            mode = QPlainTextEdit.LineWrapMode.WidgetWidth
        else:
            mode = QPlainTextEdit.LineWrapMode.NoWrap

        self.editor.setLineWrapMode(mode)

    def change_text_size(self):
        size, ok = QInputDialog.getInt(
            self,
            "Change Text Size",
            "Font size:",
            self.editor.font().pointSize(),
            6,
            72,
        )

        if ok:
            font = self.editor.font()
            font.setPointSize(size)
            self.editor.setFont(font)

    def select_all(self):
        self.editor.selectAll()
        self.editor.setFocus()

    def sort_lines(self):
        lines = self.editor.toPlainText().splitlines()
        lines.sort()
        self.editor.setPlainText("\n".join(lines))

    def jump_to_line(self):
        line, ok = QInputDialog.getInt(
            self,
            f"Jump To Line ({self.side})",
            "Line:",
            1,
            1,
        )

        if not ok:
            return

        block = self.editor.document().findBlockByLineNumber(line - 1)

        if block.isValid():
            cursor = QTextCursor(block)
            self.editor.setTextCursor(cursor)
            self.editor.ensureCursorVisible()
            self.editor.setFocus()

    # ------------------------------------------------------------------
    # Incorrect-line markers
    # ------------------------------------------------------------------

    def mark_incorrect_lines(self):
        bad_lines = find_incorrect_lines(
            self.editor.toPlainText()
        )

        selections = []

        for line_number in bad_lines:
            block = self.editor.document().findBlockByLineNumber(
                line_number
            )

            if not block.isValid():
                continue

            selection = QTextEdit.ExtraSelection()
            selection.format.setBackground(QColor(ERROR_COLOR))
            selection.format.setForeground(QColor(ERROR_TEXT_COLOR))

            cursor = QTextCursor(block)
            cursor.select(QTextCursor.SelectionType.LineUnderCursor)
            selection.cursor = cursor

            selections.append(selection)

        self.editor.setExtraSelections(selections)

    # ------------------------------------------------------------------
    # Status and maximize
    # ------------------------------------------------------------------

    def update_status(self):
        if self.file_path is None and not self.editor.toPlainText():
            self.status.setText("No file open")
            return

        cursor = self.editor.textCursor()

        self.status.setText(
            f"Ln {cursor.blockNumber() + 1}, "
            f"Col {cursor.columnNumber() + 1}"
        )

    def toggle_maximize(self):
        if not self.maximized:
            if self.other_pane:
                self.other_pane.hide()

            self.maximized = True
            self.max_button.setText(f"Restore {self.side}")

        else:
            if self.other_pane:
                self.other_pane.show()

            self.maximized = False
            self.max_button.setText(f"Maximize {self.side}")

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def new_file(self):
        self.editor.clear()
        self.file_path = None
        self.update_status()

    def open_file(self, filter_string):
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Open Text File ({self.side})",
            "",
            filter_string,
        )

        if path:
            self.open_file_path(path)

    def open_file_path(self, path):
        if not os.path.isfile(path):
            return

        self.backup_if_needed(path)

        try:
            with open(
                path,
                "r",
                encoding="utf-8",
                errors="replace",
            ) as file:
                content = file.read()

            self.editor.setPlainText(content)
            self.file_path = path
            self.update_status()

        except OSError as error:
            QMessageBox.critical(
                self,
                "Open Error",
                str(error),
            )

    def quick_save(self):
        if self.file_path:
            self.write_file(self.file_path)
        else:
            self.save_as()

    def save(self):
        if self.file_path:
            self.write_file(self.file_path)
        else:
            self.save_as()

    def save_as(self, extension=""):
        default_name = ""

        if extension:
            default_name = f"untitled{extension}"

        path, _ = QFileDialog.getSaveFileName(
            self,
            f"Save File ({self.side})",
            default_name,
            "All Files (*)",
        )

        if not path:
            return

        if extension and not path.lower().endswith(extension):
            path += extension

        self.file_path = path
        self.write_file(path)

    def write_file(self, path):
        try:
            with open(path, "w", encoding="utf-8") as file:
                file.write(self.editor.toPlainText())

            self.file_path = path
            self.update_status()

        except OSError as error:
            QMessageBox.critical(
                self,
                "Save Error",
                str(error),
            )

    def backup_if_needed(self, path):
        extension = os.path.splitext(path)[1].lower()

        if extension not in BACKUP_EXTS:
            return

        os.makedirs(BACKUP_DIR, exist_ok=True)

        destination = os.path.join(
            BACKUP_DIR,
            os.path.basename(path) + "Backup",
        )

        if not os.path.exists(destination):
            try:
                shutil.copy2(path, destination)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Editing
    # ------------------------------------------------------------------

    def cut(self):
        self.editor.cut()

    def copy(self):
        self.editor.copy()

    def paste(self):
        self.editor.paste()

    def delete(self):
        cursor = self.editor.textCursor()
        cursor.removeSelectedText()
        self.editor.setTextCursor(cursor)

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def show_context_menu(self, position):
        menu = QMenu(self)

        menu.addAction("Undo", self.editor.undo)
        menu.addAction("Redo", self.editor.redo)
        menu.addSeparator()

        menu.addAction("Cut", self.cut)
        menu.addAction("Copy", self.copy)
        menu.addAction("Paste", self.paste)
        menu.addAction("Delete", self.delete)
        menu.addAction("Select All", self.select_all)

        menu.addSeparator()

        menu.addAction("Toggle Bold", self.toggle_bold)
        menu.addAction("Word Wrap", self.toggle_wrap)
        menu.addAction("Change Text Size", self.change_text_size)

        menu.exec(self.editor.mapToGlobal(position))


# ----------------------------------------------------------------------
# Main window
# ----------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Two-Pane File Editor")
        self.resize(1100, 700)

        central = QWidget()
        self.setCentralWidget(central)

        layout = QHBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)

        self.left = EditorPane("Left", self)
        self.right = EditorPane("Right", self)

        self.left.other_pane = self.right
        self.right.other_pane = self.left

        layout.addWidget(self.left, 1)
        layout.addWidget(self.right, 1)

        self.build_menus()
        self.apply_colors()

    # ------------------------------------------------------------------
    # Colors
    # ------------------------------------------------------------------

    def apply_colors(self):
        stylesheet = f"""
        QMainWindow, QWidget, QPlainTextEdit, QLabel, QMenuBar, QMenu {{
            background-color: {BG_COLOR};
            color: {TEXT_COLOR};
        }}

        QPlainTextEdit {{
            selection-background-color: {HIGHLIGHT_COLOR};
            selection-color: {TEXT_COLOR};
            border: 1px solid {OBJECT_COLOR};
        }}

        QPushButton {{
            background-color: {OBJECT_COLOR};
            color: {TEXT_COLOR};
            border: 1px solid {TEXT_COLOR};
            padding: 5px;
        }}

        QPushButton:hover {{
            background-color: #B000B0;
        }}

        QMenuBar::item:selected,
        QMenu::item:selected {{
            background-color: {OBJECT_COLOR};
            color: {TEXT_COLOR};
        }}

        QMenuBar {{
            border-bottom: 1px solid {OBJECT_COLOR};
        }}
        """

        self.setStyleSheet(stylesheet)

    def change_background_color(self):
        color = QColorDialog.getColor(
            QColor(BG_COLOR),
            self,
            "Background Color",
        )

        if color.isValid():
            self.setStyleSheet(
                self.styleSheet().replace(BG_COLOR, color.name())
            )

    # ------------------------------------------------------------------
    # Menus
    # ------------------------------------------------------------------

    def build_menus(self):
        files = self.menuBar().addMenu("Files")

        files.addAction(
            "Create New Text File (Left Pane)",
            self.left.new_file,
        )
        files.addAction(
            "Create New Text File (Right Pane)",
            self.right.new_file,
        )

        files.addAction(
            "Open Text File (Left Pane)",
            lambda: self.left.open_file(self.text_filter()),
        )
        files.addAction(
            "Open Text File (Right Pane)",
            lambda: self.right.open_file(self.text_filter()),
        )

        files.addAction(
            "Save File (Left Pane)",
            self.left.save,
        )
        files.addAction(
            "Save File (Right Pane)",
            self.right.save,
        )

        files.addSeparator()
        files.addAction("Exit", self.close)

        edit = self.menuBar().addMenu("Edit")

        edit.addAction("Undo", self.focus_editor().undo)
        edit.addAction("Redo", self.focus_editor().redo)
        edit.addSeparator()

        edit.addAction("Cut", self.focus_cut)
        edit.addAction("Copy", self.focus_copy)
        edit.addAction("Paste", self.focus_paste)
        edit.addAction("Delete", self.focus_delete)

        edit.addSeparator()

        edit.addAction(
            "Search (Left Pane)",
            lambda: self.search(self.left),
        )
        edit.addAction(
            "Search (Right Pane)",
            lambda: self.search(self.right),
        )
        edit.addAction(
            "Replace (Left Pane)",
            lambda: self.replace(self.left),
        )
        edit.addAction(
            "Replace (Right Pane)",
            lambda: self.replace(self.right),
        )

        edit.addSeparator()

        edit.addAction(
            "Toggle Bold (Left Pane)",
            self.left.toggle_bold,
        )
        edit.addAction(
            "Toggle Bold (Right Pane)",
            self.right.toggle_bold,
        )

        edit.addAction(
            "Word Wrap (Left Pane)",
            self.left.toggle_wrap,
        )
        edit.addAction(
            "Word Wrap (Right Pane)",
            self.right.toggle_wrap,
        )

        edit.addAction(
            "Sort Lines (Left Pane)",
            self.left.sort_lines,
        )
        edit.addAction(
            "Sort Lines (Right Pane)",
            self.right.sort_lines,
        )

        edit.addAction(
            "Jump To Line (Left Pane)",
            self.left.jump_to_line,
        )
        edit.addAction(
            "Jump To Line (Right Pane)",
            self.right.jump_to_line,
        )

        saints_row = self.menuBar().addMenu("Saints Row")

        extensions = [
            (".cts", "Cts"),
            (".lua", "Lua"),
            (".xtbl", "Xtbl"),
        ]

        for pane, side in (
            (self.left, "Left"),
            (self.right, "Right"),
        ):
            for extension, label in extensions:
                saints_row.addAction(
                    f"Open {label} File ({side} Pane)",
                    lambda p=pane, e=extension:
                    self.open_extension(p, e),
                )

                saints_row.addAction(
                    f"Save {label} File ({side} Pane)",
                    lambda p=pane, e=extension:
                    self.save_extension(p, e),
                )

        self.add_shortcuts()

    @staticmethod
    def text_filter():
        return "Text Files (*.txt);;All Files (*)"

    # ------------------------------------------------------------------
    # Search and replace
    # ------------------------------------------------------------------

    def search(self, pane):
        term, ok = QInputDialog.getText(
            self,
            f"Search ({pane.side})",
            "Find:",
        )

        if not ok or not term:
            return

        cursor = pane.editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        pane.editor.setTextCursor(cursor)

        if not pane.editor.find(term):
            QMessageBox.information(
                self,
                "Search",
                "Text was not found.",
            )

    def replace(self, pane):
        find_text, ok = QInputDialog.getText(
            self,
            f"Replace ({pane.side})",
            "Find:",
        )

        if not ok or not find_text:
            return

        replace_text, ok = QInputDialog.getText(
            self,
            f"Replace ({pane.side})",
            "Replace with:",
        )

        if not ok:
            return

        content = pane.editor.toPlainText()
        pane.editor.setPlainText(
            content.replace(find_text, replace_text)
        )

    # ------------------------------------------------------------------
    # Extension-specific file operations
    # ------------------------------------------------------------------

    def open_extension(self, pane, extension):
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Open {extension.upper()} File",
            "",
            f"*{extension}",
        )

        if path:
            pane.open_file_path(path)

    def save_extension(self, pane, extension):
        pane.save_as(extension)

    # ------------------------------------------------------------------
    # Focus-based actions
    # ------------------------------------------------------------------

    def focus_editor(self):
        if self.right.editor.hasFocus():
            return self.right.editor
        return self.left.editor

    def focus_pane(self):
        if self.right.editor.hasFocus():
            return self.right
        return self.left

    def focus_cut(self):
        self.focus_pane().cut()

    def focus_copy(self):
        self.focus_pane().copy()

    def focus_paste(self):
        self.focus_pane().paste()

    def focus_delete(self):
        self.focus_pane().delete()

    def add_shortcuts(self):
        QShortcut(
            QKeySequence("Ctrl+A"),
            self,
            activated=self.focus_pane().select_all,
        )
        QShortcut(
            QKeySequence("Ctrl+C"),
            self,
            activated=self.focus_copy,
        )
        QShortcut(
            QKeySequence("Ctrl+V"),
            self,
            activated=self.focus_paste,
        )
        QShortcut(
            QKeySequence("Ctrl+X"),
            self,
            activated=self.focus_cut,
        )
        QShortcut(
            QKeySequence("Ctrl+B"),
            self,
            activated=lambda: self.focus_pane().toggle_bold(),
        )
        QShortcut(
            QKeySequence("Ctrl+F"),
            self,
            activated=lambda: self.search(self.focus_pane()),
        )


# ----------------------------------------------------------------------
# Application entry point
# ----------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

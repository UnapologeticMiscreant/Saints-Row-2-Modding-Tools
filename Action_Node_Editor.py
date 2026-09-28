#!/usr/bin/env python3
"""
Saints Row 2 Action Node Editor
Single-file PySide6 editor for .xtbl action node files.
Python 3.10+
"""

import sys
import os
import xml.etree.ElementTree as ET
from copy import deepcopy
from typing import Optional

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTreeWidget, QTreeWidgetItem,
    QSplitter, QScrollArea, QFormLayout, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox,
    QCheckBox, QPushButton, QFileDialog, QMessageBox, QMenuBar,
    QMenu, QStatusBar, QPlainTextEdit, QWidget, QGroupBox
)

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QFont, QAction, QUndoStack, QUndoCommand
# ─────────────────────────────────────────────
# THEME
# ─────────────────────────────────────────────
QSS = """
* {
    background-color: #000000;
    color: #00FF90;
    font-size: 15px;
}
QMainWindow, QWidget {
    background-color: #000000;
}
QTreeWidget, QTreeView {
    background-color: #000000;
    color: #00FF90;
    border: 1px solid #8B004B;
    selection-background-color: #00008B;
    selection-color: #00FF90;
}
QTreeWidget::item {
    padding: 2px 0;
}
QTreeWidget::item:selected {
    background-color: #00008B;
    color: #00FF90;
}
QTreeWidget::item:hover {
    background-color: #1a0033;
}
QHeaderView::section {
    background-color: #1a0011;
    color: #8B004B;
    border: none;
    padding: 4px;
}
QPushButton {
    background-color: #4B0082;
    color: #00FF90;
    border: 1px solid #8B004B;
    padding: 4px 12px;
    border-radius: 2px;
}
QPushButton:hover {
    background-color: #5B2D9E;
}
QPushButton:pressed {
    background-color: #3A0068;
}
QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {
    background-color: #0d0008;
    color: #00FF90;
    border: 1px solid #8B004B;
    padding: 2px 6px;
    selection-background-color: #00008B;
}
QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus {
    border: 1px solid #00FF90;
}
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox QAbstractItemView {
    background-color: #0d0008;
    color: #00FF90;
    selection-background-color: #00008B;
    border: 1px solid #8B004B;
}
QLabel {
    color: #00FF90;
    background-color: transparent;
}
QGroupBox {
    color: #8B004B;
    border: 1px solid #8B004B;
    border-radius: 3px;
    margin-top: 8px;
    padding-top: 12px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
    color: #8B004B;
}
QCheckBox {
    color: #00FF90;
    spacing: 6px;
}
QCheckBox::indicator {
    width: 14px;
    height: 14px;
    border: 1px solid #8B004B;
    background-color: #0d0008;
}
QCheckBox::indicator:checked {
    background-color: #4B0082;
    border-color: #00FF90;
}
QMenuBar {
    background-color: #000000;
    color: #00FF90;
    border-bottom: 1px solid #8B004B;
}
QMenuBar::item:selected {
    background-color: #00008B;
}
QMenu {
    background-color: #0d0008;
    color: #00FF90;
    border: 1px solid #8B004B;
}
QMenu::item:selected {
    background-color: #00008B;
}
QStatusBar {
    background-color: #0d0008;
    color: #00FF90;
    border-top: 1px solid #8B004B;
}
QPlainTextEdit {
    background-color: #0d0008;
    color: #00FF90;
    border: 1px solid #8B004B;
    font-family: "Cascadia Code", "Consolas", monospace;
    font-size: 14px;
}
QSplitter::handle {
    background-color: #8B004B;
    width: 2px;
}
QScrollBar:vertical {
    background: #0d0008;
    width: 10px;
    border: none;
}
QScrollBar::handle:vertical {
    background: #4B0082;
    min-height: 20px;
    border-radius: 3px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QScrollBar:horizontal {
    background: #0d0008;
    height: 10px;
    border: none;
}
QScrollBar::handle:horizontal {
    background: #4B0082;
    min-width: 20px;
    border-radius: 3px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}
QToolTip {
    background-color: #00008B;
    color: #00FF90;
    border: 1px solid #8B004B;
}
"""


# ─────────────────────────────────────────────
# UNDO COMMANDS
# ─────────────────────────────────────────────
class SetElementTextCommand(QUndoCommand):
    """Undo command for changing a leaf element's text."""

    def __init__(self, element: ET.Element, old_text: str, new_text: str, description: str):
        super().__init__(description)
        self._element = element
        self._old_text = old_text
        self._new_text = new_text

    def redo(self):
        self._element.text = self._new_text

    def undo(self):
        self._element.text = self._old_text


class SetAttributeCommand(QUndoCommand):
    """Undo command for changing a parent element's child (simulated attribute)."""

    def __init__(self, parent: ET.Element, tag: str, old_text: str, new_text: str, description: str):
        super().__init__(description)
        self._parent = parent
        self._tag = tag
        self._old_text = old_text
        self._new_text = new_text

    def redo(self):
        child = self._parent.find(self._tag)
        if child is not None:
            child.text = self._new_text

    def undo(self):
        child = self._parent.find(self._tag)
        if child is not None:
            child.text = self._old_text


class AddChildCommand(QUndoCommand):
    """Undo command for adding a child element."""

    def __init__(self, parent: ET.Element, new_element: ET.Element, description: str):
        super().__init__(description)
        self._parent = parent
        self._element = new_element
        self._index = len(parent)

    def redo(self):
        self._parent.insert(self._index, self._element)

    def undo(self):
        self._parent.remove(self._element)


class RemoveChildCommand(QUndoCommand):
    """Undo command for removing a child element."""

    def __init__(self, parent: ET.Element, element: ET.Element, description: str):
        super().__init__(description)
        self._parent = parent
        self._element = element
        self._index = list(parent).index(element)

    def redo(self):
        self._parent.remove(self._element)

    def undo(self):
        self._parent.insert(self._index, self._element)


# ─────────────────────────────────────────────
# XTLB MODEL
# ─────────────────────────────────────────────
class XtblFile:
    """Represents a single loaded .xtbl file."""

    def __init__(self, path: str):
        self.path = path
        self.filename = os.path.basename(path)
        self.tree: Optional[ET.ElementTree] = None
        self.root: Optional[ET.Element] = None
        self._load()

    def _load(self):
        self.tree = ET.parse(self.path)
        self.root = self.tree.getroot()

    def save(self, path: Optional[str] = None):
        if path:
            self.path = path
        # Write with pretty-print (tabs, matching Volition style)
        ET.indent(self.tree, space="\t")
        self.tree.write(self.path, encoding="unicode", xml_declaration=False)

    def get_element_by_path(self, path: list[str]) -> Optional[ET.Element]:
        """Navigate from root by list of tags."""
        current = self.root
        for tag in path:
            current = current.find(tag)
            if current is None:
                return None
        return current


# ─────────────────────────────────────────────
# MAIN WINDOW
# ─────────────────────────────────────────────
class NodeEditor(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("SR2 Action Node Editor")
        self.setMinimumSize(1100, 700)

        self.undo_stack = QUndoStack(self)
        self.undo_stack.setUndoLimit(100)

        self.files: dict[str, XtblFile] = {}  # filename -> XtblFile
        self.current_file: Optional[XtblFile] = None
        self.word_wrap_enabled = False

        self._build_ui()
        self._build_menus()
        self._build_statusbar()

    # ── UI CONSTRUCTION ──────────────────────
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)

        self.splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(self.splitter)

        # LEFT: Tree
        self.tree_widget = QTreeWidget()
        self.tree_widget.setHeaderHidden(True)
        self.tree_widget.setWordWrap(False)
        self.tree_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree_widget.customContextMenuRequested.connect(self._tree_context_menu)
        self.tree_widget.currentItemChanged.connect(self._on_tree_selection)
        self.splitter.addWidget(self.tree_widget)

        # RIGHT: Form panel
        self.form_scroll = QScrollArea()
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setFrameShape(QScrollArea.StyledPanel)
        self.form_container = QWidget()
        self.form_layout = QFormLayout(self.form_container)
        self.form_layout.setContentsMargins(12, 12, 12, 12)
        self.form_layout.setSpacing(8)
        self.form_scroll.setWidget(self.form_container)
        self.splitter.addWidget(self.form_scroll)

        self.splitter.setSizes([400, 500])

        # BOTTOM: Raw XML view (collapsible)
        self.raw_view = QPlainTextEdit()
        self.raw_view.setReadOnly(True)
        self.raw_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.raw_view.setMaximumHeight(0)  # hidden by default
        self.raw_view.setVisible(False)
        main_layout.addWidget(self.raw_view)

        # Adjust raw view visibility
        self._raw_view_visible = False

    def _build_menus(self):
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("&File")

        act_open = QAction("&Open...", self)
        act_open.setShortcut(QKeySequence.Open)
        act_open.triggered.connect(self._open_file)
        file_menu.addAction(act_open)

        act_open_dir = QAction("Open &Folder...", self)
        act_open_dir.triggered.connect(self._open_folder)
        file_menu.addAction(act_open_dir)

        file_menu.addSeparator()

        act_save = QAction("&Save", self)
        act_save.setShortcut(QKeySequence.Save)
        act_save.triggered.connect(self._save_file)
        file_menu.addAction(act_save)

        act_save_as = QAction("Save &As...", self)
        act_save_as.setShortcut(QKeySequence.SaveAs)
        act_save_as.triggered.connect(self._save_file_as)
        file_menu.addAction(act_save_as)

        file_menu.addSeparator()

        act_exit = QAction("&Exit", self)
        act_exit.setShortcut(QKeySequence.Quit)
        act_exit.triggered.connect(self.close)
        file_menu.addAction(act_exit)

        # Edit menu
        edit_menu = menubar.addMenu("&Edit")

        act_undo = self.undo_stack.createUndoAction(self, "&Undo")
        act_undo.setShortcut(QKeySequence.Undo)
        edit_menu.addAction(act_undo)

        act_redo = self.undo_stack.createRedoAction(self, "&Redo")
        act_redo.setShortcut(QKeySequence.Redo)
        edit_menu.addAction(act_redo)

        edit_menu.addSeparator()

        act_add_node = QAction("&Add Action Node", self)
        act_add_node.triggered.connect(self._add_action_node)
        edit_menu.addAction(act_add_node)

        act_remove_node = QAction("&Remove Selected", self)
        act_remove_node.triggered.connect(self._remove_selected)
        edit_menu.addAction(act_remove_node)

        # View menu
        view_menu = menubar.addMenu("&View")

        self.act_word_wrap = QAction("Word &Wrap", self)
        self.act_word_wrap.setCheckable(True)
        self.act_word_wrap.setChecked(False)
        self.act_word_wrap.toggled.connect(self._toggle_word_wrap)
        view_menu.addAction(self.act_word_wrap)

        self.act_raw_view = QAction("Show &Raw XML", self)
        self.act_raw_view.setCheckable(True)
        self.act_raw_view.setChecked(False)
        self.act_raw_view.toggled.connect(self._toggle_raw_view)
        view_menu.addAction(self.act_raw_view)

        # Validate menu
        val_menu = menubar.addMenu("&Validate")

        act_validate = QAction("&Cross-Reference Check", self)
        act_validate.triggered.connect(self._validate_xrefs)
        val_menu.addAction(act_validate)

    def _build_statusbar(self):
        self.statusBar().showMessage("Ready. Open a .xtbl file to begin.")

    # ── FILE OPERATIONS ──────────────────────
    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open .xtbl File", "",
            "Xtbl Files (*.xtbl);;All Files (*)"
        )
        if path:
            self._load_file(path)

    def _open_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Open Folder with .xtbl files")
        if folder:
            for f in sorted(os.listdir(folder)):
                if f.endswith(".xtbl"):
                    self._load_file(os.path.join(folder, f))

    def _load_file(self, path: str):
        try:
            xf = XtblFile(path)
            self.files[xf.filename] = xf
            if self.current_file is None:
                self.current_file = xf
            self._rebuild_tree()
            self._update_raw_view()
            self.statusBar().showMessage(f"Loaded: {xf.filename} ({path})")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load {path}:\n{e}")

    def _save_file(self):
        if self.current_file:
            try:
                self.current_file.save()
                self.statusBar().showMessage(f"Saved: {self.current_file.filename}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save:\n{e}")

    def _save_file_as(self):
        if self.current_file:
            path, _ = QFileDialog.getSaveFileName(
                self, "Save As", self.current_file.filename,
                "Xtbl Files (*.xtbl);;All Files (*)"
            )
            if path:
                try:
                    self.current_file.save(path)
                    self.files.pop(self.current_file.filename, None)
                    self.current_file.filename = os.path.basename(path)
                    self.files[self.current_file.filename] = self.current_file
                    self._rebuild_tree()
                    self.statusBar().showMessage(f"Saved: {path}")
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"Failed to save:\n{e}")

    # ── TREE ─────────────────────────────────
    def _rebuild_tree(self):
        self.tree_widget.clear()
        for fname, xf in sorted(self.files.items()):
            file_item = QTreeWidgetItem(self.tree_widget)
            file_item.setText(0, f"📄 {fname}")
            file_item.setFont(0, QFont("", 15, QFont.Bold))
            self._populate_tree(file_item, xf.root, xf)
        self.tree_widget.expandToDepth(1)

    def _populate_tree(self, parent_item: QTreeWidgetItem, element: ET.Element, xf: XtblFile):
        # Store the element reference
        parent_item.setData(0, Qt.UserRole, (element, xf))

        for child in element:
            tag = child.tag
            text = (child.text or "").strip()
            display = f"{tag}"
            if text:
                # Truncate long text
                if len(text) > 50:
                    display = f"{tag}: {text[:47]}..."
                else:
                    display = f"{tag}: {text}"

            child_item = QTreeWidgetItem(parent_item)
            child_item.setText(0, display)
            self._populate_tree(child_item, child, xf)

    def _get_selected_element(self) -> tuple[Optional[ET.Element], Optional[XtblFile]]:
        item = self.tree_widget.currentItem()
        if item is None:
            return None, None
        data = item.data(0, Qt.UserRole)
        if data is None:
            return None, None
        return data[0], data[1]

    def _tree_context_menu(self, pos):
        item = self.tree_widget.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        act_remove = menu.addAction("Remove This Node")
        act_remove.triggered.connect(lambda: self._remove_item(item))
        menu.exec(self.tree_widget.mapToGlobal(pos))

    # ── FORM PANEL ───────────────────────────
    def _on_tree_selection(self, current: QTreeWidgetItem, _):
        self._clear_form()
        if current is None:
            return
        data = current.data(0, Qt.UserRole)
        if data is None:
            return
        element, xf = data
        self._build_form(element, xf)

    def _clear_form(self):
        while self.form_layout.count():
            item = self.form_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _build_form(self, element: ET.Element, xf: XtblFile):
        # Title
        title = QLabel(f"<b>{element.tag}</b>")
        title.setStyleSheet("color: #8B004B; font-size: 17px;")
        self.form_layout.addRow(title)

        # If element has text (leaf), show editable field
        if len(element) == 0 and element.text:
            val = element.text.strip()
            widget = self._make_value_widget(element, val)
            self.form_layout.addRow(element.tag, widget)
        elif len(element) == 0 and not element.text:
            # Empty leaf - show empty field
            widget = QLineEdit("")
            widget.editingFinished.connect(lambda w=widget, e=element: self._on_leaf_edit(w, e, xf))
            self.form_layout.addRow(element.tag, widget)
        else:
            # Has children - show each child as a row
            for child in element:
                ctag = child.tag
                ctext = (child.text or "").strip()
                if len(child) == 0:
                    widget = self._make_value_widget(child, ctext)
                    self.form_layout.addRow(ctag, widget)
                else:
                    # Nested element - show as label (click in tree to edit)
                    label = QLabel(f"▶ {ctag} ({len(child)} children)")
                    label.setStyleSheet("color: #8B004B;")
                    self.form_layout.addRow(ctag, label)

        # Add/remove buttons for list-like elements
        if element.tag in ("action_node", "object", "Action_node_npc",
                           "Action_node_group", "Action_Node_Notoriety",
                           "Node", "NPC", "Animation_action", "State"):
            btn_row = QHBoxLayout()
            btn_add = QPushButton("+ Add")
            btn_add.clicked.connect(lambda: self._add_sibling(element, xf))
            btn_remove = QPushButton("− Remove")
            btn_remove.clicked.connect(lambda: self._remove_element(element, xf))
            btn_row.addWidget(btn_add)
            btn_row.addWidget(btn_remove)
            btn_row.addStretch()
            self.form_layout.addRow(btn_row)

    def _make_value_widget(self, element: ET.Element, value: str) -> QWidget:
        """Create the appropriate widget for a value based on its type."""
        # Try int
        try:
            v = int(value)
            spin = QSpinBox()
            spin.setRange(-2**31, 2**31 - 1)
            spin.setValue(v)
            spin.editingFinished.connect(lambda: self._on_spin_edit(element, spin, v))
            return spin
        except ValueError:
            pass

        # Try float
        try:
            v = float(value)
            spin = QDoubleSpinBox()
            spin.setRange(-1e6, 1e6)
            spin.setDecimals(4)
            spin.setValue(v)
            spin.editingFinished.connect(lambda: self._on_double_spin_edit(element, spin, v))
            return spin
        except ValueError:
            pass

        # Try bool
        if value.lower() in ("true", "false"):
            cb = QCheckBox()
            cb.setChecked(value.lower() == "true")
            cb.toggled.connect(lambda: self._on_bool_edit(element, cb, value))
            return cb

        # Default: text
        le = QLineEdit(value)
        le.editingFinished.connect(lambda: self._on_text_edit(element, le, value))
        return le

    # ── EDIT HANDLERS ────────────────────────
    def _on_text_edit(self, element: ET.Element, widget: QLineEdit, old_val: str):
        new_val = widget.text()
        if new_val != old_val:
            self.undo_stack.push(
                SetElementTextCommand(element, old_val, new_val, f"Edit {element.tag}")
            )
            self._rebuild_tree()
            self._update_raw_view()

    def _on_spin_edit(self, element: ET.Element, widget: QSpinBox, old_val: str):
        new_val = str(widget.value())
        if new_val != old_val:
            self.undo_stack.push(
                SetElementTextCommand(element, old_val, new_val, f"Edit {element.tag}")
            )
            self._rebuild_tree()
            self._update_raw_view()

    def _on_double_spin_edit(self, element: ET.Element, widget: QDoubleSpinBox, old_val: str):
        new_val = str(widget.value())
        if new_val != old_val:
            self.undo_stack.push(
                SetElementTextCommand(element, old_val, new_val, f"Edit {element.tag}")
            )
            self._rebuild_tree()
            self._update_raw_view()

    def _on_bool_edit(self, element: ET.Element, widget: QCheckBox, old_val: str):
        new_val = "True" if widget.isChecked() else "False"
        if new_val != old_val:
            self.undo_stack.push(
                SetElementTextCommand(element, old_val, new_val, f"Edit {element.tag}")
            )
            self._rebuild_tree()
            self._update_raw_view()

    def _on_leaf_edit(self, widget: QLineEdit, element: ET.Element, xf: XtblFile):
        new_val = widget.text()
        old_val = (element.text or "").strip()
        if new_val != old_val:
            self.undo_stack.push(
                SetElementTextCommand(element, old_val, new_val, f"Edit {element.tag}")
            )
            self._rebuild_tree()
            self._update_raw_view()

    # ── ADD / REMOVE ─────────────────────────
    def _add_action_node(self):
        """Add a new action_node to the selected object."""
        element, xf = self._get_selected_element()
        if element is None or xf is None:
            QMessageBox.information(self, "Info", "Select an <object> in the tree first.")
            return
        if element.tag != "object":
            QMessageBox.information(self, "Info", "Select an <object> element to add a node to.")
            return

        # Create new action_node
        new_node = ET.SubElement(element, "action_node")
        ET.SubElement(new_node, "name").text = "new_node"
        ET.SubElement(new_node, "group").text = "R_NewGroup"
        ET.SubElement(new_node, "npc").text = "NewNPC"
        transform = ET.SubElement(new_node, "transform")
        transform.text = "0.0 0.0 0.0\n0.0 0.0 0.0 1.0"

        # Update count
        count_el = element.find("num_action_nodes")
        if count_el is not None:
            old_count = count_el.text or "0"
            new_count = str(int(old_count) + 1)
            self.undo_stack.beginMacro("Add Action Node")
            self.undo_stack.push(
                SetElementTextCommand(count_el, old_count, new_count, "Update count")
            )
            self.undo_stack.endMacro()

        self._rebuild_tree()
        self._update_raw_view()
        self.statusBar().showMessage("Added new action_node")

    def _add_sibling(self, element: ET.Element, xf: XtblFile):
        """Add a sibling of the same tag to the parent."""
        parent = None
        # Find parent by searching
        parent = self._find_parent(xf.root, element)
        if parent is None:
            return
        new_el = ET.Element(element.tag)
        # Copy child structure (empty values)
        for child in element:
            new_child = ET.SubElement(new_el, child.tag)
            new_child.text = ""
        self.undo_stack.push(
            AddChildCommand(parent, new_el, f"Add {element.tag}")
        )
        self._rebuild_tree()
        self._update_raw_view()

    def _remove_selected(self):
        element, xf = self._get_selected_element()
        if element is None or xf is None:
            return
        if element.tag in ("root", "Table", "TableDescription", "TableTemplates",
                           "EntryCategories", "root"):
            QMessageBox.information(self, "Info", "Cannot remove root elements.")
            return
        self._remove_element(element, xf)

    def _remove_element(self, element: ET.Element, xf: XtblFile):
        parent = self._find_parent(xf.root, element)
        if parent is None:
            return
        self.undo_stack.push(
            RemoveChildCommand(parent, element, f"Remove {element.tag}")
        )
        self._rebuild_tree()
        self._update_raw_view()

    def _remove_item(self, item: QTreeWidgetItem):
        data = item.data(0, Qt.UserRole)
        if data is None:
            return
        element, xf = data
        if element.tag in ("root", "Table", "TableDescription", "TableTemplates",
                           "EntryCategories"):
            QMessageBox.information(self, "Info", "Cannot remove root elements.")
            return
        self._remove_element(element, xf)

    def _find_parent(self, root: ET.Element, target: ET.Element) -> Optional[ET.Element]:
        """BFS to find the parent of target element."""
        if root is target:
            return None
        stack = [root]
        while stack:
            current = stack.pop()
            for child in current:
                if child is target:
                    return current
                stack.append(child)
        return None

    # ── VIEW TOGGLES ─────────────────────────
    def _toggle_word_wrap(self, checked: bool):
        self.word_wrap_enabled = checked
        self.tree_widget.setWordWrap(checked)

    def _toggle_raw_view(self, checked: bool):
        self._raw_view_visible = checked
        self.raw_view.setVisible(checked)
        if checked:
            self.raw_view.setMaximumHeight(200)
            self._update_raw_view()
        else:
            self.raw_view.setMaximumHeight(0)

    def _update_raw_view(self):
        if self.current_file and self._raw_view_visible:
            xml_str = ET.tostring(self.current_file.root, encoding="unicode")
            self.raw_view.setPlainText(xml_str)

    # ── VALIDATION ───────────────────────────
    def _validate_xrefs(self):
        """Check cross-references between loaded files."""
        errors = []
        warnings = []

        # Collect all NPC names from action_nodes_npcs.xtbl
        npc_names = set()
        npc_file = self.files.get("action_nodes_npcs.xtbl")
        if npc_file:
            for npc_el in npc_file.root.iter("Action_node_npc"):
                name_el = npc_el.find("Name")
                if name_el is not None and name_el.text:
                    npc_names.add(name_el.text.strip())

        # Collect all group names from action_node_groups.xtbl
        group_names = set()
        group_file = self.files.get("action_node_groups.xtbl")
        if group_file:
            for grp_el in group_file.root.iter("Action_node_group"):
                name_el = grp_el.find("Name")
                if name_el is not None and name_el.text:
                    group_names.add(name_el.text.strip())

        # Check action_nodes.xtbl
        placement_file = self.files.get("action_nodes.xtbl")
        if placement_file:
            for node_el in placement_file.root.iter("action_node"):
                npc_el = node_el.find("npc")
                grp_el = node_el.find("group")
                if npc_el is not None and npc_el.text:
                    npc_val = npc_el.text.strip()
                    if npc_names and npc_val not in npc_names:
                        errors.append(
                            f"action_nodes.xtbl: <npc>{npc_val}</npc> not found in action_nodes_npcs.xtbl"
                        )
                if grp_el is not None and grp_el.text:
                    grp_val = grp_el.text.strip()
                    if group_names and grp_val not in group_names:
                        warnings.append(
                            f"action_nodes.xtbl: <group>{grp_val}</group> not found in action_node_groups.xtbl"
                        )

        # Check group NPC lists
        if group_file and npc_file:
            for grp_el in group_file.root.iter("Action_node_group"):
                npc_list = grp_el.find("NPC_List")
                if npc_list is not None:
                    for npc in npc_list.findall("NPC"):
                        if npc.text and npc.text.strip() not in npc_names:
                            errors.append(
                                f"action_node_groups.xtbl: NPC '{npc.text.strip()}' not in action_nodes_npcs.xtbl"
                            )

        # Report
        if not errors and not warnings:
            self.statusBar().showMessage("✓ Validation passed: no cross-reference errors.")
            QMessageBox.information(self, "Validation", "All cross-references are valid.")
        else:
            msg = ""
            if errors:
                msg += f"ERRORS ({len(errors)}):\n" + "\n".join(errors) + "\n\n"
            if warnings:
                msg += f"WARNINGS ({len(warnings)}):\n" + "\n".join(warnings)
            self.statusBar().showMessage(f"⚠ {len(errors)} errors, {len(warnings)} warnings")
            QMessageBox.warning(self, "Validation Results", msg)


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)
    app.setFont(QFont("", 15))

    window = NodeEditor()
    window.show()

    # Auto-open folder if passed as arg
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if os.path.isdir(arg):
            for f in sorted(os.listdir(arg)):
                if f.endswith(".xtbl"):
                    window._load_file(os.path.join(arg, f))
        elif os.path.isfile(arg):
            window._load_file(arg)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

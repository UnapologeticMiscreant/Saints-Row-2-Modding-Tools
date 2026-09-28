#!/usr/bin/env python3
"""
Vehicle Color Pool Editor - Auto-calculating Version
Fixed XML Parser & Serializer
User sets base color only; program calculates all other values
"""

import sys
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QListWidget, QListWidgetItem, QLabel, QPushButton,
    QLineEdit, QColorDialog, QSpinBox, QDoubleSpinBox, QDialog,
    QMessageBox, QMenuBar, QToolBar, QStatusBar, QScrollArea,
    QFormLayout, QComboBox, QCheckBox, QFileDialog, QInputDialog,
    QMenu, QAbstractItemView, QGroupBox
)
from PySide6.QtGui import (
    QFont, QTextCursor, QColor, QIcon, QAction, QPalette
)
from PySide6.QtCore import Qt, QSize, Signal

VERSION = "1.0.1"  # Fixed XML parsing
APP_NAME = "Vehicle Color Pool Editor"
DEFAULT_FONT_SIZE = 14

THEME_COLORS = {
    "background": "#000000",
    "panel_background": "#151515",
    "text": "#00FF90",
    "highlight_bg": "#6A0DAD",
    "button": "#1E3A8A",
    "button_hover": "#2563EB",
    "error": "#DC2626",
    "warning": "#F59E0B",
    "success": "#10B981",
    "border": "#374151",
    "muted_text": "#A1A1AA",
}

PAINT_TYPES = ["Opaque", "Matte", "Gloss", "Metallic", "Standard", "Not_Carpaint"]
DEFAULT_PAINT_TYPE = "Standard"

@dataclass
class VectorValue:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    
    def to_rgb(self) -> Tuple[int, int, int]:
        return (max(0, min(255, int(round(self.x)))),
                max(0, min(255, int(round(self.y)))),
                max(0, min(255, int(round(self.z)))))
    
    @classmethod
    def from_rgb(cls, r: int, g: int, b: int) -> 'VectorValue':
        return cls(float(r), float(g), float(b))

@dataclass
class ShaderVariable:
    name: str = ""
    var_type: str = "float"  # "vector" or "float"
    vector_value: Optional[VectorValue] = None
    float_value: Optional[float] = None
    is_calculated: bool = True

@dataclass  
class ColorEntry:
    name: str = ""
    paint_type: str = DEFAULT_PAINT_TYPE
    shader_variables: List[ShaderVariable] = field(default_factory=list)
    
    def get_variable(self, name: str) -> Optional[ShaderVariable]:
        for var in self.shader_variables:
            if var.name.lower() == name.lower():
                return var
        return None
    
    def set_variable(self, var: ShaderVariable):
        for i, existing in enumerate(self.shader_variables):
            if existing.name.lower() == var.name.lower():
                self.shader_variables[i] = var
                return
        self.shader_variables.append(var)


class ColorEntryParser:
    """
    FIXED: Parses XML structure matching the actual XTBL format
    Handles: <Vector_Element><Vector><X>..</X><Y>..</Y><Z>..</Z></Vector></Vector_Element>
             <Float_Element><Float>..</Float></Float_Element>
    """
    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []
    
    def parse(self, text: str) -> List[ColorEntry]:
        self.errors = []
        self.warnings = []
        entries = []
        
        # Remove duplicate opening tags like "<Color><Color>"
        text = re.sub(r'<Color>\s*<Color>', '<Color>', text)
        
        matches = list(re.finditer(r'<Color>(.*?)</Color>', text, re.DOTALL))
        
        if not matches:
            self.errors.append("No <Color> entries found")
            return entries
        
        for match in matches:
            try:
                content = match.group(1)
                entry = self._parse_entry(content)
                if entry.name:  # Only add entries with valid names
                    entries.append(entry)
                else:
                    self.warnings.append("Skipped entry with no Name")
            except Exception as e:
                self.errors.append(f"Parse error for entry: {str(e)}")
        
        return entries
    
    def _parse_entry(self, content: str) -> ColorEntry:
        entry = ColorEntry()
        
        # Parse Name
        name_match = re.search(r'<Name>([^<]+?)</Name>\s*<Grid>', content)
        if name_match:
            entry.name = name_match.group(1).strip()
        else:
            # Try fallback
            name_match = re.search(r'<Name>([^<]+?)</Name>', content)
            if name_match:
                entry.name = name_match.group(1).strip()
        
        # Parse Paint_Type
        paint_match = re.search(r'<Paint_Type>([^<]+?)</Paint_Type>', content)
        if paint_match:
            entry.paint_type = paint_match.group(1).strip()
        
        # Parse Shader_Values - FIXED regex to match actual XML structure
        shader_pattern = (
            r'<Shader_Values>\s*'
            r'<Shader_Variable>([^<]+?)</Shader_Variable>\s*'
            r'<Value>(.*?)'
            r'</Value>\s*'
            r'</Shader_Values>'
        )
        shader_matches = re.findall(shader_pattern, content, re.DOTALL)
        
        for var_name, var_content in shader_matches:
            var = self._parse_variable(var_name.strip(), var_content.strip())
            if var:
                entry.shader_variables.append(var)
            else:
                self.warnings.append(f"Could not parse variable: {var_name}")
        
        return entry
    
    def _parse_variable(self, name: str, content: str) -> Optional[ShaderVariable]:
        """
        FIXED: Matches actual XML structure:
        <Vector_Element><Vector><X>..</X><Y>..</Y><Z>..</Z></Vector></Vector_Element>
        <Float_Element><Float>..</Float></Float_Element>
        """
        var = ShaderVariable(name=name)
        
        # Try to find Vector_Element with Vector containing X, Y, Z
        vector_pattern = (
            r'<Vector_Element>\s*'
            r'<Vector>\s*'
            r'<X>([^<]+?)</X>\s*'
            r'<Y>([^<]+?)</Y>\s*'
            r'<Z>([^<]+?)</Z>\s*'
            r'</Vector>\s*'
            r'</Vector_Element>'
        )
        vector_match = re.search(vector_pattern, content, re.DOTALL)
        
        if vector_match:
            try:
                var.var_type = "vector"
                var.vector_value = VectorValue(
                    x=float(vector_match.group(1).strip()),
                    y=float(vector_match.group(2).strip()),
                    z=float(vector_match.group(3).strip())
                )
                return var
            except ValueError as e:
                print(f"[PARSER WARNING] Invalid vector value for {name}: {e}")
        
        # Try to find Float_Element with Float value
        float_pattern = r'<Float_Element>\s*<Float>([^<]+?)</Float>\s*</Float_Element>'
        float_match = re.search(float_pattern, content, re.DOTALL)
        
        if float_match:
            try:
                var.var_type = "float"
                var.float_value = float(float_match.group(1).strip())
                return var
            except ValueError as e:
                print(f"[PARSER WARNING] Invalid float value for {name}: {e}")
        
        print(f"[PARSER WARNING] Could not determine type for variable: {name}")
        return None
    
    def serialize(self, entries: List[ColorEntry]) -> str:
        """
        FIXED: Produces XML matching the template structure exactly
        """
        return '\n'.join(self._serialize_entry(e) for e in entries)
    
    def _serialize_entry(self, entry: ColorEntry) -> str:
        lines = ["<Color>", f"    <Name>{entry.name}</Name>", "    <Grid>"]
        
        for var in entry.shader_variables:
            lines.append("        <Shader_Values>")
            lines.append(f"            <Shader_Variable>{var.name}</Shader_Variable>")
            lines.append("            <Value>")
            
            if var.var_type == "vector" and var.vector_value:
                v = var.vector_value
                lines.extend([
                    "                <Vector_Element>",
                    "                    <Vector>",
                    f"                        <X>{v.x}</X>",
                    f"                        <Y>{v.y}</Y>",
                    f"                        <Z>{v.z}</Z>",
                    "                    </Vector>",
                    "                </Vector_Element>"
                ])
            elif var.var_type == "float" and var.float_value is not None:
                lines.extend([
                    "                <Float_Element>",
                    f"                    <Float>{var.float_value}</Float>",
                    "                </Float_Element>"
                ])
            
            lines.extend(["            </Value>", "        </Shader_Values>"])
        
        lines.extend([
            "    </Grid>",
            "    <_Editor>",
            "        <Category>Entries</Category>",
            "    </_Editor>",
            f"    <Paint_Type>{entry.paint_type}</Paint_Type>",
            "</Color>"
        ])
        
        return '\n'.join(lines)


class ColorCalculator:
    @staticmethod
    def calculate_specular_color(base_rgb: Tuple[int, int, int]) -> Tuple[int, int, int]:
        r, g, b = base_rgb
        factor = 1.4
        return (min(255, int(r * factor)), min(255, int(g * factor)), min(255, int(b * factor)))
    
    @staticmethod
    def calculate_fresnel_color(base_rgb: Tuple[int, int, int]) -> Tuple[int, int, int]:
        r, g, b = base_rgb
        factor = 0.7
        return (max(0, int(r * factor)), max(0, int(g * factor)), max(0, int(b * factor)))
    
    @staticmethod
    def calculate_defaults(base_rgb: Tuple[int, int, int], paint_type: str) -> Dict[str, any]:
        r, g, b = base_rgb
        brightness = (r + g + b) / 3
        
        paint_glossiness = {"Gloss": 0.75, "Metallic": 0.85, "Standard": 0.55, "Matte": 0.35, "Opaque": 0.50, "Not_Carpaint": 0.45}
        paint_sharpness = {"Metallic": 60.0, "Gloss": 50.0, "Standard": 40.0, "Matte": 25.0, "Opaque": 35.0, "Not_Carpaint": 30.0}
        
        specular_alpha = paint_glossiness.get(paint_type, 0.55)
        specular_power = paint_sharpness.get(paint_type, 40.0)
        fresnel_brightness = 1.0
        fresnel_contrast = 1.5 if brightness > 128 else 1.8
        reflection_opacity = min(1.0, brightness / 200.0)
        reflection_contrast = 0.6
        reflection_brightness = 0.3
        
        return {
            "Specular_Color": ColorCalculator.calculate_specular_color(base_rgb),
            "Fresnel_Color": ColorCalculator.calculate_fresnel_color(base_rgb),
            "Specular_Alpha": specular_alpha, 
            "Specular_Power": specular_power,
            "Fresnel_Falloff_Brightness_Amount": fresnel_brightness, 
            "Fresnel_Falloff_Contrast_Amount": fresnel_contrast,
            "Reflection_Map_Opacity": reflection_opacity, 
            "Reflection_Falloff_Contrast_Amount": reflection_contrast,
            "Reflection_Falloff_Brightness_Amount": reflection_brightness,
        }
    
    @staticmethod
    def recalculate_entry(entry: ColorEntry):
        """Recalculate all auto-generated values based on Base_Paint_Color"""
        base_var = entry.get_variable("Base_Paint_Color")
        if not base_var or base_var.var_type != "vector" or not base_var.vector_value:
            print("[CALCULATOR] ERROR: No valid base color found")
            return
        
        base_rgb = base_var.vector_value.to_rgb()
        print(f"[CALCULATOR] Base RGB: {base_rgb}, Paint Type: {entry.paint_type}")
        calc_values = ColorCalculator.calculate_defaults(base_rgb, entry.paint_type)
        
        for var_name, value in calc_values.items():
            var = entry.get_variable(var_name)
            if not var:
                print(f"[CALCULATOR] WARNING: No variable '{var_name}' exists, skipping")
                continue
            
            if not var.is_calculated:
                print(f"[CALCULATOR] SKIPPED '{var_name}': marked as manually edited")
                continue
            
            if var.var_type == "vector" and isinstance(value, tuple):
                var.vector_value = VectorValue.from_rgb(*value)
                print(f"[CALCULATOR] Updated {var_name}: RGB{value}")
            elif var.var_type == "float" and isinstance(value, float):
                var.float_value = value
                print(f"[CALCULATOR] Updated {var_name}: {value:.3f}")


class ColorPreviewDialog(QDialog):
    """
    FIXED: Shows colors for vector types, numbers for float types with proper labels
    """
    def __init__(self, parent=None, var_name: str = "", var: ShaderVariable = None):
        super().__init__(parent)
        self.setWindowTitle(f"Preview: {var_name}")
        self.setModal(True)
        self.setMinimumSize(450, 350)
        self._var = var
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(25, 25, 25, 25)
        layout.setSpacing(20)
        
        title = QLabel(f"{self._var.name}" if self._var else "Unknown")
        title.setStyleSheet("color: #00FF90; font-size: 20px; font-weight: bold;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        
        self.display_area = QLabel()
        self.display_area.setMinimumSize(380, 200)
        self.display_area.setMaximumSize(500, 300)
        self.display_area.setAlignment(Qt.AlignCenter)
        self.display_area.setTextFormat(Qt.PlainText)
        
        if self._var and self._var.var_type == "vector" and self._var.vector_value:
            # COLOR DISPLAY - Shows RGB colors
            r, g, b = self._var.vector_value.to_rgb()
            text_color = 'black' if (r + g + b) / 3 > 128 else 'white'
            
            self.display_area.setStyleSheet(
                f"background-color: rgb({r}, {g}, {b}); "
                f"color: {text_color}; "
                f"font-size: 22px; "
                f"font-weight: bold; "
                f"border: 5px solid #6A0DAD; "
                f"border-radius: 15px; "
                f"padding: 25px;"
            )
            self.display_area.setText(
                f"RGB: {r}, {g}, {b}\n\n"
                f"Hex: #{r:02X}{g:02X}{b:02X}\n\n"
                f"(Color Value)"
            )
            
        elif self._var and self._var.var_type == "float":
            # NUMBER DISPLAY - Shows float values with context labels
            val = self._var.float_value
            label_text = f"{val:.3f}"
            
            # Add helpful labels based on variable name
            if "Alpha" in self._var.name or "Opacity" in self._var.name:
                label_text += f"\n\n({val:.0%} Opacity)"
            elif "Power" in self._var.name or "Sharpness" in self._var.name:
                label_text += "\n\n(Higher = sharper highlight)"
            elif "Brightness" in self._var.name:
                label_text += "\n\n(Brightness multiplier)"
            elif "Contrast" in self._var.name:
                label_text += "\n\n(Contrast multiplier)"
            else:
                label_text += "\n\n(Numerical value)"
            
            self.display_area.setStyleSheet(
                "background-color: #1A1A1A; "
                "color: #00FF90; "
                "font-size: 36px; "
                "font-weight: bold; "
                "border: 5px solid #6A0DAD; "
                "border-radius: 15px; "
                "padding: 25px;"
            )
            self.display_area.setText(label_text)
            
        else:
            self.display_area.setStyleSheet(
                "background-color: #1A1A1A; "
                "color: #DC2626; "
                "font-size: 16px; "
                "border: 5px dashed #6A0DAD; "
                "border-radius: 15px; "
                "padding: 25px;"
            )
            self.display_area.setText("ERROR: No value to display")
        
        layout.addWidget(self.display_area)
        
        # Status indicator
        status = QLabel()
        if self._var:
            if self._var.var_type == "vector":
                status.setText("✓ Color value (Vector)")
            else:
                status.setText("✓ Numerical value (Float)")
            status.setText(status.text() + (" - Auto-calculated" if self._var.is_calculated else " - Manually edited"))
        else:
            status.setText("? Unknown status")
        status.setStyleSheet("color: #A1A1AA; font-size: 13px; font-weight: bold;")
        status.setAlignment(Qt.AlignCenter)
        layout.addWidget(status)
        
        layout.addStretch()
        
        close_btn = QPushButton("Close Preview")
        close_btn.setStyleSheet(
            "background: #1E3A8A; "
            "color: white; "
            "font-size: 14px; "
            "font-weight: bold; "
            "padding: 15px 35px; "
            "border-radius: 5px;"
        )
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)


class InspectorPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_entry: Optional[ColorEntry] = None
        self.on_update_callback = None
        self.parent_window = parent
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        
        self.content = QWidget()
        self.layout = QVBoxLayout(self.content)
        self.layout.setContentsMargins(10, 10, 10, 10)
        self.layout.setSpacing(8)
        scroll.setWidget(self.content)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)
        self.setStyleSheet("background: #151515;")
    
    def set_entry(self, entry: Optional[ColorEntry], callback=None):
        self.current_entry = entry
        self.on_update_callback = callback
        if entry:
            ColorCalculator.recalculate_entry(entry)
        self._render()
    
    def _render(self):
        while self.layout.count():
            item = self.layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        if not self.current_entry:
            label = QLabel("Select a color entry from the left panel")
            label.setStyleSheet("color: #A1A1AA; font-size: 14px;")
            label.setAlignment(Qt.AlignCenter)
            self.layout.addWidget(label)
            return
        
        header = QLabel("Selected Entry - Auto-Calculated Mode")
        header.setStyleSheet("color: #00FF90; font-size: 16px; font-weight: bold; margin-bottom: 10px;")
        self.layout.addWidget(header)
        
        hint = QLabel("💡 You only edit Base Paint Color. All others are auto-calculated!")
        hint.setStyleSheet("color: #F59E0B; font-size: 12px; margin-bottom: 5px;")
        self.layout.addWidget(hint)
        
        name_layout = QHBoxLayout()
        name_label = QLabel("Name:")
        name_label.setStyleSheet("color: white; font-weight: bold;")
        name_layout.addWidget(name_label)
        self.name_edit = QLineEdit(self.current_entry.name)
        self.name_edit.setStyleSheet("color: #00FF90; background: #1A1A1A; padding: 5px;")
        self.name_edit.editingFinished.connect(lambda: self._update_field('name', self.name_edit.text()))
        name_layout.addWidget(self.name_edit)
        self.layout.addLayout(name_layout)
        
        paint_layout = QHBoxLayout()
        paint_label = QLabel("Paint Type:")
        paint_label.setStyleSheet("color: white; font-weight: bold;")
        paint_layout.addWidget(paint_label)
        self.paint_combo = QComboBox()
        self.paint_combo.addItems(PAINT_TYPES)
        self.paint_combo.setCurrentText(self.current_entry.paint_type)
        self.paint_combo.setStyleSheet("color: #00FF90; background: #1A1A1A; padding: 5px;")
        self.paint_combo.currentTextChanged.connect(lambda t: self._on_paint_type_change(t))
        paint_layout.addWidget(self.paint_combo)
        self.layout.addLayout(paint_layout)
        
        divider = QLabel("")
        divider.setMinimumHeight(15)
        divider.setFixedHeight(2)
        divider.setStyleSheet("background: #374151;")
        self.layout.addWidget(divider)
        
        self._add_base_color_widget()
        
        divider2 = QLabel("")
        divider2.setMinimumHeight(10)
        divider2.setFixedHeight(2)
        divider2.setStyleSheet("background: #374151;")
        self.layout.addWidget(divider2)
        
        calc_header = QLabel("Auto-Calculated Values (Click to preview)")
        calc_header.setStyleSheet("color: #00FF90; font-weight: bold;")
        self.layout.addWidget(calc_header)
        
        # Show summary of what's a color vs number
        vector_vars = [v for v in self.current_entry.shader_variables if v.var_type == "vector"]
        float_vars = [v for v in self.current_entry.shader_variables if v.var_type == "float"]
        summary = QLabel(f"Colors (3): {[v.name for v in vector_vars]} | Numbers: {len(float_vars)} fields")
        summary.setStyleSheet("color: #A1A1AA; font-size: 11px; margin-bottom: 10px;")
        self.layout.addWidget(summary)
        
        for var in self.current_entry.shader_variables:
            if var.name != "Base_Paint_Color":
                self._add_preview_widget(var)
    
    def _add_base_color_widget(self):
        group = QGroupBox()
        group.setTitle("🎨 BASE PAINT COLOR - CLICK TO CHANGE")
        group.setStyleSheet("QGroupBox { color: #00FF90; border: 3px solid #00FF90; border-radius: 5px; margin-top: 10px; padding-top: 10px; font-weight: bold; background: #1A1A1A; }")
        grp_layout = QVBoxLayout(group)
        grp_layout.setSpacing(8)
        
        var = self.current_entry.get_variable("Base_Paint_Color")
        current_rgb = (128, 128, 128)
        
        if var and var.vector_value:
            rgb = var.vector_value.to_rgb()
            r, g, b = rgb
            current_rgb = rgb
            avg = (r + g + b) / 3
            text_color = 'white' if avg < 128 else 'black'
            
            color_box = QLabel()
            color_box.setFixedSize(200, 120)
            color_box.setAlignment(Qt.AlignCenter)
            color_box.setCursor(Qt.PointingHandCursor)
            
            def make_click_handler(rgb_tuple):
                def handler(event):
                    self._open_color_picker('Base_Paint_Color', rgb_tuple)
                return handler
            
            color_box.mousePressEvent = make_click_handler(current_rgb)
            color_box.setStyleSheet(f"QLabel {{ background: rgb({r},{g},{b}); color: {text_color}; font-size: 18px; font-weight: bold; border: 4px solid white; border-radius: 8px; }}")
            color_box.setText(f"CLICK ME\n{r} | {g} | {b}")
            
            grp_layout.addWidget(color_box)
            
            instruction = QLabel("⬆️ CLICK THE BOX ABOVE TO CHANGE THIS COLOR")
            instruction.setStyleSheet("color: #F59E0B; font-size: 13px; font-weight: bold;")
            instruction.setAlignment(Qt.AlignCenter)
            grp_layout.addWidget(instruction)
            
            value_lbl = QLabel(f"Current: RGB({r}, {g}, {b})")
            value_lbl.setStyleSheet("color: #00FF90; font-size: 14px;")
            value_lbl.setAlignment(Qt.AlignCenter)
            grp_layout.addWidget(value_lbl)
        
        edit_btn = QPushButton("🎨 CHANGE COLOR NOW")
        edit_btn.setStyleSheet("QPushButton { background: #6A0DAD; color: white; font-size: 14px; font-weight: bold; padding: 10px 20px; border-radius: 5px; } QPushButton:hover { background: #7B1FA2; }")
        edit_btn.clicked.connect(lambda: self._open_color_picker('Base_Paint_Color', current_rgb))
        grp_layout.addWidget(edit_btn)
        
        grp_layout.setContentsMargins(10, 10, 10, 10)
        self.layout.addWidget(group)
    
    def _add_preview_widget(self, var):
        group = QGroupBox()
        group.setTitle(var.name)
        
        if var.is_calculated:
            group.setStyleSheet("QGroupBox { color: #00FF90; border: 2px solid #10B981; border-radius: 5px; margin-top: 10px; padding-top: 10px; font-weight: bold; background: #0A2A0A; }")
        else:
            group.setStyleSheet("QGroupBox { color: #F59E0B; border: 2px solid #F59E0B; border-radius: 5px; margin-top: 10px; padding-top: 10px; font-weight: bold; background: #2A1A0A; }")
        
        grp_layout = QVBoxLayout(group)
        grp_layout.setSpacing(8)
        
        # TYPE INDICATOR
        type_label = QLabel(f"Type: {'🎨 Color' if var.var_type == 'vector' else '🔢 Number'}")
        type_label.setStyleSheet("color: #A1A1AA; font-size: 11px; font-style: italic;")
        grp_layout.addWidget(type_label)
        
        if var.var_type == "vector" and var.vector_value:
            # COLOR PREVIEW
            rgb = var.vector_value.to_rgb()
            r, g, b = rgb
            avg = (r + g + b) / 3
            text_color = 'white' if avg < 128 else 'black'
            
            color_box = QLabel()
            color_box.setFixedSize(180, 80)
            color_box.setAlignment(Qt.AlignCenter)
            color_box.setCursor(Qt.PointingHandCursor)
            
            def make_click_handler(var_obj, var_nm):
                def handler(event):
                    print(f"[PREVIEW] Opening: {var_nm} (type={var_obj.var_type})")
                    self._open_color_preview(var_nm, var_obj)
                return handler
            
            color_box.mousePressEvent = make_click_handler(var, var.name)
            color_box.setStyleSheet(f"QLabel {{ background: rgb({r},{g},{b}); color: {text_color}; font-size: 14px; font-weight: bold; border: 4px solid #6A0DAD; border-radius: 8px; }}")
            color_box.setText(f"RGB({r}, {g}, {b})\n#{r:02X}{g:02X}{b:02X}")
            
            grp_layout.addWidget(color_box)
            
        elif var.var_type == "float" and var.float_value is not None:
            # NUMBER PREVIEW
            value_label = QLabel(f"{var.float_value:.3f}")
            value_label.setFixedSize(180, 80)
            value_label.setStyleSheet("color: #00FF90; font-size: 16px; font-weight: bold; background: #1A1A1A; border: 4px solid #6A0DAD; border-radius: 8px;")
            value_label.setAlignment(Qt.AlignCenter)
            value_label.setCursor(Qt.PointingHandCursor)
            
            def make_float_click_handler(var_obj, var_nm):
                def handler(event):
                    print(f"[PREVIEW] Opening: {var_nm} (type={var_obj.var_type})")
                    self._open_color_preview(var_nm, var_obj)
                return handler
            
            value_label.mousePressEvent = make_float_click_handler(var, var.name)
            grp_layout.addWidget(value_label)
        
        status = QLabel("✓ Auto-calculated" if var.is_calculated else "✎ Manually edited")
        status.setStyleSheet("color: #A1A1AA; font-size: 12px;")
        status.setAlignment(Qt.AlignCenter)
        grp_layout.addWidget(status)
        
        grp_layout.setContentsMargins(10, 10, 10, 10)
        self.layout.addWidget(group)
    
    def _open_color_picker(self, var_name: str, current_rgb: Tuple[int, int, int]):
        dialog = QDialog(self)
        dialog.setWindowTitle("🎨 Change Base Paint Color")
        dialog.resize(450, 320)
        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.setContentsMargins(15, 15, 15, 15)
        dialog_layout.setSpacing(10)
        
        preview = QLabel()
        preview.setFixedSize(350, 120)
        preview.setAlignment(Qt.AlignCenter)
        
        r_spin = QSpinBox()
        r_spin.setRange(0, 255)
        r_spin.setValue(current_rgb[0])
        r_spin.setMinimumWidth(60)
        r_spin.setStyleSheet("color: #00FF90; background: #101010; font-size: 14px; padding: 5px;")
        
        g_spin = QSpinBox()
        g_spin.setRange(0, 255)
        g_spin.setValue(current_rgb[1])
        g_spin.setMinimumWidth(60)
        g_spin.setStyleSheet("color: #00FF90; background: #101010; font-size: 14px; padding: 5px;")
        
        b_spin = QSpinBox()
        b_spin.setRange(0, 255)
        b_spin.setValue(current_rgb[2])
        b_spin.setMinimumWidth(60)
        b_spin.setStyleSheet("color: #00FF90; background: #101010; font-size: 14px; padding: 5px;")
        
        form = QHBoxLayout()
        form.addWidget(QLabel("Red:", styleSheet="color: white;"))
        form.addWidget(r_spin)
        form.addWidget(QLabel("Green:", styleSheet="color: white;"))
        form.addWidget(g_spin)
        form.addWidget(QLabel("Blue:", styleSheet="color: white;"))
        form.addWidget(b_spin)
        
        def update_preview():
            r, g, b = r_spin.value(), g_spin.value(), b_spin.value()
            avg = (r + g + b) / 3
            text = 'black' if avg > 128 else 'white'
            preview.setStyleSheet(f"background: rgb({r},{g},{b}); color: {text}; font-size: 18px; font-weight: bold; padding: 10px; border: 3px solid #6A0DAD; border-radius: 8px;")
            preview.setText(f"RGB({r}, {g}, {b})\n#{r:02X}{g:02X}{b:02X}")
            dialog.adjustSize()
        
        update_preview()
        r_spin.valueChanged.connect(update_preview)
        g_spin.valueChanged.connect(update_preview)
        b_spin.valueChanged.connect(update_preview)
        
        dialog_layout.addWidget(preview)
        dialog_layout.addLayout(form)
        
        btn_layout = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet("background: #DC2626; color: white; padding: 8px 16px;")
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)
        
        ok_btn = QPushButton("✅ Apply & Recalculate ALL Fields")
        ok_btn.setStyleSheet("background: #10B981; color: black; font-weight: bold; font-size: 14px; padding: 8px 16px;")
        ok_btn.clicked.connect(lambda: self._confirm_base_change(r_spin, g_spin, b_spin, dialog))
        btn_layout.addWidget(ok_btn)
        
        dialog_layout.addLayout(btn_layout)
        dialog.exec()
    
    def _confirm_base_change(self, r_spin, g_spin, b_spin, dialog: QDialog):
        r, g, b = r_spin.value(), g_spin.value(), b_spin.value()
        base_var = self.current_entry.get_variable("Base_Paint_Color")
        if base_var:
            base_var.vector_value = VectorValue.from_rgb(r, g, b)
            base_var.is_calculated = False
        
        for var in self.current_entry.shader_variables:
            if var.name != "Base_Paint_Color":
                var.is_calculated = True
        
        ColorCalculator.recalculate_entry(self.current_entry)
        dialog.accept()
        self._render()
        
        if self.on_update_callback:
            self.on_update_callback()
        
        if self.parent_window:
            self.parent_window.statusBar().showMessage("Base color changed - All values recalculated!")
    
    def _open_color_preview(self, var_name: str, var: ShaderVariable):
        print(f"[OPENING PREVIEW] {var_name}: type={var.var_type}")
        dialog = ColorPreviewDialog(self, var_name, var)
        dialog.exec()
        print("[OPENING PREVIEW] Dialog closed")
    
    def _on_paint_type_change(self, new_type: str):
        if self.current_entry:
            self.current_entry.paint_type = new_type
            for var in self.current_entry.shader_variables:
                if var.name != "Base_Paint_Color":
                    var.is_calculated = True
            ColorCalculator.recalculate_entry(self.current_entry)
            self._render()
            if self.on_update_callback:
                self.on_update_callback()
            if self.parent_window:
                self.parent_window.statusBar().showMessage(f"Paint type changed - Values recalculated!")
    
    def _update_field(self, field: str, value):
        if field == 'name':
            self.current_entry.name = value
        elif field == 'paint_type':
            self.current_entry.paint_type = value
        if self.on_update_callback:
            self.on_update_callback()


class EntryNavigator(QListWidget):
    entry_selected = Signal(ColorEntry)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.entries: List[ColorEntry] = []
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.itemActivated.connect(self._on_activate)
        self.itemClicked.connect(self._on_click)
        
        self.setStyleSheet("QListWidget { background: #151515; color: #00FF90; border: 2px solid #374151; font-size: 14px; } QListWidget::item:selected { background: #6A0DAD; }")
    
    def set_entries(self, entries: List[ColorEntry]):
        self.entries = entries
        self.clear()
        for entry in entries:
            item = QListWidgetItem(f"• {entry.name}")
            item.setData(Qt.UserRole, entry)
            self.addItem(item)
        if self.count() > 0:
            self.setCurrentRow(0)
    
    def _on_click(self, item: QListWidgetItem):
        self._emit_selection(item)
    
    def _on_activate(self, item: QListWidgetItem):
        self._emit_selection(item)
    
    def _emit_selection(self, item: QListWidgetItem):
        if item:
            entry = item.data(Qt.UserRole)
            if entry:
                self.entry_selected.emit(entry)
    
    def get_selected(self) -> Optional[ColorEntry]:
        item = self.currentItem()
        return item.data(Qt.UserRole) if item else None
    
    def set_entries_and_select_first(self, entries: List[ColorEntry]):
        self.set_entries(entries)
        if self.count() > 0:
            self._emit_selection(self.item(0))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.parser = ColorEntryParser()
        self.entries: List[ColorEntry] = []
        self.current_file: Optional[str] = None
        
        self._setup_ui()
        self._apply_theme()
        self._setup_menu()
    
    def _setup_ui(self):
        self.setWindowTitle(APP_NAME)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        
        splitter = QSplitter(Qt.Horizontal)
        
        self.navigator = EntryNavigator()
        self.navigator.entry_selected.connect(self._on_entry_selected)
        self.navigator.setMinimumWidth(250)
        self.navigator.setMaximumWidth(400)
        splitter.addWidget(self.navigator)
        
        self.inspector = InspectorPanel(self)
        self.inspector.setMinimumWidth(500)
        splitter.addWidget(self.inspector)
        
        splitter.setSizes([300, 600])
        layout.addWidget(splitter)
        
        toolbar = QToolBar()
        toolbar.setStyleSheet("QToolBar { background: #151515; padding: 5px; }")
        self.addToolBar(toolbar)
        
        buttons = [("📂 Open", self._open_file), ("💾 Save", self._save_file), ("➕ Add", self._add_entry)]
        for icon, func in buttons:
            btn = QPushButton(icon)
            btn.setStyleSheet("QPushButton { background: #1E3A8A; color: white; padding: 8px 12px; }")
            btn.clicked.connect(func)
            toolbar.addWidget(btn)
        
        tip = QLabel("💡 Only edit Base Paint Color! Colors auto-calculate.")
        tip.setStyleSheet("color: #F59E0B; background: #1A1A1A; padding: 5px 10px;")
        toolbar.addWidget(tip)
        
        self.statusBar().showMessage("Ready - Load a file to begin")
        self.statusBar().setStyleSheet("QStatusBar { background: #151515; color: #00FF90; }")
    
    def _apply_theme(self):
        palette = self.palette()
        palette.setColor(QPalette.Window, QColor("#000000"))
        palette.setColor(QPalette.WindowText, QColor("#00FF90"))
        palette.setColor(QPalette.Base, QColor("#151515"))
        palette.setColor(QPalette.Text, QColor("#00FF90"))
        palette.setColor(QPalette.Button, QColor("#1E3A8A"))
        palette.setColor(QPalette.ButtonText, QColor("#00FF90"))
        palette.setColor(QPalette.Highlight, QColor("#6A0DAD"))
        QApplication.setPalette(palette)
        font = QFont("Monospace", DEFAULT_FONT_SIZE)
        QApplication.setFont(font)
    
    def _setup_menu(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")
        for name, shortcut, func in [("New", "Ctrl+N", self._new_file), ("Open...", "Ctrl+O", self._open_file), ("Save", "Ctrl+S", self._save_file), ("Exit", "Ctrl+Q", self.close)]:
            action = QAction(name, self)
            action.setShortcut(shortcut)
            action.triggered.connect(func)
            file_menu.addAction(action)
        
        help_menu = menubar.addMenu("Help")
        about_act = QAction("About", self)
        about_act.triggered.connect(self._show_about)
        help_menu.addAction(about_act)
    
    def _new_file(self):
        self.entries = []
        self.navigator.set_entries([])
        self.current_file = None
        self.statusBar().showMessage("New file created")
    
    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Color Pool", "", "XTBL (*.xtbl);;All Files (*)")
        if path:
            self._load_file(path)
    
    def _load_file(self, filepath: str):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                text = f.read()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Cannot open file: {e}")
            return
        
        self.current_file = filepath
        self.entries = self.parser.parse(text)
        
        # Report any parser warnings/errors
        if self.parser.warnings:
            for w in self.parser.warnings[:3]:  # Show first 3 warnings
                print(w)
        if self.parser.errors:
            QMessageBox.warning(self, "Parse Warnings", f"Loaded {len(self.entries)} entries with some warnings:\n" + "\n".join(self.parser.errors[:5]))
        
        for entry in self.entries:
            ColorCalculator.recalculate_entry(entry)
        self.navigator.set_entries_and_select_first(self.entries)
        self.statusBar().showMessage(f"Loaded: {filepath} ({len(self.entries)} entries)")
    
    def _save_file(self) -> bool:
        if not self.current_file:
            return self._save_file_as()
        
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        backup_path = Path(self.current_file).with_name(f"{Path(self.current_file).stem}_{timestamp}.bak")
        try:
            shutil.copy2(self.current_file, backup_path)
        except Exception:
            pass
        
        content = self.parser.serialize(self.entries)
        try:
            with open(self.current_file, 'w', encoding='utf-8') as f:
                f.write(content)
            self.statusBar().showMessage(f"Saved: {self.current_file}")
            return True
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Save failed: {e}")
            return False
    
    def _save_file_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(self, "Save As", "", "XTBL (*.xtbl);;;All Files (*)")
        if path:
            self.current_file = path
            return self._save_file()
        return False
    
    def _on_entry_selected(self, entry: ColorEntry):
        self.inspector.set_entry(entry, callback=self._on_inspector_change)
        self.statusBar().showMessage(f"Selected: {entry.name} - Click any box to preview")
    
    def _on_inspector_change(self):
        self.statusBar().showMessage("Entry updated")
    
    def _add_entry(self):
        name, ok = QInputDialog.getText(self, "Add Color", "Color name:")
        if ok and name:
            entry = ColorEntry(name=name, paint_type=DEFAULT_PAINT_TYPE,
                shader_variables=[
                    ShaderVariable(name="Base_Paint_Color", var_type="vector", vector_value=VectorValue(128, 128, 128), is_calculated=False),
                    ShaderVariable(name="Specular_Color", var_type="vector", vector_value=VectorValue(180, 180, 180), is_calculated=True),
                    ShaderVariable(name="Specular_Alpha", var_type="float", float_value=0.55, is_calculated=True),
                    ShaderVariable(name="Specular_Power", var_type="float", float_value=40.0, is_calculated=True),
                    ShaderVariable(name="Fresnel_Falloff_Brightness_Amount", var_type="float", float_value=1.0, is_calculated=True),
                    ShaderVariable(name="Fresnel_Falloff_Contrast_Amount", var_type="float", float_value=1.5, is_calculated=True),
                    ShaderVariable(name="Fresnel_Color", var_type="vector", vector_value=VectorValue(90, 90, 90), is_calculated=True),
                    ShaderVariable(name="Reflection_Map_Opacity", var_type="float", float_value=0.5, is_calculated=True),
                    ShaderVariable(name="Reflection_Falloff_Contrast_Amount", var_type="float", float_value=0.6, is_calculated=True),
                    ShaderVariable(name="Reflection_Falloff_Brightness_Amount", var_type="float", float_value=0.3, is_calculated=True),
                ])
            self.entries.append(entry)
            self.navigator.set_entries(self.entries)
            self.statusBar().showMessage(f"Added: {name}")
    
    def _show_about(self):
        QMessageBox.about(self, "About", f"<b>{APP_NAME}</b><br>Version {VERSION}<br><br><u>Fixed Features:</u><br>- Correct XML parsing/serialization<br>- Color preview shows RGB colors for vector fields<br>- Type indicators show 🎨 Color vs 🔢 Number<br>- Duplicate tag detection<br><br><u>Workflow:</u><br>1. Select or create a color entry<br>2. <b>Edit ONLY Base Paint Color</b><br>3. All other values auto-calculate from base<br>4. Click any field to see its preview<br>5. Manual edits are protected from recalculation")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    window = MainWindow()
    window.resize(1000, 700)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

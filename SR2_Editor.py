#!/usr/bin/env python3
"""
Saints Row 2 Mesh Viewer - Fixed Version 3.0
Fixes:
  1. Index buffer return tuple unpacking
  2. None-check before len() on indices
  3. Real-time has_real_indices tracking in renderer
  4. Improved index validation (80% rule)
  5. View mode combo box wiring
  6. Better header pointer scanning
  7. Structured diagnostics passing
  8. LEGACY OPengl - NO VAOs (fixed for Linux)
  9. Downgraded to OpenGL 2.1 compatibility
"""

import sys
import math
import struct
import ctypes
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

import numpy as np

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPalette, QSurfaceFormat, QAction
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QGroupBox, QHBoxLayout,
    QLabel, QMainWindow, QMessageBox, QPushButton,
    QSizePolicy, QSplitter, QTextEdit, QVBoxLayout,
    QWidget, QMenuBar, QMenu,
)

# ============================================================================
# OPENGL IMPORT WITH FALLBACK
# ============================================================================

OPENGL_AVAILABLE = False
try:
    from OpenGL.GL import *
    from OpenGL.GLU import *
    OPENGL_AVAILABLE = True
except ImportError as e:
    print(f"[Critical] PyOpenGL not available: {e}")
    OPENGL_AVAILABLE = False

# ============================================================================
# CONFIGURATION
# ============================================================================

BACKGROUND = "#050505"
GREEN = "#00FF90"
PURPLE = "#6A0DAD"
BLUE = "#2563EB"
YELLOW = "#FFAA00"

# CRITICAL FIX: Increased header skip size
MIN_HEADER_SIZE = 0x200  # 512 bytes minimum before vertex data

# Index validation threshold
INDEX_VALID_RATIO_THRESHOLD = 0.80

# Known probe offsets from document analysis (used as quick checks before full scan)
KNOWN_PROBE_OFFSETS = [0x200, 0x210, 0x220, 0x8000, 0xC000]

# ============================================================================
# ENHANCED BINARY PARSER WITH DIAGNOSTICS
# ============================================================================

class BinaryReader:
    def __init__(self, filename):
        self.path = Path(filename)
        self.data = self.path.read_bytes()

        self.vertices = None
        self.indices = None
        self.vertex_count = 0
        self.index_count = 0
        self.vertex_offset = None
        self.vertex_stride = None
        self.index_offset = None
        self.index_stride = None
        self.diagnostics = []
        self.bounding_box = {}

        print(f"[File] {self.path}")
        print(f"[File] Size: {len(self.data):,} bytes")
        print(f"[File] Header: {self.data[:64].hex(' ')}")
        
        self.signature = self.data[:16]

    def detect_file_type(self):
        """Try to identify the specific SR2 mesh format."""
        sig = self.signature.upper()
        
        types = {
            b'.G_SMESH_PC': 'geometry_smesh',
            b'.G_CMESH_PC': 'geometry_cmesh',
            b'.G_CAR_PC': 'vehicle_mesh',
        }
        
        for magic, typename in types.items():
            if magic in self.data[:32]:
                self.diagnostics.append(f"Detected format: {typename}")
                return typename
        
        self.diagnostics.append("Unknown format - using heuristic detection")
        return "unknown"

    def find_section_pointers(self, max_section=0x20000):
        """Scan first 0x100 bytes for 4-byte LE pointers to plausible data sections."""
        pointers = []
        for i in range(0, min(0x100, len(self.data)-4), 4):
            try:
                ptr = struct.unpack('<I', self.data[i:i+4])[0]
                if 0x200 < ptr < max_section:
                    pointers.append((ptr, i))
            except:
                continue
        return pointers

    def find_strings(self, minimum_length=3):
        strings = []
        current = bytearray()

        for byte in self.data:
            if 0x20 <= byte <= 0x7E:
                current.append(byte)
            else:
                if len(current) >= minimum_length:
                    strings.append(
                        current.decode("ascii", errors="ignore")
                    )
                current.clear()

        if len(current) >= minimum_length:
            strings.append(
                current.decode("ascii", errors="ignore")
            )

        return strings

    def get_hex_preview(self, offset=0, length=512):
        chunk = self.data[offset:offset + length]
        lines = []

        for row in range(0, len(chunk), 16):
            data = chunk[row:row + 16]

            address = f"{offset + row:08X}"
            hex_part = " ".join(f"{b:02X}" for b in data)
            ascii_part = "".join(
                chr(b) if 32 <= b <= 126 else "."
                for b in data
            )

            lines.append(
                f"{address}  {hex_part:<48}  {ascii_part}"
            )

        return "\n".join(lines)

    @staticmethod
    def _is_valid_position(x, y, z):
        values = (x, y, z)
        if not all(math.isfinite(v) for v in values):
            return False
        if any(abs(v) > 10000.0 for v in values):
            return False
        return True

    def _validate_vertex_set(self, positions):
        """Check if extracted positions look like valid mesh geometry."""
        if len(positions) < 20:
            return False, "Too few vertices (need at least 20)"
        
        values = np.asarray(positions, dtype=np.float32)
        minimum = values.min(axis=0)
        maximum = values.max(axis=0)
        extent = maximum - minimum
        
        if np.max(np.abs(minimum)) > 10000 or np.max(np.abs(maximum)) > 10000:
            return False, "Coordinates too large"
        
        if float(np.max(extent)) < 0.001:
            return False, "No spatial variation"
        
        degenerate_axes = np.count_nonzero(extent > 0.01)
        if degenerate_axes < 2:
            return False, "Geometry too flat/degenerate"
        
        if not np.all(np.isfinite(values)):
            return False, "Contains NaN/Infinity"
        
        return True, "Valid"

    def _scan_for_stride_with_pos_offset(self, offset, stride, pos_in_vertex=0, max_samples=256):
        """Test a specific offset/stride combination with position offset within vertex."""
        available = (len(self.data) - offset) // stride
        if available < max_samples:
            return None
        
        positions = []
        valid_count = 0
        
        for i in range(min(available, max_samples)):
            pos_offset = offset + i * stride + pos_in_vertex
            try:
                x, y, z = struct.unpack_from("<3f", self.data, pos_offset)
                if self._is_valid_position(x, y, z):
                    positions.append((x, y, z))
                    valid_count += 1
                else:
                    break
            except struct.error:
                break
        
        if len(positions) < 20:
            return None
        
        values = np.asarray(positions, dtype=np.float32)
        variance = np.var(values, axis=0)
        
        return {
            "offset": offset,
            "stride": stride,
            "pos_in_vertex": pos_in_vertex,
            "valid_count": valid_count,
            "mean_variance": np.mean(variance),
            "position_range": np.ptp(values, axis=0),
        }

    def find_vertex_buffer_fixed(self):
        """
        Fixed vertex buffer detection:
        1. Scan known probe offsets first
        2. Skip header properly (MIN_HEADER_SIZE)
        3. Test multiple position offsets within vertex
        4. Require minimum 20 vertices to reject noise
        5. Prefer contiguous regions
        """
        common_strides = [12, 16, 20, 24, 28, 32, 36, 40]
        pos_offsets = [0, 4, 8, 12]  # Test position at different offsets
        
        best_candidate = None
        best_score = 0
        
        # First check known probe offsets
        for probe_offset in KNOWN_PROBE_OFFSETS:
            if probe_offset >= len(self.data) - 100:
                continue
            
            for stride in common_strides:
                for pos_in_vertex in pos_offsets:
                    result = self._scan_for_stride_with_pos_offset(
                        probe_offset, stride, pos_in_vertex
                    )
                    
                    if result is None:
                        continue
                    
                    score = result["valid_count"] * 100
                    score += min(result["mean_variance"] * 100, 1000)
                    
                    if score > best_score:
                        best_score = score
                        best_candidate = result
        
        # If nothing found in probes, do full scan
        if best_candidate is None:
            self.diagnostics.append("[Scan] Known probes failed, doing full scan")
            
            for base_offset in range(MIN_HEADER_SIZE, len(self.data) - 100, 16):
                for stride in common_strides:
                    for pos_in_vertex in pos_offsets:
                        result = self._scan_for_stride_with_pos_offset(
                            base_offset, stride, pos_in_vertex
                        )
                        
                        if result is None:
                            continue
                        
                        score = result["valid_count"] * 100
                        score += min(result["mean_variance"] * 100, 1000)
                        
                        if score > best_score:
                            best_score = score
                            best_candidate = result
        
        return best_candidate

    def find_index_buffer(self, vertex_count, vertex_offset, vertex_stride):
        """
        Fixed index buffer detection with proper validation.
        Returns (indices_array, stride) or None.
        """
        if vertex_offset is None or vertex_stride is None:
            return None
        
        # End of vertex data
        vertex_end = vertex_offset + (vertex_count * vertex_stride)
        
        # Search area after vertex buffer (typically within 4KB)
        search_start = vertex_end
        search_end = min(vertex_end + 0x2000, len(self.data))
        
        if search_end <= search_start:
            return None
        
        self.diagnostics.append(f"[Index Search] Scanning {search_end - search_start} bytes after vertex buffer")
        
        # Try 16-bit indices first (more common in games)
        for stride in [2, 4]:
            # Test multiple alignments
            for align in range(0, 64, stride):
                offset = search_start + align
                if offset >= search_end:
                    continue
                
                available_bytes = search_end - offset
                expected_count = vertex_count * 2  # Typical: 2x vertices in indices
                
                if available_bytes < (expected_count * stride):
                    continue
                
                try:
                    if stride == 2:
                        indices = np.frombuffer(
                            self.data[offset:], 
                            dtype=np.uint16,
                            count=available_bytes // 2
                        ).copy()
                    else:
                        indices = np.frombuffer(
                            self.data[offset:], 
                            dtype=np.uint32,
                            count=available_bytes // 4
                        ).copy()
                    
                    if len(indices) < expected_count // 2:
                        continue
                    
                    # CRITICAL VALIDATION: Index values must be within valid range
                    max_idx = int(indices.max())
                    min_idx = int(indices.min())
                    
                    self.diagnostics.append(f"[Index] Testing: count={len(indices)}, min={min_idx}, max={max_idx}")
                    
                    # NEW: Check 80% validity rule from document
                    valid_count = np.sum((indices >= 0) & (indices < vertex_count))
                    valid_ratio = valid_count / len(indices) if len(indices) > 0 else 0
                    
                    self.diagnostics.append(f"[Index] Valid ratio: {valid_ratio:.1%}")
                    
                    # Valid indices must reference actual vertices
                    if max_idx >= vertex_count and max_idx < vertex_count * 3:
                        # Relaxed acceptance with warning
                        if valid_ratio >= INDEX_VALID_RATIO_THRESHOLD and max_idx > 0:
                            self.diagnostics.append(f"[Index] ACCEPTED with {valid_ratio:.1%} validity")
                            self.index_offset = offset
                            self.index_stride = stride
                            
                            num_triangles = len(indices) // 3
                            return indices[:num_triangles * 3], stride
                    
                    elif max_idx < vertex_count and max_idx > 0:
                        # Also valid if within vertex range
                        self.diagnostics.append(f"[Index] VALID INDEX BUFFER FOUND at 0x{offset:X}")
                        self.index_offset = offset
                        self.index_stride = stride
                        
                        num_triangles = len(indices) // 3
                        return indices[:num_triangles * 3], stride
                        
                except Exception as e:
                    self.diagnostics.append(f"[Index] Failed at offset 0x{offset:X}: {e}")
                    continue
        
        self.diagnostics.append("[Index] No valid index buffer found, will use fallback")
        return None

    def extract_gpu_geometry(self, max_vertices=50000, debug=False):
        """
        Fixed geometry extraction with real index buffer detection.
        """
        self.detect_file_type()
        
        # Log section pointers if any found
        pointers = self.find_section_pointers()
        if pointers:
            self.diagnostics.append(f"[Pointers] Found {len(pointers)} section pointers: {[hex(p[0]) for p in pointers]}")
        
        candidate = self.find_vertex_buffer_fixed()

        if candidate is None:
            print("[Parser] No plausible vertex buffer found")
            self.diagnostics.append("FAILED: No vertex buffer detected")
            return None, None, None

        offset = candidate["offset"]
        stride = candidate["stride"]
        pos_in_vertex = candidate.get("pos_in_vertex", 0)

        self.vertex_offset = offset
        self.vertex_stride = stride

        self.diagnostics.append(f"Best match: offset 0x{offset:X}, stride {stride}, pos_offset={pos_in_vertex}")
        print(f"[Parser] Vertex buffer: 0x{offset:X}, stride {stride}, "
              f"valid={candidate['valid_count']}, variance={candidate['mean_variance']:.4f}")

        available = (len(self.data) - offset) // stride
        count = min(available, max_vertices)

        # Extract full vertex data with position offset
        vertices = []
        valid_count = 0

        for i in range(count):
            position_offset = offset + i * stride + pos_in_vertex

            try:
                x, y, z = struct.unpack_from("<3f", self.data, position_offset)
            except struct.error:
                print(f"[Parser] Struct error at vertex {i}")
                break

            if not self._is_valid_position(x, y, z):
                print(f"[Parser] Invalid position at vertex {i}: ({x:.2f}, {y:.2f}, {z:.2f})")
                break

            vertices.append((x, y, z))
            valid_count += 1

        if len(vertices) < 20:
            print("[Parser] Candidate did not contain enough vertices")
            self.diagnostics.append(f"FAILED: Only {len(vertices)} vertices found (need 20+)")
            return None, None, None

        self.vertices = np.asarray(vertices, dtype=np.float32)
        self.vertex_count = len(self.vertices)

        # Validate extracted geometry
        valid, msg = self._validate_vertex_set(self.vertices)
        if not valid:
            print(f"[Parser] Validation failed: {msg}")
            self.diagnostics.append(f"FAILED: {msg}")
            
            bbox_min = self.vertices.min(axis=0)
            bbox_max = self.vertices.max(axis=0)
            self.diagnostics.append(f"Bounding box: min={bbox_min}, max={bbox_max}")
            
            if debug:
                print("[Parser] Returning invalid geometry for debug inspection")
            else:
                return None, None, None

        # Find real index buffer
        index_result = self.find_index_buffer(
            self.vertex_count, 
            self.vertex_offset, 
            self.vertex_stride
        )
        
        real_indices_used = False
        if index_result is not None:
            self.indices, self.index_stride = index_result
            self.index_count = len(self.indices)
            self.diagnostics.append(f"SUCCESS: {self.vertex_count} vertices, {self.index_count} real indices")
            real_indices_used = True
        else:
            # Fallback to sequential indices (will likely be wrong)
            triangle_count = self.vertex_count // 3
            self.indices = np.arange(triangle_count * 3, dtype=np.uint32)
            self.index_count = len(self.indices)
            self.diagnostics.append(f"WARNING: Using {self.index_count} fallback sequential indices")
        
        # Compute bounding box info
        bbox_min = self.vertices.min(axis=0)
        bbox_max = self.vertices.max(axis=0)
        bbox_center = (bbox_min + bbox_max) * 0.5
        bbox_extent = bbox_max - bbox_min
        
        self.bounding_box = {
            "min": bbox_min.tolist(),
            "max": bbox_max.tolist(),
            "center": bbox_center.tolist(),
            "extent": bbox_extent.tolist(),
            "vertex_offset": offset,
            "vertex_stride": stride
        }
        
        print(f"[Parser] Bounding box:")
        print(f"  Min: {bbox_min}")
        print(f"  Max: {bbox_max}")
        print(f"  Center: {bbox_center}")
        print(f"  Extent: {bbox_extent}")
        
        self.diagnostics.extend([
            f"BBox min: {bbox_min}",
            f"BBox max: {bbox_max}",
            f"BBox center: {bbox_center}",
            f"BBox extent: {bbox_extent}",
        ])

        print(
            f"[Parser] Extracted {self.vertex_count} vertices "
            f"and {self.index_count} {'real' if real_indices_used else 'fallback'} indices"
        )

        return self.vertices, self.indices, real_indices_used

# ============================================================================
# OPENGL VIEWER WITH DIAGNOSTIC OVERLAY (LEGACY - NO VAOs)
# ============================================================================

class MeshRenderer(QOpenGLWidget):
    def __init__(self):
        super().__init__()

        self.setMinimumSize(600, 400)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # VAO removed - using legacy OpenGL client states
        self.vbo = None
        self.ebo = None

        self.index_count = 0
        self.vertex_count = 0
        self.has_real_mesh = False
        self.has_real_indices = False

        self.camera_distance = 3.0
        self.camera_pitch = 20.0
        self.camera_yaw = 35.0

        self.mouse_down = False
        self.last_mouse = None
        
        self.show_bounding_box = False
        self.debug_text = []
        self.render_mode = GL_FILL
        self.current_render_mode = "solid"

    def initializeGL(self):
        glClearColor(0.02, 0.02, 0.02, 1.0)
        glEnable(GL_DEPTH_TEST)
        glDisable(GL_CULL_FACE)

        if OPENGL_AVAILABLE:
            version = glGetString(GL_VERSION)
            vendor = glGetString(GL_VENDOR)
            renderer = glGetString(GL_RENDERER)
            if version:
                print(f"[OpenGL] {version.decode(errors='replace')}")
            if vendor:
                print(f"[OpenGL] Vendor: {vendor.decode(errors='replace')}")
            if renderer:
                print(f"[OpenGL] Renderer: {renderer.decode(errors='replace')}")
        else:
            print("[OpenGL] Backend unavailable")

        self.create_placeholder()

    def delete_buffers(self):
        if self.vbo is not None:
            try:
                glDeleteBuffers(1, [self.vbo])
            except:
                pass
            self.vbo = None
        if self.ebo is not None:
            try:
                glDeleteBuffers(1, [self.ebo])
            except:
                pass
            self.ebo = None

    def upload_buffers(self, positions, indices, real_indices=True):
        if not OPENGL_AVAILABLE:
            return
            
        positions = np.ascontiguousarray(positions, dtype=np.float32)
        indices = np.ascontiguousarray(indices, dtype=np.uint32)

        self.delete_buffers()

        self.makeCurrent()

        try:
            # CREATE VBOs WITHOUT VAOs (legacy OpenGL)
            self.vbo = glGenBuffers(1)
            self.ebo = glGenBuffers(1)

            glBindBuffer(GL_ARRAY_BUFFER, self.vbo)
            glBufferData(GL_ARRAY_BUFFER, positions.nbytes, positions, GL_STATIC_DRAW)

            glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, self.ebo)
            glBufferData(GL_ELEMENT_ARRAY_BUFFER, indices.nbytes, indices, GL_STATIC_DRAW)

            # LEGACY CLIENT STATE SETUP (replaces VAO)
            glEnableClientState(GL_VERTEX_ARRAY)
            glVertexPointer(3, GL_FLOAT, 0, None)

            self.index_count = len(indices)
            self.vertex_count = len(positions)
            self.has_real_indices = real_indices
        finally:
            self.doneCurrent()

    def create_placeholder(self):
        positions = np.array([
            -1.0, -1.0, 0.0,
             1.0, -1.0, 0.0,
             1.0,  1.0, 0.0,
            -1.0,  1.0, 0.0,
        ], dtype=np.float32)

        indices = np.array([0, 1, 2, 2, 3, 0], dtype=np.uint32)
        self.upload_buffers(positions, indices, real_indices=False)
        self.has_real_mesh = False
        self.debug_text = ["No mesh loaded"]

    def load_mesh_data(self, vertices, indices, diagnostics=None, real_indices_used=False):
        """FIXED: Accept real_indices_used directly instead of reading stale flag."""
        if vertices is None or len(vertices) < 20:
            print("[Renderer] No usable vertices")
            return False

        positions = np.asarray(vertices[:, :3], dtype=np.float32)
        valid_mask = np.all(np.isfinite(positions), axis=1)
        positions = positions[valid_mask]

        if len(positions) < 20:
            print("[Renderer] All vertices invalid (NaN/Inf)")
            return False

        # Center mesh at origin
        minimum = positions.min(axis=0)
        maximum = positions.max(axis=0)
        center = (minimum + maximum) * 0.5
        positions -= center

        extent = float(np.max(np.abs(positions)))
        
        # Less aggressive normalization
        if extent > 0.000001:
            positions /= extent
            if diagnostics is not None:
                diagnostics.append(f"Normalized to extent: {extent:.4f}")

        if indices is None:
            indices = np.arange(len(positions), dtype=np.uint32)

        indices = np.asarray(indices, dtype=np.uint32)
        indices = indices[indices < len(positions)]
        indices = indices[:len(indices) - len(indices) % 3]

        if len(indices) < 3:
            print("[Renderer] Insufficient indices")
            return False

        print(f"[Renderer] Uploading {len(positions)} vertices and {len(indices)} indices")
        
        # FIXED: Use passed real_indices_used parameter, not stale self.has_real_indices
        self.upload_buffers(positions, indices, real_indices=real_indices_used)
        self.has_real_mesh = True
        
        self.debug_text = [
            f"Vertices: {len(positions):,}",
            f"Triangles: {len(indices)//3:,}",
            f"Real indices: {'Yes' if real_indices_used else 'No (fallback)'}",
            f"Camera: dist={self.camera_distance:.2f}",
            f"Mode: {self.current_render_mode.upper()}"
        ]
        
        if diagnostics and isinstance(diagnostics, dict):
            # If diagnostics is structured, include key info
            if "vertex_offset" in diagnostics:
                self.debug_text.append(f"Vtx offset: 0x{diagnostics['vertex_offset']:X}")
                self.debug_text.append(f"Vtx stride: {diagnostics['vertex_stride']}")

        self.camera_distance = 3.0
        self.camera_pitch = 20.0
        self.camera_yaw = 35.0
        self.update()

        return True

    def set_render_mode(self, mode):
        """Wire up render mode changes from combo box."""
        self.current_render_mode = mode.lower()
        
        if mode.lower() == "wireframe":
            self.render_mode = GL_LINE
        elif mode.lower() == "points":
            self.render_mode = GL_POINT
        else:  # solid
            self.render_mode = GL_FILL
        
        if self.has_real_mesh:
            self.update()

    def paintGL(self):
        if not OPENGL_AVAILABLE:
            return
            
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

        if self.vbo is None or self.index_count < 3:
            return

        aspect = self.width() / max(self.height(), 1)

        # PROJECTION MATRIX (legacy fixed-function)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        gluPerspective(45.0, aspect, 0.01, 100.0)

        # MODELVIEW MATRIX (legacy fixed-function)
        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()

        glTranslatef(0.0, 0.0, -self.camera_distance)
        glRotatef(self.camera_pitch, 1.0, 0.0, 0.0)
        glRotatef(self.camera_yaw, 0.0, 1.0, 0.0)

        # Color based on mesh validity
        if self.has_real_mesh:
            if self.has_real_indices:
                glColor3f(0.0, 1.0, 0.5)  # Green = real indices
            else:
                glColor3f(1.0, 0.8, 0.0)  # Yellow = fallback indices
        else:
            glColor3f(0.1, 0.8, 1.0)  # Blue = placeholder

        # Set polygon mode
        glPolygonMode(GL_FRONT_AND_BACK, self.render_mode)

        # Bind EBO and draw
        glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, self.ebo)

        glDrawElements(
            GL_TRIANGLES,
            self.index_count,
            GL_UNSIGNED_INT,
            ctypes.c_void_p(0)
        )

        glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, 0)

        error = glGetError()
        if error != GL_NO_ERROR:
            print(f"[OpenGL] Draw error: 0x{error:04X}")

    def resizeGL(self, width, height):
        glViewport(0, 0, width, max(height, 1))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.mouse_down = True
            self.last_mouse = event.position()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.mouse_down = False

    def mouseMoveEvent(self, event):
        if not self.mouse_down or self.last_mouse is None:
            return

        current = event.position()
        dx = current.x() - self.last_mouse.x()
        dy = current.y() - self.last_mouse.y()

        self.camera_yaw += dx * 0.5
        self.camera_pitch += dy * 0.5
        self.camera_pitch = max(-89.0, min(89.0, self.camera_pitch))

        self.last_mouse = current
        self.update()

    def wheelEvent(self, event):
        if event.angleDelta().y() > 0:
            self.camera_distance *= 0.9
        else:
            self.camera_distance *= 1.1

        self.camera_distance = max(0.5, min(30.0, self.camera_distance))
        self.update()

# ============================================================================
# HEX VIEW
# ============================================================================

class HexView(QWidget):
    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        label = QLabel("Hex Dump")
        label.setStyleSheet(f"color: {GREEN};")
        layout.addWidget(label)

        self.editor = QTextEdit()
        self.editor.setReadOnly(True)
        self.editor.setFont(QFont("Monospace", 10))
        self.editor.setStyleSheet(
            f"""
            QTextEdit {{
                background-color: {BACKGROUND};
                color: {GREEN};
            }}
            """
        )

        layout.addWidget(self.editor)

    def set_text(self, text):
        self.editor.setPlainText(text)

# ============================================================================
# MAIN WINDOW WITH DIAGNOSTICS PANEL
# ============================================================================

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Saints Row 2 Mesh Viewer - Fixed Version 3.0")
        self.resize(1400, 900)

        self.reader = None
        self.view_combo = None  # Reference to view mode combo
        self.create_ui()
        self.apply_theme()

    def create_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)

        # Menu bar
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")
        
        open_action = QAction("Open...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.load_file)
        file_menu.addAction(open_action)
        
        exit_action = QAction("Exit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        help_menu = menubar.addMenu("Help")
        about_action = QAction("About", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

        # Toolbar
        toolbar = QWidget()
        toolbar.setFixedHeight(52)
        toolbar.setStyleSheet(f"background-color: {PURPLE};")

        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(10, 5, 10, 5)

        button = QPushButton("Load GPU Mesh")
        button.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {BLUE};
                color: white;
                padding: 8px 16px;
                border: none;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background-color: #3B82F6;
            }}
            """
        )
        button.clicked.connect(self.load_file)
        toolbar_layout.addWidget(button)

        # View controls - WIRED UP
        toolbar_layout.addWidget(QLabel("View:"))
        self.view_combo = QComboBox()
        self.view_combo.addItems(["Solid", "Wireframe", "Points"])
        self.view_combo.setCurrentText("Solid")
        # FIXED: Connect combo box to renderer
        self.view_combo.currentTextChanged.connect(self.on_view_mode_changed)
        toolbar_layout.addWidget(self.view_combo)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar_layout.addWidget(spacer)

        root.addWidget(toolbar)

        # Main splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left panel
        left = QWidget()
        left.setMaximumWidth(400)

        left_layout = QVBoxLayout(left)

        file_group = QGroupBox("File")
        file_layout = QVBoxLayout(file_group)

        self.file_label = QLabel("No file loaded")
        self.file_label.setWordWrap(True)
        self.file_label.setStyleSheet(f"color: {GREEN};")
        file_layout.addWidget(self.file_label)

        left_layout.addWidget(file_group)

        stats_group = QGroupBox("Statistics")
        stats_layout = QVBoxLayout(stats_group)

        self.stats_label = QLabel("Load a file")
        self.stats_label.setWordWrap(True)
        self.stats_label.setStyleSheet(f"color: {GREEN};")
        stats_layout.addWidget(self.stats_label)

        left_layout.addWidget(stats_group)

        # Diagnostics panel
        diag_group = QGroupBox("Parsing Diagnostics")
        diag_layout = QVBoxLayout(diag_group)

        self.diag_text = QTextEdit()
        self.diag_text.setReadOnly(True)
        self.diag_text.setMaximumHeight(150)
        self.diag_text.setFont(QFont("Monospace", 9))
        self.diag_text.setStyleSheet(
            f"""
            QTextEdit {{
                background-color: {BACKGROUND};
                color: {GREEN};
            }}
            """
        )
        diag_layout.addWidget(self.diag_text)

        left_layout.addWidget(diag_group)

        texture_group = QGroupBox("Textures")
        texture_layout = QVBoxLayout(texture_group)

        self.texture_text = QTextEdit()
        self.texture_text.setReadOnly(True)
        self.texture_text.setMaximumHeight(120)
        self.texture_text.setStyleSheet(
            f"""
            QTextEdit {{
                background-color: {PURPLE};
                color: {GREEN};
            }}
            """
        )
        texture_layout.addWidget(self.texture_text)

        left_layout.addWidget(texture_group)

        self.hex_view = HexView()
        left_layout.addWidget(self.hex_view)

        splitter.addWidget(left)

        self.renderer = MeshRenderer()
        splitter.addWidget(self.renderer)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        root.addWidget(splitter)

        self.statusBar().showMessage("Ready")

    def apply_theme(self):
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(BACKGROUND))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(GREEN))
        self.setPalette(palette)

    def on_view_mode_changed(self, mode):
        """Handle view mode selection from combo box."""
        if self.renderer:
            self.renderer.set_render_mode(mode)

    def show_about(self):
        QMessageBox.about(
            self,
            "About Saints Row 2 Mesh Viewer",
            "Saints Row 2 Mesh Viewer (Fixed Version 3.0)\n\n"
            "Loads .g_smesh_pc, .g_cmesh_pc, .g_car_pc files.\n\n"
            "FIXES IN THIS VERSION:\n"
            "• Index buffer tuple unpacking\n"
            "• None-check before len() on indices\n"
            "• Real-time has_real_indices tracking\n"
            "• Index validation (80% rule)\n"
            "• View mode combo box wired\n"
            "• Section pointer scanning\n"
            "• Better diagnostic structure\n"
            "• LEGACY OpenGL (NO VAOs) - Linux compatible\n\n"
            "Controls: Left-click+drag to rotate, scroll to zoom.\n\n"
            "Color guide:\n"
            "• Green = Real index buffer\n"
            "• Yellow = Fallback sequential indices\n"
            "• Blue = Placeholder (no mesh)"
        )

    def load_file(self):
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Load GPU Mesh",
            "",
            "Saints Row Files (*.g_smesh_pc *.g_cmesh_pc *.g_car_pc);;All Files (*)"
        )

        if filename:
            self.parse_file(filename)

    def parse_file(self, filename):
        try:
            path = Path(filename)
            reader = BinaryReader(filename)

            # FIXED: Get three return values
            vertices, indices, real_indices_used = reader.extract_gpu_geometry(debug=True)

            self.reader = reader

            self.file_label.setText(path.name)

            # FIXED: Safe None-check before len()
            if indices is not None and len(indices) > 0:
                real_indices = not np.array_equal(
                    indices, np.arange(len(indices), dtype=np.uint32)
                )
            else:
                real_indices = False

            # Build diagnostics dict to pass to renderer
            bbox_info = reader.bounding_box
            
            self.stats_label.setText(
                f"Size: {path.stat().st_size:,} bytes\n"
                f"Vertices: {reader.vertex_count:,}\n"
                f"Indices: {reader.index_count:,}\n"
                f"Index buffer: {'REAL' if real_indices_used else 'FALLBACK'}\n"
                f"Vertex offset: {'0x%X' % reader.vertex_offset if reader.vertex_offset is not None else 'None'}\n"
                f"Vertex stride: {reader.vertex_stride or 'None'}\n"
                f"Index offset: {'0x%X' % reader.index_offset if reader.index_offset is not None else 'None'}"
            )

            # Display diagnostics
            self.diag_text.setPlainText("\n".join(reader.diagnostics))

            strings = reader.find_strings()
            textures = [
                item for item in strings
                if any(ext in item.lower() for ext in (".tga", ".dds", ".xtb", ".png"))
            ]

            self.texture_text.setPlainText("\n".join(textures[:20]) if textures else "None found")

            # FIXED: Show hex preview at vertex buffer offset if found
            if reader.vertex_offset is not None:
                self.hex_view.set_text(reader.get_hex_preview(reader.vertex_offset, 512))
            else:
                self.hex_view.set_text(reader.get_hex_preview(0, 512))

            if vertices is None:
                QMessageBox.warning(
                    self,
                    "No Geometry Found",
                    "No plausible floating-point vertex buffer was found.\n\n"
                    "Check diagnostics panel for details.\n\n"
                    "Common causes:\n"
                    "- File is corrupted or wrong format\n"
                    "- Mesh has unusual layout\n"
                    "- Need to adjust MIN_HEADER_SIZE"
                )
                return

            # FIXED: Pass real_indices_used to renderer
            if not self.renderer.load_mesh_data(vertices, indices, reader.diagnostics, real_indices_used):
                QMessageBox.warning(
                    self,
                    "Invalid Geometry",
                    "The vertex data did not contain valid triangles."
                )
                return

            self.statusBar().showMessage(
                f"Loaded {reader.vertex_count:,} vertices, "
                f"{reader.index_count:,} indices"
            )

        except Exception as error:
            print("[Error]", repr(error))
            QMessageBox.critical(self, "Error", str(error))

# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def main():
    if OPENGL_AVAILABLE:
        # DOWNGRADED to 2.1 for legacy OpenGL compatibility (Linux fix)
        format_ = QSurfaceFormat()
        format_.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
        format_.setVersion(2, 1)  # Legacy fixed-function pipeline
        format_.setProfile(QSurfaceFormat.OpenGLContextProfile.NoProfile)
        format_.setDepthBufferSize(24)
        format_.setSwapBehavior(QSurfaceFormat.SwapBehavior.DoubleBuffer)
        QSurfaceFormat.setDefaultFormat(format_)

    app = QApplication(sys.argv)
    app.setApplicationName("Saints Row 2 Mesh Viewer")
    app.setOrganizationName("MeshViewer")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()

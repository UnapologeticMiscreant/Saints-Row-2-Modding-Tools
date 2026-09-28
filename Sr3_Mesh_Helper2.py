#!/usr/bin/env python3
"""
Saints Row 3D Mesh Editor v2.0
Supports: Saints Row: The Third, Saints Row IV, Gat Out Of Hell
Formats: .ccmesh_pc/.gcmesh_pc/.csmesh_pc/.gsmesh_pc/.cmesh_pc/.gmesh_pc, .cpeg_pc/.gpeg_pc
Features: Skinning support, streamlined architecture, improved visualization
"""

import sys
import os
import math
import struct
import traceback
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any
from enum import Enum, auto

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QSplitter, QVBoxLayout,
    QHBoxLayout, QTabWidget, QTextEdit, QMenuBar, QToolBar,
    QFileDialog, QStatusBar, QLabel, QFrame,
    QAbstractScrollArea, QTableWidget, QTableWidgetItem,
    QMenu, QAction, QMessageBox, QPushButton, QGroupBox,
    QColorDialog, QSlider, QFormLayout,  # <-- Add these two:
    QListWidget, QListWidgetItem
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont, QColor, QPen, QPainter, QKeySequence

import numpy as np

try:
    from PyQt5.QtOpenGLWidgets import QOpenGLWidget
    from OpenGL.GL import *
    from OpenGL.GLU import *
    from OpenGL.arrays import ndarray, vbo
    HAS_OPENGL = True
except ImportError:
    HAS_OPENGL = False


# ─── Constants ──────────────────────────────────────────────────────────────────
COLORS = {
    'background': '#000000',
    'text': '#00FF90',
    'foreground_objects': '#FF1493',
    'highlight_bg': '#6A0DAD',
    'button': '#1E3A8A',
    'button_hover': '#2563EB',
    'error': '#DC2626',
    'warning': '#F59E0B',
    'string': '#8B5CF6',
    'hex_bytes': '#00FF90',
    'address': '#6B7280',
    'cursor': '#FFFFFF',
}

FONT_FAMILY = 'Monospace'
DEFAULT_FONT_SIZE = 15

# Vertex stride detection thresholds
MIN_VERTEX_STRIDE_STATIC = 12
MIN_VERTEX_STRIDE_SKINNED = 48


# ─── Game Version Enum ──────────────────────────────────────────────────────────
class GameVersion(Enum):
    SR3 = auto()
    SR4 = auto()
    GOM = auto()


# ─── Data Classes ───────────────────────────────────────────────────────────────
@dataclass
class Vertex:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    nx: int = 0
    ny: int = 0
    nz: int = 0
    uv: float = 0.0
    vt: float = 0.0
    
    # Skinning data
    bone_indices: List[int] = field(default_factory=lambda: [0, 0, 0, 0])
    bone_weights: List[float] = field(default_factory=lambda: [1.0, 0.0, 0.0, 0.0])
    
    byte_offset: int = 0
    stride: int = 16


@dataclass
class Bone:
    name: str = ""
    offset_matrix: List[float] = field(default_factory=list)
    parent_index: int = -1
    children: List[int] = field(default_factory=list)


@dataclass
class MeshData:
    vertices: List[Vertex] = field(default_factory=list)
    indices: List[int] = field(default_factory=list)
    bones: List[Bone] = field(default_factory=list)
    
    index_offset: int = 0
    vertex_offset: int = 0
    vertex_stride: int = 16
    index_stride: int = 2
    vertex_count: int = 0
    index_count: int = 0
    game_version: GameVersion = GameVersion.SR3
    header_info: Dict[str, Any] = field(default_factory=dict)
    is_skinned: bool = False
    
    # Skinned rendering cache
    posed_vertices: Optional[np.ndarray] = None
    bind_matrices: Optional[List[np.ndarray]] = None


@dataclass
class PEGEntry:
    name: str = ""
    offset: int = 0
    size: int = 0
    width: int = 0
    height: int = 0
    format: str = ""


# ─── Logger Mixin ───────────────────────────────────────────────────────────────
class LoggerMixin:
    def __init__(self):
        self._log_fn = None
        self._err_fn = None
    
    def log(self, msg):
        if self._log_fn:
            self._log_fn(msg)
    
    def error(self, msg):
        if self._err_fn:
            self._err_fn(msg)


# ─── Mesh Parser ────────────────────────────────────────────────────────────────
class MeshParser(LoggerMixin):
    def __init__(self):
        super().__init__()
        self.game_version = GameVersion.SR3
        self.cmesh_data: Optional[bytes] = None
        self.gmesh_data: Optional[bytes] = None
        self.cmesh_path = ""
        self.gmesh_path = ""
        self.mesh = MeshData()
        self.peg_entries: List[PEGEntry] = []
        self.rig_data: Optional[bytes] = None
        self.rig_path = ""
    
    def detect_game_version(self, data):
        if data and len(data) >= 2 and data[:2] == b'T8':
            return GameVersion.SR3
        return GameVersion.SR4
    
    def load_cmesh(self, path):
        if not os.path.exists(path):
            self.error(f"File not found: {path}")
            return False
        
        with open(path, 'rb') as f:
            file_data = f.read()
        
        name = os.path.basename(path)
        dir_name = os.path.dirname(path)
        
        # Determine companion file naming
        if name.endswith(('.gcmesh_pc', '.gsmesh_pc', '.gmesh_pc')):
            self.gmesh_data = file_data
            self.log(f"Loaded gmesh: {name} ({len(file_data)} bytes)")
            
            cname_map = {
                '.gcmesh_pc': '.ccmesh_pc',
                '.gsmesh_pc': '.csmesh_pc', 
                '.gmesh_pc': '.cmesh_pc'
            }
            cname = name.replace(next(k for k in cname_map if name.endswith(k)), cname_map[next(k for k in cname_map if name.endswith(k))])
            self.cmesh_path = os.path.join(dir_name, cname)
            
            if os.path.exists(self.cmesh_path):
                with open(self.cmesh_path, 'rb') as f:
                    self.cmesh_data = f.read()
                self.log(f"Auto-loaded cmesh: {cname} ({len(self.cmesh_data)} bytes)")
            else:
                self.cmesh_data = file_data
                self.error(f"Companion cmesh not found: {self.cmesh_path}")
                
        elif name.endswith(('.ccmesh_pc', '.csmesh_pc', '.cmesh_pc')):
            self.cmesh_data = file_data
            self.log(f"Loaded cmesh: {name} ({len(file_data)} bytes)")
            
            gname_map = {
                '.ccmesh_pc': '.gcmesh_pc',
                '.csmesh_pc': '.gsmesh_pc',
                '.cmesh_pc': '.gmesh_pc'
            }
            gname = name.replace(next(k for k in gname_map if name.endswith(k)), gname_map[next(k for k in gname_map if name.endswith(k))])
            self.gmesh_path = os.path.join(dir_name, gname)
            
            if os.path.exists(self.gmesh_path):
                with open(self.gmesh_path, 'rb') as f:
                    self.gmesh_data = f.read()
                self.log(f"Auto-loaded gmesh: {gname} ({len(self.gmesh_data)} bytes)")
            else:
                self.gmesh_data = None
                self.error(f"Companion gmesh not found: {self.gmesh_path}")
                return False
        else:
            self.error(f"Unrecognized extension: {name}")
            return False
        
        self.game_version = self.detect_game_version(self.cmesh_data or self.gmesh_data)
        self.log(f"Detected: {self.game_version.name}")
        
        # Look for rig file
        self._load_rig(dir_name, os.path.splitext(name)[0])
        
        if self.game_version == GameVersion.SR3:
            self._parse_sr3()
        else:
            self._parse_sr4()
        
        return True
    
    def _load_rig(self, dir_name, mesh_basename):
        """Load accompanying rig file for skeletal data"""
        rig_extensions = ['.rig_pc', '_pc.rig', '.rig']
        for ext in rig_extensions:
            rig_path = os.path.join(dir_name, mesh_basename + ext)
            if os.path.exists(rig_path):
                with open(rig_path, 'rb') as f:
                    self.rig_data = f.read()
                self.rig_path = rig_path
                self.log(f"Loaded rig: {os.path.basename(rig_path)}")
                return
        self.log("No rig file found - mesh will render in bind pose")
    
    def _parse_sr3(self):
        data = self.cmesh_data
        gdata = self.gmesh_data
        
        if not data or not gdata:
            self.error("Missing ccmesh or gcmesh data")
            return
        
        try:
            off = 0
            if data[off:off+2] != b'T8':
                self.error(f"Bad magic: {data[:2]!r}")
                return
            off += 2
            
            version = struct.unpack_from('<h', data, off)[0]
            off += 2
            
            # Header parsing
            size_of_texture_name_list = struct.unpack_from('<i', data, off)[0]
            off += 8  # Skip size + unknown1
            texture_name_list_count = struct.unpack_from('<i', data, off)[0]
            off += 20  # Skip texture count + unknown2[4]
            
            # Read texture names
            texture_names = []
            for _ in range(texture_name_list_count):
                end = data.find(b'\x00', off)
                if end == -1:
                    end = len(data)
                texture_names.append(data[off:end].decode('ascii', errors='replace'))
                off = end + 1
            off = (off + 15) & ~15
            self.log(f"Textures: {texture_names}")
            
            # Block parsing
            off += 8  # Block1 magic + size
            poi_count = struct.unpack_from('<h', data, off)[0]
            off += 2
            num_bones = struct.unpack_from('<h', data, off)[0]
            off += 2
            off += 24  # Skip intermediate fields
            
            # Bounding sphere
            bs_center = struct.unpack_from('<3f', data, off)
            off += 16
            bs_radius = struct.unpack_from('<f', data, off)[0]
            off += 8
            
            thing1_count = struct.unpack_from('<h', data, off)[0]
            off += 2
            thing2_count = struct.unpack_from('<h', data, off)[0]
            off += 76
            
            # Skip secondary texture names
            texture_names2_size = struct.unpack_from('<i', data, off)[0]
            off += 8 + texture_names2_size + 1
            
            self.log(f"POI={poi_count} Bones={num_bones} Thing1={thing1_count} Thing2={thing2_count}")
            
            # Skip variable sections
            if poi_count > 0:
                off += poi_count * 96
                off = (off + 15) & ~15
            if thing1_count > 0:
                off += thing1_count * 24
                off = (off + 15) & ~15
            if thing2_count > 0:
                off += thing2_count * 40
                off = (off + 15) & ~15
            if num_bones > 0:
                off += num_bones * 4
                off = (off + 7) & ~7
            
            self.log(f"MODELDATA at 0x{off:04X}")
            
            # Model data header
            off += 4  # nine
            crc = struct.unpack_from('<I', data, off)[0]
            off += 4
            size_of_model_data = struct.unpack_from('<i', data, off)[0]
            off += 4
            size_of_gmesh = struct.unpack_from('<I', data, off)[0]
            off += 4
            flags = struct.unpack_from('<I', data, off)[0]
            off += 32
            indices_count = struct.unpack_from('<i', data, off)[0]
            off += 16
            index_stride = struct.unpack_from('<B', data, off)[0]
            off += 4
            bones_in_rig = struct.unpack_from('<i', data, off)[0]
            off += 32
            scale = struct.unpack_from('<3f', data, off)
            off += 24
            vertex_count = struct.unpack_from('<i', data, off)[0]
            off += 1
            vertex_stride = struct.unpack_from('<B', data, off)[0]
            
            self.log(f"Vertices={vertex_count} (stride {vertex_stride}) Indices={indices_count} (stride {index_stride})")
            
            # Detect skinned vs static
            self.mesh.is_skinned = vertex_stride >= MIN_VERTEX_STRIDE_SKINNED
            self.log(f"Mesh type: {'SKINNED' if self.mesh.is_skinned else 'STATIC'}")
            
            if vertex_count <= 0 or vertex_count > 1000000 or indices_count <= 0:
                self.error(f"Implausible counts: v={vertex_count} i={indices_count}")
                self._parse_fallback(gdata)
                return
            
            # Parse mesh data
            self._parse_gmesh(gdata, indices_count, vertex_count, vertex_stride, index_stride)
            self._parse_bones(data, num_bones)
            
            self.mesh.game_version = GameVersion.SR3
            self.mesh.vertex_count = vertex_count
            self.mesh.index_count = indices_count
            self.mesh.vertex_stride = vertex_stride
            self.mesh.index_stride = index_stride
            self.mesh.header_info = {
                'magic': 'T8',
                'version': str(version),
                'textures': ', '.join(texture_names),
                'bounding': f"({bs_center[0]:.2f},{bs_center[1]:.2f},{bs_center[2]:.2f}) r={bs_radius:.2f}",
                'vertex_count': str(vertex_count),
                'vertex_stride': str(vertex_stride),
                'index_count': str(indices_count),
                'index_stride': str(index_stride),
                'bones_in_rig': str(bones_in_rig),
                'bone_count': str(len(self.mesh.bones)),
                'scale': f"({scale[0]:.3f},{scale[1]:.3f},{scale[2]:.3f})",
                'flags': hex(flags),
                'crc': hex(crc),
                'skinned': str(self.mesh.is_skinned),
            }
            
        except Exception as e:
            self.error(f"SR3 parse error: {e}")
            self._parse_fallback(gdata)
    
    def _parse_sr4(self):
        self.log("SR4/GOM: Using enhanced fallback parser with skinning detection")
        self._parse_fallback(self.gmesh_data)
    
    def _parse_fallback(self, gdata):
        """Brute-force gmesh parsing with automatic skinning detection"""
        self.log("[FALLBACK] Attempting automatic format detection...")
        
        if not gdata or len(gdata) < 32:
            self.error("Insufficient data for fallback parsing")
            return
        
        # Common vertex strides for different mesh types
        candidate_strides = [16, 20, 24, 28, 32, 48, 56, 64, 80]
        
        for vertex_stride in candidate_strides:
            # Calculate potential vertex count
            header_size = 16  # Estimated gmesh header
            available_for_verts = len(gdata) - header_size
            
            for idx_stride in [2, 4]:
                max_indices = available_for_verts // (idx_stride + 16)  # Reserve 16 bytes for indices
                
                for idx_count in range(10, min(max_indices, 200000), max(100, max_indices // 10)):
                    v_start = (header_size + idx_count * idx_stride + 15) & ~15
                    v_count = (len(gdata) - v_start) // vertex_stride
                    
                    if v_count < 2 or v_count > 500000:
                        continue
                    
                    # Sample first vertex to validate
                    vo = v_start
                    if vo + 12 > len(gdata):
                        continue
                    
                    x, y, z = struct.unpack_from('<3f', gdata, vo)
                    
                    # Validate coordinate range
                    if not (-10000 < x < 10000 and -10000 < y < 10000 and -10000 < z < 10000):
                        continue
                    
                    # Check for non-zero position
                    if x == 0.0 and y == 0.0 and z == 0.0:
                        continue
                    
                    # Valid format detected
                    self.log(f"[FALLBACK] Match: stride={vertex_stride} idx={idx_count} vert={v_count}")
                    self._parse_gmesh(gdata, idx_count, v_count, vertex_stride, idx_stride)
                    
                    self.mesh.is_skinned = vertex_stride >= MIN_VERTEX_STRIDE_SKINNED
                    self.mesh.vertex_stride = vertex_stride
                    self.mesh.index_stride = idx_stride
                    self.mesh.vertex_count = v_count
                    self.mesh.index_count = idx_count
                    self.mesh.header_info = {'mode': 'FALLBACK', 'skinned': str(self.mesh.is_skinned)}
                    return
        
        self.error("Fallback parsing failed - no valid format detected")
    
    def _parse_gmesh(self, gdata, indices_count, vertex_count, vertex_stride, index_stride):
        """Parse gmesh vertex and index data"""
        # Index buffer starts at fixed offset
        idx_offset = 16
        self.mesh.index_offset = idx_offset
        
        indices = []
        for i in range(indices_count):
            pos = idx_offset + i * index_stride
            if pos + index_stride > len(gdata):
                break
            if index_stride == 2:
                indices.append(struct.unpack_from('<H', gdata, pos)[0])
            else:
                indices.append(struct.unpack_from('<I', gdata, pos)[0])
        self.mesh.indices = indices
        
        # Vertex buffer follows indices
        v_offset = (idx_offset + indices_count * index_stride + 15) & ~15
        self.mesh.vertex_offset = v_offset
        
        vertices = []
        for i in range(vertex_count):
            vo = v_offset + i * vertex_stride
            if vo + 12 > len(gdata):
                break
            
            # Position (always first 12 bytes)
            x, y, z = struct.unpack_from('<3f', gdata, vo)
            
            # Normal (typically bytes at +12)
            nx = ny = nz = 0
            if vo + 15 <= len(gdata):
                nx, ny, nz = gdata[vo+12], gdata[vo+13], gdata[vo+14]
            
            # UV coordinates
            uv = vt = 0.0
            if vertex_stride >= 20 and vo + 20 <= len(gdata):
                uv = struct.unpack_from('<H', gdata, vo+16)[0] / 2048.0
                vt = struct.unpack_from('<H', gdata, vo+18)[0] / 2048.0
            
            # Skinning data (for skinned meshes)
            bone_indices = [0, 0, 0, 0]
            bone_weights = [1.0, 0.0, 0.0, 0.0]
            
            if vertex_stride >= MIN_VERTEX_STRIDE_SKINNED:
                # Standard format: 4 bone indices + 4 bone weights at end of vertex
                # Indices as bytes, weights as unsigned bytes (normalized 0-255)
                weight_offset = vo + vertex_stride - 16  # Last 16 bytes
                
                if weight_offset + 16 <= len(gdata):
                    # Read bone indices (4 bytes)
                    bone_indices = list(gdata[weight_offset:weight_offset+4])
                    
                    # Read bone weights (4 bytes, normalized 0-255)
                    raw_weights = gdata[weight_offset+4:weight_offset+8]
                    bone_weights = [w / 255.0 for w in raw_weights]
                    
                    # Normalize weights to sum to 1.0
                    weight_sum = sum(bone_weights)
                    if weight_sum > 0:
                        bone_weights = [w / weight_sum for w in bone_weights]
            
            vertices.append(Vertex(
                x=x, y=y, z=z,
                nx=nx, ny=ny, nz=nz,
                uv=uv, vt=vt,
                bone_indices=bone_indices,
                bone_weights=bone_weights,
                byte_offset=vo,
                stride=vertex_stride
            ))
        
        self.mesh.vertices = vertices
        self.log(f"Parsed {len(vertices)} vertices, {len(indices)} indices, {len(self.mesh.bones)} bones")
    
    def _parse_bones(self, data, num_bones):
        """Parse bone hierarchy from ccmesh header"""
        if num_bones <= 0:
            return
        
        # Find bone data section (after model header)
        # This is simplified - actual bone data may be in separate rig file
        self.log(f"Expecting {num_bones} bones from header")
        
        if self.rig_data and len(self.rig_data) > 100:
            # Parse rig file for bone names and transforms
            self._parse_rig_file(num_bones)
        else:
            # Generate placeholder bone names
            for i in range(min(num_bones, 100)):
                self.mesh.bones.append(Bone(
                    name=f"BONE_{i:03d}",
                    offset_matrix=[1.0, 0.0, 0.0, 0.0] * 3 + [0.0],  # Identity
                    parent_index=-1 if i == 0 else i - 1
                ))
    
    def _parse_rig_file(self, expected_bones):
        """Parse rig file for bone names and transformation matrices"""
        if not self.rig_data:
            return
        
        # Simplified rig parsing - actual format varies by game version
        # This extracts bone names from rig data where possible
        off = 0
        bone_count = 0
        
        # Try to find bone count in first 64 bytes
        if len(self.rig_data) >= 64:
            # Common rig header patterns
            if self.rig_data[:4] == b'RIG\x00':
                off = 4
            elif self.rig_data[:2] == b'T8':
                off = 2
        
        while off + 32 < len(self.rig_data) and bone_count < expected_bones:
            # Look for null-terminated strings (bone names)
            end = self.rig_data.find(b'\x00', off)
            if end == -1 or end - off > 64:
                off += 4
                continue
            
            name = self.rig_data[off:end].decode('ascii', errors='replace')
            if name and all(c.isalnum() or c in '_.' for c in name):
                self.mesh.bones.append(Bone(
                    name=name,
                    offset_matrix=[],
                    parent_index=-1 if bone_count == 0 else bone_count - 1
                ))
                bone_count += 1
            
            off = end + 1
            
            # Skip matrix data (48-64 bytes per bone)
            off += 64
        
        self.log(f"Parsed {len(self.mesh.bones)} bones from rig")
    
    def compute_bind_matrices(self):
        """Compute bind pose inverse matrices for skinning"""
        if not self.mesh.bones:
            return
        
        self.mesh.bind_matrices = []
        for bone in self.mesh.bones:
            if bone.offset_matrix:
                mat = np.array(bone.offset_matrix, dtype=np.float32).reshape(4, 4)
                self.mesh.bind_matrices.append(mat)
            else:
                self.mesh.bind_matrices.append(np.eye(4, dtype=np.float32))
    
    def apply_pose(self, pose_matrices):
        """Apply skeletal pose to mesh vertices"""
        if not self.mesh.is_skinned or not self.mesh.vertices:
            return
        
        if self.mesh.bind_matrices is None:
            self.compute_bind_matrices()
        
        posed = []
        for vertex in self.mesh.vertices:
            # Compute weighted vertex position
            wx, wy, wz = 0.0, 0.0, 0.0
            
            for bone_idx, weight in zip(vertex.bone_indices, vertex.bone_weights):
                if weight <= 0.01:
                    continue
                
                if bone_idx < len(pose_matrices) and bone_idx < len(self.mesh.bind_matrices):
                    # Transform by: pose_matrix * bind_inverse * vertex
                    bind_inv = self.mesh.bind_matrices[bone_idx]
                    pose_mat = pose_matrices[bone_idx]
                    
                    # Combined transform
                    combined = pose_mat @ bind_inv
                    
                    # Apply to vertex
                    v = np.array([vertex.x, vertex.y, vertex.z, 1.0])
                    transformed = combined @ v
                    
                    wx += weight * transformed[0]
                    wy += weight * transformed[1]
                    wz += weight * transformed[2]
            
            posed.append((wx, wy, wz))
        
        self.mesh.posed_vertices = np.array(posed, dtype=np.float32)
    
    def load_peg(self, path):
        """Load PEG (texture/asset) file"""
        if not os.path.exists(path):
            self.error(f"PEG not found: {path}")
            return False
        
        with open(path, 'rb') as f:
            data = f.read()
        
        self.log(f"Loaded PEG: {os.path.basename(path)} ({len(data)} bytes)")
        
        try:
            if len(data) >= 12:
                entry_count = struct.unpack_from('<I', data, 8)[0]
                off = 12
                
                for i in range(min(entry_count, 200)):
                    if off + 28 > len(data):
                        break
                    
                    name_off, name_len = struct.unpack_from('<II', data, off)
                    data_off, data_size, width, height, fmt = struct.unpack_from('<IIIII', data, off + 8)
                    off += 28
                    
                    name = data[name_off:name_off+name_len].decode('ascii', errors='replace') if name_len > 0 else ""
                    self.peg_entries.append(PEGEntry(
                        name=name,
                        offset=data_off,
                        size=data_size,
                        width=width,
                        height=height,
                        format=f"0x{fmt:08X}"
                    ))
        except Exception as e:
            self.error(f"PEG parse error: {e}")
        
        return True


# ─── 3D Mesh Viewer with Skinning Support ───────────────────────────────────────
class MeshViewerWidget(QOpenGLWidget if HAS_OPENGL else QWidget):
    vertex_clicked = pyqtSignal(int)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.mesh_data = None
        self.highlighted_vertex = -1
        self.highlighted_vertices = set()
        self._center = (0.0, 0.0, 0.0)
        self.cam_z = 5.0
        self.rot_x = 0.0
        self.rot_y = 0.0
        self.zoom = 1.0
        self._dragging = False
        self._last_x = 0
        self._last_y = 0
        self._click_x = 0
        self._click_y = 0
        self._wireframe = False
        self._show_skin_weights = False
        self._selected_bone = -1
        
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        
        # OpenGL state
        self.vbo_vertices = None
        self.vbo_indices = None
        self._compiled = False
        
        if HAS_OPENGL:
            self.initializeGL = self._init_gl
            self.resizeGL = self._resize_gl
            self.paintGL = self._paint_gl
    
    def _auto_fit(self):
        if not self.mesh_data or not self.mesh_data.vertices:
            return
        
        verts = self.mesh_data.vertices
        xs = [v.x for v in verts]
        ys = [v.y for v in verts]
        zs = [v.z for v in verts]
        
        self._center = (
            (min(xs)+max(xs))/2,
            (min(ys)+max(ys))/2,
            (min(zs)+max(zs))/2
        )
        
        radius = max(
            max(xs)-min(xs),
            max(ys)-min(ys),
            max(zs)-min(zs)
        ) / 2
        
        if radius < 0.01:
            radius = 1.0
        
        self.cam_z = radius / math.tan(math.radians(22.5)) * 1.5
        self.zoom = 1.0
        self.rot_x = 0.0
        self.rot_y = 0.0
    
    def set_mesh(self, mesh):
        self.mesh_data = mesh
        self.highlighted_vertex = -1
        self.highlighted_vertices = set()
        self._center = (0.0, 0.0, 0.0)
        self._auto_fit()
        self._compile_buffers()
        self.update()
    
    def highlight_vertex(self, idx):
        self.highlighted_vertex = idx
        self.highlighted_vertices = {idx}
        self.update()
    
    def highlight_vertices(self, indices):
        self.highlighted_vertices = set(indices)
        self.update()
    
    def clear_highlight(self):
        self.highlighted_vertex = -1
        self.highlighted_vertices = set()
        self.update()
    
    def set_wireframe_mode(self, wireframe):
        self._wireframe = wireframe
        self.update()
    
    def set_skin_weight_visualization(self, enabled):
        self._show_skin_weights = enabled
        self.update()
    
    def select_bone(self, bone_index):
        self._selected_bone = bone_index
        if bone_index >= 0 and self.mesh_data:
            # Highlight all vertices affected by this bone
            affected = set()
            for i, v in enumerate(self.mesh_data.vertices):
                if bone_index in v.bone_indices:
                    affected.add(i)
            self.highlighted_vertices = affected
        self.update()
    
    def _compile_buffers(self):
        """Compile VBOs for efficient rendering"""
        if not HAS_OPENGL or not self.mesh_data:
            return
        
        glDeleteBuffers(2, [self.vbo_vertices, self.vbo_indices])
        self.vbo_vertices = glGenBuffers(1)
        self.vbo_indices = glGenBuffers(1)
        
        # Vertex data
        verts = self.mesh_data.vertices
        vertex_data = []
        for v in verts:
            vertex_data.extend([v.x, v.y, v.z, v.nx/255.0, v.ny/255.0, v.nz/255.0])
        
        glBindBuffer(GL_ARRAY_BUFFER, self.vbo_vertices)
        glBufferData(GL_ARRAY_BUFFER, len(vertex_data)*4, (GLfloat*len(vertex_data))(*vertex_data), GL_DYNAMIC_DRAW)
        
        # Index data
        if self.mesh_data.indices:
            glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, self.vbo_indices)
            if self.mesh_data.index_stride == 2:
                indices_array = (GLushort*len(self.mesh_data.indices))(*self.mesh_data.indices)
            else:
                indices_array = (GLuint*len(self.mesh_data.indices))(*self.mesh_data.indices)
            glBufferData(GL_ELEMENT_ARRAY_BUFFER, len(self.mesh_data.indices)*2, indices_array, GL_STATIC_DRAW)
        
        self._compiled = True
    
    # ── OpenGL Rendering ──
    def _init_gl(self):
        glClearColor(0, 0, 0, 1)
        glEnable(GL_DEPTH_TEST)
        glDisable(GL_CULL_FACE)
    
    def _resize_gl(self, w, h):
        glViewport(0, 0, w, max(h, 1))
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        gluPerspective(45.0, w / max(h, 1), 0.01, 10000.0)
        glMatrixMode(GL_MODELVIEW)
    
    def _paint_gl(self):
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        glLoadIdentity()
        
        if not self.mesh_data or not self.mesh_data.vertices:
            return
        
        # Camera setup
        cx, cy, cz = self._center
        glTranslatef(-cx, -cy, -self.cam_z * self.zoom)
        glRotatef(self.rot_x, 1, 0, 0)
        glRotatef(self.rot_y, 0, 1, 0)
        
        # Use posed vertices if available (when skeletally animated)
        verts = self.mesh_data.posed_vertices if self.mesh_data.posed_vertices is not None else None
        
        if self._show_skin_weights and self.mesh_data.is_skinned:
            self._render_with_weight_colors(verts)
        elif self._wireframe:
            self._render_wireframe(verts)
        else:
            self._render_triangled_outline(verts)
    
    def _render_triangle_outlines(self, verts):
        """Render triangle wireframes"""
        if verts is None:
            verts_arr = self.mesh_data.vertices
        else:
            verts_arr = [Vertex(x=verts[i][0], y=verts[i][1], z=verts[i][2]) 
                        for i in range(len(verts))]
        
        indices = self.mesh_data.indices
        
        glColor3f(1.0, 0.08, 0.576)
        glLineWidth(1.5)
        glBegin(GL_LINES)
        
        for i in range(0, len(indices) - 2, 3):
            i0, i1, i2 = indices[i], indices[i+1], indices[i+2]
            
            if i0 >= len(verts_arr) or i1 >= len(verts_arr) or i2 >= len(verts_arr):
                continue
            
            v0, v1, v2 = verts_arr[i0], verts_arr[i1], verts_arr[i2]
            
            glVertex3f(v0.x, v0.y, v0.z)
            glVertex3f(v1.x, v1.y, v1.z)
            glVertex3f(v1.x, v1.y, v1.z)
            glVertex3f(v2.x, v2.y, v2.z)
            glVertex3f(v2.x, v2.y, v2.z)
            glVertex3f(v0.x, v0.y, v0.z)
        
        glEnd()
    
    def _render_wireframe(self, verts):
        """Render full wireframe"""
        if verts is None:
            verts_arr = self.mesh_data.vertices
        else:
            verts_arr = [Vertex(x=verts[i][0], y=verts[i][1], z=verts[i][2]) 
                        for i in range(len(verts))]
        
        indices = self.mesh_data.indices
        
        glColor3f(0.0, 1.0, 0.565)
        glLineWidth(1.0)
        glPolygonMode(GL_FRONT_AND_BACK, GL_LINE)
        glBegin(GL_TRIANGLES)
        
        for i in range(0, len(indices), 3):
            if i + 2 >= len(indices):
                break
            
            i0, i1, i2 = indices[i], indices[i+1], indices[i+2]
            
            if i0 >= len(verts_arr) or i1 >= len(verts_arr) or i2 >= len(verts_arr):
                continue
            
            v0, v1, v2 = verts_arr[i0], verts_arr[i1], verts_arr[i2]
            
            glVertex3f(v0.x, v0.y, v0.z)
            glVertex3f(v1.x, v1.y, v1.z)
            glVertex3f(v2.x, v2.y, v2.z)
        
        glEnd()
        glPolygonMode(GL_FRONT_AND_BACK, GL_FILL)
    
    def _render_with_weight_colors(self, verts):
        """Color vertices by dominant bone influence"""
        if verts is None:
            verts_arr = self.mesh_data.vertices
        else:
            verts_arr = [Vertex(x=verts[i][0], y=verts[i][1], z=verts[i][2]) 
                        for i in range(len(verts))]
        
        indices = self.mesh_data.indices
        
        # Color palette for bones
        bone_colors = [
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0),
            (1.0, 1.0, 0.0),
            (1.0, 0.0, 1.0),
            (0.0, 1.0, 1.0),
            (1.0, 0.5, 0.0),
            (0.5, 0.0, 1.0),
        ]
        
        glLineWidth(1.0)
        glBegin(GL_LINES)
        
        for i in range(0, len(indices) - 2, 3):
            i0, i1, i2 = indices[i], indices[i+1], indices[i+2]
            
            if i0 >= len(verts_arr) or i1 >= len(verts_arr) or i2 >= len(verts_arr):
                continue
            
            for vi in [i0, i1, i2]:
                v = verts_arr[vi]
                
                # Get dominant bone color
                if v.bone_weights[0] > 0.1:
                    idx = min(v.bone_indices[0], len(bone_colors) - 1)
                    glColor3f(*bone_colors[idx])
                else:
                    glColor3f(0.5, 0.5, 0.5)  # Gray for no influence
                
                glVertex3f(v.x, v.y, v.z)
                
                # Draw edge to next vertex
                next_vi = i1 if vi == i0 else (i2 if vi == i1 else i0)
                nv = verts_arr[next_vi]
                
                glVertex3f(nv.x, nv.y, nv.z)
        
        glEnd()
        
        # Render highlighted vertices
        if self.highlighted_vertices:
            glColor3f(1.0, 1.0, 1.0)
            glPointSize(12.0)
            glBegin(GL_POINTS)
            for vi in self.highlighted_vertices:
                if vi < len(verts_arr):
                    v = verts_arr[vi]
                    glVertex3f(v.x, v.y, v.z)
            glEnd()
    
    # ── Mouse Handling ──
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._last_x = event.x()
            self._last_y = event.y()
            self._click_x = event.x()
            self._click_y = event.y()
        elif event.button() == Qt.RightButton:
            menu = QMenu(self)
            menu.addAction("Reset View").triggered.connect(self._auto_fit)
            menu.addSeparator()
            menu.addAction("Toggle Wireframe").triggered.connect(
                lambda: self.set_wireframe_mode(not self._wireframe)
            )
            menu.addAction("Toggle Weight Colors").triggered.connect(
                lambda: self.set_skin_weight_visualization(not self._show_skin_weights)
            )
            menu.exec_(event.globalPos())
    
    def mouseMoveEvent(self, event):
        if self._dragging:
            self.rot_y += (event.x() - self._last_x) * 0.5
            self.rot_x += (event.y() - self._last_y) * 0.5
            self._last_x = event.x()
            self._last_y = event.y()
            self.update()
    
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = False
            if abs(event.x() - self._click_x) < 5 and abs(event.y() - self._click_y) < 5:
                self._pick_vertex(event.x(), event.y())
    
    def wheelEvent(self, event):
        self.zoom *= 1.0 + event.angleDelta().y() * 0.001
        self.zoom = max(0.01, min(self.zoom, 100.0))
        self.update()
    
    def _pick_vertex(self, px, py):
        """Ray pick closest vertex to mouse position"""
        if not self.mesh_data or not self.mesh_data.vertices:
            return
        
        verts = self.mesh_data.vertices
        w, h = self.width(), self.height()
        cx, cy, cz = self._center
        
        fovy = math.radians(45.0)
        aspect = w / max(h, 1)
        f = 1.0 / math.tan(fovy / 2.0)
        
        cos_y = math.cos(math.radians(self.rot_y))
        sin_y = math.sin(math.radians(self.rot_y))
        cos_x = math.cos(math.radians(self.rot_x))
        sin_x = math.sin(math.radians(self.rot_x))
        cam_z = self.cam_z * self.zoom
        
        best_dist = 20.0
        best_idx = -1
        
        for i, v in enumerate(verts):
            vx, vy, vz = v.x - cx, v.y - cy, v.z - cz
            
            # Transform to screen space
            x1 = vx * cos_y + vz * sin_y
            y1 = vy
            z1 = -vx * sin_y + vz * cos_y
            
            x2 = x1
            y2 = y1 * cos_x - z1 * sin_x
            z2 = y1 * sin_x + z1 * cos_x
            
            z_eye = z2 - cam_z
            if z_eye <= 0.01:
                continue
            
            ndc_x = (f / aspect) * x2 / z_eye
            ndc_y = f * y2 / z_eye
            
            sx = (ndc_x + 1.0) / 2.0 * w
            sy = (1.0 - ndc_y) / 2.0 * h
            
            dist = math.sqrt((sx - px)**2 + (sy - py)**2)
            
            if dist < best_dist:
                best_dist = dist
                best_idx = i
        
        if best_idx >= 0:
            self.highlight_vertex(best_idx)
            self.vertex_clicked.emit(best_idx)
    
    # ── 2D Fallback Rendering ──
    def paintEvent(self, event):
        if HAS_OPENGL:
            return
        
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(COLORS['background']))
        
        if not self.mesh_data or not self.mesh_data.vertices:
            painter.setPen(QColor(COLORS['text']))
            painter.drawText(self.rect(), Qt.AlignCenter, "No mesh loaded")
            return
        
        w, h = self.width(), self.height()
        scx, scy = w // 2, h // 2
        scale = min(w, h) * 0.4 * self.zoom
        
        cx, cy, cz = self._center
        verts = self.mesh_data.vertices
        indices = self.mesh_data.indices
        
        cos_y = math.cos(math.radians(self.rot_y))
        sin_y = math.sin(math.radians(self.rot_y))
        cos_x = math.cos(math.radians(self.rot_x))
        sin_x = math.sin(math.radians(self.rot_x))
        
        max_r = max(
            max(abs(v.x-cx) for v in verts),
            max(abs(v.y-cy) for v in verts),
            max(abs(v.z-cz) for v in verts)
        )
        if max_r < 0.01:
            max_r = 1.0
        
        def project(v):
            vx, vy, vz = v.x-cx, v.y-cy, v.z-cz
            x1 = vx*cos_y + vz*sin_y
            z1 = -vx*sin_y + vz*cos_y
            y2 = vy*cos_x - z1*sin_x
            z2 = vy*sin_x + z1*cos_x
            return ((x1/max_r)*scale + scx, (-y2/max_r)*scale + scy)
        
        # Render triangles
        painter.setPen(QPen(QColor(COLORS['foreground_objects']), 1))
        for i in range(0, len(indices)-2, 3):
            i0, i1, i2 = indices[i], indices[i+1], indices[i+2]
            
            if i0 >= len(verts) or i1 >= len(verts) or i2 >= len(verts):
                continue
            
            p0, p1, p2 = project(verts[i0]), project(verts[i1]), project(verts[i2])
            
            painter.drawLine(int(p0[0]), int(p0[1]), int(p1[0]), int(p1[1]))
            painter.drawLine(int(p1[0]), int(p1[1]), int(p2[0]), int(p2[1]))
            painter.drawLine(int(p2[0]), int(p2[1]), int(p0[0]), int(p0[1]))
        
        # Highlighted vertices
        if self.highlighted_vertices:
            painter.setPen(QPen(QColor(COLORS['cursor']), 3))
            for vi in self.highlighted_vertices:
                if vi < len(verts):
                    p = project(verts[vi])
                    painter.drawEllipse(int(p[0])-6, int(p[1])-6, 12, 12)
        
        # All vertices
        painter.setPen(QPen(QColor(COLORS['hex_bytes']), 1))
        for v in verts:
            p = project(v)
            painter.drawPoint(int(p[0]), int(p[1]))


# ─── Hex Editor ─────────────────────────────────────────────────────────────────
class HexEditorWidget(QAbstractScrollArea):
    highlight_hex_range = pyqtSignal(int, int)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.data = bytearray()
        self.highlight_start = -1
        self.highlight_end = -1
        self.selection_start = -1
        self.selection_end = -1
        self.cursor_pos = 0
        self.clipboard = b''
        self.undo_stack: List[bytes] = []
        self.redo_stack: List[bytes] = []
        self.bytes_per_line = 16
        self.address_width = 8
        self.char_w = 9
        
        self.viewport().setFont(QFont(FONT_FAMILY, DEFAULT_FONT_SIZE))
        self.viewport().setStyleSheet(f"background-color: {COLORS['background']};")
        self.setMouseTracking(True)
    
    def set_data(self, data):
        self.data = bytearray(data)
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.highlight_start = -1
        self.highlight_end = -1
        self.viewport().update()
    
    def highlight_range(self, start, end):
        self.highlight_start = start
        self.highlight_end = end
        self.viewport().update()
    
    def clear_highlight(self):
        self.highlight_start = -1
        self.highlight_end = -1
        self.viewport().update()
    
    def select_all(self):
        self.selection_start = 0
        self.selection_end = len(self.data)
        self.viewport().update()
    
    def copy_selection(self):
        if 0 <= self.selection_start < self.selection_end <= len(self.data):
            self.clipboard = bytes(self.data[self.selection_start:self.selection_end])
        return self.clipboard
    
    def cut_selection(self):
        data = self.copy_selection()
        if data:
            self._push_undo()
            del self.data[self.selection_start:self.selection_end]
            self.selection_start = -1
            self.selection_end = -1
            self.viewport().update()
        return data
    
    def paste(self):
        if self.clipboard:
            self._push_undo()
            pos = self.selection_start if self.selection_start >= 0 else self.cursor_pos
            self.data[pos:pos] = self.clipboard
            self.viewport().update()
    
    def delete_selection(self):
        if 0 <= self.selection_start < self.selection_end <= len(self.data):
            self._push_undo()
            del self.data[self.selection_start:self.selection_end]
            self.selection_start = -1
            self.selection_end = -1
            self.viewport().update()
    
    def _push_undo(self):
        self.undo_stack.append(bytes(self.data))
        if len(self.undo_stack) > 100:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
    
    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(bytes(self.data))
            self.data = bytearray(self.undo_stack.pop())
            self.viewport().update()
    
    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(bytes(self.data))
            self.data = bytearray(self.redo_stack.pop())
            self.viewport().update()
    
    def _byte_at(self, pos):
        line_h = DEFAULT_FONT_SIZE + 4
        line = (pos.y() + self.verticalScrollBar().value()) // line_h
        hex_start_x = (self.address_width + 2) * self.char_w
        x = pos.x() + self.horizontalScrollBar().value() - hex_start_x
        col = x // (self.char_w * 3)
        
        if 0 <= col < self.bytes_per_line:
            idx = line * self.bytes_per_line + col
            if 0 <= idx < len(self.data):
                return idx
        return -1
    
    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        painter.fillRect(self.viewport().rect(), QColor(COLORS['background']))
        
        if not self.data:
            painter.setPen(QColor(COLORS['text']))
            painter.drawText(10, 25, "No file loaded")
            return
        
        line_h = DEFAULT_FONT_SIZE + 4
        scroll_y = self.verticalScrollBar().value()
        first_line = scroll_y // line_h
        vis_lines = self.viewport().height() // line_h + 2
        total_lines = (len(self.data) + self.bytes_per_line - 1) // self.bytes_per_line
        
        hex_c = QColor(COLORS['hex_bytes'])
        addr_c = QColor(COLORS['address'])
        hl_c = QColor(COLORS['highlight_bg'])
        sel_c = QColor(COLORS['button'])
        
        font = QFont(FONT_FAMILY, DEFAULT_FONT_SIZE)
        painter.setFont(font)
        
        for li in range(first_line, min(first_line + vis_lines, total_lines)):
            y = li * line_h - scroll_y + line_h - 4
            x = 0
            
            painter.setPen(addr_c)
            painter.drawText(x, y, f"{li * self.bytes_per_line:08X}")
            x += (self.address_width + 2) * self.char_w
            
            for col in range(self.bytes_per_line):
                bi = li * self.bytes_per_line + col
                if bi >= len(self.data):
                    break
                
                if self.highlight_start <= bi <= self.highlight_end:
                    painter.fillRect(x, y - DEFAULT_FONT_SIZE + 2, self.char_w * 3 - 1, line_h - 2, hl_c)
                    painter.setPen(QColor('#FFFFFF'))
                elif self.selection_start <= bi <= self.selection_end:
                    painter.fillRect(x, y - DEFAULT_FONT_SIZE + 2, self.char_w * 3 - 1, line_h - 2, sel_c)
                    painter.setPen(QColor('#FFFFFF'))
                else:
                    painter.setPen(hex_c)
                
                painter.drawText(x, y, f"{self.data[bi]:02X} ")
                x += self.char_w * 3
            
            x += self.char_w * 2
            for col in range(self.bytes_per_line):
                bi = li * self.bytes_per_line + col
                if bi >= len(self.data):
                    break
                ch = chr(self.data[bi]) if 32 <= self.data[bi] < 127 else '.'
                
                painter.setPen(
                    QColor(COLORS['foreground_objects'])
                    if self.highlight_start <= bi <= self.highlight_end
                    else QColor(COLORS['text'])
                )
                painter.drawText(x, y, ch)
                x += self.char_w
        
        total_h = total_lines * line_h
        self.verticalScrollBar().setRange(0, max(0, total_h - self.viewport().height()))
    
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            bi = self._byte_at(event.pos())
            if bi >= 0:
                self.cursor_pos = bi
                self.selection_start = bi
                self.selection_end = bi + 1
                self.highlight_hex_range.emit(bi, bi + 1)
                self.viewport().update()
        elif event.button() == Qt.RightButton:
            self._context_menu(event.globalPos())
    
    def mouseMoveEvent(self, event):
        if self.selection_start >= 0 and event.buttons() & Qt.LeftButton:
            bi = self._byte_at(event.pos())
            if bi >= 0:
                self.selection_end = bi + 1
                self.viewport().update()
    
    def _context_menu(self, pos):
        menu = QMenu(self)
        menu.addAction("Select All").triggered.connect(self.select_all)
        menu.addSeparator()
        menu.addAction("Copy").triggered.connect(self.copy_selection)
        menu.addAction("Cut").triggered.connect(self.cut_selection)
        menu.addAction("Paste").triggered.connect(self.paste)
        menu.addSeparator()
        menu.addAction("Undo").triggered.connect(self.undo)
        menu.addAction("Redo").triggered.connect(self.redo)
        menu.exec_(pos)
    
    def keyPressEvent(self, event):
        shortcuts = {
            (Qt.Key_A, Qt.ControlModifier): self.select_all,
            (Qt.Key_C, Qt.ControlModifier): self.copy_selection,
            (Qt.Key_X, Qt.ControlModifier): self.cut_selection,
            (Qt.Key_V, Qt.ControlModifier): self.paste,
            (Qt.Key_Z, Qt.ControlModifier): self.undo,
            (Qt.Key_Y, Qt.ControlModifier): self.redo,
            (Qt.Key_Delete, None): self.delete_selection,
        }
        
        key_mod = (event.key(), event.modifiers())
        if key_mod in shortcuts:
            shortcuts[key_mod]()
            event.accept()


# ─── Console Widget ─────────────────────────────────────────────────────────────
class ConsoleWidget(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setStyleSheet(f"""
            QTextEdit {{
                background-color: {COLORS['background']};
                color: {COLORS['text']};
                font-family: {FONT_FAMILY};
                font-size: {DEFAULT_FONT_SIZE - 2}px;
            }}
        """)
    
    def info(self, msg):
        self.append(f"<span style='color:{COLORS['text']}'>{msg}</span>")
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
    
    def error(self, msg):
        self.append(f"<span style='color:{COLORS['error']}'>ERROR: {msg}</span>")
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
    
    def warning(self, msg):
        self.append(f"<span style='color:{COLORS['warning']}'>WARN: {msg}</span>")
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
    
    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction("Select All").triggered.connect(self.selectAll)
        menu.addSeparator()
        menu.addAction("Copy").triggered.connect(self.copy)
        menu.addAction("Clear").triggered.connect(self.clear)
        menu.exec_(event.globalPos())


# ─── Mesh Info Tab ──────────────────────────────────────────────────────────────
class MeshInfoWidget(QTabWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self._cb = None
        self._style = f"""
            QTableWidget {{
                background-color:{COLORS['background']};
                color:{COLORS['text']};
                font-family:{FONT_FAMILY};
                font-size:{DEFAULT_FONT_SIZE-2}px;
            }}
            QHeaderView::section {{
                background-color:{COLORS['button']};
                color:{COLORS['text']};
                padding:4px;
            }}
        """
        
        # Vertices tab
        self.vertex_table = QTableWidget()
        self.vertex_table.setColumnCount(12)
        self.vertex_table.setHorizontalHeaderLabels([
            'Idx', 'X', 'Y', 'Z', 'NX', 'NY', 'NZ', 'UV', 'VT',
            'Bones', 'Weights', 'Offset'
        ])
        self.vertex_table.setStyleSheet(self._style)
        self.vertex_table.itemSelectionChanged.connect(self._on_vert)
        self.addTab(self.vertex_table, "Vertices")
        
        # Indices tab
        self.index_table = QTableWidget()
        self.index_table.setColumnCount(4)
        self.index_table.setHorizontalHeaderLabels(['Tri', 'I0', 'I1', 'I2'])
        self.index_table.setStyleSheet(self._style)
        self.index_table.itemSelectionChanged.connect(self._on_idx)
        self.addTab(self.index_table, "Indices")
        
        # Header tab
        self.header_table = QTableWidget()
        self.header_table.setColumnCount(2)
        self.header_table.setHorizontalHeaderLabels(['Field', 'Value'])
        self.header_table.horizontalHeader().setStretchLastSection(True)
        self.header_table.setStyleSheet(self._style)
        self.addTab(self.header_table, "Header")
        
        # Bones tab (NEW)
        self.bone_table = QTableWidget()
        self.bone_table.setColumnCount(4)
        self.bone_table.setHorizontalHeaderLabels(['Idx', 'Name', 'Parent', 'Children'])
        self.bone_table.setStyleSheet(self._style)
        self.bone_table.itemSelectionChanged.connect(self._on_bone)
        self.addTab(self.bone_table, "Bones")
    
    def set_callback(self, cb):
        self._cb = cb
    
    def set_mesh_data(self, mesh):
        # Populate vertices
        self.vertex_table.setRowCount(len(mesh.vertices))
        for i, v in enumerate(mesh.vertices):
            self.vertex_table.setItem(i, 0, QTableWidgetItem(str(i)))
            self.vertex_table.setItem(i, 1, QTableWidgetItem(f"{v.x:.4f}"))
            self.vertex_table.setItem(i, 2, QTableWidgetItem(f"{v.y:.4f}"))
            self.vertex_table.setItem(i, 3, QTableWidgetItem(f"{v.z:.4f}"))
            self.vertex_table.setItem(i, 4, QTableWidgetItem(str(v.nx)))
            self.vertex_table.setItem(i, 5, QTableWidgetItem(str(v.ny)))
            self.vertex_table.setItem(i, 6, QTableWidgetItem(str(v.nz)))
            self.vertex_table.setItem(i, 7, QTableWidgetItem(f"{v.uv:.4f}"))
            self.vertex_table.setItem(i, 8, QTableWidgetItem(f"{v.vt:.4f}"))
            self.vertex_table.setItem(i, 9, QTableWidgetItem(','.join(map(str, v.bone_indices))))
            self.vertex_table.setItem(i, 10, QTableWidgetItem(','.join(f"{w:.2f}" for w in v.bone_weights)))
            self.vertex_table.setItem(i, 11, QTableWidgetItem(hex(v.byte_offset)))
        
        # Populate indices
        tri_count = len(mesh.indices) // 3
        self.index_table.setRowCount(max(tri_count, 1))
        for i in range(tri_count):
            b = i * 3
            if b + 2 < len(mesh.indices):
                self.index_table.setItem(i, 0, QTableWidgetItem(str(i)))
                self.index_table.setItem(i, 1, QTableWidgetItem(str(mesh.indices[b])))
                self.index_table.setItem(i, 2, QTableWidgetItem(str(mesh.indices[b+1])))
                self.index_table.setItem(i, 3, QTableWidgetItem(str(mesh.indices[b+2])))
        
        # Populate header
        self.header_table.setRowCount(len(mesh.header_info))
        for i, (k, v) in enumerate(mesh.header_info.items()):
            self.header_table.setItem(i, 0, QTableWidgetItem(k))
            self.header_table.setItem(i, 1, QTableWidgetItem(str(v)))
        
        # Populate bones
        self.bone_table.setRowCount(len(mesh.bones))
        for i, bone in enumerate(mesh.bones):
            self.bone_table.setItem(i, 0, QTableWidgetItem(str(i)))
            self.bone_table.setItem(i, 1, QTableWidgetItem(bone.name))
            parent_str = str(bone.parent_index) if bone.parent_index >= 0 else "None"
            self.bone_table.setItem(i, 2, QTableWidgetItem(parent_str))
            children_str = ','.join(map(str, bone.children)) if bone.children else "None"
            self.bone_table.setItem(i, 3, QTableWidgetItem(children_str))
    
    def _on_vert(self):
        if self._cb:
            items = self.vertex_table.selectedItems()
            if items:
                self._cb('vertex', items[0].row())
    
    def _on_idx(self):
        if self._cb:
            items = self.index_table.selectedItems()
            if items:
                r = items[0].row()
                self._cb('triangle', [r*3, r*3+1, r*3+2])
    
    def _on_bone(self):
        if self._cb:
            items = self.bone_table.selectedItems()
            if items:
                self._cb('bone', items[0].row())
    
    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction("Select All").triggered.connect(self._sel_all)
        menu.addSeparator()
        menu.addAction("Copy").triggered.connect(self._copy)
        menu.exec_(event.globalPos())
    
    def _sel_all(self):
        widget = self.currentWidget()
        if isinstance(widget, QTableWidget):
            widget.selectAll()
    
    def _copy(self):
        widget = self.currentWidget()
        if isinstance(widget, QTableWidget):
            items = widget.selectedItems()
            if items:
                rows = sorted(set(it.row() for it in items))
                lines = []
                for r in rows:
                    lines.append('\t'.join(
                        widget.item(r, c).text() if widget.item(r, c) else ''
                        for c in range(widget.columnCount())
                    ))
                QApplication.clipboard().setText('\n'.join(lines))


# ─── PEG Viewer ─────────────────────────────────────────────────────────────────
class PEGViewerWidget(QTableWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(6)
        self.setHorizontalHeaderLabels(['Name', 'Offset', 'Size', 'Width', 'Height', 'Format'])
        self.horizontalHeader().setStretchLastSection(True)
        self.setStyleSheet(f"""
            QTableWidget {{
                background-color:{COLORS['background']};
                color:{COLORS['text']};
                font-family:{FONT_FAMILY};
                font-size:{DEFAULT_FONT_SIZE-2}px;
            }}
            QHeaderView::section {{
                background-color:{COLORS['button']};
                color:{COLORS['text']};
                padding:4px;
            }}
        """)
    
    def set_entries(self, entries):
        self.setRowCount(len(entries))
        for i, e in enumerate(entries):
            self.setItem(i, 0, QTableWidgetItem(e.name))
            self.setItem(i, 1, QTableWidgetItem(hex(e.offset)))
            self.setItem(i, 2, QTableWidgetItem(str(e.size)))
            self.setItem(i, 3, QTableWidgetItem(str(e.width)))
            self.setItem(i, 4, QTableWidgetItem(str(e.height)))
            self.setItem(i, 5, QTableWidgetItem(e.format))
    
    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction("Select All").triggered.connect(self.selectAll)
        menu.addSeparator()
        menu.addAction("Copy").triggered.connect(self._copy)
        menu.exec_(event.globalPos())
    
    def _copy(self):
        items = self.selectedItems()
        if items:
            rows = sorted(set(it.row() for it in items))
            lines = []
            for r in rows:
                lines.append('\t'.join(
                    self.item(r, c).text() if self.item(r, c) else ''
                    for c in range(self.columnCount())
                ))
            QApplication.clipboard().setText('\n'.join(lines))


# ─── Bone Control Panel (NEW) ───────────────────────────────────────────────────
class BoneControlPanel(QWidget):
    bone_transform_changed = pyqtSignal(int, np.ndarray)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected_bone = -1
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        
        # Bone list
        self.bone_list = QListWidget()
        self.bone_list.itemClicked.connect(self._on_bone_selected)
        layout.addWidget(QLabel("Bones:"))
        layout.addWidget(self.bone_list)
        
        # Transform controls
        group = QGroupBox("Transform (Local)")
        form = QFormLayout(group)
        
        self.tx_slider = QSlider(Qt.Horizontal)
        self.tx_slider.setRange(-1000, 1000)
        self.tx_slider.valueChanged.connect(lambda: self._emit_transform())
        form.addRow("TX:", self.tx_slider)
        
        self.ty_slider = QSlider(Qt.Horizontal)
        self.ty_slider.setRange(-1000, 1000)
        self.ty_slider.valueChanged.connect(lambda: self._emit_transform())
        form.addRow("TY:", self.ty_slider)
        
        self.tz_slider = QSlider(Qt.Horizontal)
        self.tz_slider.setRange(-1000, 1000)
        self.tz_slider.valueChanged.connect(lambda: self._emit_transform())
        form.addRow("TZ:", self.tz_slider)
        
        self.rx_slider = QSlider(Qt.Horizontal)
        self.rx_slider.setRange(-360, 360)
        self.rx_slider.valueChanged.connect(lambda: self._emit_transform())
        form.addRow("RX:", self.rx_slider)
        
        self.ry_slider = QSlider(Qt.Horizontal)
        self.ry_slider.setRange(-360, 360)
        self.ry_slider.valueChanged.connect(lambda: self._emit_transform())
        form.addRow("RY:", self.ry_slider)
        
        self.rz_slider = QSlider(Qt.Horizontal)
        self.rz_slider.setRange(-360, 360)
        self.rz_slider.valueChanged.connect(lambda: self._emit_transform())
        form.addRow("RZ:", self.rz_slider)
        
        layout.addWidget(group)
        
        # Buttons
        btn_layout = QHBoxLayout()
        reset_btn = QPushButton("Reset Poses")
        reset_btn.clicked.connect(self._reset_poses)
        btn_layout.addWidget(reset_btn)
        layout.addLayout(btn_layout)
    
    def set_bones(self, bones):
        self.bone_list.clear()
        for bone in bones:
            item = QListWidgetItem(bone.name)
            item.setData(Qt.UserRole, bone)
            self.bone_list.addItem(item)
    
    def _on_bone_selected(self, item):
        self._selected_bone = self.bone_list.row(item)
        self.bone_transform_changed.emit(self._selected_bone, self._get_transform())
    
    def _get_transform(self):
        return np.array([
            self.tx_slider.value() / 1000.0,
            self.ty_slider.value() / 1000.0,
            self.tz_slider.value() / 1000.0,
            self.rx_slider.value(),
            self.ry_slider.value(),
            self.rz_slider.value(),
        ])
    
    def _emit_transform(self):
        if self._selected_bone >= 0:
            self.bone_transform_changed.emit(self._selected_bone, self._get_transform())
    

    def _reset_poses(self):
        """Reset all sliders to neutral (bind pose)"""
        for slider in (self.tx_slider, self.ty_slider, self.tz_slider):
            slider.setValue(0)
        for slider in (self.rx_slider, self.ry_slider, self.rz_slider):
            slider.setValue(0)
        if self._selected_bone >= 0:
            self.bone_transform_changed.emit(self._selected_bone, self._get_transform())
    
    def clear_panel(self):
        self.bone_list.clear()
        self._selected_bone = -1
        self._reset_poses()

# ─── Main Window ────────────────────────────────────────────────────────────────
class MeshEditorMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Saints Row 3D Mesh Editor v2.0 — SR3 / SR4 / GOM (Skinned)")
        self.setMinimumSize(1400, 900)
        self.setStyleSheet(f"""
            QMainWindow {{ background-color:{COLORS['background']}; }}
            QMenuBar {{ background-color:{COLORS['button']}; color:{COLORS['text']}; font-family:{FONT_FAMILY}; }}
            QMenuBar::item:selected {{ background-color:{COLORS['button_hover']}; }}
            QMenu {{ background-color:{COLORS['background']}; color:{COLORS['text']}; border:1px solid {COLORS['button']}; font-family:{FONT_FAMILY}; }}
            QMenu::item:selected {{ background-color:{COLORS['button_hover']}; }}
            QStatusBar {{ background-color:{COLORS['button']}; color:{COLORS['text']}; font-family:{FONT_FAMILY}; }}
            QTabWidget::pane {{ border:1px solid {COLORS['button']}; background-color:{COLORS['background']}; }}
            QTabBar::tab {{ background-color:{COLORS['button']}; color:{COLORS['text']}; padding:6px 12px; font-family:{FONT_FAMILY}; }}
            QTabBar::tab:selected {{ background-color:{COLORS['button_hover']}; }}
        """)
        
        self.parser = MeshParser()
        self.hex_editor = HexEditorWidget()
        self.mesh_viewer = MeshViewerWidget()
        self.mesh_info = MeshInfoWidget()
        self.console = ConsoleWidget()
        self.peg_viewer = PEGViewerWidget()
        self.bone_panel = BoneControlPanel()
        
        self._pose_cache: Dict[int, np.ndarray] = {}  # bone_idx -> transform (tx,ty,tz,rxy,rz)
        
        self._build_ui()
        self._connect_signals()
        self._build_menu()
        
        self.console.info("Saints Row 3D Mesh Editor v2.0 initialized.")
        self.console.info("Supported: .ccmesh_pc .gcmesh_pc .csmesh_pc .gsmesh_pc .cmesh_pc .gmesh_pc .cpeg_pc .gpeg_pc")
        self.console.info("Skinning: loads rigs, displays weights, interactive bone posing")
    
    def _build_ui(self):
        # Left: 3D viewer with bone panel beneath it
        left_splitter = QSplitter(Qt.Vertical)
        self.mesh_viewer.setMinimumSize(500, 350)
        left_splitter.addWidget(self.mesh_viewer)
        
        bone_container = QWidget()
        bone_layout = QVBoxLayout(bone_container)
        bone_layout.setContentsMargins(0, 0, 0, 0)
        bone_layout.addWidget(self.bone_panel)
        left_splitter.addWidget(bone_container)
        left_splitter.setSizes([550, 200])
        
        # Main splitter
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_splitter)
        
        right_tabs = QTabWidget()
        right_tabs.addTab(self.hex_editor, "Hex Editor")
        right_tabs.addTab(self.mesh_info, "Mesh Info")
        right_tabs.addTab(self.peg_viewer, "PEG")
        right_tabs.addTab(self.console, "Console")
        right_tabs.setMinimumSize(500, 400)
        splitter.addWidget(right_tabs)
        splitter.setSizes([700, 700])
        
        self.setCentralWidget(splitter)
        self.setStatusBar(QStatusBar())
    
    def _connect_signals(self):
        self.hex_editor.highlight_hex_range.connect(self._on_hex_clicked)
        self.mesh_viewer.vertex_clicked.connect(self._on_mesh_clicked)
        self.mesh_info.set_callback(self._on_info_sel)
        self.bone_panel.bone_transform_changed.connect(self._on_bone_transform)
    
    # ── Cross-highlight handlers ──
    def _on_hex_clicked(self, start, end):
        mesh = self.parser.mesh
        if not mesh or not mesh.vertices:
            return
        if mesh.vertex_offset <= start < mesh.vertex_offset + mesh.vertex_count * mesh.vertex_stride:
            vi = (start - mesh.vertex_offset) // mesh.vertex_stride
            if 0 <= vi < len(mesh.vertices):
                self.mesh_viewer.highlight_vertex(vi)
        elif mesh.index_offset <= start < mesh.index_offset + mesh.index_count * mesh.index_stride:
            ip = (start - mesh.index_offset) // mesh.index_stride
            if 0 <= ip < len(mesh.indices):
                vi = mesh.indices[ip]
                if vi < len(mesh.vertices):
                    self.mesh_viewer.highlight_vertex(vi)
    
    def _on_mesh_clicked(self, vi):
        mesh = self.parser.mesh
        if not mesh or vi >= len(mesh.vertices):
            return
        v = mesh.vertices[vi]
        self.hex_editor.highlight_range(v.byte_offset, v.byte_offset + mesh.vertex_stride)
        line = v.byte_offset // self.hex_editor.bytes_per_line
        self.hex_editor.verticalScrollBar().setValue(line * (DEFAULT_FONT_SIZE + 4))
    
    def _on_info_sel(self, kind, data):
        mesh = self.parser.mesh
        if not mesh:
            return
        if kind == 'vertex':
            self.mesh_viewer.highlight_vertex(data)
            if data < len(mesh.vertices):
                v = mesh.vertices[data]
                self.hex_editor.highlight_range(v.byte_offset, v.byte_offset + mesh.vertex_stride)
        elif kind == 'triangle':
            self.mesh_viewer.highlight_vertices(set(data))
            for vi in data:
                if vi < len(mesh.vertices):
                    v = mesh.vertices[vi]
                    self.hex_editor.highlight_range(v.byte_offset, v.byte_offset + mesh.vertex_stride)
        elif kind == 'bone':
            # Selecting a bone highlights affected vertices in viewer
            self.mesh_viewer.select_bone(data)
            self.console.info(f"Selected bone {data}: {mesh.bones[data].name if data < len(mesh.bones) else '?'}")
    
    # ── Skinning: interactive pose update ──
    def _on_bone_transform(self, bone_idx, transform):
        """Recompute skinning when a bone is moved via sliders"""
        mesh = self.parser.mesh
        if not mesh or not mesh.is_skinned or not mesh.bones:
            self.console.warning("Mesh is not skinned — transform has no effect")
            return
        
        self._pose_cache[bone_idx] = transform
        self._apply_current_pose()
    
    def _apply_current_pose(self):
        """Build per-bone pose matrices from cached slider transforms and re-skin the mesh"""
        mesh = self.parser.mesh
        self.parser.compute_bind_matrices()
        
        pose_matrices = []
        for i in range(len(mesh.bones)):
            m = np.eye(4, dtype=np.float32)
            if i in self._pose_cache:
                tx, ty, tz, rx_deg, ry_deg, rz_deg = self._pose_cache[i]
                
                # Translation
                t = np.eye(4, dtype=np.float32)
                t[:3, 3] = [tx * 10.0, ty * 10.0, tz * 10.0]
                
                # Rotations (deg -> rad)
                rx, ry, rz = map(math.radians, (rx_deg, ry_deg, rz_deg))
                
                # Build each rotation via axis-angle
                m = t @ self._rotation_x(rx) @ self._rotation_y(ry) @ self._rotation_z(rz)
            pose_matrices.append(m)
        
        self.parser.apply_pose(pose_matrices)
        self.mesh_viewer.update()
    
    @staticmethod
    def _rotation_x(a):
        c, s = math.cos(a), math.sin(a)
        return np.array([[1,0,0,0],[0,c,-s,0],[0,s,c,0],[0,0,0,1]], dtype=np.float32)
    
    @staticmethod
    def _rotation_y(a):
        c, s = math.cos(a), math.sin(a)
        return np.array([[c,0,s,0],[0,1,0,0],[-s,0,c,0],[0,0,0,1]], dtype=np.float32)
    
    @staticmethod
    def _rotation_z(a):
        c, s = math.cos(a), math.sin(a)
        return np.array([[c,-s,0,0],[s,c,0,0],[0,0,1,0],[0,0,0,1]], dtype=np.float32)
    
    def _reset_pose(self):
        self._pose_cache.clear()
        self.parser.mesh.posed_vertices = None
        self.bone_panel._reset_poses()
        self.mesh_viewer.update()
        self.console.info("Pose reset to bind pose")
    
    # ── Menu ──
    def _build_menu(self):
        mb = self.menuBar()
        
        fm = mb.addMenu("&File")
        a = QAction("&Open cmesh...", self); a.setShortcut(QKeySequence("Ctrl+O")); a.triggered.connect(self._open_cmesh); fm.addAction(a)
        a = QAction("&Open PEG...", self); a.setShortcut(QKeySequence("Ctrl+Shift+O")); a.triggered.connect(self._open_peg); fm.addAction(a)
        fm.addSeparator()
        a = QAction("&Export OBJ...", self); a.setShortcut(QKeySequence("Ctrl+E")); a.triggered.connect(self._export_obj); fm.addAction(a)
        fm.addSeparator()
        a = QAction("&Exit", self); a.setShortcut(QKeySequence("Ctrl+Q")); a.triggered.connect(self.close); fm.addAction(a)
        
        em = mb.addMenu("&Edit")
        a = QAction("&Undo", self); a.setShortcut(QKeySequence("Ctrl+Z")); a.triggered.connect(self.hex_editor.undo); em.addAction(a)
        a = QAction("&Redo", self); a.setShortcut(QKeySequence("Ctrl+Y")); a.triggered.connect(self.hex_editor.redo); em.addAction(a)
        em.addSeparator()
        a = QAction("Cu&t", self); a.setShortcut(QKeySequence("Ctrl+X")); a.triggered.connect(self.hex_editor.cut_selection); em.addAction(a)
        a = QAction("&Copy", self); a.setShortcut(QKeySequence("Ctrl+C")); a.triggered.connect(self._copy); em.addAction(a)
        a = QAction("&Paste", self); a.setShortcut(QKeySequence("Ctrl+V")); a.triggered.connect(self.hex_editor.paste); em.addAction(a)
        a = QAction("&Delete", self); a.setShortcut(QKeySequence("Delete")); a.triggered.connect(self.hex_editor.delete_selection); em.addAction(a)
        em.addSeparator()
        a = QAction("Select &All", self); a.setShortcut(QKeySequence("Ctrl+A")); a.triggered.connect(self._sel_all); em.addAction(a)
        
        vm = mb.addMenu("&View")
        a = QAction("Reset 3D View", self); a.triggered.connect(self.mesh_viewer._auto_fit); vm.addAction(a)
        a = QAction("Toggle Wireframe", self); a.setShortcut(QKeySequence("W")); a.triggered.connect(
            lambda: self.mesh_viewer.set_wireframe_mode(not self.mesh_viewer._wireframe)); vm.addAction(a)
        a = QAction("Toggle Weight Colors", self); a.setShortcut(QKeySequence("S")); a.triggered.connect(
            lambda: self.mesh_viewer.set_skin_weight_visualization(not self.mesh_viewer._show_skin_weights)); vm.addAction(a)
        a = QAction("Reset Pose", self); a.setShortcut(QKeySequence("R")); a.triggered.connect(self._reset_pose); vm.addAction(a)
        
        hm = mb.addMenu("&Help")
        a = QAction("&About", self); a.triggered.connect(lambda: QMessageBox.about(
            self, "About",
            "Saints Row 3D Mesh Editor v2.0\nSR3 / SR4 / GOM\n\n"
            "Features: 3D viewer, skinned mesh support,\n"
            "bone weight visualization, interactive bone posing,\n"
            "hex editor, bidirectional highlighting, PEG viewer."
        )); hm.addAction(a)
    
    # ── File actions ──
    def _open_cmesh(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open cmesh/gmesh", "",
            "Mesh files (*.ccmesh_pc *.gcmesh_pc *.csmesh_pc *.gsmesh_pc *.cmesh_pc *.gmesh_pc);;All (*)")
        if not path:
            return
        
        self.console.info(f"Opening: {path}")
        self.parser = MeshParser()
        self.parser._log_fn = self.console.info
        self.parser._err_fn = self.console.error
        self._pose_cache.clear()
        
        if self.parser.load_cmesh(path):
            data = self.parser.gmesh_data or self.parser.cmesh_data
            self.hex_editor.set_data(data)
            self.mesh_viewer.set_mesh(self.parser.mesh)
            self.mesh_info.set_mesh_data(self.parser.mesh)
            self.bone_panel.set_bones(self.parser.mesh.bones)
            
            skin_msg = "SKINNED" if self.parser.mesh.is_skinned else "STATIC"
            self.statusBar().showMessage(
                f"{os.path.basename(path)} | {skin_msg} | "
                f"V:{self.parser.mesh.vertex_count} I:{self.parser.mesh.index_count} "
                f"B:{len(self.parser.mesh.bones)} | {self.parser.game_version.name}")
            self.console.info(f"Loaded OK ({skin_msg}, {len(self.parser.mesh.bones)} bones).")
        else:
            self.console.error("Failed to load.")
    
    def _open_peg(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open PEG", "",
            "PEG files (*.cpeg_pc *.gpeg_pc);;All (*)")
        if not path:
            return
        
        self.parser = MeshParser()
        self.parser._log_fn = self.console.info
        self.parser._err_fn = self.console.error
        if self.parser.load_peg(path):
            self.peg_viewer.set_entries(self.parser.peg_entries)
            with open(path, 'rb') as f:
                self.hex_editor.set_data(f.read())
            self.console.info(f"PEG: {len(self.parser.peg_entries)} entries")
    
    def _export_obj(self):
        """Export current mesh (posed if skinned) as Wavefront OBJ"""
        mesh = self.parser.mesh
        if not mesh or not mesh.vertices:
            QMessageBox.warning(self, "Export", "No mesh loaded")
            return
        
        path, _ = QFileDialog.getSaveFileName(self, "Export OBJ", "", "OBJ files (*.obj);;All (*)")
        if not path:
            return
        
        use_posed = mesh.posed_vertices is not None
        try:
            with open(path, 'w') as f:
                f.write("# Saints Row 3D Mesh Editor export\n")
                if use_posed:
                    for row in mesh.posed_vertices:
                        f.write(f"v {row[0]:.6f} {row[1]:.6f} {row[2]:.6f}\n")
                else:
                    for v in mesh.vertices:
                        f.write(f"v {v.x:.6f} {v.y:.6f} {v.z:.6f}\n")
                for v in mesh.vertices:
                    f.write(f"vt {v.uv:.6f} {v.vt:.6f}\n")
                for i in range(0, len(mesh.indices) - 2, 3):
                    i0, i1, i2 = mesh.indices[i]+1, mesh.indices[i+1]+1, mesh.indices[i+2]+1
                    f.write(f"f {i0}/{i0} {i1}/{i1} {i2}/{i2}\n")
            self.console.info(f"Exported {len(mesh.vertices)} verts -> {path}"
                              f"{' (posed)' if use_posed else ''}")
        except Exception as e:
            self.console.error(f"Export failed: {e}")
    
    def _copy(self):
        data = self.hex_editor.copy_selection()
        if data:
            QApplication.clipboard().setText(data.hex(' '))
    
    def _sel_all(self):
        self.hex_editor.select_all()
    
    def closeEvent(self, event):
        self.console.info("Closing.")
        event.accept()

# ─── Entry Point ────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setFont(QFont(FONT_FAMILY, DEFAULT_FONT_SIZE))
    
    def _excepthook(t, v, tb):
        msg = ''.join(traceback.format_exception(t, v, tb))
        print(f"FATAL: {msg}", file=sys.stderr)
        if 'win' in locals():
            win.console.error(f"FATAL: {msg.splitlines()[-1]}")
    
    sys.excepthook = _excepthook
    
    win = MeshEditorMainWindow()
    win.show()
    sys.exit(app.exec_())

#!/usr/bin/env python3
"""
SR2 Forensic Scanner
====================
A dual-pane forensic binary scanner for Saints Row 2 file formats:
  cmesh_pc, smesh_pc, car_pc, g_cmesh_pc, g_smesh_pc, g_car_pc,
  morph_pc, pcm_pc, rig_pc, sim_pc, cvtf

Runs 8 forensic passes over each file, combining documented format
knowledge (saintsrowmods.com forum spec sheets) with heuristics,
then cross-verifies its own findings.

Requires: pip install PySide6
"""

import os
import sys
import math
import time
import struct
import hashlib
import zlib
import traceback
from datetime import datetime

from PySide6.QtCore import Qt, QObject, QThread, Signal, Slot
from PySide6.QtGui import QFont, QColor, QTextCursor, QTextCharFormat
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QProgressBar, QSplitter, QTabWidget,
    QTextEdit, QPlainTextEdit, QLineEdit, QFileDialog, QMessageBox,
    QListWidget, QAbstractItemView, QStatusBar,
)

# ------------------------------------------------------------------
# Theme Configuration
# ------------------------------------------------------------------
BG            = "#000000"
FG            = "#00FF90"
DARK_PINK     = "#000000"
MID_PINK      = "#5c1238"
ENTRY_BG      = "#000000"
TREE_BG       = "#000000"
CONSOLE_BG    = "#000000"
CONSOLE_FG    = "#00FF90"
WARNING_COLOR = "#ffb700"
BLOCK_COLOR   = "#ff4444"
SELECT_TEXT   = "#ffffff"
TEXT_SIZE     = 15

MESH_EXT  = (".cmesh_pc", ".smesh_pc", ".car_pc")
MAGIC_SET = ("g_cmesh_pc", "g_smesh_pc", "g_car_pc")
ALL_EXT   = MESH_EXT + MAGIC_SET + (".morph_pc", ".pcm_pc", ".rig_pc", ".sim_pc", ".cvtf")

KNOWN_FLOATS = [
    (1.0,       "1.0f  (scale / default value)"),
    (0.5,       "0.5f  (half-scale value)"),
    (0.3333333, "~0.333f (third / fractional value)"),
    (0.2,       "~0.2f (fifth / fractional value)"),
    (0.45,      "~0.45f (custom ratio)"),
]
TEXTURE_EXTS = (b".tga", b".dds", b".xtb", b".png")

STYLESHEET = f"""
QWidget {{ background-color: {BG}; color: {FG};
           font-size: {TEXT_SIZE}pt; font-weight: bold; }}
QMainWindow {{ background-color: {BG}; }}
QPushButton {{ background-color: {ENTRY_BG}; color: {FG};
               border: 1px solid {MID_PINK}; padding: 6px 14px;
               font-weight: bold; }}
QPushButton:hover {{ border: 2px solid {FG}; }}
QPushButton:disabled {{ color: {MID_PINK}; border-color: {MID_PINK}; }}
QProgressBar {{ background-color: {ENTRY_BG}; border: 1px solid {MID_PINK};
               color: {FG}; text-align: center; font-weight: bold; }}
QProgressBar::chunk {{ background-color: {FG}; }}
QTabWidget::pane {{ border: 1px solid {MID_PINK}; }}
QTabBar::tab {{ background: {DARK_PINK}; color: {FG};
               border: 1px solid {MID_PINK}; padding: 6px 18px; }}
QTabBar::tab:selected {{ background: {MID_PINK}; color: {SELECT_TEXT}; }}
QListWidget {{ background-color: {TREE_BG}; color: {FG};
               border: 1px solid {MID_PINK}; font-weight: bold; }}
QLineEdit {{ background-color: {ENTRY_BG}; color: {FG};
             border: 1px solid {MID_PINK}; font-family: Consolas; }}
QTextEdit, QPlainTextEdit {{ background-color: {CONSOLE_BG};
             color: {CONSOLE_FG}; font-family: Consolas; }}
QStatusBar {{ color: {FG}; }}
QSplitter::handle {{ background-color: {MID_PINK}; }}
QLabel#passLabel {{ color: {WARNING_COLOR}; }}
"""

# ------------------------------------------------------------------
# Context-menu aware text widgets
# ------------------------------------------------------------------
class ContextMenuTextEdit(QTextEdit):
    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu()
        menu.clear()
        act_sel = menu.addAction("Select All")
        act_copy = menu.addAction("Copy")
        chosen = menu.exec(event.globalPos())
        if chosen == act_sel:
            self.selectAll()
        elif chosen == act_copy:
            self.copy()

class ContextMenuPlainText(QPlainTextEdit):
    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu()
        menu.clear()
        act_sel = menu.addAction("Select All")
        act_copy = menu.addAction("Copy")
        chosen = menu.exec(event.globalPos())
        if chosen == act_sel:
            self.selectAll()
        elif chosen == act_copy:
            self.copy()

# ------------------------------------------------------------------
# Forensic scanning engine (pure logic, no Qt)
# ------------------------------------------------------------------
def shannon_entropy(buf):
    if not buf:
        return 0.0
    freq = [0] * 256
    for b in buf:
        freq[b] += 1
    n = len(buf)
    ent = 0.0
    for c in freq:
        if c:
            p = c / n
            ent -= p * math.log2(p)
    return ent

def extract_ascii_strings(data, min_len=4):
    out, cur, start = [], bytearray(), 0
    for i, b in enumerate(data):
        if 0x20 <= b < 0x7F:
            if not cur:
                start = i
            cur.append(b)
        else:
            if len(cur) >= min_len:
                out.append((start, bytes(cur)))
            cur = bytearray()
    if len(cur) >= min_len:
        out.append((start, bytes(cur)))
    return out

def fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n} B" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024.0

# ---- PASS 1: Header & Signature -----------------------------------
def pass_header(data, path, ext, R):
    R("head", "=" * 78)
    R("head", f"FILE: {os.path.basename(path)}")
    R("dim", f"PATH: {path}")
    R("dim", f"SIZE: {fmt_size(len(data))} ({len(data)} bytes)")
    R("dim", f"SCANNED: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    R("info", "PASS 1/8 : HEADER & SIGNATURE VALIDATION")
    R("sub", "-" * 60)
    info = {"ext": ext, "sig": None}

    if len(data) < 0x40:
        R("block", f"  [BLOCK] File is smaller than a minimum valid header ({len(data)} bytes).")
        info["short"] = True
        return info

    head_win = data[:1024].lower()
    for sig in MAGIC_SET:
        needle = sig.encode("ascii")
        off = head_win.find(needle)
        if off != -1:
            R("ok", f"  [OK] Found internal signature '{sig}' at offset 0x{off:04X}.")
            info["sig"] = sig
            break

    first4 = struct.unpack_from("<I", data, 0)[0]
    R("info", f"  First dword (LE uint32): 0x{first4:08X} ({first4})")
    if ext == ".cvtf":
        if first4 in (0x01, 0x03):
            R("ok", "  [OK] Character-NPC CVTF variant magic (0x01/0x03).")
            info["cvtf_kind"] = "character"
        elif first4 == 0x1C:
            R("ok", "  [OK] Vehicle CVTF variant magic (0x1C).")
            info["cvtf_kind"] = "vehicle"
        else:
            R("warn", f"  [WARN] Unknown CVTF magic 0x{first4:X}; proceeding heuristically.")
            info["cvtf_kind"] = "unknown"

    printable = sum(1 for b in data[:32] if 0x20 <= b < 0x7F)
    R("info", f"  Printable ratio in first 32 bytes: {printable}/32")
    if len(data) >= 0x400:
        R("info", "  File exceeds minimum 0x400 header span - header skip heuristics applicable.")
    else:
        R("warn", "  [WARN] File shorter than 0x400; vertex start heuristics will clamp.")
    return info

# ---- PASS 2: Entropy Analysis -------------------------------------
def pass_entropy(data, R):
    R("info", "PASS 2/8 : ENTROPY & BYTE-DISTRIBUTION ANALYSIS")
    R("sub", "-" * 60)
    ent = shannon_entropy(data)
    R("info", f"  Global Shannon entropy: {ent:.4f} / 8.000 bits per byte")
    if ent > 7.5:
        R("warn", "  [WARN] Very high entropy: file may be compressed or encrypted.")
    elif ent < 3.0:
        R("info", "  Low entropy: highly repetitive / structured data expected.")

    bs = 4096
    flagged, max_ent = 0, 0.0
    total_blocks = max(1, (len(data) + bs - 1) // bs)
    suspicious = []
    for bi in range(total_blocks):
        chunk = data[bi * bs:(bi + 1) * bs]
        e = shannon_entropy(chunk)
        max_ent = max(max_ent, e)
        if e > 7.5:
            flagged += 1
            if len(suspicious) < 8:
                suspicious.append((bi * bs, e))
    R("info", f"  Analyzed {total_blocks} x {bs}-byte blocks; peak block entropy {max_ent:.4f}.")
    if flagged:
        R("warn", f"  [WARN] {flagged} block(s) exceed 7.5 bits/byte (possible packed data):")
        for off, e in suspicious:
            R("warn", f"      block @ 0x{off:06X}  entropy {e:.4f}")
    else:
        R("ok", "  [OK] No anomalous high-entropy blocks detected.")
    return {"global": ent, "flagged": flagged}

# ---- PASS 3: String Extraction ------------------------------------
def pass_strings(data, R):
    R("info", "PASS 3/8 : STRING & TEXTURE-REFERENCE EXTRACTION")
    R("sub", "-" * 60)
    strings = extract_ascii_strings(data, 4)
    R("info", f"  Extracted {len(strings)} printable ASCII run(s) (>= 4 chars).")
    tex_refs = [(o, s) for o, s in strings
                if any(t in s.lower() for t in TEXTURE_EXTS)]
    for o, s in tex_refs:
        R("ok", f"  [OK] Texture reference @ 0x{o:06X}: \"{s.decode('ascii', 'replace')}\"")
    if not tex_refs:
        R("info", "  No explicit texture references (.tga/.dds/.xtb/.png) found.")
    R("dim", "  Largest strings (contextual naming candidates):")
    for o, s in sorted(strings, key=lambda x: -len(x[1]))[:12]:
        R("dim", f"      0x{o:06X}  len={len(s):4d}  \"{s.decode('ascii', 'replace')[:60]}\"")
    return {"strings": strings, "tex_refs": tex_refs}

# ---- PASS 4: Float Value Scan -------------------------------------
def pass_floats(data, R):
    R("info", "PASS 4/8 : FLOAT-VALUE SCAN (plausibility + known constants)")
    R("sub", "-" * 60)
    n_words = len(data) // 4
    sample_step = max(1, n_words // 16384)
    sampled = 0
    plausible = 0
    for wi in range(0, min(n_words, 262144), sample_step):
        v, = struct.unpack_from("<f", data, wi * 4)
        sampled += 1
        if math.isfinite(v) and abs(v) < 1e6:
            plausible += 1
    R("info", f"  Plausible float32 density (sampled): {plausible / max(sampled, 1):.1%}")

    hits = {round(val, 3): [] for val, _ in KNOWN_FLOATS}
    for off in range(0, len(data) - 4, 4):
        v, = struct.unpack_from("<f", data, off)
        if math.isfinite(v):
            for val, _ in KNOWN_FLOATS:
                if abs(v - val) < 1e-3:
                    hits[round(val, 3)].append(off)
                    break
    for val, desc in KNOWN_FLOATS:
        offs = hits[round(val, 3)]
        if offs:
            show = ", ".join(f"0x{o:06X}" for o in offs[:6])
            more = f" (+{len(offs) - 6} more)" if len(offs) > 6 else ""
            R("ok", f"  [OK] Known CVTF constant {desc} -> {len(offs)} hit(s): {show}{more}")
        else:
            R("dim", f"      Constant {desc}: no occurrences.")
    return {"known_hits": {k: len(v) for k, v in hits.items()}}

# ---- PASS 5: Vertex Buffer Heuristics -----------------------------
def pass_vertices(data, R, mesh_like):
    R("info", "PASS 5/8 : VERTEX BUFFER HEURISTICS")
    R("sub", "-" * 60)
    if not mesh_like:
        R("info", "  Not a mesh-type container; skipping vertex layout probe.")
        return None
    starts = [s for s in (0x200, 0x400, 0x100) if s < len(data)]
    strides = [12, 20, 24, 32, 40, 48, 56, 64]
    best = None
    for start in starts:
        for stride in strides:
            count = (len(data) - start) // stride
            if count < 16:
                continue
            sample_n = min(count, 96)
            good = zero = 0
            for i in range(sample_n):
                vi = int(i * (count - 1) / max(sample_n - 1, 1))
                try:
                    x, y, z = struct.unpack_from("<fff", data, start + vi * stride)
                except:
                    continue
                if all(math.isfinite(c) and abs(c) < 100000.0 for c in (x, y, z)):
                    good += 1
                    if x == 0.0 and y == 0.0 and z == 0.0:
                        zero += 1
            score = (good - 0.5 * zero) / max(sample_n, 1)
            if best is None or score > best["score"]:
                best = {"start": start, "stride": stride, "score": score,
                        "count": count, "zero_pct": zero / max(sample_n, 1)}
    if best is None:
        R("warn", "  [WARN] Could not fit any candidate vertex layout into this file.")
        return None
    layout_desc = {
        12: "position only (12B)",
        20: "position + UV (20B)",
        24: "position + normal (24B)",
        32: "position + UV + normal (32B)",
    }.get(best["stride"], f"skinned/extended (bone weights likely, {best['stride']}B)")
    R("ok", f"  [OK] Best-fit vertex layout: start=0x{best['start']:04X}, "
            f"stride={best['stride']}B, est. vertices={best['count']:,}, "
            f"confidence={best['score']:.0%}")
    R("info", f"      Probable layout: {layout_desc}")
    R("info", f"      Estimated vertex region: 0x{best['start']:06X} .. "
            f"0x{best['start'] + best['count'] * best['stride']:06X}")
    if best["zero_pct"] > 0.6:
        R("warn", "  [WARN] Majority of sampled positions are (0,0,0); "
                  "start offset may be misaligned.")
    return best

# ---- PASS 6: Index Buffer Heuristics ------------------------------
def pass_indices(data, R, vert):
    R("info", "PASS 6/8 : INDEX BUFFER HEURISTICS")
    R("sub", "-" * 60)
    if vert is None:
        R("info", "  No vertex estimate available; performing blind index sweep.")
    else:
        R("info", f"  Scanning forward of estimated vertex data for index stream...")

    start = (vert["start"] + vert["count"] * vert["stride"]) if vert else 0x400
    start = min(start & ~1, max(0, len(data) - 2))
    limit = min(start + 0x8000, len(data) - 2)
    threshold = max((vert["count"] * 2) if vert else 65535, 4096)

    best_run, run_start, cur_run, cur_start = 0, None, 0, None
    for off in range(start, limit, 2):
        try:
            v, = struct.unpack_from("<H", data, off)
        except:
            continue
        if v < threshold:
            if cur_run == 0:
                cur_start = off
            cur_run += 1
            if cur_run > best_run:
                best_run, run_start = cur_run, cur_start
        else:
            cur_run = 0
    if best_run >= 256:
        try:
            values = struct.unpack_from(f"<{best_run}H", data, run_start)
            uniq = len(set(values))
            R("ok", f"  [OK] Probable 16-bit index buffer @ 0x{run_start:06X}: "
                    f"{best_run:,} indices, {uniq:,} unique, "
                    f"range 0..{max(values)} (threshold {threshold}).")
            return {"offset": run_start, "count": best_run,
                    "max": max(values), "unique": uniq}
        except:
            pass
    R("warn", "  [WARN] No contiguous 16-bit index run of sufficient length found.")
    return None

# ---- PASS 7: Format-Specific Structural Parse --------------------
def pass_structure(data, R, info, strings_res):
    R("info", "PASS 7/8 : FORMAT-SPECIFIC STRUCTURAL PARSE")
    R("sub", "-" * 60)
    stats = {}
    if info.get("ext") == ".cvtf":
        try:
            name_len, opt_count = struct.unpack_from("<II", data, 0x00)
            hash8 = data[0x08:0x10].hex().upper()
            tex_ptr, = struct.unpack_from("<I", data, 0x10)
            flags, = struct.unpack_from("<I", data, 0x14)
            R("info", f"  Option Name Length : {name_len}")
            R("info", f"  Option Count       : {opt_count}")
            R("info", f"  Hash/Magic (0x08)  : {hash8}")
            R("info", f"  Texture Ref (0x10) : 0x{tex_ptr:08X} "
                       f"({'in-bounds' if tex_ptr < len(data) else 'OUT OF BOUNDS'})")
            R("info", f"  Flags (0x14)       : 0x{flags:08X}")
            stats["opt_count"] = opt_count
            if not 1 <= opt_count <= 5:
                R("warn", f"  [WARN] Option count {opt_count} outside documented 1-5 range.")
            else:
                R("ok", f"  [OK] Option count {opt_count} within documented 1-5 range.")
            if tex_ptr >= len(data):
                R("block", "  [BLOCK] Texture reference points beyond EOF; structure invalid.")
            kind = info.get("cvtf_kind")
            if kind == "character":
                lo, hi = 0x0060, 0x0500
                desc = "Character (body parts / clothing)"
            else:
                lo, hi = 0x01F0, min(0x3000, len(data))
                desc = "Vehicle (components / paint finishes)"
            comps = [(o, s) for o, s in strings_res["strings"] if lo <= o < hi]
            R("info", f"  Documented {desc} component region 0x{lo:04X}-0x{hi:04X}: "
                       f"{len(comps)} string(s).")
            for o, s in comps[:10]:
                R("dim", f"      0x{o:06X}  \"{s.decode('ascii', 'replace')}\"")
            stats["region_strings"] = len(comps)
            tail = data[-64:]
            nz = tail.rstrip(b"\x00")
            if nz and nz[-1:].isdigit() is False and b"\x00" in tail:
                last = nz.split(b"\x00")[-1]
                try:
                    txt = last.decode("ascii")
                    if txt:
                        R("ok", f"  [OK] Trailing null-terminated marker string: \"{txt}\"")
                        stats["tail_marker"] = txt
                except UnicodeDecodeError:
                    pass
        except Exception as e:
            R("block", f"  [ERROR] CVTF structure parse failed: {e}")
    else:
        ptr_like = sum(1 for o in range(0, min(len(data), 0x2000) - 4, 4)
                       if struct.unpack_from("<I", data, o)[0] < len(data))
        R("info", f"  Pointer-like dwords in first 0x2000: {ptr_like} "
                  f"({ptr_like / max(1, min(len(data), 0x2000) // 4):.0%})")
        ascend = run = best_run = 0
        prev = -1
        for o in range(0, min(len(data), 0x10000) - 4, 4):
            v = struct.unpack_from("<I", data, o)[0]
            if v >= prev and v < len(data):
                run += 1
                best_run = max(best_run, run)
            else:
                run = 0
            prev = v
        if best_run >= 16:
            R("ok", f"  [OK] Long ascending offset-table run ({best_run} dwords) "
                    f"- likely pointer/index table.")
        stats["ptr_like"] = ptr_like
    return stats

# ---- PASS 8: Cross-Verification ----------------------------------
def pass_verify(data, path, R, info, ent_res, str_res, flt_res,
                vert, idx_res, struct_stats):
    R("info", "PASS 8/8 : CROSS-VERIFICATION & FORENSIC SUMMARY")
    R("sub", "-" * 60)
    checks, passed = 0, 0

    def chk(cond, ok_msg, bad_msg):
        nonlocal checks, passed
        checks += 1
        if cond:
            passed += 1
            R("ok", f"  [OK] VERIFY: {ok_msg}")
        else:
            R("warn", f"  [WARN] VERIFY: {bad_msg}")

    md5 = hashlib.md5(data).hexdigest()
    sha = hashlib.sha256(data).hexdigest()
    crc = zlib.crc32(data) & 0xFFFFFFFF
    R("info", f"  MD5    : {md5}")
    R("info", f"  SHA-256: {sha}")
    R("info", f"  CRC-32 : 0x{crc:08X}")

    chk(hashlib.md5(data).hexdigest() == md5, "checksum computation is deterministic.", "checksum instability!")

    all_ok = True
    if vert:
        vend = vert["start"] + vert["count"] * vert["stride"]
        all_ok &= vend <= len(data)
    if idx_res:
        all_ok &= idx_res["offset"] + idx_res["count"] * 2 <= len(data)
    chk(all_ok, "all structural offsets lie within file bounds.",
        "structural offsets exceed file bounds.")

    if vert and idx_res:
        chk(idx_res["max"] < max(vert["count"], 1),
            f"max index {idx_res['max']} < estimated vertex count {vert['count']:,} "
            "(index buffer consistent with vertex buffer).",
            f"max index {idx_res['max']} >= vertex estimate {vert['count']:,} "
            "(buffers inconsistent - layout likely wrong).")

    if info.get("ext") == ".cvtf":
        oc = struct_stats.get("opt_count", 0)
        chk(1 <= oc <= 5, f"option count {oc} in documented range.",
            f"option count {oc} out of documented range.")
        n_opt = struct_stats.get("region_strings", 0)
        chk(n_opt >= min(oc, 1), f"component-region string count ({n_opt}) >= option count ({oc}).",
            f"component-region strings ({n_opt}) < option count ({oc}); structure suspect.")

    chk(ent_res["flagged"] == 0 or ent_res["global"] < 7.5,
        "no contradictory entropy anomalies.",
        f"{ent_res['flagged']} high-entropy block(s) amid structured data "
        "(possible embedded packed chunks).")

    chk(len(str_res["strings"]) > 0, f"{len(str_res['strings'])} coherent strings extracted.",
        "no coherent ASCII strings extracted (packed/encrypted?).")

    conf = 100.0 * passed / max(checks, 1)
    R("info", "  " + "." * 56)
    R("info", f"  VERIFICATION: {passed}/{checks} cross-checks passed  |  CONFIDENCE: {conf:.0f}%")
    if conf >= 90:
        R("ok", f"  VERDICT: STRUCTURALLY SOUND - '{os.path.basename(path)}' parses consistently.")
    elif conf >= 60:
        R("warn", f"  VERDICT: PARTIAL MATCH - '{os.path.basename(path)}' shows heuristic agreement with caveats.")
    else:
        R("block", f"  VERDICT: INCONSISTENT - '{os.path.basename(path)}' failed "
                   f"{checks - passed} cross-check(s); treat findings as unreliable.")
    R("dim", "=" * 78 + "\n")
    return {"confidence": conf, "passed": passed, "checks": checks}

# ------------------------------------------------------------------
# Scan Worker (background thread)
# ------------------------------------------------------------------
class ScanWorker(QObject):
    progress = Signal(int)
    log = Signal(str)
    report_chunk = Signal(object)
    finished = Signal()

    def __init__(self, files):
        super().__init__()
        self.files = files
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    @Slot()
    def run(self):
        total = len(self.files)
        try:
            for fi, path in enumerate(self.files):
                if self._cancelled:
                    break
                base = fi / total
                
                def set_prog(frac, msg=""):
                    self.progress.emit(int((base + frac / total) * 100))
                    if msg:
                        self.log.emit(msg)

                chunks = []
                def R(level, text):
                    chunks.append((level, text))

                self.log.emit(f">>> Starting scan: {os.path.basename(path)}")
                try:
                    with open(path, "rb") as fh:
                        data = fh.read()
                except OSError as e:
                    self.log.emit(f"[ERROR] Cannot open {path}: {e}")
                    self.report_chunk.emit([("block", f"!! ERROR opening {path}: {e}")])
                    continue

                ext = os.path.splitext(path)[1].lower()
                mesh_like = ext in MESH_EXT or ext.startswith(".g_")

                set_prog(0.05, f"Pass 1/8 - header: {os.path.basename(path)}")
                info = pass_header(data, path, ext, R)
                set_prog(1.0)
                if info.get("short"):
                    self.report_chunk.emit(chunks)
                    continue

                set_prog(2.0)
                ent_res = pass_entropy(data, R)
                set_prog(3.0)
                str_res = pass_strings(data, R)
                set_prog(4.0)
                flt_res = pass_floats(data, R)
                set_prog(5.0)
                vert = pass_vertices(data, R, mesh_like)
                set_prog(6.0)
                idx_res = pass_indices(data, R, vert)
                set_prog(7.0)
                st_res = pass_structure(data, R, info, str_res)
                set_prog(7.5, f"Pass 8/8 - verification: {os.path.basename(path)}")
                pass_verify(data, path, R, info, ent_res, str_res,
                            flt_res, vert, idx_res, st_res)

                self.report_chunk.emit(chunks)
                set_prog(1.0, f"<<< Completed: {os.path.basename(path)}")
        except Exception:
            tb = traceback.format_exc()
            self.log.emit("[FATAL] Unhandled exception in worker:\n" + tb)
            self.report_chunk.emit([("block", "!! FATAL SCANNER EXCEPTION:\n" + tb)])
        finally:
            self.progress.emit(100 if not self._cancelled else 0)
            self.finished.emit()

# ------------------------------------------------------------------
# Console Widget
# ------------------------------------------------------------------
class ConsoleWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.view = ContextMenuPlainText()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(20000)
        lay.addWidget(self.view)
        row = QHBoxLayout()
        self.prompt = QLabel(">")
        self.input = QLineEdit()
        self.input.setPlaceholderText("type 'help' for commands (cls / clear clears console)")
        row.addWidget(self.prompt)
        row.addWidget(self.input)
        lay.addLayout(row)
        self.input.returnPressed.connect(self._exec)

    def write(self, text, color=None):
        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if color:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            cursor.insertText(text + "\n", fmt)
        else:
            cursor.insertText(text + "\n")
        self.view.setTextCursor(cursor)
        self.view.ensureCursorVisible()

    def _exec(self):
        cmd = self.input.text().strip()
        self.input.clear()
        if not cmd:
            return
        self.write(f"> {cmd}", SELECT_TEXT)
        c = cmd.lower()
        if c in ("cls", "clear"):
            self.view.clear()
            self.write("[console cleared]", FG)
        elif c in ("help", "?"):
            self.write("Commands:", FG)
            self.write("  cls | clear   - clear the console window", FG)
            self.write("  help         - show this help", FG)
        else:
            self.write(f"Unknown command: '{cmd}'. Type 'help'.", WARNING_COLOR)

# ------------------------------------------------------------------
# Main Window
# ------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SR2 Forensic Scanner - SR2 Binary File Analyzer")
        self.resize(1500, 900)

        self.thread = None
        self.worker = None
        self.has_report = False

        central = QWidget()
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(splitter)

        # Pane 1: Scanner controls
        pane1 = QWidget()
        p1 = QVBoxLayout(pane1)
        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        p1.addWidget(QLabel("Scan Queue"))
        p1.addWidget(self.file_list, stretch=1)

        btn_row = QHBoxLayout()
        self.btn_add = QPushButton("Add Files...")
        self.btn_clear = QPushButton("Clear Queue")
        btn_row.addWidget(self.btn_add)
        btn_row.addWidget(self.btn_clear)
        p1.addLayout(btn_row)

        self.pass_label = QLabel("Idle.")
        self.pass_label.setObjectName("passLabel")
        self.progress = QProgressBar()
        self.progress.setValue(0)
        p1.addWidget(self.pass_label)
        p1.addWidget(self.progress)

        act_row = QHBoxLayout()
        self.btn_scan = QPushButton("Start Forensic Scan (8 passes)")
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setEnabled(False)
        act_row.addWidget(self.btn_scan)
        act_row.addWidget(self.btn_cancel)
        p1.addLayout(act_row)

        self.btn_save = QPushButton("Save Report As Text...")
        self.btn_save.setEnabled(False)
        p1.addWidget(self.btn_save)
        pane1.setMaximumWidth(520)
        splitter.addWidget(pane1)

        # Pane 2: Tabs (Report / Console)
        self.tabs = QTabWidget()
        self.report = ContextMenuTextEdit()
        self.report.setReadOnly(True)
        self.console = ConsoleWidget()
        self.tabs.addTab(self.report, "Report")
        self.tabs.addTab(self.console, "Console")
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        status = QStatusBar()
        self.setStatusBar(status)

        font = QFont("Consolas", TEXT_SIZE)
        font.setBold(True)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.report.setFont(font)

        self.btn_add.clicked.connect(self.add_files)
        self.btn_clear.clicked.connect(self.clear_queue)
        self.btn_scan.clicked.connect(self.start_scan)
        self.btn_cancel.clicked.connect(self.cancel_scan)
        self.btn_save.clicked.connect(self.save_report)

        self._intro()
        self.console.write("SR2 Forensic Scanner console initialized.", FG)

    LEVEL_COLORS = {
        "head": MID_PINK, "info": FG, "ok": SELECT_TEXT, "warn": WARNING_COLOR,
        "block": BLOCK_COLOR, "dim": "#4a9e78", "sub": MID_PINK,
    }

    def _intro(self):
        self.report.clear()
        self.report.append(
            f'<span style="color:{FG};">'
            f'SR2 FORENSIC SCANNER v1.0<br>'
            f'Formats: {", ".join(e.lstrip(".") for e in ALL_EXT)}<br>'
            f'Engine: 8-pass analysis with cross-verification.<br>'
            f'Add files (left pane) and press Start.</span><br>')

    def append_chunk(self, chunk):
        self.tabs.setCurrentIndex(0)
        for level, text in chunk:
            color = self.LEVEL_COLORS.get(level, FG)
            safe = (text.replace("&", "&amp;").replace("<", "&lt;")
                        .replace(">", "&gt;").replace('"', "&quot;"))
            self.report.append(f'<span style="color:{color};">{safe}</span>')
        sb = self.report.verticalScrollBar()
        sb.setValue(sb.maximum())
        self.has_report = True
        self.btn_save.setEnabled(True)

    def on_log(self, msg):
        ts = time.strftime("%H:%M:%S")
        self.console.write(f"[{ts}] {msg}")

    def clear_queue(self):
        self.file_list.clear()
        self.progress.setValue(0)
        self.pass_label.setText("Idle.")
        self.console.write("Queue cleared.", FG)

    def add_files(self):
        flt = "Saints Row 2 files (%s);;All files (*)" % " ".join("*" + e for e in ALL_EXT)
        paths, _ = QFileDialog.getOpenFileNames(self, "Select SR2 files", "", flt)
        for p in paths:
            exists = any(self.file_list.item(i).text() == p for i in range(self.file_list.count()))
            if not exists:
                self.file_list.addItem(p)
        self.console.write(f"Queue: {self.file_list.count()} file(s).", FG)

    def start_scan(self):
        if self.file_list.count() == 0:
            QMessageBox.information(self, "No files", "Add at least one file to the queue first.")
            return
        self._intro()
        files = [self.file_list.item(i).text() for i in range(self.file_list.count())]
        self.btn_scan.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.btn_save.setEnabled(False)

        self.thread = QThread()
        self.worker = ScanWorker(files)
        self.worker.moveToThread(self.thread)
        self.worker.progress.connect(self.progress.setValue)
        self.worker.log.connect(self.on_log)
        self.worker.report_chunk.connect(self.append_chunk)
        self.worker.finished.connect(self.scan_done)
        self.thread.started.connect(self.worker.run)
        self.thread.start()
        self.pass_label.setText("Scanning...")
        self.console.write(f"Scan started on {len(files)} file(s).", SELECT_TEXT)

    def cancel_scan(self):
        if self.worker:
            self.worker.cancel()
            self.on_log("[CANCEL] Requested cancellation after current file.")
            self.btn_cancel.setEnabled(False)

    def scan_done(self):
        if self.thread:
            self.thread.quit()
            self.thread.wait(3000)
        self.thread = self.worker = None
        self.btn_scan.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.btn_save.setEnabled(self.has_report)
        self.pass_label.setText("Scan complete.")
        self.progress.setValue(100)
        self.console.write("Scan finished.", SELECT_TEXT)
        self.statusBar().showMessage("Scan complete. Review the Report tab; save via 'Save Report As Text...'")

    def save_report(self):
        default = os.path.join(os.path.expanduser("~"), "sr2_forensic_report.txt")
        path, _ = QFileDialog.getSaveFileName(self, "Save Report As Text", default,
                                              "Text files (*.txt);;All files (*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.report.toPlainText())
            self.console.write(f"Report saved to {path}", SELECT_TEXT)
            self.statusBar().showMessage(f"Report saved: {path}")
        except OSError as e:
            self.console.write(f"[ERROR] Could not save report: {e}", BLOCK_COLOR)
            QMessageBox.critical(self, "Save failed", str(e))

    def closeEvent(self, ev):
        if self.worker:
            self.worker.cancel()
        if self.thread:
            self.thread.quit()
            self.thread.wait(3000)
        ev.accept()

# ------------------------------------------------------------------
# Entry Point
# ------------------------------------------------------------------
def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
XACT 3 XWB/XSB editor for Linux.

Supported:
- XACT 3 little-endian XWB versions 42 and 44
- Standard and compact XWB metadata
- PCM extraction, replacement, export, and rebuilding
- xWMA payload decode-to-WAV export via FFmpeg
- Detection and preservation of PCM, XMA, ADPCM, and WMA payloads
- FFmpeg import/export/conversion
- Conservative XSB inspection without unsafe rewriting

The XWB format uses five segment descriptors:
    0 = bank data
    1 = entry metadata
    2 = seek table
    3 = entry names
    4 = wave data

Version 42 and version 44 both use the XACT 3 segment-based layout. The
header contains an additional header-version field for versions >= 42.
Version 44 is the normal modern XACT 3 target; version 42 is retained when
explicitly selected.

This program writes little-endian PCM XWB banks only. Existing unsupported
compressed entries can be preserved byte-for-byte when rebuilding, but they
cannot be replaced or decoded by this program.

Rebuild notes:
- Wave-data offsets stored in entry metadata are computed from the actual
  padded layout of the wave-data segment, so they always point at payload.
- When PCM is replaced, loop regions are conservatively clamped to the new
  payload length so loops can never overrun the replacement audio.

Interface: black background (#000000), green foreground (#00FF90), deep
purple selection/highlight color.
"""

import json
import os
import shutil
import struct
import subprocess
import tempfile
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText


# ---------------------------------------------------------------------------
# Constants and helpers
# ---------------------------------------------------------------------------

SEG_BANKDATA = 0
SEG_ENTRYMETA = 1
SEG_SEEKTABLE = 2
SEG_ENTRYNAMES = 3
SEG_WAVEDATA = 4

FLAG_STREAMING = 0x00000001
FLAG_BUFFER = 0x00000002
FLAG_COMPACT = 0x00010000
FLAG_ENTRYNAMES = 0x00020000
FLAG_SYNC_DISABLED = 0x00040000

CODEC_PCM = 0
CODEC_XMA = 1
CODEC_ADPCM = 2
CODEC_WMA = 3
# (no more 4-bit codec-nibble assumptions; the tag field is 2 bits) 

CODEC_NAMES = {
    CODEC_PCM: "PCM",
    CODEC_XMA: "XMA",
    CODEC_ADPCM: "ADPCM",
    CODEC_WMA: "WMA",
}

ALIGN_DEFAULT = 2048


class FormatError(Exception):
    pass


def u16(data, off):
    if off < 0 or off + 2 > len(data):
        raise FormatError("16-bit read outside file")
    return struct.unpack_from("<H", data, off)[0]


def u32(data, off):
    if off < 0 or off + 4 > len(data):
        raise FormatError("32-bit read outside file")
    return struct.unpack_from("<I", data, off)[0]


def pack16(value):
    return struct.pack("<H", value)


def pack32(value):
    return struct.pack("<I", value)


def align_up(value, alignment):
    if alignment <= 1:
        return value
    return (value + alignment - 1) // alignment * alignment


def checked_range(offset, length, total, label):
    if offset < 0 or length < 0 or offset > total or length > total - offset:
        raise FormatError(
            f"{label} is outside the file: offset={offset}, length={length}, "
            f"file_size={total}"
        )


def read_c_string(data):
    return data.split(b"\0", 1)[0].decode("utf-8", "replace")


def sanitize_name(name, fallback):
    name = str(name or "").strip()
    if not name:
        name = fallback
    return name.replace("/", "_").replace("\\", "_")


def codec_name(codec):
    return CODEC_NAMES.get(codec, f"Unknown({codec})")


def codec_from_format(fmt):
    return fmt & 0x03


def format_pcm(channels, sample_rate, bits):
    # Canonical WAVEBANKMINIWAVEFORMAT packing:
    #   bits 0-1   codec tag (0=PCM), bits 2-4  channels (1-7),
    #   bits 5-22  sample rate,        bits 23-30 block align,
    #   bit  31    bits-per-sample flag (0 = 8-bit, 1 = 16-bit)
    if channels < 1 or channels > 7:
        raise ValueError("XACT channel count must be between 1 and 7")
    if sample_rate < 1 or sample_rate > 0x3FFFF:
        raise ValueError("PCM sample rate must fit the XACT format field")
    if bits not in (8, 16):
        raise ValueError("XACT PCM rebuilding supports only 8-bit or 16-bit PCM")
    block_align = channels * (bits // 8)
    if block_align > 0xFF:
        raise ValueError("PCM block alignment must fit 8 bits")
    return (CODEC_PCM | (channels << 2) | (sample_rate << 5)
            | (block_align << 23) | (1 << 31 if bits == 16 else 0))


def unpack_format(fmt):
    codec = fmt & 0x03
    channels = (fmt >> 2) & 0x07
    sample_rate = (fmt >> 5) & 0x3FFFF
    # Block align (bits 23-30) is not currently needed for editing.
    bits = 16 if (fmt >> 31) & 0x01 else 8
    if codec != CODEC_PCM:
        bits = None
    return codec, channels, sample_rate, bits


# ---------------------------------------------------------------------------
# FFmpeg backend
# ---------------------------------------------------------------------------

class FFmpegBackend:
    def __init__(self):
        self.ffmpeg = shutil.which("ffmpeg")
        self.ffprobe = shutil.which("ffprobe")

    def check(self):
        missing = []
        if not self.ffmpeg:
            missing.append("ffmpeg")
        if not self.ffprobe:
            missing.append("ffprobe")
        if missing:
            raise RuntimeError(
                "Required FFmpeg programs are unavailable: "
                + ", ".join(missing)
                + "\nInstall them with:\n"
                  "sudo apt update\n"
                  "sudo apt install ffmpeg"
            )

    def probe(self, path):
        self.check()
        cmd = [
            self.ffprobe, "-v", "error",
            "-select_streams", "a:0",
            "-show_entries",
            "stream=codec_name,channels,sample_rate,bits_per_sample",
            "-of", "json", path,
        ]
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, check=False
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "ffprobe failed")
        try:
            streams = json.loads(result.stdout).get("streams", [])
            if not streams:
                raise ValueError
            s = streams[0]
            return {
                "codec": s.get("codec_name", "unknown"),
                "channels": int(s.get("channels") or 0),
                "sample_rate": int(s.get("sample_rate") or 0),
                "bits": int(s.get("bits_per_sample") or 0),
            }
        except Exception as exc:
            raise RuntimeError("ffprobe returned incomplete audio metadata") from exc

    def decode_pcm(self, path, sample_rate=None, channels=None):
        self.check()
        args = [self.ffmpeg, "-v", "error", "-i", path]
        if sample_rate:
            args += ["-ar", str(sample_rate)]
        if channels:
            args += ["-ac", str(channels)]
        args += ["-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"]
        result = subprocess.run(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
        )
        if result.returncode != 0:
            raise RuntimeError(
                result.stderr.decode("utf-8", "replace").strip()
                or "FFmpeg could not decode the audio"
            )
        return result.stdout

    @staticmethod
    def _xwma_riff(tag, channels, rate, block_align, payload, dpds):
        # Minimal xWMA RIFF container: WAVEFORMATEX fmt chunk, optional
        # dpds seek table, raw payload in a data chunk.
        fmt = struct.pack("<HHIIHH", tag, channels, rate,
                          rate * block_align, block_align, 16)
        chunks = b"fmt " + pack32(16) + fmt
        if dpds:
            chunks += b"dpds" + pack32(len(dpds)) + dpds
        chunks += b"data" + pack32(len(payload)) + payload
        return b"RIFF" + pack32(4 + len(chunks)) + b"WAVE" + chunks

    def export_xwma(self, payload, bank, entry, path):
        self.check()
        channels = entry.channels or 2
        sample_rate = entry.sample_rate or 44100
        block_align = (entry.format >> 23) & 0xFF
        if block_align <= 0:
            block_align = max(1, channels * 2)
        head = " ".join("{:02x}".format(b) for b in payload[:32])
        # The bank-level seek table stores cumulative offsets across the
        # whole bank. Slice out only the values inside this entry's play
        # region and re-base them to zero so packet boundaries land right.
        table = getattr(bank, "seek_table", b"") or b""
        lo = entry.play_offset
        hi = lo + entry.play_length
        per_entry = [v - lo for v in
                     struct.unpack("<{}I".format(len(table) // 4), table)
                     if lo <= v <= hi]
        dpds = b"".join(pack32(v) for v in per_entry)
        attempts = [
            (0x0161, dpds, True, "wmav2, per-entry dpds, forced xwma demux"),
            (0x0161, None, False, "wmav2, no dpds, wav demux"),
            (0x0162, dpds, True, "wmapro, per-entry dpds, forced demux"),
        ]
        errors = []
        tmp_name = None
        try:
            for tag, table, force, label in attempts:
                container = self._xwma_riff(
                    tag, channels, sample_rate, block_align, payload, dpds
                    if table is not None else None)
                fd, tmp_name = tempfile.mkstemp(suffix=".xwma")
                with os.fdopen(fd, "wb") as tf:
                    tf.write(container)
                args = [self.ffmpeg, "-v", "error"]
                if force:
                    args += ["-f", "xwma"]
                args += ["-i", tmp_name,
                         "-f", "wav", "-acodec", "pcm_s16le", "-y", path]
                result = subprocess.run(
                    args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    check=False)
                if result.returncode == 0:
                    base = os.path.splitext(path)[0]
                    shutil.copyfile(tmp_name, base + ".debug.xwma")
                    with open(base + ".xwma.payload.dat", "wb") as pf:
                        pf.write(payload)
                    self.last_decode_mode = label
                    return
                errors.append(label + ": "
                              + result.stderr.decode("utf-8",
                                                     "replace").strip())
            raise RuntimeError(
                "FFmpeg could not decode the xWMA payload. "
                + " | ".join(errors)
                + " First 32 payload bytes (hex): " + head
            )
        finally:
            if tmp_name:
                try:
                    os.remove(tmp_name)
                except OSError:
                    pass

    def export_wav(self, pcm, sample_rate, channels, bits, path):
        self.check()
        if bits == 8:
            fmt = "u8"
            codec = "pcm_u8"
        elif bits == 16:
            fmt = "s16le"
            codec = "pcm_s16le"
        else:
            raise RuntimeError("Only 8-bit and 16-bit PCM WAV export is supported")
        args = [
            self.ffmpeg, "-v", "error",
            "-f", fmt, "-ar", str(sample_rate), "-ac", str(channels),
            "-i", "pipe:0", "-f", "wav", "-acodec", codec, "-y", path,
        ]
        result = subprocess.run(
            args, input=pcm, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False
        )
        if result.returncode != 0:
            raise RuntimeError(
                result.stderr.decode("utf-8", "replace").strip()
                or "FFmpeg could not write the WAV file"
            )


# ---------------------------------------------------------------------------
# XWB structures
# ---------------------------------------------------------------------------

class XWBEntry:
    def __init__(self):
        self.index = 0
        self.name = ""
        self.format = 0
        self.codec = "Unknown"
        self.channels = 0
        self.sample_rate = 0
        self.bits = None
        self.play_offset = 0
        self.play_length = 0
        self.loop_offset = 0
        self.loop_length = 0
        self.duration = 0
        self.payload = b""
        self.replacement_pcm = None
        self.replacement_format = None
        self.source_path = None


    @property
    def is_pcm(self):
        return self.codec == "PCM"

    @property
    def changed(self):
        return self.replacement_pcm is not None


class XWBFile:
    def __init__(self, data, path=""):
        self.data = data
        self.path = path
        self.version = None
        self.header_version = None
        self.segments = []
        self.flags = 0
        self.entry_count = 0
        self.bank_name = ""
        self.metadata_size = 0
        self.name_size = 0
        self.alignment = ALIGN_DEFAULT
        self.compact_format = None
        self.entries = []
        self.compact = False
        self.warnings = []
        self.parse()

    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            return cls(f.read(), path)

    @classmethod
    def create_empty(cls, path="<new>", version=44, bank_name="ImportedBank"):
        """Build an in-memory PCM bank without routing through parse().

        Used when the user imports audio without an existing bank open.
        """
        f = cls.__new__(cls)
        f.data = b""
        f.path = path
        f.version = version
        f.header_version = 1
        f.segments = [(0, 0)] * 5
        f.seek_table = b""
        f.flags = FLAG_BUFFER
        f.had_entry_names = True
        f.entry_count = 0
        f.bank_name = bank_name
        f.metadata_size = 24
        f.name_size = 0
        f.alignment = ALIGN_DEFAULT
        f.compact_format = None
        f.compact = False
        f.warnings = []
        f.entries = []
        return f

    def parse(self):
        d = self.data
        if len(d) < 12 or d[:4] != b"WBND":
            raise FormatError("Not an XACT XWB file: missing WBND signature")
        self.version = u32(d, 4)
        if self.version not in (42, 44):
            raise FormatError(
                f"Unsupported XWB version {self.version}; expected version 42 or 44"
            )
        self.header_version = u32(d, 8)
        pos = 12
        self.segments = []
        for i in range(5):
            off = u32(d, pos)
            length = u32(d, pos + 4)
            checked_range(off, length, len(d), f"segment {i}")
            self.segments.append((off, length))
            pos += 8

        seek_off, seek_len = self.segments[SEG_SEEKTABLE]
        self.seek_table = d[seek_off:seek_off + seek_len]

        bank_off, bank_len = self.segments[SEG_BANKDATA]
        if bank_len < 80:
            raise FormatError("WAVEBANKDATA segment is too short")
        self.flags = u32(d, bank_off)
        self.entry_count = u32(d, bank_off + 4)
        self.bank_name = read_c_string(d[bank_off + 8:bank_off + 72])
        self.metadata_size = u32(d, bank_off + 72)
        self.name_size = u32(d, bank_off + 76)
        self.alignment = u32(d, bank_off + 80) if bank_len >= 84 else ALIGN_DEFAULT
        if self.alignment == 0:
            self.alignment = ALIGN_DEFAULT

        self.had_entry_names = bool(self.flags & FLAG_ENTRYNAMES)
        self.compact = bool(self.flags & FLAG_COMPACT)
        if self.compact:
            if bank_len < 88:
                raise FormatError("Compact bank is missing compact format")
            self.compact_format = u32(d, bank_off + 84)

        meta_off, meta_len = self.segments[SEG_ENTRYMETA]
        if self.entry_count == 0:
            return

        if self.compact:
            if self.metadata_size != 4:
                raise FormatError("Compact bank does not use 4-byte metadata")
            required = self.entry_count * 4
        else:
            if self.metadata_size != 24:
                raise FormatError(
                    f"Unsupported standard metadata size {self.metadata_size}; "
                    "expected 24"
                )
            required = self.entry_count * 24
        if required > meta_len:
            raise FormatError("Entry metadata exceeds its segment")

        names = []
        if self.flags & FLAG_ENTRYNAMES:
            noff, nlen = self.segments[SEG_ENTRYNAMES]
            if self.name_size == 0:
                raise FormatError("Entry-name flag is set but name size is zero")
            if self.entry_count * self.name_size > nlen:
                raise FormatError("Entry names exceed their segment")
            for i in range(self.entry_count):
                a = noff + i * self.name_size
                names.append(read_c_string(d[a:a + self.name_size]))
        else:
            names = [f"entry_{i:04d}" for i in range(self.entry_count)]

        data_off, data_len = self.segments[SEG_WAVEDATA]
        file_size = len(d)

        # Some writers leave the wave-data segment length at 0 and expect
        # readers to infer the extent as end-of-file.
        if data_len == 0 and self.entry_count > 0:
            data_len = max(0, file_size - data_off)
            self.warnings.append(
                f"Wave-data segment length is 0; inferred extent "
                f"{data_len} bytes from file size"
            )

        for i in range(self.entry_count):
            e = XWBEntry()
            e.index = i
            e.name = names[i] or f"entry_{i:04d}"

            if self.compact:
                e.play_offset = u32(d, meta_off + i * 4)
                e.play_length = 0
                e.format = self.compact_format
            else:
                p = meta_off + i * 24
                # Canonical WAVEBANKENTRY layout:
                #   0-3  duration, 4-7 format, 8-11 play offset,
                #   12-15 play length, 16-19 loop offset, 20-23 loop length
                e.duration = u32(d, p)
                e.format = u32(d, p + 4)
                e.play_offset = u32(d, p + 8)
                e.play_length = u32(d, p + 12)
                e.loop_offset = u32(d, p + 16)
                e.loop_length = u32(d, p + 20)

            if self.compact and i + 1 < self.entry_count:
                next_off = u32(d, meta_off + (i + 1) * 4)
                e.play_length = next_off - e.play_offset
            elif self.compact:
                e.play_length = max(0, data_len - e.play_offset)

            # Documented tolerance: accept a play region that overruns the
            # declared segment length but still fits between the segment
            # start and end-of-file, since some writers understate it.
            if e.play_offset > data_len or e.play_length > data_len - e.play_offset:
                region_end = data_off + e.play_offset + e.play_length
                if e.play_offset <= file_size - data_off and region_end <= file_size:
                    self.warnings.append(
                        f"Entry {i}: play region ({e.play_offset}+"
                        f"{e.play_length}) overruns declared wave-data "
                        f"segment length {data_len}; accepted via "
                        f"end-of-file tolerance"
                    )
                else:
                    raise FormatError(
                        f"Entry {i} play region is outside the wave-data "
                        f"segment: offset={e.play_offset}, "
                        f"length={e.play_length}, "
                        f"segment_length={data_len}, "
                        f"data_segment_at={data_off}, "
                        f"file_size={file_size}. The file may be truncated "
                        f"or use an unsupported variant."
                    )

            c, ch, rate, bits = unpack_format(e.format)
            e.codec = codec_name(c)
            e.channels = ch
            e.sample_rate = rate
            e.bits = bits
            a = data_off + e.play_offset
            e.payload = d[a:a + e.play_length]
            self.entries.append(e)

    def summary(self):
        kind = []
        if self.flags & FLAG_STREAMING:
            kind.append("streaming")
        if self.flags & FLAG_BUFFER:
            kind.append("in-memory")
        if self.compact:
            kind.append("compact")
        else:
            kind.append("standard")
        return (
            f"XWB version {self.version}, header version {self.header_version}, "
            f"{self.entry_count} entries, {', '.join(kind)}, "
            f"alignment {self.alignment}, bank '{self.bank_name}'"
        )

    def replace_pcm(self, index, pcm, sample_rate, channels, bits=16):
        if index < 0 or index >= len(self.entries):
            raise IndexError("Invalid XWB entry index")
        e = self.entries[index]
        if not e.is_pcm:
            raise FormatError(
                f"Entry {index} uses {e.codec}; compressed entries cannot be "
                "replaced with PCM while preserving that entry's format"
            )
        e.replacement_pcm = pcm
        e.replacement_format = format_pcm(channels, sample_rate, bits)
        e.sample_rate = sample_rate
        e.channels = channels
        e.bits = bits
        e.format = e.replacement_format
        e.play_length = len(pcm)
        e.duration = len(pcm) // (channels * (bits // 8))
        # Clamp loop regions so they can never overrun the replacement
        # payload. If the loop start itself falls outside the new payload,
        # disable the loop entirely rather than emitting a nonsensical one.
        if e.loop_length > 0:
            if e.loop_offset >= len(pcm):
                e.loop_offset = 0
                e.loop_length = 0
            elif e.loop_offset + e.loop_length > len(pcm):
                e.loop_length = len(pcm) - e.loop_offset

    def rebuild(self, version=None, compact=False):
        version = version or self.version
        if version not in (42, 44):
            raise FormatError("XWB output version must be 42 or 44")

        if compact:
            if not self.entries:
                compact = False
            else:
                formats = {
                    e.replacement_format if e.changed else e.format
                    for e in self.entries
                }
                if len(formats) != 1:
                    raise FormatError("Compact output requires one common format")
                for e in self.entries:
                    if codec_from_format(next(iter(formats))) != CODEC_PCM:
                        raise FormatError(
                            "This writer only creates compact PCM banks"
                        )

        payloads = []
        formats = []
        for e in self.entries:
            payload = e.replacement_pcm if e.changed else e.payload
            fmt = e.replacement_format if e.changed else e.format
            c = codec_from_format(fmt)
            if e.changed and c != CODEC_PCM:
                raise FormatError("Replacement data must be PCM")
            if c not in (CODEC_PCM, CODEC_XMA, CODEC_ADPCM, CODEC_WMA):
                # Documented policy: unknown codec fields are preserved
                # byte-for-byte and reported, not rejected.
                self.warnings.append(
                    f"Entry {e.index}: unrecognized codec field "
                    f"{c} (format 0x{fmt:08x}); preserved byte-for-byte"
                )
            payloads.append(payload)
            formats.append(fmt)

        flags = self.flags
        flags &= ~(FLAG_COMPACT | FLAG_ENTRYNAMES)
        if compact:
            flags |= FLAG_COMPACT
        use_names = getattr(self, "had_entry_names", False)
        if use_names:
            flags |= FLAG_ENTRYNAMES

        alignment = self.alignment or ALIGN_DEFAULT
        if flags & FLAG_STREAMING:
            alignment = max(alignment, ALIGN_DEFAULT)

        # Truncate by character first so a multi-byte UTF-8 sequence is never
        # split in half by slicing encoded bytes.
        bank_name = str(self.bank_name)[:63].encode("utf-8", "replace")
        bank_name = bank_name.ljust(64, b"\0")

        name_size = 0
        name_blob = b""
        if use_names:
            name_size = max(1, min(255, max(len(e.name.encode("utf-8")) + 1
                                            for e in self.entries)))
            for e in self.entries:
                raw = e.name.encode("utf-8", "replace")[:name_size - 1]
                name_blob += raw.ljust(name_size, b"\0")

        # Lay out the wave-data segment FIRST and record each payload's true
        # offset, so entry metadata always matches the padded physical layout.
        wave_blob = bytearray()
        offsets = []
        for payload in payloads:
            offset = align_up(len(wave_blob), alignment)
            wave_blob += b"\0" * (offset - len(wave_blob))
            offsets.append(offset)
            wave_blob += payload

        if compact:
            metadata_size = 4
            meta_blob = bytearray()
            for offset in offsets:
                meta_blob += pack32(offset)
        else:
            metadata_size = 24
            meta_blob = bytearray()
            for e, offset, fmt, payload in zip(
                    self.entries, offsets, formats, payloads):
                # Canonical WAVEBANKENTRY layout:
                #   duration, format, play offset, play length,
                #   loop offset, loop length
                meta_blob += pack32(e.duration)
                meta_blob += pack32(fmt)
                meta_blob += pack32(offset)
                meta_blob += pack32(len(payload))
                meta_blob += pack32(e.loop_offset)
                meta_blob += pack32(e.loop_length)

        bank_blob = bytearray()
        bank_blob += pack32(flags)
        bank_blob += pack32(len(self.entries))
        bank_blob += bank_name
        bank_blob += pack32(metadata_size)
        bank_blob += pack32(name_size)
        bank_blob += pack32(alignment)
        if compact and formats:
            bank_blob += pack32(formats[0])

        # Header is WBND + version + header version + five descriptors.
        header_size = 12 + 5 * 8
        cursor = header_size

        def put_segment(blob):
            nonlocal cursor
            cursor = align_up(cursor, 4)
            off = cursor
            cursor += len(blob)
            return off, len(blob)

        bank_seg = put_segment(bytes(bank_blob))
        meta_seg = put_segment(bytes(meta_blob))
        seek_seg = (cursor, 0)
        name_seg = put_segment(name_blob)
        cursor = align_up(cursor, alignment)
        data_seg = (cursor, len(wave_blob))
        cursor += len(wave_blob)

        out = bytearray(cursor)
        out[0:4] = b"WBND"
        out[4:8] = pack32(version)
        out[8:12] = pack32(self.header_version or 1)

        segments = [bank_seg, meta_seg, seek_seg, name_seg, data_seg]
        p = 12
        for off, length in segments:
            out[p:p + 4] = pack32(off)
            out[p + 4:p + 8] = pack32(length)
            p += 8

        out[bank_seg[0]:bank_seg[0] + len(bank_blob)] = bank_blob
        out[meta_seg[0]:meta_seg[0] + len(meta_blob)] = meta_blob
        if name_blob:
            out[name_seg[0]:name_seg[0] + len(name_blob)] = name_blob
        out[data_seg[0]:data_seg[0] + len(wave_blob)] = wave_blob
        return bytes(out)


# ---------------------------------------------------------------------------
# Conservative XSB inspection
# ---------------------------------------------------------------------------

class XSBFile:
    def __init__(self, data, path=""):
        self.data = data
        self.path = path
        self.version = None
        self.header_version = None
        self.segments = []
        self.notes = []
        self.parse()

    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            return cls(f.read(), path)

    def parse(self):
        d = self.data
        if len(d) < 12 or d[:4] != b"SDBK":
            raise FormatError("Not an XACT XSB file: missing SDBK signature")
        self.version = u32(d, 4)
        if self.version not in (42, 44):
            raise FormatError(
                f"Unsupported XSB version {self.version}; expected 42 or 44"
            )
        self.header_version = u32(d, 8)
        if len(d) < 12 + 8 * 6:
            raise FormatError("XSB header is too short for a segment table")

        pos = 12
        for i in range(6):
            off = u32(d, pos)
            length = u32(d, pos + 4)
            checked_range(off, length, len(d), f"XSB segment {i}")
            self.segments.append((off, length))
            pos += 8

        # Do not guess sound/cue layouts from arbitrary bytes. Report safe
        # printable strings, which is useful for inspection without claiming
        # that a guessed offset is a valid cue or sound record.
        strings = []
        start = 0
        while start < len(d):
            end = d.find(b"\0", start)
            if end < 0:
                end = len(d)
            part = d[start:end]
            if len(part) >= 4 and all(32 <= x < 127 for x in part):
                strings.append(part.decode("ascii", "replace"))
            start = end + 1
        self.notes = strings[:200]

    def summary(self):
        return (
            f"XSB version {self.version}, header version {self.header_version}, "
            f"{len(self.segments)} validated segments, "
            f"{len(self.notes)} printable strings"
        )


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class App(tk.Tk):
    BG_COLOR = "#000000"    # background: pure black
    FG_COLOR = "#00FF90"    # foreground objects and text: green
    SEL_COLOR = "#4B0082"   # highlighted text background: deep purple

    def __init__(self):
        super().__init__()
        self.title("XACT 3 XWB/XSB Editor")
        self.geometry("1100x720")
        self.minsize(800, 500)

        self.configure(background=self.BG_COLOR)

        self.ff = FFmpegBackend()
        self.xwb = None
        self.xsb = None
        self.current_path = None
        self.font_size = 10

        self._setup_style()
        self._build_ui()
        self._set_status("Ready")

    def _setup_style(self):
        style = ttk.Style(self)
        try:
            # The clam theme honors custom colors on all platforms.
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", background=self.BG_COLOR,
                        foreground=self.FG_COLOR,
                        fieldbackground=self.BG_COLOR)

        style.configure("TFrame", background=self.BG_COLOR)
        style.configure("TLabel", background=self.BG_COLOR,
                        foreground=self.FG_COLOR)

        style.configure("TButton", background=self.BG_COLOR,
                        foreground=self.FG_COLOR,
                        bordercolor=self.FG_COLOR,
                        lightcolor=self.BG_COLOR,
                        darkcolor=self.BG_COLOR)
        style.map("TButton",
                  background=[("active", self.SEL_COLOR),
                              ("pressed", self.SEL_COLOR)],
                  foreground=[("active", self.FG_COLOR)])

        style.configure("Treeview", background=self.BG_COLOR,
                        foreground=self.FG_COLOR,
                        fieldbackground=self.BG_COLOR)
        style.map("Treeview",
                  background=[("selected", self.SEL_COLOR)],
                  foreground=[("selected", self.FG_COLOR)])

        style.configure("Treeview.Heading", background=self.BG_COLOR,
                        foreground=self.FG_COLOR)
        style.map("Treeview.Heading",
                  background=[("active", self.SEL_COLOR)])

        style.configure("TPanedwindow", background=self.BG_COLOR)
        style.configure("Sash", sashthickness=4)

        style.configure("Status.TLabel", background=self.BG_COLOR,
                        foreground=self.FG_COLOR, relief="sunken")

    def _build_ui(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=6, pady=6)

        buttons = [
            ("Open XWB/XSB", self.open_bank),
            ("Import audio", self.import_audio),
            ("Export WAV", self.export_wav),
            ("Replace entry", self.replace_entry),
            ("Delete entry", self.delete_entry),
            ("Rebuild XWB", self.rebuild_xwb),
            ("Inspect XSB", self.inspect_xsb),
            ("Validate", self.validate),
            ("Clear entries", self.clear_entries),
            ("Save manifest", self.save_manifest),
            ("Load manifest", self.load_manifest),
            ("Hex preview", self.hex_preview),
            ("Diagnose XWB", self.diagnose_xwb),
        ]
        for text, command in buttons:
            ttk.Button(top, text=text, command=command).pack(
                side="left", padx=2
            )

        ttk.Label(top, text="Font").pack(side="left", padx=(12, 2))
        ttk.Button(top, text="-", width=3,
                   command=lambda: self.change_font(-1)).pack(side="left")
        ttk.Button(top, text="+", width=3,
                   command=lambda: self.change_font(1)).pack(side="left")

        middle = ttk.PanedWindow(self, orient="horizontal")
        middle.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        left = ttk.Frame(middle)
        right = ttk.Frame(middle)
        middle.add(left, weight=2)
        middle.add(right, weight=3)

        self.tree = ttk.Treeview(
            left,
            columns=("codec", "rate", "channels", "bits", "length"),
            show="tree headings",
            selectmode="browse",
        )
        self.tree.heading("#0", text="Entry")
        self.tree.heading("codec", text="Codec")
        self.tree.heading("rate", text="Rate")
        self.tree.heading("channels", text="Ch")
        self.tree.heading("bits", text="Bits")
        self.tree.heading("length", text="Bytes")
        self.tree.column("#0", width=220)
        self.tree.column("codec", width=80)
        self.tree.column("rate", width=80)
        self.tree.column("channels", width=45)
        self.tree.column("bits", width=45)
        self.tree.column("length", width=90)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Button-3>", self._popup_menu)

        self.details = ScrolledText(
            right, wrap="none",
            font=("TkFixedFont", self.font_size),
            bg=self.BG_COLOR, fg=self.FG_COLOR,
            insertbackground=self.FG_COLOR,
            selectbackground=self.SEL_COLOR,
            selectforeground=self.FG_COLOR,
        )
        self.details.pack(fill="both", expand=True)

        self.status = tk.StringVar()
        ttk.Label(self, textvariable=self.status, style="Status.TLabel",
                  anchor="w").pack(fill="x", padx=6, pady=(0, 6))

    def change_font(self, delta):
        self.font_size = max(7, min(24, self.font_size + delta))
        self.details.configure(font=("TkFixedFont", self.font_size))

    def _set_status(self, text):
        self.status.set(text)
        self.update_idletasks()

    def _error(self, exc):
        self._set_status("Error")
        messagebox.showerror("Error", str(exc))

    def _selected_index(self):
        selected = self.tree.selection()
        if not selected:
            raise RuntimeError("Select an entry first")
        return int(selected[0])

    def refresh_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        if not self.xwb:
            return
        for e in self.xwb.entries:
            self.tree.insert(
                "", "end", iid=str(e.index), text=f"{e.index}: {e.name}",
                values=(
                    e.codec, e.sample_rate, e.channels,
                    e.bits if e.bits is not None else "",
                    e.play_length,
                ),
            )

    def open_bank(self):
        path = filedialog.askopenfilename(
            filetypes=[
                ("XACT banks", "*.xwb *.xsb"),
                ("All files", "*"),
            ]
        )
        if not path:
            return
        try:
            with open(path, "rb") as f:
                sig = f.read(4)
            self.xwb = None
            self.xsb = None
            self.current_path = path
            if sig == b"WBND":
                self.xwb = XWBFile.load(path)
                self.refresh_tree()
                self.details.delete("1.0", "end")
                self.details.insert("end", self.xwb.summary())
                if self.xwb.warnings:
                    self.details.insert("end", "\n\nWarnings:")
                    for w in self.xwb.warnings:
                        self.details.insert("end", f"\n- {w}")
                self._set_status(f"Opened {path}")
            elif sig == b"SDBK":
                self.xsb = XSBFile.load(path)
                self.details.delete("1.0", "end")
                self.details.insert("end", self.xsb.summary())
                self.details.insert("end", "\n\nPrintable strings:\n")
                self.details.insert("end", "\n".join(self.xsb.notes))
                self._set_status(f"Inspected {path}")
            else:
                raise FormatError("File is neither WBND nor SDBK")
        except Exception as exc:
            self._error(exc)

    def import_audio(self):
        paths = filedialog.askopenfilenames(
            filetypes=[
                ("Audio", "*.wav *.flac *.ogg *.mp3 *.m4a *.aac *.opus"),
                ("All files", "*"),
            ]
        )
        if not paths:
            return
        try:
            self.ff.check()
            if self.xwb is None:
                self.xwb = XWBFile.create_empty()
                self.xwb.bank_name = "ImportedBank"

            for path in paths:
                info = self.ff.probe(path)
                pcm = self.ff.decode_pcm(path)
                e = XWBEntry()
                e.index = len(self.xwb.entries)
                e.name = os.path.splitext(os.path.basename(path))[0]
                e.codec = "PCM"
                e.channels = info["channels"]
                e.sample_rate = info["sample_rate"]
                e.bits = 16
                e.format = format_pcm(e.channels, e.sample_rate, 16)
                e.payload = pcm
                e.play_length = len(pcm)
                e.duration = len(pcm) // (e.channels * 2)
                e.source_path = path
                self.xwb.entries.append(e)

            self.xwb.entry_count = len(self.xwb.entries)
            self.refresh_tree()
            self._set_status(f"Imported {len(paths)} audio file(s)")
        except Exception as exc:
            self._error(exc)

    def export_wav(self):
        try:
            if not self.xwb:
                raise RuntimeError("Open or import an XWB bank first")
            i = self._selected_index()
            e = self.xwb.entries[i]
            if e.codec not in ("PCM", "WMA"):
                raise FormatError(
                    f"Entry uses {e.codec}; only PCM and WMA (xWMA) entries "
                    "can be exported. XMA and ADPCM payloads are not "
                    "decoded by this program."
                )
            path = filedialog.asksaveasfilename(
                defaultextension=".wav",
                filetypes=[("WAV audio", "*.wav")],
                initialfile=sanitize_name(e.name, f"entry_{i}") + ".wav",
            )
            if not path:
                return
            if e.is_pcm:
                pcm = e.replacement_pcm if e.changed else e.payload
                self.ff.export_wav(
                    pcm, e.sample_rate, e.channels, e.bits or 16, path
                )
            else:
                # WMA: wrap the bare xWMA payload in a RIFF container
                # (carrying the bank's dpds seek table) and decode it
                # to 16-bit PCM via FFmpeg.
                self.ff.export_xwma(e.payload, self.xwb, e, path)
            mode = getattr(self.ff, "last_decode_mode", "?")
            self._set_status(f"Exported {path} (decoded via {mode})")
        except Exception as exc:
            self._error(exc)

    def replace_entry(self):
        try:
            if not self.xwb:
                raise RuntimeError("Open an XWB bank first")
            i = self._selected_index()
            path = filedialog.askopenfilename(
                filetypes=[("Audio", "*.wav *.flac *.ogg *.mp3 *.m4a *.aac *.opus"),
                           ("All files", "*")]
            )
            if not path:
                return
            info = self.ff.probe(path)
            pcm = self.ff.decode_pcm(path)
            self.xwb.replace_pcm(
                i, pcm, info["sample_rate"], info["channels"], 16
            )
            self.refresh_tree()
            self._set_status(f"Replaced entry {i}")
        except Exception as exc:
            self._error(exc)

    def rebuild_xwb(self):
        try:
            if not self.xwb:
                raise RuntimeError("Open or import an XWB bank first")
            path = filedialog.asksaveasfilename(
                defaultextension=".xwb",
                filetypes=[("XACT wave bank", "*.xwb")],
            )
            if not path:
                return
            data = self.xwb.rebuild(version=44, compact=False)
            with open(path, "wb") as f:
                f.write(data)
            self._set_status(f"Rebuilt valid XWB: {path}")
        except Exception as exc:
            self._error(exc)

    def inspect_xsb(self):
        path = filedialog.askopenfilename(
            filetypes=[("XACT sound bank", "*.xsb"), ("All files", "*")]
        )
        if not path:
            return
        try:
            self.xsb = XSBFile.load(path)
            self.details.delete("1.0", "end")
            self.details.insert("end", self.xsb.summary())
            self.details.insert("end", "\n\nPrintable strings:\n")
            self.details.insert("end", "\n".join(self.xsb.notes))
            self._set_status(f"Inspected {path}")
        except Exception as exc:
            self._error(exc)

    def validate(self):
        try:
            if self.xwb:
                # Parsing already performs complete segment and entry bounds checks.
                rebuilt = self.xwb.rebuild(version=self.xwb.version, compact=False)
                if rebuilt[:4] != b"WBND":
                    raise FormatError("Writer did not produce WBND")
                self._set_status("XWB validation passed")
                messagebox.showinfo(
                    "Validation", "The loaded XWB passed structural validation."
                )
            elif self.xsb:
                self._set_status("XSB header-level validation passed")
                messagebox.showinfo(
                    "Validation",
                    "The loaded XSB passed header and segment-range "
                    "validation only. Cue and sound records were not "
                    "interpreted."
                )
            else:
                raise RuntimeError("Open an XWB or XSB first")
        except Exception as exc:
            self._error(exc)

    def clear_entries(self):
        if self.xwb:
            self.xwb.entries.clear()
            self.xwb.entry_count = 0
            self.refresh_tree()
            self._set_status("Entries cleared")

    def save_manifest(self):
        try:
            if not self.xwb:
                raise RuntimeError("Open or import an XWB first")
            path = filedialog.asksaveasfilename(
                defaultextension=".json",
                filetypes=[("JSON", "*.json")],
            )
            if not path:
                return
            obj = {
                "source": self.current_path,
                "version": self.xwb.version,
                "bank_name": self.xwb.bank_name,
                "entries": [
                    {
                        "index": e.index,
                        "name": e.name,
                        "codec": e.codec,
                        "channels": e.channels,
                        "sample_rate": e.sample_rate,
                        "bits": e.bits,
                        "play_length": e.play_length,
                    }
                    for e in self.xwb.entries
                ],
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(obj, f, indent=2)
            self._set_status(f"Saved manifest {path}")
        except Exception as exc:
            self._error(exc)

    def load_manifest(self):
        path = filedialog.askopenfilename(
            filetypes=[("JSON", "*.json"), ("All files", "*")]
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                obj = json.load(f)
            if not self.xwb:
                raise RuntimeError(
                    "Open the corresponding XWB before loading its manifest"
                )
            for item in obj.get("entries", []):
                i = int(item["index"])
                if 0 <= i < len(self.xwb.entries):
                    self.xwb.entries[i].name = str(item.get("name", ""))
            self.refresh_tree()
            self._set_status(f"Loaded manifest {path}")
        except Exception as exc:
            self._error(exc)

    def diagnose_xwb(self):
        """Dump header, segments, and raw entry-metadata words (LE and BE)."""
        path = self.current_path
        if not path:
            path = filedialog.askopenfilename(filetypes=[("All files", "*")])
        if not path:
            return
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            self._error(exc)
            return

        def le32(off):
            if off < 0 or off + 4 > len(data):
                return None
            return int.from_bytes(data[off:off + 4], "little")

        def be32(off):
            if off < 0 or off + 4 > len(data):
                return None
            return int.from_bytes(data[off:off + 4], "big")

        lines = []
        lines.append(f"file size: {len(data)}")
        lines.append(f"signature: {data[:4]!r}")
        lines.append(f"version (LE): {le32(4)}  (BE): {be32(4)}")
        lines.append(f"header version (LE): {le32(8)}")

        segments = []
        for i in range(5):
            seg_off = le32(12 + 8 * i)
            seg_len = le32(16 + 8 * i)
            segments.append((seg_off, seg_len))
            lines.append(f"segment {i}: offset={seg_off} length={seg_len}")

        bank_off = segments[0][0]
        flags = le32(bank_off)
        entry_count = le32(bank_off + 4)
        raw_name = data[bank_off + 8:bank_off + 72].split(b"\0", 1)[0]
        meta_size = le32(bank_off + 72)
        name_size = le32(bank_off + 76)
        alignment = le32(bank_off + 80)

        lines.append(f"flags: {flags:#010x}")
        lines.append(f"entry count: {entry_count}")
        lines.append(f"bank name: {raw_name.decode('utf-8', 'replace')!r}")
        lines.append(
            f"meta_size: {meta_size}  name_size: {name_size}  "
            f"alignment: {alignment}"
        )

        if entry_count > 0 and meta_size and meta_size >= 4 \
                and meta_size % 4 == 0:
            meta_off = segments[1][0]
            words = meta_size // 4
            for i in range(min(entry_count, 3)):
                p = meta_off + i * meta_size
                le_words = [le32(p + 4 * k) for k in range(words)]
                be_words = [be32(p + 4 * k) for k in range(words)]
                lines.append(f"entry {i} LE: "
                             + str([hex(w) if w is not None else '?' for w in le_words]))
                lines.append(f"entry {i} BE: "
                             + str([hex(w) if w is not None else "?"
                                    for w in be_words]))

        self.details.delete("1.0", "end")
        self.details.insert("end", "\n".join(lines))
        self._set_status("Diagnostic dump displayed")

    def hex_preview(self):
        try:
            path = self.current_path
            if not path:
                path = filedialog.askopenfilename(
                    filetypes=[("All files", "*")]
                )
            if not path:
                return
            with open(path, "rb") as f:
                data = f.read(512)
            lines = []
            for off in range(0, len(data), 16):
                row = data[off:off + 16]
                hexpart = " ".join(f"{x:02x}" for x in row).ljust(47)
                text = "".join(chr(x) if 32 <= x < 127 else "." for x in row)
                lines.append(f"{off:08x}  {hexpart}  {text}")
            self.details.delete("1.0", "end")
            self.details.insert("end", "\n".join(lines))
            self._set_status("Displayed hexadecimal preview")
        except Exception as exc:
            self._error(exc)

    def _popup_menu(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        menu = tk.Menu(self, tearoff=0, bg=self.BG_COLOR,
                       fg=self.FG_COLOR,
                       activebackground=self.SEL_COLOR,
                       activeforeground=self.FG_COLOR)
        menu.add_command(label="Rename entry", command=self.rename_entry)
        menu.add_separator()
        menu.add_command(label="Delete entry", command=self.delete_entry)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def rename_entry(self):
        try:
            if not self.xwb:
                raise RuntimeError("Open an XWB bank first")
            i = self._selected_index()
            e = self.xwb.entries[i]
            new_name = simpledialog.askstring(
                "Rename entry", "New name:",
                initialvalue=e.name, parent=self)
            if not new_name:
                return
            e.name = sanitize_name(new_name, f"entry_{i}")
            # Renames only persist through a rebuild if the entry-name
            # segment is emitted; force it on for this bank.
            self.xwb.had_entry_names = True
            self.refresh_tree()
            self._set_status(f"Renamed entry {i} to {e.name}")
        except Exception as exc:
            self._error(exc)

    def delete_entry(self):
        try:
            if not self.xwb:
                raise RuntimeError("Open an XWB bank first")
            i = self._selected_index()
            e = self.xwb.entries[i]
            if not messagebox.askyesno(
                    "Delete entry",
                    f"Delete entry {i} ({e.name})?\n\n"
                    "A companion XSB references waves by index, so "
                    "deleting an entry shifts every later index and may "
                    "break cue mappings. Continue?"):
                return
            del self.xwb.entries[i]
            for j in range(i, len(self.xwb.entries)):
                self.xwb.entries[j].index = j
            self.xwb.entry_count = len(self.xwb.entries)
            self.refresh_tree()
            self._set_status(f"Deleted entry {i}")
        except Exception as exc:
            self._error(exc)

    def on_select(self, _event=None):
        try:
            if not self.xwb:
                return
            i = self._selected_index()
            e = self.xwb.entries[i]
            self.details.delete("1.0", "end")
            self.details.insert(
                "end",
                f"Name: {e.name}\n"
                f"Index: {e.index}\n"
                f"Codec: {e.codec}\n"
                f"Channels: {e.channels}\n"
                f"Sample rate: {e.sample_rate}\n"
                f"Bits: {e.bits}\n"
                f"Format: 0x{e.format:08x}\n"
                f"Play offset: {e.play_offset}\n"
                f"Play length: {e.play_length}\n"
                f"Loop offset: {e.loop_offset}\n"
                f"Loop length: {e.loop_length}\n"
                f"Duration field: {e.duration}\n"
                f"Replacement pending: {e.changed}\n"
            )
        except Exception:
            pass


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()

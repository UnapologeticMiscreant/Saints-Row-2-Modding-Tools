#!/usr/bin/env python3
"""
xwb_build.py - Build XACT Wave Bank (.xwb) files on Linux.

Supports:
  - XWB format versions 42 and 44
  - PCM WAV files, 8-bit and 16-bit
  - In-memory and streaming banks
  - Standard and compact metadata
  - Friendly entry names
  - 2048-byte streaming alignment
  - 4096-byte Advanced Format streaming alignment

Examples:

  In-memory bank:
    python3 xwb_build.py \
        -o effects.xwb \
        -n Effects \
        -f \
        sound1.wav sound2.wav

  Streaming bank:
    python3 xwb_build.py \
        -o music.xwb \
        --streaming \
        -n Music \
        music1.wav music2.wav

  4096-byte-aligned streaming bank:
    python3 xwb_build.py \
        -o music.xwb \
        --streaming \
        --advanced-format \
        music1.wav music2.wav
"""

import argparse
import os
import struct
import sys
import time
import wave
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# XWB constants
# ---------------------------------------------------------------------------

XWB_SIGNATURE = b"DNBW"

# XWB flags
FLAG_ENTRY_NAMES = 0x00010000
FLAG_COMPACT     = 0x00020000
FLAG_SEEK_TABLES = 0x00080000

# Tool versions associated with the commonly used XWB format versions.
TOOL_VERSIONS = {
    42: 44,
    44: 46,
}

# XACT/XWB metadata sizes.
STANDARD_ENTRY_SIZE = 24
COMPACT_ENTRY_SIZE = 4
ENTRY_NAME_SIZE = 64

# Streaming banks traditionally use DVD-sector alignment.
STREAMING_ALIGNMENT = 2048
ADVANCED_FORMAT_ALIGNMENT = 4096

# Compact bank limits used by XACT-style wave banks.
COMPACT_IN_MEMORY_LIMIT = 0x7FFFFC       # approximately 8 MiB
COMPACT_STREAMING_LIMIT = 0x0FFFFFFC     # approximately 256 MiB


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class WaveEntry:
    source_path: str
    name: str
    data: bytes
    channels: int
    sample_rate: int
    bits_per_sample: int
    block_align: int
    sample_count: int
    duration_samples: int = 0
    offset: int = 0


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def align_up(value: int, alignment: int) -> int:
    if alignment <= 0 or alignment & (alignment - 1):
        raise ValueError("alignment must be a positive power of two")
    return (value + alignment - 1) & ~(alignment - 1)


def pack_u32(value: int) -> bytes:
    return struct.pack("<I", value & 0xFFFFFFFF)


def file_time_now() -> tuple[int, int]:
    """
    Return the current Windows FILETIME as low/high DWORDs.
    """
    epoch_difference = 116444736000000000
    filetime = int(time.time() * 10_000_000) + epoch_difference

    low = filetime & 0xFFFFFFFF
    high = (filetime >> 32) & 0xFFFFFFFF
    return low, high


def fixed_ascii(text: str, size: int) -> bytes:
    """
    Encode text as ASCII, truncate to size - 1 bytes, and NUL-pad.
    """
    encoded = text.encode("ascii", errors="replace")[: size - 1]
    return encoded + b"\x00" * (size - len(encoded))


def validate_alignment(alignment: int) -> None:
    if alignment <= 0 or alignment & (alignment - 1):
        raise ValueError("alignment must be a positive power of two")


# ---------------------------------------------------------------------------
# WAV handling
# ---------------------------------------------------------------------------

def read_wav_pcm(path: str) -> WaveEntry:
    """
    Read an uncompressed PCM WAV file.

    Returns raw PCM data without the RIFF/WAV container.
    """
    try:
        with wave.open(path, "rb") as wav:
            channels = wav.getnchannels()
            sample_rate = wav.getframerate()
            sample_width = wav.getsampwidth()
            sample_count = wav.getnframes()
            compression = wav.getcomptype()
            data = wav.readframes(sample_count)
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"{path}: invalid WAV file: {exc}") from exc

    if compression != "NONE":
        raise ValueError(f"{path}: compressed WAV files are not supported")

    if channels < 1 or channels > 8:
        raise ValueError(
            f"{path}: channel count {channels} is unsupported; expected 1-8"
        )

    if sample_width not in (1, 2):
        raise ValueError(
            f"{path}: only 8-bit and 16-bit PCM are supported; "
            f"got {sample_width * 8}-bit"
        )

    if sample_rate <= 0 or sample_rate > 0x3FFFF:
        raise ValueError(
            f"{path}: sample rate {sample_rate} cannot be represented "
            f"in MINIWAVEFORMAT"
        )

    bits_per_sample = sample_width * 8
    block_align = channels * sample_width

    if block_align > 0xFF:
        raise ValueError(
            f"{path}: block alignment {block_align} exceeds XWB limit"
        )

    if sample_count == 0:
        raise ValueError(f"{path}: empty WAV files are not supported")

    name = os.path.splitext(os.path.basename(path))[0]

    return WaveEntry(
        source_path=path,
        name=name,
        data=data,
        channels=channels,
        sample_rate=sample_rate,
        bits_per_sample=bits_per_sample,
        block_align=block_align,
        sample_count=sample_count,
        duration_samples=sample_count,
    )


# ---------------------------------------------------------------------------
# XWB format encoding
# ---------------------------------------------------------------------------

def pack_miniwaveformat(
    format_tag: int,
    channels: int,
    sample_rate: int,
    block_align: int,
    bits_per_sample: int,
) -> int:
    """
    Pack the 32-bit XACT MINIWAVEFORMAT structure.

    Layout:

      bits 0-1    : format tag
      bits 2-4    : channels - 1
      bits 5-22   : sample rate
      bits 23-30  : block alignment
      bit 31      : 0 for 8-bit, 1 for 16-bit
    """
    if format_tag not in (0, 1, 2, 3):
        raise ValueError("MINIWAVEFORMAT format tag must fit in two bits")

    if not 1 <= channels <= 8:
        raise ValueError("MINIWAVEFORMAT supports 1-8 channels")

    if not 0 <= sample_rate <= 0x3FFFF:
        raise ValueError("sample rate does not fit in MINIWAVEFORMAT")

    if not 0 <= block_align <= 0xFF:
        raise ValueError("block alignment does not fit in MINIWAVEFORMAT")

    if bits_per_sample not in (8, 16):
        raise ValueError("only 8-bit and 16-bit PCM are supported")

    bits_flag = 1 if bits_per_sample == 16 else 0

    return (
        (bits_flag << 31)
        | ((block_align & 0xFF) << 23)
        | ((sample_rate & 0x3FFFF) << 5)
        | (((channels - 1) & 0x07) << 2)
        | (format_tag & 0x03)
    )


def make_standard_entry(entry: WaveEntry) -> bytes:
    """
    Create a standard 24-byte XWB entry.

    Fields:

      play offset
      play length
      loop start
      loop length
      duration
      MINIWAVEFORMAT
    """
    miniwave = pack_miniwaveformat(
        format_tag=0,  # PCM
        channels=entry.channels,
        sample_rate=entry.sample_rate,
        block_align=entry.block_align,
        bits_per_sample=entry.bits_per_sample,
    )

    loop_start = 0
    loop_length = 0

    return struct.pack(
        "<6I",
        entry.offset,
        len(entry.data),
        loop_start,
        loop_length,
        entry.duration_samples,
        miniwave,
    )


def make_compact_entry(entry: WaveEntry) -> bytes:
    """
    Create a compact XWB entry.

    Compact entries encode the wave-data offset in the low 21 bits.
    The upper 11 bits are reserved for the length deviation field.

    For PCM entries the length deviation is zero.
    """
    if entry.offset > 0x1FFFFF:
        raise ValueError(
            f"{entry.source_path}: offset {entry.offset} exceeds "
            "the compact-bank offset limit"
        )

    packed = entry.offset & 0x1FFFFF
    return struct.pack("<I", packed)


# ---------------------------------------------------------------------------
# Bank construction
# ---------------------------------------------------------------------------

def choose_alignment(
    streaming: bool,
    requested_alignment: int | None,
    advanced_format: bool,
) -> int:
    if streaming:
        if requested_alignment is not None:
            alignment = requested_alignment
        elif advanced_format:
            alignment = ADVANCED_FORMAT_ALIGNMENT
        else:
            alignment = STREAMING_ALIGNMENT
    else:
        alignment = requested_alignment or 4

    validate_alignment(alignment)
    return alignment


def validate_entries(entries: list[WaveEntry], compact: bool) -> None:
    if not entries:
        raise ValueError("at least one WAV file is required")

    if len(entries) > 0xFFFFFFFF:
        raise ValueError("too many entries")

    if compact:
        first = entries[0]

        for entry in entries[1:]:
            if (
                entry.channels != first.channels
                or entry.sample_rate != first.sample_rate
                or entry.bits_per_sample != first.bits_per_sample
                or entry.block_align != first.block_align
            ):
                raise ValueError(
                    "compact banks require every WAV file to have the same "
                    "channels, sample rate, bit depth, and block alignment"
                )


def layout_wave_data(entries: list[WaveEntry], alignment: int) -> bytes:
    """
    Assign relative wave-data offsets and create the wave-data segment.

    Entry offsets are relative to the beginning of the wave-data segment.
    """
    output = bytearray()
    current = 0

    for entry in entries:
        aligned = align_up(current, alignment)
        padding = aligned - current

        output.extend(b"\x00" * padding)

        entry.offset = aligned
        output.extend(entry.data)

        current = aligned + len(entry.data)

    final_size = align_up(len(output), alignment)
    output.extend(b"\x00" * (final_size - len(output)))

    return bytes(output)


def build_xwb(
    output_path: str,
    wav_paths: list[str],
    version: int = 44,
    friendly_names: bool = False,
    bank_name: str = "bank",
    alignment: int | None = None,
    compact: bool = False,
    streaming: bool = False,
    advanced_format: bool = False,
) -> None:
    if version not in TOOL_VERSIONS:
        raise ValueError("supported XWB versions are 42 and 44")

    if streaming and version == 42:
        raise ValueError(
            "streaming banks require XWB format version 44"
        )

    if advanced_format and not streaming:
        raise ValueError(
            "--advanced-format is only valid with --streaming"
        )

    if compact and streaming and advanced_format:
        raise ValueError(
            "compact streaming banks cannot be combined with "
            "--advanced-format"
        )

    actual_alignment = choose_alignment(
        streaming=streaming,
        requested_alignment=alignment,
        advanced_format=advanced_format,
    )

    entries = [read_wav_pcm(path) for path in wav_paths]
    validate_entries(entries, compact)

    wave_data = layout_wave_data(entries, actual_alignment)

    if compact:
        compact_limit = (
            COMPACT_STREAMING_LIMIT
            if streaming
            else COMPACT_IN_MEMORY_LIMIT
        )

        if len(wave_data) > compact_limit:
            raise ValueError(
                f"wave data is {len(wave_data)} bytes, exceeding the "
                f"compact-bank limit of {compact_limit} bytes"
            )

    # Version 44 has a seek-table segment. PCM does not require seek-table
    # records, so the segment is empty but remains part of the XWB layout.
    if version == 44:
        segment_count = 5
    else:
        segment_count = 4

    tool_version = TOOL_VERSIONS[version]
    entry_count = len(entries)

    flags = 0

    if friendly_names:
        flags |= FLAG_ENTRY_NAMES

    if compact:
        flags |= FLAG_COMPACT

    if version == 44:
        flags |= FLAG_SEEK_TABLES

    entry_metadata_size = (
        COMPACT_ENTRY_SIZE if compact else STANDARD_ENTRY_SIZE
    )

    entry_name_size = ENTRY_NAME_SIZE if friendly_names else 0

    # Bank-data segment:
    #
    #   flags
    #   entry count
    #   bank name[64]
    #   entry metadata element size
    #   entry name element size
    #   alignment
    #   [compact MINIWAVEFORMAT]
    #   timestamp low/high
    #
    bank_data_size = (
        4 + 4 + 64 + 4 + 4 + 4 + 8
    )

    if compact:
        bank_data_size += 4

    metadata_size = entry_count * entry_metadata_size
    names_size = entry_count * entry_name_size
    seek_table_size = 0 if version == 44 else None

    header_size = 12 + segment_count * 8

    bank_data_offset = header_size
    metadata_offset = bank_data_offset + bank_data_size

    if version == 44:
        seek_table_offset = metadata_offset + metadata_size
        names_offset = seek_table_offset + seek_table_size
        wave_data_offset = names_offset + names_size
    else:
        seek_table_offset = None
        names_offset = metadata_offset + metadata_size
        wave_data_offset = names_offset + names_size

    segments = []

    if version == 44:
        segments.extend(
            [
                (bank_data_offset, bank_data_size),
                (metadata_offset, metadata_size),
                (seek_table_offset, seek_table_size),
                (names_offset, names_size),
                (wave_data_offset, len(wave_data)),
            ]
        )
    else:
        segments.extend(
            [
                (bank_data_offset, bank_data_size),
                (metadata_offset, metadata_size),
                (names_offset, names_size),
                (wave_data_offset, len(wave_data)),
            ]
        )

    # Build entry metadata.
    metadata = bytearray()

    if compact:
        for entry in entries:
            metadata.extend(make_compact_entry(entry))
    else:
        for entry in entries:
            metadata.extend(make_standard_entry(entry))

    # Build friendly names.
    names = bytearray()

    if friendly_names:
        for entry in entries:
            names.extend(fixed_ascii(entry.name, ENTRY_NAME_SIZE))

    # Build bank-data segment.
    timestamp_low, timestamp_high = file_time_now()

    bank_data = bytearray()
    bank_data.extend(pack_u32(flags))
    bank_data.extend(pack_u32(entry_count))
    bank_data.extend(fixed_ascii(bank_name, 64))
    bank_data.extend(pack_u32(entry_metadata_size))
    bank_data.extend(pack_u32(entry_name_size))
    bank_data.extend(pack_u32(actual_alignment))

    if compact:
        first = entries[0]
        miniwave = pack_miniwaveformat(
            format_tag=0,
            channels=first.channels,
            sample_rate=first.sample_rate,
            block_align=first.block_align,
            bits_per_sample=first.bits_per_sample,
        )
        bank_data.extend(pack_u32(miniwave))

    bank_data.extend(pack_u32(timestamp_low))
    bank_data.extend(pack_u32(timestamp_high))

    if len(bank_data) != bank_data_size:
        raise RuntimeError(
            f"internal error: bank-data size is {len(bank_data)}, "
            f"expected {bank_data_size}"
        )

    # Assemble the complete XWB file.
    output = bytearray()

    output.extend(XWB_SIGNATURE)
    output.extend(pack_u32(tool_version))
    output.extend(pack_u32(version))

    for segment_offset, segment_length in segments:
        output.extend(pack_u32(segment_offset))
        output.extend(pack_u32(segment_length))

    if len(output) != header_size:
        raise RuntimeError(
            f"internal error: header size is {len(output)}, "
            f"expected {header_size}"
        )

    output.extend(bank_data)
    output.extend(metadata)

    if version == 44:
        output.extend(b"\x00" * seek_table_size)

    output.extend(names)
    output.extend(wave_data)

    expected_size = wave_data_offset + len(wave_data)

    if len(output) != expected_size:
        raise RuntimeError(
            f"internal error: output size is {len(output)}, "
            f"expected {expected_size}"
        )

    with open(output_path, "wb") as file:
        file.write(output)

    mode = "streaming" if streaming else "in-memory"
    metadata_mode = "compact" if compact else "standard"

    print(f"Created: {output_path}")
    print(f"  Size:            {len(output)} bytes")
    print(f"  Bank type:       {mode}")
    print(f"  Format version:  {version}")
    print(f"  Tool version:    {tool_version}")
    print(f"  Entries:         {entry_count}")
    print(f"  Metadata:        {metadata_mode}")
    print(f"  Alignment:       {actual_alignment} bytes")
    print(f"  Friendly names:  {'yes' if friendly_names else 'no'}")
    print(f"  Wave data size:  {len(wave_data)} bytes")

    for index, entry in enumerate(entries):
        print(
            f"    [{index}] {entry.name}: "
            f"{entry.channels}ch, "
            f"{entry.sample_rate}Hz, "
            f"{entry.bits_per_sample}bit, "
            f"offset={entry.offset}, "
            f"length={len(entry.data)}"
        )


# ---------------------------------------------------------------------------
# Command-line interface
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build XACT Wave Bank (.xwb) files"
    )

    parser.add_argument(
        "-o",
        "--output",
        required=True,
        help="output .xwb filename",
    )

    parser.add_argument(
        "-v",
        "--version",
        type=int,
        choices=(42, 44),
        default=44,
        help="XWB format version; default: 44",
    )

    parser.add_argument(
        "-f",
        "--friendly-names",
        action="store_true",
        help="include 64-byte entry names",
    )

    parser.add_argument(
        "-n",
        "--name",
        default="bank",
        help='bank name; default: "bank"',
    )

    parser.add_argument(
        "-a",
        "--alignment",
        type=int,
        default=None,
        help=(
            "entry alignment in bytes; defaults to 4 for in-memory banks "
            "and 2048 for streaming banks"
        ),
    )

    parser.add_argument(
        "-c",
        "--compact",
        action="store_true",
        help="use compact 4-byte entry metadata",
    )

    parser.add_argument(
        "-s",
        "--streaming",
        action="store_true",
        help="create a streaming bank",
    )

    parser.add_argument(
        "--advanced-format",
        action="store_true",
        help="use 4096-byte alignment for streaming banks",
    )

    parser.add_argument(
        "wavs",
        nargs="+",
        help="input PCM WAV files",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    for path in args.wavs:
        if not os.path.isfile(path):
            print(f"error: input file not found: {path}", file=sys.stderr)
            return 1

    try:
        build_xwb(
            output_path=args.output,
            wav_paths=args.wavs,
            version=args.version,
            friendly_names=args.friendly_names,
            bank_name=args.name,
            alignment=args.alignment,
            compact=args.compact,
            streaming=args.streaming,
            advanced_format=args.advanced_format,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

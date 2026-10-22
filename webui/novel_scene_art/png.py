"""Strict PNG validation and metadata-free container re-encoding."""

from __future__ import annotations

import binascii
import struct
import zlib
from dataclasses import dataclass

from .errors import SceneArtError, fail


SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_CHUNK_BYTES = 24 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class SanitizedPng:
    data: bytes
    width: int
    height: int
    source_bytes: int


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", binascii.crc32(kind + payload) & 0xFFFFFFFF)
    )


def sanitize_png(
    data: bytes,
    *,
    expected_width: int,
    expected_height: int,
    maximum_bytes: int,
) -> SanitizedPng:
    """Validate one non-interlaced 8-bit RGB/RGBA PNG and remove metadata.

    The concatenated image stream is inflated with an exact byte ceiling,
    filter bytes are checked, and it is deflated again.  Only IHDR, one IDAT,
    and IEND are emitted, so text, EXIF, ICC, timestamps, and workflow metadata
    cannot cross the Castle-to-Oracle boundary.
    """

    if (
        not isinstance(data, bytes)
        or not data.startswith(SIGNATURE)
        or not data
        or len(data) > maximum_bytes
    ):
        fail("scene_art_png_invalid", "Renderer output is not a bounded PNG", definite_pre_submit=False)
    offset = len(SIGNATURE)
    ihdr: bytes | None = None
    compressed: list[bytes] = []
    saw_idat = False
    ended_idat = False
    saw_iend = False
    chunk_index = 0
    while offset < len(data):
        if len(data) - offset < 12:
            fail("scene_art_png_invalid", "PNG chunk framing is invalid", definite_pre_submit=False)
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8]
        if length > MAX_CHUNK_BYTES or offset + 12 + length > len(data):
            fail("scene_art_png_invalid", "PNG chunk length is invalid", definite_pre_submit=False)
        payload = data[offset + 8 : offset + 8 + length]
        stored_crc = struct.unpack(">I", data[offset + 8 + length : offset + 12 + length])[0]
        if binascii.crc32(kind + payload) & 0xFFFFFFFF != stored_crc:
            fail("scene_art_png_invalid", "PNG chunk checksum is invalid", definite_pre_submit=False)
        offset += 12 + length
        if chunk_index == 0 and kind != b"IHDR":
            fail("scene_art_png_invalid", "PNG header is not the first chunk", definite_pre_submit=False)
        chunk_index += 1
        if kind == b"IHDR":
            if ihdr is not None or saw_idat or length != 13:
                fail("scene_art_png_invalid", "PNG header is invalid", definite_pre_submit=False)
            ihdr = payload
        elif kind == b"IDAT":
            if ihdr is None or ended_idat or saw_iend:
                fail("scene_art_png_invalid", "PNG image stream is invalid", definite_pre_submit=False)
            saw_idat = True
            compressed.append(payload)
        elif kind == b"IEND":
            if not saw_idat or saw_iend or length != 0 or offset != len(data):
                fail("scene_art_png_invalid", "PNG terminator is invalid", definite_pre_submit=False)
            saw_iend = True
        else:
            if saw_idat:
                ended_idat = True
            # APNG and unknown critical chunks are not accepted.  Known
            # ancillary chunks are intentionally discarded from the output.
            if kind == b"acTL" or not (kind[0] & 0x20):
                fail("scene_art_png_invalid", "PNG contains an unsupported critical chunk", definite_pre_submit=False)
    if ihdr is None or not saw_idat or not saw_iend:
        fail("scene_art_png_invalid", "PNG is incomplete", definite_pre_submit=False)
    width, height, depth, color_type, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", ihdr
    )
    if (
        width != expected_width
        or height != expected_height
        or depth != 8
        or color_type not in {2, 6}
        or compression != 0
        or filtering != 0
        or interlace != 0
    ):
        fail("scene_art_png_profile_invalid", "PNG profile does not match the release", definite_pre_submit=False)
    channels = 3 if color_type == 2 else 4
    row_bytes = 1 + width * channels
    expected_raw_bytes = row_bytes * height
    try:
        inflater = zlib.decompressobj()
        raw = inflater.decompress(b"".join(compressed), expected_raw_bytes + 1)
        if len(raw) <= expected_raw_bytes:
            raw += inflater.flush(expected_raw_bytes + 1 - len(raw))
    except zlib.error as error:
        raise SceneArtError(
            "scene_art_png_invalid",
            "PNG image stream cannot be decoded",
            definite_pre_submit=False,
        ) from error
    if (
        len(raw) != expected_raw_bytes
        or inflater.unconsumed_tail
        or inflater.unused_data
        or not inflater.eof
    ):
        fail("scene_art_png_invalid", "PNG image stream size is invalid", definite_pre_submit=False)
    if any(raw[index] > 4 for index in range(0, len(raw), row_bytes)):
        fail("scene_art_png_invalid", "PNG scanline filter is invalid", definite_pre_submit=False)
    encoded = zlib.compress(raw, level=9)
    sanitized = SIGNATURE + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", encoded) + _chunk(b"IEND", b"")
    if len(sanitized) > maximum_bytes:
        fail("scene_art_result_too_large", "Sanitized PNG exceeds the upload ceiling", definite_pre_submit=False)
    return SanitizedPng(sanitized, width, height, len(data))

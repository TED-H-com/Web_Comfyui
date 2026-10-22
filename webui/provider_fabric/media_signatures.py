"""Strict, bounded media signature classification.

The classifier is intentionally smaller than a general-purpose file sniffer.
It accepts only the exact MIME types used by the provider import contract and
returns the canonical signature family stored by schema 8.  Unknown MIME
types, malformed containers, truncated signature structures, and ambiguous
ISO BMFF brand combinations all fail closed.

Binary callers may pass a bounded prefix.  ``SIGNATURE_PROBE_BYTES`` is large
enough for every bounded structure parsed here.  Text callers must pass the
complete payload so UTF-8 can be validated; importers should still perform
their own full decode before consuming or scanning text.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping


SIGNATURE_PROBE_BYTES = 8192
_MAX_FTYP_BOX_BYTES = 4096
_MAX_EBML_HEADER_BYTES = 4096

MIME_SIGNATURE_FAMILIES: Mapping[str, str] = MappingProxyType(
    {
        "image/png": "png",
        "image/jpeg": "jpeg",
        "image/webp": "webp",
        "image/gif": "gif",
        "image/avif": "avif",
        "video/mp4": "mp4",
        "audio/mp4": "mp4",
        "video/quicktime": "quicktime",
        "video/webm": "webm",
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/mpeg": "mpeg_audio",
        "audio/ogg": "ogg",
        "audio/flac": "flac",
        "text/plain": "utf8_text",
        "text/markdown": "utf8_text",
    }
)

_AVIF_BRANDS = frozenset({b"avif", b"avis"})
_QUICKTIME_BRANDS = frozenset({b"qt  "})
# Generic HEIF brands such as mif1/heic are deliberately absent.  A generic
# ISO BMFF file is not enough evidence that a payload is MP4.
_MP4_BRANDS = frozenset(
    {
        b"F4A ",
        b"F4B ",
        b"F4P ",
        b"F4V ",
        b"M4A ",
        b"M4B ",
        b"M4P ",
        b"M4V ",
        b"M4VH",
        b"M4VP",
        b"MSNV",
        b"avc1",
        b"cmfc",
        b"cmff",
        b"cmfs",
        b"dash",
        b"iso2",
        b"iso3",
        b"iso4",
        b"iso5",
        b"iso6",
        b"iso7",
        b"iso8",
        b"iso9",
        b"isom",
        b"mp41",
        b"mp42",
    }
)


def signature_family_for_mime(mime_type: object) -> str | None:
    """Return the exact canonical family for an allowlisted MIME type."""

    if not isinstance(mime_type, str):
        return None
    return MIME_SIGNATURE_FAMILIES.get(mime_type)


def _iso_bmff_family(probe: bytes) -> str | None:
    """Classify a complete, bounded leading ``ftyp`` box."""

    if len(probe) < 16 or probe[4:8] != b"ftyp":
        return None

    size_32 = int.from_bytes(probe[0:4], "big")
    header_size = 8
    if size_32 == 0:
        # A box extending to EOF cannot be safely established from a prefix.
        return None
    if size_32 == 1:
        if len(probe) < 24:
            return None
        box_size = int.from_bytes(probe[8:16], "big")
        header_size = 16
    else:
        box_size = size_32

    minimum_size = header_size + 8  # major brand + minor version
    if (
        box_size < minimum_size
        or box_size > _MAX_FTYP_BOX_BYTES
        or box_size > len(probe)
        or (box_size - minimum_size) % 4 != 0
    ):
        return None

    major_offset = header_size
    major_brand = probe[major_offset : major_offset + 4]
    compatible_offset = major_offset + 8
    compatible_brands = {
        probe[offset : offset + 4]
        for offset in range(compatible_offset, box_size, 4)
    }
    brands = compatible_brands | {major_brand}

    candidates: set[str] = set()
    if brands & _AVIF_BRANDS:
        candidates.add("avif")
    if brands & _QUICKTIME_BRANDS:
        candidates.add("quicktime")
    if brands & _MP4_BRANDS:
        candidates.add("mp4")
    if len(candidates) != 1:
        # Mixed family brands are ambiguous and therefore not importable.
        return None
    return candidates.pop()


def _read_ebml_vint(
    data: bytes,
    offset: int,
    *,
    maximum_length: int,
    retain_marker: bool,
) -> tuple[int, int, bool] | None:
    if offset < 0 or offset >= len(data):
        return None
    first = data[offset]
    if first == 0:
        return None

    marker = 0x80
    length = 1
    while not first & marker:
        marker >>= 1
        length += 1
    if length > maximum_length or offset + length > len(data):
        return None

    if retain_marker:
        value = int.from_bytes(data[offset : offset + length], "big")
        return value, length, False

    value = first & (marker - 1)
    for byte in data[offset + 1 : offset + length]:
        value = (value << 8) | byte
    unknown = value == (1 << (7 * length)) - 1
    return value, length, unknown


def _webm_signature(probe: bytes) -> bool:
    header_id = _read_ebml_vint(
        probe,
        0,
        maximum_length=4,
        retain_marker=True,
    )
    if header_id is None or header_id[0] != 0x1A45DFA3:
        return False
    cursor = header_id[1]
    header_size = _read_ebml_vint(
        probe,
        cursor,
        maximum_length=8,
        retain_marker=False,
    )
    if header_size is None or header_size[2]:
        return False
    cursor += header_size[1]
    payload_size = header_size[0]
    if payload_size <= 0 or payload_size > _MAX_EBML_HEADER_BYTES:
        return False
    header_end = cursor + payload_size
    if header_end > len(probe):
        return False

    document_type_seen = False
    while cursor < header_end:
        element_id = _read_ebml_vint(
            probe,
            cursor,
            maximum_length=4,
            retain_marker=True,
        )
        if element_id is None:
            return False
        cursor += element_id[1]
        element_size = _read_ebml_vint(
            probe,
            cursor,
            maximum_length=8,
            retain_marker=False,
        )
        if element_size is None or element_size[2]:
            return False
        cursor += element_size[1]
        element_end = cursor + element_size[0]
        if element_end > header_end:
            return False
        if element_id[0] == 0x4282:
            if document_type_seen or probe[cursor:element_end] != b"webm":
                return False
            document_type_seen = True
        cursor = element_end

    if not document_type_seen or cursor != header_end:
        return False

    # A bare EBML header is truncated, not a WebM container.  Require the
    # following Segment element and at least one byte of segment payload.
    segment_id = _read_ebml_vint(
        probe,
        header_end,
        maximum_length=4,
        retain_marker=True,
    )
    if segment_id is None or segment_id[0] != 0x18538067:
        return False
    cursor = header_end + segment_id[1]
    segment_size = _read_ebml_vint(
        probe,
        cursor,
        maximum_length=8,
        retain_marker=False,
    )
    if segment_size is None or (not segment_size[2] and segment_size[0] == 0):
        return False
    cursor += segment_size[1]
    return cursor < len(probe)


def _mpeg_frame_header(probe: bytes, offset: int) -> bool:
    if offset < 0 or offset + 4 > len(probe):
        return False
    first, second, third, _ = probe[offset : offset + 4]
    if first != 0xFF or second & 0xE0 != 0xE0:
        return False
    version = (second >> 3) & 0x03
    layer = (second >> 1) & 0x03
    bitrate_index = (third >> 4) & 0x0F
    sample_rate_index = (third >> 2) & 0x03
    return (
        version != 0x01
        and layer != 0x00
        and bitrate_index not in {0x00, 0x0F}
        and sample_rate_index != 0x03
    )


def _mpeg_audio_signature(probe: bytes) -> bool:
    if not probe.startswith(b"ID3"):
        return _mpeg_frame_header(probe, 0)
    if len(probe) < 10:
        return False

    major_version = probe[3]
    revision = probe[4]
    flags = probe[5]
    allowed_flags = {2: 0xC0, 3: 0xE0, 4: 0xF0}.get(major_version)
    if revision == 0xFF or allowed_flags is None or flags & ~allowed_flags:
        return False

    size_bytes = probe[6:10]
    if any(byte & 0x80 for byte in size_bytes):
        return False
    tag_size = 0
    for byte in size_bytes:
        tag_size = (tag_size << 7) | byte

    frame_offset = 10 + tag_size
    # ID3v2.4's optional footer is not included in the sync-safe tag size.
    if major_version == 4 and flags & 0x10:
        footer_end = frame_offset + 10
        if (
            footer_end > len(probe)
            or probe[frame_offset : frame_offset + 3] != b"3DI"
            or probe[frame_offset + 3 : footer_end] != probe[3:10]
        ):
            return False
        frame_offset += 10
    if frame_offset > SIGNATURE_PROBE_BYTES:
        return False
    return _mpeg_frame_header(probe, frame_offset)


def _ogg_signature(probe: bytes) -> bool:
    # The fixed page header is 27 bytes, followed by a bounded lacing table.
    if len(probe) < 27 or probe[:4] != b"OggS" or probe[4] != 0:
        return False
    header_type = probe[5]
    if header_type & ~0x07 or header_type & 0x01 or not header_type & 0x02:
        return False
    if int.from_bytes(probe[18:22], "little") != 0:
        return False
    segment_count = probe[26]
    return segment_count > 0 and 27 + segment_count <= len(probe)


def _wav_signature(probe: bytes) -> bool:
    # This verifies only the bounded RIFF prefix.  It does not claim that the
    # complete chunk payload or the rest of the file has been decoded.
    if (
        len(probe) < 20
        or probe[:4] != b"RIFF"
        or probe[8:12] != b"WAVE"
    ):
        return False
    riff_size = int.from_bytes(probe[4:8], "little")
    if riff_size < 12 or riff_size == 0xFFFFFFFF:
        return False
    chunk_id = probe[12:16]
    if any(byte < 0x20 or byte > 0x7E for byte in chunk_id):
        return False
    chunk_size = int.from_bytes(probe[16:20], "little")
    padded_chunk_end = 20 + chunk_size + (chunk_size & 1)
    declared_file_size = riff_size + 8
    return padded_chunk_end <= declared_file_size


def classify_declared_media(
    declared_mime: object,
    prefix: object,
    *,
    complete_text: object | None = None,
) -> str | None:
    """Return the declared MIME's family only when its signature matches.

    Binary callers pass at most ``SIGNATURE_PROBE_BYTES`` leading bytes in
    ``prefix``.  Text callers must additionally pass the complete payload as
    ``complete_text``; a prefix is never enough evidence of complete UTF-8.

    ``None`` is the only failure result.  This keeps callers from accidentally
    treating parser errors or unsupported types as a successful sniff.
    """

    expected = signature_family_for_mime(declared_mime)
    if expected is None or not isinstance(prefix, (bytes, bytearray, memoryview)):
        return None
    if len(prefix) > SIGNATURE_PROBE_BYTES:
        return None
    probe = bytes(prefix)

    if expected == "png":
        matched = probe.startswith(b"\x89PNG\r\n\x1a\n")
    elif expected == "jpeg":
        matched = probe.startswith(b"\xff\xd8\xff")
    elif expected == "webp":
        matched = len(probe) >= 12 and probe[:4] == b"RIFF" and probe[8:12] == b"WEBP"
    elif expected == "gif":
        matched = probe.startswith((b"GIF87a", b"GIF89a"))
    elif expected in {"avif", "mp4", "quicktime"}:
        matched = _iso_bmff_family(probe) == expected
    elif expected == "webm":
        matched = _webm_signature(probe)
    elif expected == "wav":
        matched = _wav_signature(probe)
    elif expected == "mpeg_audio":
        matched = _mpeg_audio_signature(probe)
    elif expected == "ogg":
        matched = _ogg_signature(probe)
    elif expected == "flac":
        matched = probe.startswith(b"fLaC")
    elif expected == "utf8_text":
        if not isinstance(complete_text, (bytes, bytearray, memoryview)):
            return None
        try:
            text = bytes(complete_text).decode("utf-8")
        except UnicodeDecodeError:
            matched = False
        else:
            matched = "\x00" not in text
    else:  # Defensive if the mapping and classifier ever drift apart.
        matched = False
    return expected if matched else None


def classify_media_signature(mime_type: object, content: object) -> str | None:
    """Compatibility wrapper for callers that hold one complete byte value."""

    complete_text = (
        content
        if signature_family_for_mime(mime_type) == "utf8_text"
        else None
    )
    return classify_declared_media(
        mime_type,
        content,
        complete_text=complete_text,
    )


def media_signature_matches(mime_type: object, content: object) -> bool:
    """Return whether content matches an exact supported MIME signature."""

    return classify_media_signature(mime_type, content) is not None

"""Deterministic, metadata-free browser renditions for reviewed profile images."""

from __future__ import annotations

import hashlib
import io
import warnings
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError


_INPUT_FORMATS = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}
_OUTPUT_MIME = "image/png"


class ProfileRenditionError(ValueError):
    """A public-safe rejection of an untrusted profile image."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise ProfileRenditionError(code, message)


@dataclass(frozen=True, slots=True)
class ProfileRenditionLimits:
    max_input_bytes: int = 32 * 1024 * 1024
    max_input_pixels: int = 40_000_000
    max_edge_pixels: int = 512
    max_output_bytes: int = 4 * 1024 * 1024

    def __post_init__(self) -> None:
        for name in (
            "max_input_bytes",
            "max_input_pixels",
            "max_edge_pixels",
            "max_output_bytes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.max_edge_pixels > 2048:
            raise ValueError("max_edge_pixels exceeds the rendition safety ceiling")


@dataclass(frozen=True, slots=True)
class ProfileRendition:
    content: bytes
    sha256: str
    mime_type: str
    width: int
    height: int
    byte_size: int
    signature_family: str

    def evidence(self) -> dict[str, Any]:
        return {
            "sha256": self.sha256,
            "mime_type": self.mime_type,
            "width": self.width,
            "height": self.height,
            "byte_size": self.byte_size,
            "signature_family": self.signature_family,
        }


def _open_static_image(
    source: bytes,
    declared_mime: str,
    limits: ProfileRenditionLimits,
) -> Image.Image:
    expected_format = _INPUT_FORMATS.get(declared_mime)
    if expected_format is None:
        _fail("profile_mime_unsupported", "Profile image type is unsupported.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            probe = Image.open(io.BytesIO(source))
            if probe.format != expected_format:
                _fail(
                    "profile_mime_mismatch",
                    "Profile image type does not match its declaration.",
                )
            if getattr(probe, "n_frames", 1) != 1 or getattr(probe, "is_animated", False):
                _fail("profile_animation_unsupported", "Animated profile images are unsupported.")
            width, height = probe.size
            if width <= 0 or height <= 0 or width * height > limits.max_input_pixels:
                _fail("profile_pixels_exceeded", "Profile image dimensions exceed the safety limit.")
            probe.verify()

            decoded = Image.open(io.BytesIO(source))
            if decoded.format != expected_format or getattr(decoded, "n_frames", 1) != 1:
                _fail("profile_decode_invalid", "Profile image could not be decoded safely.")
            decoded.load()
            return decoded
    except ProfileRenditionError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
    ) as error:
        raise ProfileRenditionError(
            "profile_decode_invalid", "Profile image could not be decoded safely."
        ) from error


def render_profile_rendition(
    source: bytes,
    declared_mime: str,
    *,
    limits: ProfileRenditionLimits = ProfileRenditionLimits(),
) -> ProfileRendition:
    """Decode one untrusted static image and return a bounded clean PNG."""

    if not isinstance(source, bytes):
        raise TypeError("source must be bytes")
    if not isinstance(declared_mime, str):
        raise TypeError("declared_mime must be a string")
    if not isinstance(limits, ProfileRenditionLimits):
        raise TypeError("limits must be ProfileRenditionLimits")
    if not source:
        _fail("profile_source_empty", "Profile image is empty.")
    if len(source) > limits.max_input_bytes:
        _fail("profile_bytes_exceeded", "Profile image exceeds the byte limit.")

    decoded = _open_static_image(source, declared_mime, limits)
    try:
        oriented = ImageOps.exif_transpose(decoded)
        has_alpha = "A" in oriented.getbands() or "transparency" in oriented.info
        clean = oriented.convert("RGBA" if has_alpha else "RGB")
        clean.thumbnail(
            (limits.max_edge_pixels, limits.max_edge_pixels),
            resample=Image.Resampling.LANCZOS,
            reducing_gap=3.0,
        )
        output = io.BytesIO()
        clean.save(output, format="PNG", optimize=False, compress_level=9)
        content = output.getvalue()
    finally:
        decoded.close()

    if not content or len(content) > limits.max_output_bytes:
        _fail("profile_rendition_exceeded", "Profile rendition exceeds the byte limit.")
    verified: Image.Image | None = None
    try:
        verified = Image.open(io.BytesIO(content))
        verified.verify()
        if (
            verified.format != "PNG"
            or getattr(verified, "n_frames", 1) != 1
            or verified.width <= 0
            or verified.height <= 0
            or verified.width > limits.max_edge_pixels
            or verified.height > limits.max_edge_pixels
        ):
            _fail("profile_rendition_invalid", "Profile rendition verification failed.")
        width, height = verified.size
    except ProfileRenditionError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as error:
        raise ProfileRenditionError(
            "profile_rendition_invalid", "Profile rendition verification failed."
        ) from error
    finally:
        if verified is not None:
            verified.close()

    return ProfileRendition(
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        mime_type=_OUTPUT_MIME,
        width=width,
        height=height,
        byte_size=len(content),
        signature_family="png",
    )


__all__ = [
    "ProfileRendition",
    "ProfileRenditionError",
    "ProfileRenditionLimits",
    "render_profile_rendition",
]

"""Fail-closed Castle worker for AI-Sister Novel Live scene art.

This package intentionally has no import-time network, GPU, or filesystem
side effects.  Production execution remains disabled until a reviewed render
release manifest is installed outside the repository.
"""

from .errors import SceneArtError

__all__ = ["SceneArtError"]

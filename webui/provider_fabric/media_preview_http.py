"""Actor-bound binary HTTP contract for exact private media renditions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from .media_materializer import MediaMaterializerError
from .service import ActorContext


_ASSET_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_SAFE_MIMES = {
    "image/jpeg": "jpeg",
    "image/png": "png",
    "image/webp": "webp",
}


@dataclass(frozen=True, slots=True)
class BinaryHttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]

    def __post_init__(self) -> None:
        if type(self.status) is not int or not 100 <= self.status <= 599:
            raise ValueError("status must be an HTTP status")
        if not isinstance(self.body, bytes):
            raise TypeError("body must be bytes")
        if not isinstance(self.headers, Mapping):
            raise TypeError("headers must be a mapping")
        object.__setattr__(
            self,
            "headers",
            MappingProxyType({str(key): str(value) for key, value in self.headers.items()}),
        )


class ProviderMediaPreviewHttpApi:
    """Resolve a database-pinned rendition, then read that exact owner object."""

    def __init__(self, provider_store: Any, media_store: Any, *, max_bytes: int = 4 * 1024 * 1024) -> None:
        if not callable(getattr(provider_store, "resolve_owned_projection_preview", None)):
            raise TypeError("provider_store cannot resolve owned previews")
        if not callable(getattr(media_store, "read_owned_object", None)):
            raise TypeError("media_store cannot read owned objects")
        if type(max_bytes) is not int or max_bytes <= 0 or max_bytes > 32 * 1024 * 1024:
            raise ValueError("max_bytes is outside the preview safety bound")
        self._provider_store = provider_store
        self._media_store = media_store
        self._max_bytes = max_bytes

    @staticmethod
    def _missing() -> BinaryHttpResponse:
        return BinaryHttpResponse(
            404,
            b"",
            {
                "Cache-Control": "private, no-store, max-age=0",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
                "Content-Length": "0",
            },
        )

    def get_preview(
        self, actor: ActorContext | None, projection_id: str
    ) -> BinaryHttpResponse:
        if not isinstance(actor, ActorContext):
            return BinaryHttpResponse(
                401,
                b"",
                {
                    "Cache-Control": "private, no-store, max-age=0",
                    "X-Content-Type-Options": "nosniff",
                    "Content-Length": "0",
                },
            )
        if not isinstance(projection_id, str) or not _ASSET_RE.fullmatch(projection_id):
            return self._missing()
        try:
            evidence = self._provider_store.resolve_owned_projection_preview(
                actor.user_id, projection_id
            )
            if not isinstance(evidence, Mapping):
                return self._missing()
            mime_type = evidence.get("declared_mime")
            signature_family = evidence.get("signature_family")
            byte_size = evidence.get("byte_size")
            digest = evidence.get("rendition_sha256")
            if (
                not isinstance(mime_type, str)
                or _SAFE_MIMES.get(mime_type) != signature_family
                or isinstance(byte_size, bool)
                or not isinstance(byte_size, int)
                or byte_size <= 0
                or byte_size > self._max_bytes
                or not isinstance(digest, str)
            ):
                return self._missing()
            body = self._media_store.read_owned_object(
                actor.user_id,
                digest,
                byte_size,
                signature_family,
                max_bytes=self._max_bytes,
            )
            if len(body) != byte_size:
                return self._missing()
        except (KeyError, ValueError, MediaMaterializerError):
            return self._missing()
        return BinaryHttpResponse(
            200,
            body,
            {
                "Content-Type": mime_type,
                "Content-Length": str(len(body)),
                "Cache-Control": "private, no-store, max-age=0",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            },
        )


__all__ = ["BinaryHttpResponse", "ProviderMediaPreviewHttpApi"]

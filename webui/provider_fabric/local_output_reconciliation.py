"""Adopt only server-attested Castle outputs into the canonical media spine."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Callable, Iterable, Mapping

from .local_history_adoption import adopt_local_history_output
from .models import CharacterRef, VisualClass
from .store import ProviderFabricStore


def reconcile_trusted_local_outputs(
    store: ProviderFabricStore,
    items: Iterable[Mapping[str, object]],
    *,
    owner_for_prompt: Callable[[str], str],
    resolve_source: Callable[[str, str, str], Path | None],
    character_for_id: Callable[[str], Mapping[str, object] | None],
    account_id: str = "local-main",
) -> dict[str, int]:
    """Reconcile bounded, pre-enumerated gallery items without trusting labels."""

    counts = {"examined": 0, "adopted": 0, "skipped": 0, "failed": 0}
    for raw in list(items)[:500]:
        counts["examined"] += 1
        try:
            provenance = raw.get("provenance")
            character_summary = raw.get("character")
            if not isinstance(provenance, Mapping) or not isinstance(character_summary, Mapping):
                counts["skipped"] += 1
                continue
            prompt_id = str(raw.get("promptId") or "").strip()
            character_id = str(character_summary.get("id") or "").strip()
            visual_class = str(raw.get("visualClass") or "").strip()
            if (
                raw.get("provenanceTrusted") is not True
                or raw.get("characterAttribution") != "server"
                or provenance.get("schema") != "castle.output-provenance.v1"
                or provenance.get("promptId") != prompt_id
                or provenance.get("characterId") != character_id
                or provenance.get("visualClass") != visual_class
                or visual_class not in {"anime", "realistic"}
            ):
                counts["skipped"] += 1
                continue
            owner = owner_for_prompt(prompt_id)
            canonical = character_for_id(character_id)
            if not owner or not isinstance(canonical, Mapping):
                counts["skipped"] += 1
                continue
            if str(canonical.get("visualClass") or "") != visual_class:
                counts["skipped"] += 1
                continue
            filename = str(raw.get("filename") or "")
            subfolder = str(raw.get("subfolder") or "")
            view_type = str(raw.get("type") or "output")
            source = resolve_source(filename, subfolder, view_type)
            mime_type = mimetypes.guess_type(filename)[0] or ""
            if source is None or not source.is_file():
                counts["skipped"] += 1
                continue
            adopt_local_history_output(
                store,
                owner_user_id=owner,
                account_id=account_id,
                source_path=source,
                source_identity="\x00".join((view_type, subfolder, filename)),
                mime_type=mime_type,
                display_name=filename[:160],
                character=CharacterRef(
                    character_id,
                    VisualClass(visual_class),
                    str(canonical.get("name") or character_id)[:160],
                ),
                audio_role=(
                    "voice"
                    if str(provenance.get("featureId") or "") in {"voice-speak", "voxcpm-tts"}
                    else "unclassified"
                ),
            )
            counts["adopted"] += 1
        except Exception:
            counts["failed"] += 1
    return counts


__all__ = ["reconcile_trusted_local_outputs"]

#!/usr/bin/env python3
"""Verify the exact Servasyy LightX v0.1 Comfy LoRA against H3 FL2VA.

The check is read-only.  It verifies the pinned artifact identity, compares
every LoRA matrix with the target tensor shape from the installed FL2VA model,
then runs the same ComfyUI key-map, conversion, and adapter parser used by
``LoraLoaderModelOnly``.  It intentionally does not materialize the 20 GB base
model or submit a GPU prompt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any


EXPECTED_BYTES = 1_956_171_984
EXPECTED_SHA256 = "fc9b6500f0331fe925b004738baaa31bd34104741c8bf9334816f9ac3005c8c1"
DEFAULT_COMFY_HOME = (Path.home() / 'comfyui')
DEFAULT_LORA = (
    DEFAULT_COMFY_HOME
    / "models/loras/H3/minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors"
)
DEFAULT_BASE = (
    DEFAULT_COMFY_HOME
    / "models/diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors"
)
LORA_SUFFIXES = (
    ".lora_A.weight",
    ".lora_B.weight",
    ".lora_A.default.weight",
    ".lora_B.default.weight",
    ".alpha",
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def root_for_key(key: str) -> str | None:
    for suffix in LORA_SUFFIXES:
        if key.endswith(suffix):
            return key[: -len(suffix)]
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comfy-home", type=Path, default=DEFAULT_COMFY_HOME)
    parser.add_argument("--lora", type=Path, default=DEFAULT_LORA)
    parser.add_argument("--base", type=Path, default=DEFAULT_BASE)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    comfy_home = args.comfy_home.expanduser().resolve()
    lora_path = args.lora.expanduser().resolve()
    base_path = args.base.expanduser().resolve()
    if not lora_path.is_file() or not base_path.is_file():
        raise SystemExit("pinned LoRA or FL2VA base is missing")
    if lora_path.stat().st_size != EXPECTED_BYTES:
        raise SystemExit(
            f"LoRA size mismatch: expected {EXPECTED_BYTES}, "
            f"found {lora_path.stat().st_size}"
        )
    digest = file_sha256(lora_path)
    if digest != EXPECTED_SHA256:
        raise SystemExit(
            f"LoRA SHA-256 mismatch: expected {EXPECTED_SHA256}, found {digest}"
        )

    if str(comfy_home) not in sys.path:
        sys.path.insert(0, str(comfy_home))
    from safetensors import safe_open
    import comfy.lora
    import comfy.lora_convert
    import comfy.utils

    with safe_open(base_path, framework="pt", device="cpu") as base_file:
        base_keys = list(base_file.keys())
        base_shapes = {key: tuple(base_file.get_slice(key).get_shape()) for key in base_keys}
    with safe_open(lora_path, framework="pt", device="cpu") as lora_file:
        lora_keys = list(lora_file.keys())
        lora_shapes = {key: tuple(lora_file.get_slice(key).get_shape()) for key in lora_keys}
        metadata = lora_file.metadata() or {}

    model_state: dict[str, Any] = {
        f"diffusion_model.{key}": None for key in base_keys
    }

    class HeaderOnlyModel:
        model_config = SimpleNamespace(unet_config={"image_model": "minimax_h3"})

        @staticmethod
        def state_dict() -> dict[str, Any]:
            return model_state

    key_map = comfy.lora.model_lora_keys_unet(HeaderOnlyModel(), {})
    roots = {root_for_key(key) for key in lora_keys}
    if None in roots:
        unknown = sorted(key for key in lora_keys if root_for_key(key) is None)
        raise SystemExit(f"unsupported LoRA tensor keys: {unknown[:5]}")
    typed_roots = {str(root) for root in roots}
    unmatched_roots = sorted(root for root in typed_roots if root not in key_map)
    if unmatched_roots:
        raise SystemExit(f"LoRA roots absent from Comfy key map: {unmatched_roots[:5]}")

    shape_errors: list[str] = []
    for root in sorted(typed_roots):
        target = key_map[root]
        if not isinstance(target, str) or not target.startswith("diffusion_model."):
            shape_errors.append(f"{root}: unexpected target {target!r}")
            continue
        base_key = target.removeprefix("diffusion_model.")
        target_shape = base_shapes.get(base_key)
        a_shape = lora_shapes.get(f"{root}.lora_A.weight")
        b_shape = lora_shapes.get(f"{root}.lora_B.weight")
        if target_shape is None or a_shape is None or b_shape is None:
            shape_errors.append(f"{root}: missing target/A/B shape")
            continue
        if (
            len(target_shape) != 2
            or len(a_shape) != 2
            or len(b_shape) != 2
            or b_shape[0] != target_shape[0]
            or a_shape[1] != target_shape[1]
            or b_shape[1] != a_shape[0]
        ):
            shape_errors.append(
                f"{root}: base={target_shape}, A={a_shape}, B={b_shape}"
            )
    if shape_errors:
        raise SystemExit(f"LoRA matrix shape mismatch: {shape_errors[:5]}")

    # ComfyUI 0.32's loader still calls ``lower()`` on the path argument.
    state = comfy.utils.load_torch_file(str(lora_path), safe_load=True)
    converted = comfy.lora_convert.convert_lora(state)
    patches = comfy.lora.load_lora(converted, key_map, log_missing=False)
    loaded_keys: set[str] = set()
    for adapter in patches.values():
        adapter_keys = getattr(adapter, "loaded_keys", ())
        loaded_keys.update(str(key) for key in adapter_keys)
    unloaded = sorted(set(converted) - loaded_keys)
    if unloaded:
        raise SystemExit(f"Comfy adapter parser left keys unloaded: {unloaded[:5]}")
    if len(patches) != len(typed_roots):
        raise SystemExit(
            f"Comfy patch count mismatch: roots={len(typed_roots)}, "
            f"patches={len(patches)}"
        )

    print(
        json.dumps(
            {
                "status": "ok",
                "lora": {
                    "path": str(lora_path),
                    "bytes": EXPECTED_BYTES,
                    "sha256": digest,
                    "metadata": metadata,
                    "tensor_keys": len(lora_keys),
                    "patch_targets": len(patches),
                    "loaded_keys": len(loaded_keys),
                },
                "base": {
                    "path": str(base_path),
                    "header_keys": len(base_keys),
                },
                "loader": "comfy.lora.model_lora_keys_unet + lora_convert + load_lora",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Download public model files used by the maintained workflows."""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

from huggingface_hub import hf_hub_download


MODEL_JOBS = [
    # Z-Image Turbo / ZIT
    {
        "repo": "Comfy-Org/z_image_turbo",
        "file": "split_files/diffusion_models/z_image_turbo_bf16.safetensors",
        "dest": "diffusion_models/z_image_turbo_bf16.safetensors",
    },
    {
        "repo": "Comfy-Org/z_image_turbo",
        "file": "split_files/text_encoders/qwen_3_4b.safetensors",
        "dest": "text_encoders/qwen_3_4b.safetensors",
    },
    {
        "repo": "Comfy-Org/z_image_turbo",
        "file": "split_files/vae/ae.safetensors",
        "dest": "vae/ae.safetensors",
    },
    # Flux Kontext
    {
        "repo": "Comfy-Org/flux1-kontext-dev_ComfyUI",
        "file": "split_files/diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors",
        "dest": "diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors",
    },
    {
        "repo": "comfyanonymous/flux_text_encoders",
        "file": "clip_l.safetensors",
        "dest": "text_encoders/clip_l.safetensors",
    },
    {
        "repo": "comfyanonymous/flux_text_encoders",
        "file": "t5xxl_fp8_e4m3fn_scaled.safetensors",
        "dest": "text_encoders/t5xxl_fp8_e4m3fn_scaled.safetensors",
    },
    # SCAIL-2 / Wan, kept here so a clean machine can reproduce the current setup.
    {
        "repo": "Comfy-Org/sam3.1",
        "file": "checkpoints/sam3.1_multiplex_fp16.safetensors",
        "dest": "checkpoints/sam3.1_multiplex_fp16.safetensors",
    },
    {
        "repo": "Comfy-Org/Wan_2.1_ComfyUI_repackaged",
        "file": "split_files/clip_vision/clip_vision_h.safetensors",
        "dest": "clip_vision/clip_vision_h.safetensors",
    },
    {
        "repo": "Comfy-Org/SCAIL-2",
        "file": "diffusion_models/wan2.1_14B_SCAIL_2_fp8_scaled.safetensors",
        "dest": "diffusion_models/wan2.1_14B_SCAIL_2_fp8_scaled.safetensors",
    },
    {
        # Real Wan 2.1 image-to-video base (480P) — honors the start frame.
        # SCAIL above is character/pose-driven and can't do plain i2v.
        "repo": "Comfy-Org/Wan_2.1_ComfyUI_repackaged",
        "file": "split_files/diffusion_models/wan2.1_i2v_480p_14B_fp8_scaled.safetensors",
        "dest": "diffusion_models/wan2.1_i2v_480p_14B_fp8_scaled.safetensors",
    },
    {
        "repo": "Comfy-Org/Wan_2.1_ComfyUI_repackaged",
        "file": "split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        "dest": "text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
    },
    {
        "repo": "Comfy-Org/Wan_2.1_ComfyUI_repackaged",
        "file": "split_files/vae/wan_2.1_vae.safetensors",
        "dest": "vae/wan_2.1_vae.safetensors",
    },
    {
        "repo": "lightx2v/Wan2.1-I2V-14B-480P-StepDistill-CfgDistill-Lightx2v",
        "file": "loras/Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors",
        "dest": "loras/Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors",
    },
]


def safetensors_complete(path: Path) -> bool:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            raw_header_size = handle.read(8)
            if len(raw_header_size) != 8:
                return False
            header_size = struct.unpack("<Q", raw_header_size)[0]
            if header_size < 2 or header_size > min(256 * 1024 * 1024, size - 8):
                return False
            header = json.loads(handle.read(header_size))
        if not isinstance(header, dict):
            return False
        ranges = sorted(
            tuple(value.get("data_offsets") or ())
            for key, value in header.items()
            if key != "__metadata__" and isinstance(value, dict)
        )
        if not ranges or any(
            len(offsets) != 2
            or not all(isinstance(value, int) and not isinstance(value, bool) for value in offsets)
            or offsets[0] < 0
            or offsets[1] < offsets[0]
            for offsets in ranges
        ):
            return False
        cursor = 0
        for start, end in ranges:
            if start != cursor:
                return False
            cursor = end
        return cursor == size - 8 - header_size
    except (OSError, ValueError, TypeError, json.JSONDecodeError, struct.error):
        return False


def local_model_ready(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return False
    except OSError:
        return False
    return path.suffix.lower() != ".safetensors" or safetensors_complete(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models-dir", default=str(Path.home() / "comfyui/models"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    models_dir = Path(args.models_dir).expanduser()
    models_dir.mkdir(parents=True, exist_ok=True)

    for job in MODEL_JOBS:
        dest = models_dir / job["dest"]
        if local_model_ready(dest) and not args.force:
            print(f"[ok] {dest.relative_to(models_dir)}")
            continue
        if dest.exists() and not args.force:
            print(f"[repair] incomplete model: {dest.relative_to(models_dir)}", flush=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        print(f"[..] {job['repo']}:{job['file']} -> {dest.relative_to(models_dir)}", flush=True)
        downloaded = hf_hub_download(
            repo_id=job["repo"],
            filename=job["file"],
            local_dir=models_dir,
            local_dir_use_symlinks=False,
            resume_download=True,
        )
        source = Path(downloaded)
        if source != dest:
            source.replace(dest)
        if not local_model_ready(dest):
            raise RuntimeError(f"downloaded model failed integrity check: {dest}")
        print(f"[ok] {dest.relative_to(models_dir)}")


if __name__ == "__main__":
    main()

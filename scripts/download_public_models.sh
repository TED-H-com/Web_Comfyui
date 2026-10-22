#!/usr/bin/env bash
# Download public model files used by the maintained workflows.
set -euo pipefail

MODELS_DIR="${MODELS_DIR:-$HOME/comfyui/models}"

download() {
  local url="$1"
  local rel="$2"
  local dest="$MODELS_DIR/$rel"
  mkdir -p "$(dirname "$dest")"
  local expected current
  expected="$(curl -sIL --max-time 45 "$url" | awk 'tolower($1) == "content-length:" {gsub("\r", "", $2); n=$2} END {print n}')"
  current=0
  [ -f "$dest" ] && current="$(stat -c '%s' "$dest")"
  if [ -n "$expected" ] && [ "$current" = "$expected" ]; then
    echo "[ok] $rel ($current bytes)"
    return
  fi
  echo "[..] $rel ($current/${expected:-unknown} bytes)"
  curl -L --fail --continue-at - --retry 8 --retry-delay 5 \
    --speed-limit 1024 --speed-time 120 \
    -o "$dest" "$url"
  echo "[ok] $rel"
}

download "https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/diffusion_models/z_image_turbo_bf16.safetensors" \
  "diffusion_models/z_image_turbo_bf16.safetensors"
download "https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/text_encoders/qwen_3_4b.safetensors" \
  "text_encoders/qwen_3_4b.safetensors"
download "https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/vae/ae.safetensors" \
  "vae/ae.safetensors"

download "https://huggingface.co/Comfy-Org/flux1-kontext-dev_ComfyUI/resolve/main/split_files/diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors" \
  "diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors"
download "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors" \
  "text_encoders/clip_l.safetensors"
download "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn_scaled.safetensors" \
  "text_encoders/t5xxl_fp8_e4m3fn_scaled.safetensors"

download "https://huggingface.co/Comfy-Org/sam3.1/resolve/main/checkpoints/sam3.1_multiplex_fp16.safetensors" \
  "checkpoints/sam3.1_multiplex_fp16.safetensors"
download "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/clip_vision/clip_vision_h.safetensors" \
  "clip_vision/clip_vision_h.safetensors"
download "https://huggingface.co/Comfy-Org/SCAIL-2/resolve/main/diffusion_models/wan2.1_14B_SCAIL_2_fp8_scaled.safetensors" \
  "diffusion_models/wan2.1_14B_SCAIL_2_fp8_scaled.safetensors"
download "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors" \
  "text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors"
download "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors" \
  "vae/wan_2.1_vae.safetensors"
download "https://huggingface.co/lightx2v/Wan2.1-I2V-14B-480P-StepDistill-CfgDistill-Lightx2v/resolve/main/loras/Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors" \
  "loras/Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors"

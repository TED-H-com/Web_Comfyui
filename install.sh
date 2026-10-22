#!/usr/bin/env bash
# install.sh — install ComfyUI on local-workstation (RTX 5090) and wire up this workspace.
#
# This repo is the *workspace/config layer*: workflows, custom-node list, model
# notes. The ComfyUI app itself installs into $COMFY_HOME (default ~/comfyui),
# kept OUT of this repo.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMFY_HOME="${COMFY_HOME:-$HOME/comfyui}"
COMFYUI_REVISION="${COMFYUI_REVISION:-c2bcbecd82ec5ae66594340b395c24ef0217b238}"
H3_BASELINE_FAMILY=""
H3_TUTORIAL_ASSETS=0
H3_OLLAMA=0
H3_OLLAMA_MODEL=0

usage() {
  cat <<'EOF'
Usage: ./install.sh [options]

The default install provisions ComfyUI, custom nodes, runtime patches, and the
workflow link only. It never downloads model checkpoints or installs Ollama.

Optional, explicit provisioning:
  --h3-baseline-models FAMILY  Download pinned H3 baseline models: fl, ref, or all
  --h3-tutorial-assets         Download every required pinned tutorial asset
  --h3-ollama                  Install pinned Ollama 0.32.11 and user-service files
  --h3-ollama-model            Also start loopback Ollama and pull the pinned 9B model
  -h, --help                   Show this help
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --h3-baseline-models)
      if [ "$#" -lt 2 ]; then
        echo "[ERROR] --h3-baseline-models requires fl, ref, or all" >&2
        exit 2
      fi
      H3_BASELINE_FAMILY="$2"
      case "$H3_BASELINE_FAMILY" in
        fl|ref|all) ;;
        *)
          echo "[ERROR] invalid H3 baseline family: $H3_BASELINE_FAMILY" >&2
          exit 2
          ;;
      esac
      shift 2
      ;;
    --h3-tutorial-assets)
      H3_TUTORIAL_ASSETS=1
      shift
      ;;
    --h3-ollama)
      H3_OLLAMA=1
      shift
      ;;
    --h3-ollama-model)
      H3_OLLAMA=1
      H3_OLLAMA_MODEL=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      if [ "$#" -ne 0 ]; then
        echo "[ERROR] positional arguments are not supported" >&2
        exit 2
      fi
      ;;
    *)
      echo "[ERROR] unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

# Validate packaged resources before any clone, package install, or model download.
for required_resource in \
  custom_nodes.txt \
  patches/minimax-h3-teacache-fresh-state.patch \
  patches/spectrum-sdxl-batch-alias.patch \
  scripts/minimax_h3/install_sageattention_sm120.sh \
  scripts/minimax_h3/install_sageattention_sm120.py \
  scripts/minimax_h3/validate_sageattention_sm120.py \
  manifests/minimax_h3/tutorial_stack.lock.json; do
  if [ ! -f "$REPO_DIR/$required_resource" ]; then
    echo "[ERROR] packaged resource is missing: $required_resource" >&2
    exit 1
  fi
done
if [ "$H3_OLLAMA" -eq 1 ] && \
   [ ! -f "$REPO_DIR/scripts/systemd/web-comfyui-ollama.service" ]; then
  echo "[ERROR] optional Ollama user-service template is missing" >&2
  exit 1
fi

echo "==> ComfyUI install — target: $COMFY_HOME"

# 1. ComfyUI app
if [ ! -d "$COMFY_HOME/.git" ]; then
  git clone https://github.com/comfyanonymous/ComfyUI "$COMFY_HOME"
fi
if [ -n "$(git -C "$COMFY_HOME" status --porcelain --untracked-files=no)" ]; then
  echo "[WARN] ComfyUI checkout is dirty; leaving its revision untouched: $COMFY_HOME"
else
  git -C "$COMFY_HOME" fetch --depth 1 origin "$COMFYUI_REVISION" || {
    echo "[ERROR] could not fetch ComfyUI revision: $COMFYUI_REVISION" >&2
    exit 1
  }
  git -C "$COMFY_HOME" checkout --detach FETCH_HEAD || {
    echo "[ERROR] could not checkout ComfyUI revision: $COMFYUI_REVISION" >&2
    exit 1
  }
fi

# 2. venv + PyTorch for RTX 5090 (Blackwell sm_120 — needs CUDA 12.8 wheels)
cd "$COMFY_HOME"
if [ ! -x ".venv/bin/python" ]; then
  uv venv --python 3.12 .venv
fi
uv pip install --python .venv/bin/python \
  "torch==2.7.1+cu128" "torchvision==0.22.1+cu128" \
  --index-url https://download.pytorch.org/whl/cu128
uv pip install --python .venv/bin/python -r requirements.txt

# 3. custom_nodes (list lives in this repo)
mkdir -p "$COMFY_HOME/custom_nodes"
while read -r name url pin _; do
  case "$name" in ''|\#*) continue ;; esac
  dest="$COMFY_HOME/custom_nodes/$name"
  install_deps=0
  if [ ! -d "$dest/.git" ]; then
    echo "[..] custom_node: $name"
    if [ -e "$dest" ]; then
      echo "[WARN] custom-node target exists but is not a git checkout: $dest"
      continue
    fi
    git clone --filter=blob:none "$url" "$dest" || {
      echo "[WARN] clone failed: $name ($url)"
      continue
    }
    install_deps=1
  fi

  if [ -n "${pin:-}" ]; then
    if [ -n "$(git -C "$dest" status --porcelain)" ]; then
      echo "[WARN] pinned custom node is dirty; leaving it untouched: $name"
      continue
    fi
    git -C "$dest" fetch --depth 1 origin "$pin" && \
      git -C "$dest" checkout --detach "$pin" || {
        echo "[WARN] could not checkout pinned commit for $name: $pin"
        continue
      }
    install_deps=1
  fi

  if [ "$install_deps" -eq 1 ] && [ -f "$dest/.gitmodules" ]; then
    git -C "$dest" submodule update --init --recursive || \
      echo "[WARN] submodule install failed: $name"
  fi

  requirements_file="$dest/requirements.txt"
  if [ "$name" = "ComfyUI-Frame-Interpolation" ]; then
    # RIFE VFI does not require CuPy.  The upstream no-CuPy set avoids its
    # optional install.py path changing the already verified CUDA/Torch ABI.
    requirements_file="$dest/requirements-no-cupy.txt"
  fi

  if [ "$install_deps" -eq 1 ] && [ -f "$requirements_file" ]; then
    if [ "$name" = "ComfyUI-Impact-Pack" ]; then
      # Impact Pack declares SAM2 as an unpinned VCS requirement.  Install its
      # remaining dependencies here; the exact SAM2 source revision is built
      # separately below so a future upstream HEAD cannot silently change the
      # FaceRefine runtime.
      impact_requirements="$(mktemp)"
      sed '\|^git+https://github.com/facebookresearch/sam2|d' \
        "$requirements_file" > "$impact_requirements"
      if ! timeout 240 uv pip install --python .venv/bin/python \
        -r "$impact_requirements"; then
        echo "[WARN] non-SAM2 requirements install failed: $name"
      fi
      rm -f "$impact_requirements"
    elif [ "$name" = "Nvidia_RTX_Nodes_ComfyUI" ]; then
      # The PyPI stub re-downloads a ~597 MB proprietary wheel while building
      # metadata.  Install the node's non-stub runtime dependencies first, then
      # NVIDIA's exact CPython 3.12 wheel with the official index digest.  Keep
      # --no-deps only on that wheel so a fresh install does not lose OpenCV or
      # Kornia while still avoiding a second pass through the PyPI stub.
      timeout 240 uv pip install --python .venv/bin/python \
        "opencv-contrib-python==4.11.0.86" "kornia==0.8.3" || \
        echo "[WARN] non-stub requirements install failed: $name"
      if .venv/bin/python -c \
        'from importlib.metadata import version; raise SystemExit(version("nvidia-vfx") != "0.1.0.1")' \
        2>/dev/null; then
        echo "[..] nvidia-vfx==0.1.0.1 already installed"
      else
        timeout 7200 uv pip install --python .venv/bin/python --no-deps \
          "https://pypi.nvidia.com/nvidia-vfx/nvidia_vfx-0.1.0.1-cp312-abi3-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl#sha256=e51d9e6faa68466e45b83be7928321af4b0c561c7c5536a8cb2b7e6aba25f905" || \
          echo "[WARN] requirements install failed: $name"
      fi
    else
      timeout 240 uv pip install --python .venv/bin/python \
        -r "$requirements_file" || \
        echo "[WARN] requirements install failed: $name"
    fi
  fi
done < "$REPO_DIR/custom_nodes.txt"

# The pinned Icyoung H3 TeaCache node keeps sampling state on its patched MODEL.
# ComfyUI may cache that MODEL between queued prompts, so the unmodified upstream
# revision can carry step counters into a later generation.  Apply Castle's
# small, auditable lifecycle overlay: V1 `IS_CHANGED = NaN` forces the patch node
# to rebuild a fresh TeaCacheState for each prompt.  The overlay is idempotent
# and its installed file is hash-checked by the H3 tutorial audit.
teacache_dir="$COMFY_HOME/custom_nodes/ComfyUI-MiniMaxH3-TeaCache"
teacache_patch="$REPO_DIR/patches/minimax-h3-teacache-fresh-state.patch"
if [ -d "$teacache_dir/.git" ]; then
  if git -C "$teacache_dir" apply --reverse --check "$teacache_patch"; then
    echo "[..] MiniMax H3 TeaCache lifecycle overlay already applied"
  elif git -C "$teacache_dir" apply --check "$teacache_patch"; then
    git -C "$teacache_dir" apply "$teacache_patch"
    echo "[..] applied MiniMax H3 TeaCache lifecycle overlay"
  else
    echo "[ERROR] MiniMax H3 TeaCache lifecycle overlay does not match the pinned source" >&2
    exit 1
  fi
fi

# Servasyy's published R2V workflow uses the historical node class name
# SpectrumSDXLBatch.  The pinned ruwwww revision implements batch-safe masking
# in SpectrumSDXL but does not export that alias.  Apply the smallest possible
# compatibility overlay; no sampler math, network access, or global ComfyUI
# state is changed.  The resulting __init__.py is locked in the tutorial
# manifest and runtime-audited through /object_info.
spectrum_sdxl_dir="$COMFY_HOME/custom_nodes/ComfyUI-Spectrum-SDXL"
spectrum_sdxl_patch="$REPO_DIR/patches/spectrum-sdxl-batch-alias.patch"
if [ -d "$spectrum_sdxl_dir/.git" ]; then
  if git -C "$spectrum_sdxl_dir" apply --reverse --check "$spectrum_sdxl_patch"; then
    echo "[..] Spectrum SDXL batch alias overlay already applied"
  elif git -C "$spectrum_sdxl_dir" apply --check "$spectrum_sdxl_patch"; then
    git -C "$spectrum_sdxl_dir" apply "$spectrum_sdxl_patch"
    echo "[..] applied Spectrum SDXL batch alias overlay"
  else
    echo "[ERROR] Spectrum SDXL batch alias overlay does not match the pinned source" >&2
    exit 1
  fi
fi

# Impact Pack's SAM2 dependency is required by the supplied H3 FaceRefine
# workflow.  Keep an auditable source checkout at an exact revision and build
# the pure-Python package without its optional connected-components CUDA
# extension.  That extension is not required for Impact Pack's SAM2 image
# predictor; disabling it also preserves the pinned Torch/CUDA ABI.
sam2_revision="2b90b9f5ceec907a1c18123530e92e794ad901a4"
sam2_source="$COMFY_HOME/.build/sam2-src"
mkdir -p "$COMFY_HOME/.build"
if [ ! -d "$sam2_source/.git" ]; then
  git clone --filter=blob:none --no-checkout \
    https://github.com/facebookresearch/sam2.git "$sam2_source"
fi
if [ -n "$(git -C "$sam2_source" status --porcelain)" ]; then
  echo "[ERROR] managed SAM2 source checkout is dirty: $sam2_source" >&2
  exit 1
fi
git -C "$sam2_source" fetch --depth 1 origin "$sam2_revision"
git -C "$sam2_source" checkout --detach "$sam2_revision"
timeout 300 env SAM2_BUILD_CUDA=0 \
  .venv/bin/python -m pip install --no-deps --no-build-isolation \
  --force-reinstall "$sam2_source"

# Build or reuse the locked SM120 SageAttention wheel without changing the
# ComfyUI Torch/Triton ABI.  The helper verifies wheel hashes, native fatbins,
# extension imports, and a small RTX 5090 BF16 kernel comparison.
"$REPO_DIR/scripts/minimax_h3/install_sageattention_sm120.sh" \
  --comfy-home "$COMFY_HOME"

.venv/bin/python - <<'PY'
import torch
import torchvision
import triton

expected = ("2.7.1+cu128", "0.22.1+cu128", "3.3.1")
found = (torch.__version__, torchvision.__version__, triton.__version__)
if found != expected:
    raise SystemExit(f"Pinned H3 runtime ABI drifted: expected {expected}, found {found}")
print(f"[..] verified H3 runtime ABI: torch={found[0]}, torchvision={found[1]}, triton={found[2]}")
PY

# The singing/lip-sync source repo intentionally keeps its reusable custom
# nodes nested beside its authoritative workflows.  Keep the parent checkout
# disabled as a top-level Comfy node, then expose only the two verified packs.
h3_parent_name="MiniMax-H3-NativeAudio-MusicVideo-Workflow.disabled"
h3_parent="$COMFY_HOME/custom_nodes/$h3_parent_name"
for nested in ComfyUI-H3-NativeAudioLock ComfyUI-H3-Multishot; do
  source_path="$h3_parent/custom_nodes/$nested"
  link_path="$COMFY_HOME/custom_nodes/$nested"
  relative_target="$h3_parent_name/custom_nodes/$nested"
  if [ ! -d "$source_path" ]; then
    echo "[WARN] nested H3 custom node missing: $source_path"
  elif [ -L "$link_path" ]; then
    if [ "$(readlink "$link_path")" != "$relative_target" ]; then
      echo "[WARN] nested H3 symlink points elsewhere; leaving it untouched: $link_path"
    fi
  elif [ -e "$link_path" ]; then
    echo "[WARN] nested H3 target already exists; leaving it untouched: $link_path"
  else
    ln -s "$relative_target" "$link_path"
  fi
done

# 4. workflows — expose this repo's workflows in ComfyUI without ever
#    replacing a real user directory.  A fresh ComfyUI checkout does not ship
#    user/default, so create the parent before making the link.  Fail closed on
#    a conflicting non-symlink instead of silently nesting another link inside
#    it or claiming success while no browser workflows are visible.
workflow_parent="$COMFY_HOME/user/default"
workflow_link="$workflow_parent/workflows"
workflow_source="$REPO_DIR/workflows"
mkdir -p "$workflow_parent"
if [ -L "$workflow_link" ]; then
  if [ "$(readlink -f "$workflow_link")" != "$(readlink -f "$workflow_source")" ]; then
    ln -sfn "$workflow_source" "$workflow_link"
  fi
elif [ -e "$workflow_link" ]; then
  echo "[ERROR] workflow target exists and is not a symlink: $workflow_link" >&2
  echo "        Move or reconcile it explicitly, then rerun install.sh." >&2
  exit 1
else
  ln -s "$workflow_source" "$workflow_link"
fi
if [ ! -L "$workflow_link" ] || \
   [ "$(readlink -f "$workflow_link")" != "$(readlink -f "$workflow_source")" ]; then
  echo "[ERROR] ComfyUI workflow link verification failed: $workflow_link" >&2
  exit 1
fi

# 5. Optional models: use the installation owner's configured model directory.
#    Downloads remain explicit and model-specific authorization gates apply.
if [ -n "$H3_BASELINE_FAMILY" ]; then
  "$COMFY_HOME/.venv/bin/python" \
    "$REPO_DIR/scripts/minimax_h3/download_models.py" \
    --family "$H3_BASELINE_FAMILY" \
    --models-dir "$COMFY_HOME/models"
fi

if [ "$H3_TUTORIAL_ASSETS" -eq 1 ]; then
  "$COMFY_HOME/.venv/bin/python" \
    "$REPO_DIR/scripts/minimax_h3/provision_tutorial_assets.py" \
    --required \
    --models-dir "$COMFY_HOME/models" \
    --accept-large-downloads \
    --execute
fi

if [ "$H3_OLLAMA" -eq 1 ]; then
  ollama_args=(--execute --install-user-service)
  if [ "$H3_OLLAMA_MODEL" -eq 1 ]; then
    ollama_args+=(--start-service --pull-model --accept-large-downloads)
  fi
  python3 "$REPO_DIR/scripts/minimax_h3/provision_ollama_qwen35.py" \
    "${ollama_args[@]}"
fi

if [ -z "$H3_BASELINE_FAMILY" ] && \
   [ "$H3_TUTORIAL_ASSETS" -eq 0 ] && \
   [ "$H3_OLLAMA" -eq 0 ]; then
  echo "[..] models/Ollama unchanged (large downloads require explicit H3 flags; see README.md)"
fi

echo "==> Done. Run: cd $COMFY_HOME && .venv/bin/python main.py --listen 127.0.0.1"
echo "    Verify GPU: .venv/bin/python -c 'import torch; print(torch.cuda.get_device_name(0))'"

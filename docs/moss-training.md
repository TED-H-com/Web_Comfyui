# KohakuMoss training integration

This optional command-line integration prepares a MOSS-Image character LoRA run in a separate KohakuMossTrainer checkout and environment. It does not add a ComfyUI training node. The wrapper's config, download guards and filesystem checks have CPU tests; training, image quality and loading the export into ComfyUI remain unqualified locally.

The reviewed trainer revision is `da5dd97e15492e4dfb3caa171f023388004884ff`. Model revision, file sizes and Hugging Face LFS SHA256 values are recorded in [the lock](../manifests/moss/upstream.lock.json). Source pinning does not lock every transitive Python dependency.

## Prepare without installing or using the GPU

Run these from this repository. Python 3.10+ is sufficient for the wrapper; it imports no ML packages and makes no network requests in `plan`, `config`, or `preflight`.

```bash
python3 scripts/moss_trainer.py plan
python3 scripts/moss_trainer.py config \
  --trainer "$HOME/tools/KohakuMossTrainer" \
  --weights "$HOME/ComfyUI/models" \
  --dataset "$HOME/datasets/moss-character" \
  --cache "$HOME/agent-work/moss-cache" \
  --name kohaku-character-pilot --steps 30 \
  --output "$HOME/agent-work/moss-config/kohaku-character-pilot.py"
python3 scripts/moss_trainer.py preflight \
  --trainer "$HOME/tools/KohakuMossTrainer" \
  --weights "$HOME/ComfyUI/models" \
  --dataset "$HOME/datasets/moss-character"
```

`config` writes the training config and its `_data.py` companion, refusing existing files. Without `--output`, it prints the training config. The 30-step pilot inherits upstream's 24 GB bf16 balanced recipe, rank 32, learning rate `1e-4`, 1 MP area and offline caches. It disables periodic samples, uses a short warmup, and requests ComfyUI adapter export. Use a new run name and config path for a longer experiment; `--steps 1500` is a candidate, not a quality guarantee. Smaller cards need another upstream memory recipe; this wrapper deliberately does not claim universal memory fitting.

Use an image folder with one nonempty UTF-8 `.txt` caption per image. Captions are natural language, not shuffled comma tags. Give the character a consistent trigger token and describe changing clothing, poses, setting and lighting. Keep a held-out evaluation set. Only use data you are authorized to train on. Filesystem preflight checks supported image filenames and captions, but does not decode images; upstream's data inspection performs that next stage.

`preflight` exits 1 for missing/mismatched prerequisites, 0 for filesystem readiness, and 2 for an operational error. It checks the trainer revision/license, required entrypoints, dataset captions and exact pinned model sizes. `--hash-models` additionally reads all model bytes to validate SHA256. Size-only success is not integrity verification, dependency validation or GPU qualification.

## Explicit installation and model acquisition

Read the [pinned trainer license](https://github.com/KohakuBlueleaf/KohakuMossTrainer/blob/da5dd97e15492e4dfb3caa171f023388004884ff/LICENSE) first. **KohakuMoss License 1.0 is Apache-2.0 with an additional naming requirement**, not plain Apache-2.0. Section 10 covers product/package/service branding incorporating the trainer, and exempts its model/image outputs. Keep the integration's Kohaku name and retain upstream notices when distributing upstream code. This repository supplies an independent wrapper and references upstream; it does not vendor the trainer.

Both download commands default to printing their command lists. Execution requires the explicit flags shown:

```bash
python3 scripts/moss_trainer.py setup --execute \
  --allow-downloads --accept-upstream-license
python3 scripts/moss_trainer.py models --execute \
  --allow-downloads --accept-model-license
```

`setup` only checks out the pinned source into a new directory; it refuses an existing directory and verifies the license hash. `models` requires the separately installed Hugging Face `hf` CLI, downloads the pinned Base transformer/int8 Qwen encoder/small VAE (15.41 GB decimal total), then checks SHA256. It refuses to overwrite existing model files that differ from the lock. The model repository declares Apache-2.0 in its card metadata; retain and review component/model notices. Use `--variant sft` to fetch the SFT transformer instead. No credentials are stored in configs or manifests; gated/private downloads, if selected separately, use the user's own CLI credentials.

Dependencies remain a separate operator action, executed inside the trainer checkout:

```bash
cd "$HOME/tools/KohakuMossTrainer"
bash scripts/install/install.sh
```

This upstream installer creates its own `.venv`, normally Python 3.12, and chooses a PyTorch wheel index for the detected driver. At the pin, its torch constraint is `>=2.13,<2.15`; do not reuse or upgrade ComfyUI's environment to satisfy it. Inspect the printed index and upstream installation guidance for the actual GPU/driver. The install command performs downloads and runs the upstream doctor; it has not been executed as part of this integration. [Pinned installation code](https://github.com/KohakuBlueleaf/KohakuMossTrainer/blob/da5dd97e15492e4dfb3caa171f023388004884ff/scripts/install/install.sh).

## Inspect, cache, train and qualify

After successful preflight, run the printed `kohakumoss-config check` and `kogine` commands from the trainer checkout. Config checking and data inspection precede the two cache passes. **Caching and training use the GPU**; schedule them when the device and host RAM are available. The wrapper never executes those commands. The cache directory should be disk-backed; allow space for latent/text caches and checkpoints.

```bash
cd "$HOME/tools/KohakuMossTrainer"
.venv/bin/kohakumoss-config check "$HOME/agent-work/moss-config/kohaku-character-pilot.py"
.venv/bin/kogine run scripts/data/inspect_data.py --config "$HOME/agent-work/moss-config/kohaku-character-pilot_data.py"
.venv/bin/kogine run scripts/data/cache_text.py --config "$HOME/agent-work/moss-config/kohaku-character-pilot_data.py"
.venv/bin/kogine run scripts/data/cache_latents.py --config "$HOME/agent-work/moss-config/kohaku-character-pilot_data.py"
.venv/bin/kogine run scripts/train/moss.py --config "$HOME/agent-work/moss-config/kohaku-character-pilot.py"
```

The expected adapter is `runs/moss/kohaku-character-pilot/export/kohaku-character-pilot-comfy.safetensors`. Keep training states and caches outside git. Copy the exported adapter into your chosen ComfyUI `models/loras/` folder only after the pilot succeeds. Load it with the native MOSS-Image model and `LoraLoaderModelOnly`; inspect unmatched-key warnings, then compare strength 0 and 1 at the same prompt/seed/model/size. A completed training process alone does not establish that the adapter loaded or preserved identity.

| Artifact | Compatibility |
| --- | --- |
| MOSS-Image Base adapter | Use the matching Base transformer first; SFT transfer needs its own comparison. |
| ComfyUI export | Uses native MOSS module names and fused q/k/v repacking. Requires ComfyUI with MOSS-Image support. |
| Raw `trainable.safetensors` | Training state; export through upstream before installing as a ComfyUI LoRA. |
| SDXL, FLUX, Qwen-Image or H3 LoRA | Different architectures; not MOSS adapters. Sharing a VAE does not make adapters compatible. |

[Upstream export documentation](https://github.com/KohakuBlueleaf/KohakuMossTrainer/blob/da5dd97e15492e4dfb3caa171f023388004884ff/docs/guides/export-to-comfy.md) explains the key conversion; [weight placement](https://github.com/KohakuBlueleaf/KohakuMossTrainer/blob/da5dd97e15492e4dfb3caa171f023388004884ff/docs/guides/weights.md) documents both ComfyUI and HF layouts.

## What the motivating experiment establishes

[DSLab's MOSS article](https://dslab.tw/moss-image-lora), dated October 20, 2026, reports a Windows RTX 5090 experiment with 27 images, rank 32 and 1,500 steps, about 72 minutes of training. Its Base-trained adapter also worked on SFT in that experiment. The author checked real loader effect, found clothing leakage from repeated training examples, and found complex repeated decorations less reliable at 4 MP. These are useful evaluation targets, not results reproduced here. The article reports native MOSS support beginning with ComfyUI 0.41.3; check your installed build and loader schema before rendering. No article training data, character assets or generated images are redistributed.

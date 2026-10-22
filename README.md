# Web_Comfyui

A local Web UI for your own ComfyUI installation: image and video tools, workflow browsing, character and voice libraries, media management, generation queues, and configurable provider integrations. The interface currently uses Traditional Chinese for many controls.

Run it on Linux or WSL2 with Python 3.11+ and Pillow. ComfyUI runs separately; this repository does not bundle models, personal characters, voices, API keys, accounts, or generation history. GPU workflows depend on the models, nodes, memory, and accelerator supported by your ComfyUI installation. A CPU can run the Web UI; this does not make demanding GPU workflows usable on a CPU.

## Quick start

Start your existing ComfyUI on `127.0.0.1:8188`, then clone this repository:

```bash
git clone https://github.com/TED-H-com/Web_Comfyui.git
cd Web_Comfyui
```

Install the small WebUI environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-webui.txt
cp .env.example .env
chmod 600 .env
```

Edit `COMFY_HOME` in `.env` if your ComfyUI directory is not `~/comfyui`. Then:

```bash
.venv/bin/python run.py --check
.venv/bin/python run.py
```

Open **http://127.0.0.1:8190** and set your local administrator password on first use. No preset account or password exists. Stop the Web UI with Ctrl+C; ComfyUI remains independently managed.

`--check` reads the configured local ComfyUI status endpoint and reports optional dependency readiness. It does not install software, download models, submit generation jobs, reveal credentials, or attest that every workflow is ready. Exit code `1` means the platform/Python/Pillow requirement or ComfyUI connectivity check failed; optional components can be unavailable while basic tools work.

## What is included

- Existing generation forms and settings, workflow library and upstream workflow catalog.
- Empty, user-managed character, voice, and material libraries.
- OpenAI/Gemini post generation, Google Drive import, and provider account APIs that you configure using your own credentials.
- Orchestration and optional voice, reel, and model-specific adapters. Their external tools and models must be installed and configured separately.

Published workflow files and catalog entries do not guarantee local readiness. Missing nodes/models must be installed into **ComfyUI's environment**. Community catalog entries retain their upstream sources and licensing context. H3 and other advanced runtime paths have no inherited private qualification receipts; a new installation must establish its own readiness.

## Configuration and data

Only this checkout's `.env` is loaded by the launcher. It parses plain `KEY=VALUE` assignments without executing shell commands, expanding `$VARIABLES`, or searching another project's files. Existing process environment settings take precedence. Use `--no-env` to skip `.env`.

Application state defaults to `~/.local/share/web-comfyui`, cache and disk temporary files to `~/.cache/web-comfyui`. Images and videos remain in your configured ComfyUI input/output directories. API credentials entered through provider account management are stored in the private application data directory; environment credentials stay in your process or explicit secrets file.

Both services should stay bound to loopback. The launcher enforces this for the Web UI and ComfyUI URL. Any reverse proxy or remote access is a separate deployment decision; do not expose an unprotected ComfyUI listener.

See [installation and feature requirements](docs/installation.md) for Linux/WSL setup, configuration, optional dependencies, and troubleshooting. The existing `install.sh` is an **optional advanced ComfyUI provisioner** targeting a pinned NVIDIA Blackwell stack. It can change your ComfyUI checkout, Python packages, custom nodes, patches, and workflow links. It is not required to install this Web UI and should not be run against an existing environment without reviewing it.

## Workflow and API guides

- [繁體中文快速開始](README.zh-TW.md)
- [Community workflows and source catalog](docs/community-workflows.md)
- [Included workflows, licenses, and coverage](docs/workflow-coverage.md)
- [Local LLM, H3, and NVIDIA VFX recipes](docs/rtx-video-research.md)
- [MOSS image LoRA setup and preflight](docs/moss-training.md)
- [AutoIP setup and supported API operations](docs/autoip-api.md)

The initial release includes **40 workflow JSON files**, a source catalog covering
both requested community collections, and **8 article-derived recipes**. The WebUI's
**工作庫** page searches categories, models and nodes; shows source/license details;
checks your local ComfyUI node inventory; and lets an operator load a visual workflow
into ComfyUI's `webui-library` folder. Loading a graph does not run it or install its
dependencies. Open it in ComfyUI to supply your files, choose installed models, and
execute. Community references without redistributed JSON remain clearly labeled.

WebUI code is GPL-3.0. Third-party files retain their own licenses; see
[NOTICE.md](NOTICE.md). Model weights and optional trainer/runtime components are
not bundled or relicensed.

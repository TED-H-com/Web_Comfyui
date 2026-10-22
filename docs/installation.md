# Installation and configuration

## Supported starting point

Use Linux, or a Linux distribution under WSL2, with Python 3.11 or newer. The backend uses POSIX file locking and process controls; native Windows is not supported by this launcher. Under WSL, install and run both applications inside the same Linux distribution so local files and paths agree. Use a Linux filesystem for application state and disk temporary files.

Install ComfyUI separately using its own installation instructions, environment, models, and GPU drivers. Keep it bound to `127.0.0.1`. Web_Comfyui does not choose PyTorch/CUDA wheels for an existing ComfyUI installation.

For an existing installation using a `.venv`, a typical start command from its directory is:

```bash
.venv/bin/python main.py --listen 127.0.0.1 --port 8188
```

Adapt the Python executable to your actual installation. Confirm that its node editor opens at `http://127.0.0.1:8188` before starting this UI.

## Install the Web UI

From the Web_Comfyui checkout:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-webui.txt
cp .env.example .env
chmod 600 .env
```

The backend uses the standard library plus Pillow, which is required at server import and provides image validation, thumbnails, and profile previews. The small requirements file installs Pillow. It does not install Torch, CUDA, voice models, or ComfyUI custom nodes.

Edit `.env` with your ComfyUI directory. Do not `source` this file: `run.py` parses it directly without executing shell syntax. Examples:

```dotenv
COMFY_HOME=/absolute/path/to/ComfyUI
COMFYUI_URL=http://127.0.0.1:8188
COMFYUI_BROWSER_URL=http://127.0.0.1:8188
CASTLE_COMFY_WEB_PORT=8190
```

Then run:

```bash
.venv/bin/python run.py --check
.venv/bin/python run.py
```

Open `http://127.0.0.1:8190`, set a password of at least eight characters, and sign in. First-run setup is local-only. Keep your own state directory when updating the checkout. No private deployment accounts, character assets, voice recordings, or generation records are included.

The launcher leaves the configured backend URL intact. Most standard forms and workflow-library operations use `COMFYUI_URL`; certain qualified H3/identity execution paths intentionally require `127.0.0.1:8188`. Use the standard port for those paths. Their model/node/authorization checks may block execution until the local installation is qualified.

## Settings

| Setting | Purpose |
| --- | --- |
| `COMFY_HOME` | Installed ComfyUI root; defaults to `~/comfyui`. |
| `COMFYUI_URL` | Existing ComfyUI loopback HTTP API; defaults to port 8188. |
| `COMFYUI_BROWSER_URL` | ComfyUI node-editor link; normally the same local URL. |
| `COMFYUI_INPUT_DIR`, `COMFYUI_OUTPUT_DIR`, `COMFYUI_TEMP_DIR`, `COMFYUI_MODELS_DIR` | Optional explicit absolute directories; otherwise derived from `COMFY_HOME`. |
| `WEB_COMFYUI_DATA_DIR` | Persistent UI data; defaults to `~/.local/share/web-comfyui`. Choose a directory outside this checkout. |
| `WEB_COMFYUI_CACHE_DIR` | Cache root; defaults to `~/.cache/web-comfyui`. |
| `WEB_COMFYUI_TMP_DIR` | Disk temporary directory; defaults to the cache root's `tmp` directory. RAM-backed locations are rejected. |
| `CASTLE_COMFY_WEB_PORT` | UI port; defaults to 8190. `--port` overrides it. |
| `OPENAI_API_KEY`, `GEMINI_API_KEY` | Your optional language-model API credentials. |
| `CASTLE_VOICE_LAB_ENV` | Optional absolute path to a local `KEY=VALUE` secrets file. No implicit credential-file search occurs. |
| `CASTLE_OPERATOR_EMAIL` | Optional operator identity for account-based login. Local admin sessions already have operator access. |

Quoted `.env` values have their surrounding quotes removed; escapes, substitutions, multiline values, and inline comments are not interpreted. For a value containing `#`, keep it as part of the value. Put comments on their own lines. Environment variables already supplied to the process override `.env` values. Keep `.env`, credentials, databases, and generated files out of source control.

Provider credentials can also be configured through the provider UI where supported. The built-in API adapters remain available, but no paid API credentials or account identifiers are supplied. Account setup and provider-specific validation remain necessary. Google Drive import additionally requires your own OAuth client, client secret, and registered redirect URI.

## Feature requirements

| Feature | Additional runtime requirements |
| --- | --- |
| Web UI, libraries, queue management | Python 3.11+, Pillow, and a reachable ComfyUI for generation. |
| Image thumbnails, image import, profile preview | Pillow in the Web UI environment. |
| Video/audio previews, export, assembly | `ffmpeg` and `ffprobe` available on PATH, plus the corresponding models/nodes. |
| URL media collection | `yt-dlp` for supported sources; certain crawler routes require a separately configured crawler helper. |
| Image/video workflow generation | Referenced checkpoints, VAEs, encoders, custom nodes, and suitable hardware in ComfyUI. |
| OpenAI/Gemini text generation | Your API credentials and selected provider model. |
| Local Claude text generation | A separately installed, authenticated Claude CLI configured with `CASTLE_CLAUDE_CLI`. |
| BreezyVoice personas | Your own service configured with `CASTLE_BREEZY_URL`, reference voices, and required voice/GPU helper tools. |
| VoxCPM cloning / voiceover | Your own VoxCPM installation, model files, configured Python executables, GPU queue helper, and reference audio/transcript. |
| Speaker/content checks | Matching optional audio environment dependencies: Torch, torchaudio, NumPy, soundfile, ONNX Runtime, and Whisper as required by the selected worker. |
| Reel rendering and specialist adapters | External rendering/QC/helper scripts, assets, and models configured through the corresponding `CASTLE_*` paths. They are not bundled. |
| H3 production paths | Required model license authorization and local runtime qualification records. Private deployment records were intentionally omitted. |

Install optional audio/GPU packages in the relevant **worker environment**, not blindly into the Web UI virtual environment. `--check` checks the lightweight UI dependencies and ComfyUI status only. It does not load or qualify models, inspect API credential values, or execute external helpers. A reachable ComfyUI and a reported GPU do not prove a specific large model will fit in VRAM.

The retained H3 authorization helper uses an owner-managed record with a generic `operator` identifier and preserves its original territory/usage validation. Do not treat a bundled workflow, successful preflight, or an authorization record as a model license grant. Advanced model-specific installation paths require separate review for your intended deployment.

## Optional advanced provisioner

`install.sh` is a separate, hardware-specific ComfyUI provisioner for the pinned Blackwell/CUDA stack. It may clone or change the ComfyUI revision, install packages/custom nodes, apply runtime patches, and replace the workflow link. Optional flags can download large model assets and install Ollama components. It is not part of the normal Web UI setup above.

Review the script and its pinned manifests first. Use an isolated ComfyUI target through `COMFY_HOME` if evaluating it. The public distribution includes no prior runtime qualification evidence or personal media; unavailable optional workflow assets and records must be supplied or regenerated by the installation owner.

## Troubleshooting

- **ComfyUI is unreachable:** start ComfyUI separately, confirm its port, and check `COMFYUI_URL`. The check does not start or restart it.
- **Models appear missing despite being installed:** make sure `COMFY_HOME` and any explicit model/input/output directories match the ComfyUI process. Restart Web_Comfyui after changing `.env`.
- **Missing nodes:** install the required custom nodes into ComfyUI's environment and restart ComfyUI. Installing packages only into the Web UI environment does not provide nodes.
- **A voice/reel tool reports unavailable helpers:** configure its separate runtime and assets. Those integrations are preserved but not automatically installed.
- **Port 8190 is in use:** run `python run.py --port 8192` and open that local port.
- **Temporary directory rejected:** choose a disk-backed path through `WEB_COMFYUI_TMP_DIR`; do not use `/dev/shm` or another tmpfs.
- **Password/state location:** the launcher stores local authentication under the application data root's `auth` directory. Back up that directory with the rest of your application state.

For a remote browser, use an authenticated, operator-managed access solution while retaining loopback listeners. Proxy headers and origin checks affect the authentication lane; review the deployment settings instead of changing the listener to `0.0.0.0`.

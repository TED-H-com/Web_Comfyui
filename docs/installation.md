# Installation and configuration

Start with [the quick start](../README.md) or [繁體中文逐步安裝](../README.zh-TW.md). This page explains environment settings, optional components and troubleshooting. After installation, follow [your first workflow](getting-started.md). The [project homepage](https://ted-h-com.github.io/Web_Comfyui/) introduces installation choices. The separate [interactive demo](https://ted-h-com.github.io/Web_Comfyui/demo/) is not a live service and shares the actual app interface: two help buttons open the source-backed guide and node view, while the existing generation button shows a recorded result when GPU execution evidence exists, or an explicit unavailable state. Local file selection or dropping provides browser-only previews, with no upload or generation. Use the top-right **繁中 / English** control on the website or in the installed app. Install locally to perform real work.

The site's 23 feature guides distinguish ComfyUI node graphs from explanatory service-process diagrams. Recorded results require evidence of actual GPU execution; features without verified results explicitly remain unavailable. Reference illustrations are input assets, not proof that a workflow ran. [Provenance](../webui/docs/examples/provenance.json) and [publication rules](site.md) keep reviewed documentation assets separate from user media. The workflow library contains 248 entries and 46 downloadable graphs.

The demo hardware selector uses planning estimates for no NVIDIA GPU, 8/12/16/24/32 GB VRAM, and two 24 GB cards. It does not detect hardware, benchmark workflows or configure the installed application. Two 24 GB cards are treated as separate devices, not pooled 48 GB. Selecting no GPU does not disable your local app settings; actual readiness comes from your own backend and dependencies. The AutoIP option opens all 23 demo forms, not all real cloud APIs. See [cloud capabilities, current integration boundaries and referral disclosure](autoip-cloud-demo.md) before choosing a provider.

## Supported starting point

Use Linux, or a Linux distribution under WSL2, with Python 3.11 or newer. Descriptor locking has a cross-platform backend, but the launcher and protected stores still use POSIX directory descriptors, ownership/mode checks and process controls. Native Windows is not supported by the complete launcher. Under WSL, install and run both applications inside the same Linux distribution so local files and paths agree. Use a Linux filesystem for application state and disk temporary files.

Install ComfyUI separately using its [official installation instructions](https://docs.comfy.org/installation/manual_install), environment, models, and GPU drivers. Keep it bound to `127.0.0.1`. Web_Comfyui does not choose PyTorch/CUDA wheels for an existing ComfyUI installation.

For an existing installation using a `.venv`, a typical start command from its directory is:

```bash
.venv/bin/python main.py --listen 127.0.0.1 --port 8188
```

Adapt the Python executable to your actual installation. Confirm that its node editor opens at `http://127.0.0.1:8188` before starting this UI. Keep ComfyUI running and open a second terminal for Web_Comfyui. These are two processes with two Python environments.

## Install the Web UI

From the Web_Comfyui checkout:

```bash
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-webui.txt
cp .env.example .env
chmod 600 .env
```

The version check must report Python 3.11 or newer. If `venv` or `ensurepip` is unavailable, install the matching Python venv package for your Linux distribution before continuing. Run the `cp` command only for initial setup; do not overwrite an existing `.env` during an update.

The backend uses the standard library plus Pillow, which is required at server import and provides image validation, thumbnails, and profile previews. The small requirements file installs Pillow. It does not install Torch, CUDA, voice models, or ComfyUI custom nodes.

Edit `.env` with your ComfyUI directory. Do not `source` this file: `run.py` parses it directly without executing shell syntax. Examples:

```dotenv
COMFY_HOME=/absolute/path/to/ComfyUI
COMFYUI_URL=http://127.0.0.1:8188
COMFYUI_BROWSER_URL=http://127.0.0.1:8188
WEB_COMFYUI_PORT=8190
```

Then run:

```bash
.venv/bin/python run.py --check
.venv/bin/python run.py
```

Open `http://127.0.0.1:8190`, set a local administrator password of at least eight characters, and choose **設定並進入**. Successful setup signs you in immediately. There is no preset username or password; on later visits use the password you chose. First-run setup is local-only. Keep your own state directory when updating the checkout. No user accounts, personal character assets, voice recordings, or generation records are included. The dedicated public demo reference and result assets are not seeded into your character or media libraries.

### Understand the readiness report

`run.py --check` prints JSON and exits. It reads ComfyUI's `/system_stats` endpoint and checks for installed command/package names; it does not create application directories, install software, load a model or queue a render.

| Result | Meaning |
| --- | --- |
| Exit `0` | Linux, Python 3.11+, Pillow and ComfyUI API connectivity checks passed. |
| Exit `1` | At least one of those four basic checks failed; inspect the `false` fields. |
| Exit `2` | Invalid configuration, such as a non-loopback API URL or unsuitable temporary directory. |
| `comfy_directory_exists: false` | Review `COMFY_HOME`; this field does not determine the exit code. |
| Optional dependency or integration is `false` | Only the corresponding feature may be unavailable; it is not a reason to install every optional tool. |

The report checks whether API environment variables are present, not whether a provider accepts the credentials. It does not print their values. `comfy_gpu_reported: true` is device detection, not proof that a particular model fits or renders correctly.

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
| `WEB_COMFYUI_PORT` | UI port; defaults to 8190. `--port` overrides it. |
| `OPENAI_API_KEY`, `GEMINI_API_KEY` | Your optional language-model API credentials. |
| `WEB_COMFYUI_VOICE_LAB_ENV` | Optional absolute path to a local `KEY=VALUE` secrets file. No implicit credential-file search occurs. |
| `WEB_COMFYUI_OPERATOR_EMAIL` | Optional operator identity for account-based login. Local admin sessions already have operator access. |

Quoted `.env` values have their surrounding quotes removed; escapes, substitutions, multiline values, and inline comments are not interpreted. The launcher expands `~` for `COMFY_HOME` and `WEB_COMFYUI_*` directories, but it does not expand `$HOME` or other variables. Use absolute paths for other directory overrides. For a value containing `#`, keep it as part of the value. Put comments on their own lines. Environment variables already supplied to the process override `.env` values. Keep `.env`, credentials, databases, and generated files out of source control.

Provider credentials can also be configured through the provider UI where supported. The built-in API adapters remain available, but no paid API credentials or account identifiers are supplied. Account setup and provider-specific validation remain necessary. Google Drive import additionally requires your own OAuth client, client secret, and registered redirect URI.

Application API keys do not automatically configure every community workflow. An imported API node may have its own endpoint, model and credential settings in ComfyUI. Configure those separately and inspect the graph before sharing it. Requests to an external provider send the selected text/media to that provider and may incur its normal charges. Local-only workflows do not require an API subscription.

## Feature requirements

| Feature | Additional runtime requirements |
| --- | --- |
| Web UI, libraries, queue management | Python 3.11+, Pillow, and a reachable ComfyUI for generation. |
| Image thumbnails, image import, profile preview | Pillow in the Web UI environment. |
| Video/audio previews, export, assembly | `ffmpeg` and `ffprobe` available on PATH, plus the corresponding models/nodes. |
| URL media collection | `yt-dlp` for supported sources; certain crawler routes require a separately configured crawler helper. |
| Image/video workflow generation | Referenced checkpoints, VAEs, encoders, custom nodes, and suitable hardware in ComfyUI. |
| OpenAI/Gemini text generation | Your API credentials and selected provider model. |
| Local Claude text generation | A separately installed, authenticated Claude CLI configured with `WEB_COMFYUI_CLAUDE_CLI`. |
| BreezyVoice personas | Your own service configured with `WEB_COMFYUI_BREEZY_URL`, reference voices, and required voice/GPU helper tools. |
| VoxCPM cloning / voiceover | Your own VoxCPM installation, model files, configured Python executables, GPU queue helper, and reference audio/transcript. |
| Speaker/content checks | Matching optional audio environment dependencies: Torch, torchaudio, NumPy, soundfile, ONNX Runtime, and Whisper as required by the selected worker. |
| Reel rendering and specialist adapters | External rendering/QC/helper scripts, assets, and models configured through the corresponding `WEB_COMFYUI_*` paths. They are not bundled. |
| H3 production paths | Required model license authorization and local runtime qualification records established by the installation owner. |

Install optional audio/GPU packages in the relevant **worker environment**, not blindly into the Web UI virtual environment. `--check` checks the lightweight UI dependencies and ComfyUI status only. It does not load or qualify models, inspect API credential values, or execute external helpers. A reachable ComfyUI and a reported GPU do not prove a specific large model will fit in VRAM.

H3's authorization helper uses an owner-managed record with a generic `operator` identifier and performs territory/usage validation. A bundled workflow, successful preflight or local record does not replace the model's license terms. Advanced model-specific installation paths require separate review for your intended deployment.

## Models and custom nodes

Open a workflow card in **工作庫** to inspect its required model filenames and node types. Choose a graph matching your existing installation before adding more dependencies. Acquire models from their documented source and place them in the ComfyUI folders specified by that source. Keep model families and variants matched: a LoRA for one architecture is not interchangeable with another.

Install custom-node dependencies using **ComfyUI's Python**, not this checkout's `.venv`. Follow each node pack's instructions and restart ComfyUI when required. **檢查 ComfyUI 節點與模型** compares registered node names and recognized loader selections with model options reported by ComfyUI’s `/object_info`. It distinguishes missing dependencies from unknown checks, including unsupported loaders or connected model selections. It does not download dependencies, validate weight integrity or prove GPU compatibility. See [the workflow walkthrough](getting-started.md) for the difference between loading a graph and running it.

## Optional advanced provisioner

`install.sh` is a separate, hardware-specific ComfyUI provisioner for the pinned Blackwell/CUDA stack. It may clone or change the ComfyUI revision, install packages/custom nodes, apply runtime patches, and replace the workflow link. Optional flags can download large model assets and install Ollama components. It is not part of the normal Web UI setup above.

Review the script and its pinned manifests first. Use an isolated ComfyUI target through `COMFY_HOME` if evaluating it. The distribution includes no prior runtime qualification evidence or personal media; unavailable optional workflow assets and records must be supplied or regenerated by the installation owner.

## Daily use and updates

Start ComfyUI, then run `.venv/bin/python run.py` from this checkout. Reuse the existing virtual environment and `.env`; repeat dependency installation only when the project requirements change. Restart Web_Comfyui after editing `.env`. Ctrl+C in its terminal stops the Web UI without stopping a separately running ComfyUI.

Before updating, back up `.env`, your persistent application data directory, and any ComfyUI inputs/outputs you want to retain. Provider credentials entered through account management live in the private application data directory; do not treat that directory as shareable project source. Cache and disk temporary files use the separate configured cache root.

## Troubleshooting

- **ComfyUI is unreachable:** start ComfyUI separately, confirm its port, and check `COMFYUI_URL`. The check does not start or restart it.
- **Models appear missing despite being installed:** make sure `COMFY_HOME` and any explicit model/input/output directories match the ComfyUI process. Restart Web_Comfyui after changing `.env`.
- **Missing nodes:** install the required custom nodes into ComfyUI's environment and restart ComfyUI. Installing packages only into the Web UI environment does not provide nodes.
- **A voice/reel tool reports unavailable helpers:** configure its separate runtime and assets. Those integrations are preserved but not automatically installed.
- **Port 8190 is in use:** run `.venv/bin/python run.py --port 8192` and open `http://127.0.0.1:8192`.
- **A loaded workflow did not start rendering:** loading only saves it into ComfyUI's `webui-library` folder. Open it in the native editor, set inputs/models and queue it there.
- **Repeated import or destination conflict:** importing an identical graph again succeeds and points to the existing copy. Different content at the same destination produces a conflict and remains untouched; inspect the saved graph in `webui-library` before deciding how to keep both versions.
- **No “載入 ComfyUI” button:** the entry may be a recipe, source-only reference or API-format graph, or the current account may not have operator access. Use the entry's available download/source actions.
- **Temporary directory rejected:** choose a disk-backed path through `WEB_COMFYUI_TMP_DIR`; do not use `/dev/shm` or another tmpfs.
- **Password/state location:** the launcher stores local authentication under the application data root's `auth` directory. Back up that directory with the rest of your application state.

For a remote browser, use an authenticated, operator-managed access solution while retaining loopback listeners. Proxy headers and origin checks affect the authentication lane; review the deployment settings instead of changing the listener to `0.0.0.0`.


## Public environment names and portability

At startup, `run.py` and direct `webui/server.py` launches translate
`WEB_COMFYUI_<suffix>` to the legacy `CASTLE_<suffix>` setting. Existing names
remain valid. `WEB_COMFYUI_HOST` and `WEB_COMFYUI_PORT` map to
`CASTLE_COMFY_WEB_HOST` and `CASTLE_COMFY_WEB_PORT`; data/cache/tmp directory
settings keep their existing public meanings. A public name wins when both
names are explicitly supplied, even if its value is empty. Process environment
values take priority over `.env` across aliases; `--port` still wins over both.
Standalone specialist scripts retain their documented configuration interfaces.

`WEB_COMFYUI_GPU_LOCK_PATH` (legacy `CASTLE_GPU_LOCK_PATH`) selects an absolute
shared GPU lock path. The default remains
`~/.cache/web-comfyui-gpu-queue/rtx5090.lock`, even before that file exists:
choosing a different name based on file existence can split an idle legacy
queue and a new worker onto two independent locks. A new installation may
explicitly choose a neutral name such as `gpu0.lock`. Keep one fixed path
across every worker and external queue script that shares a GPU. Never remove
an active lock file to migrate names.

The `webui/file_lock.py` primitive retains `fcntl.flock` on POSIX and uses
[Portalocker's Win32 implementation](https://github.com/wolph/portalocker/blob/v3.2.0/portalocker/portalocker.py)
on Windows. Shared/exclusive lock and contention behavior have isolated tests.
The Windows-only requirements include Portalocker and pywin32. This removes
unconditional `fcntl` imports, but is **not full Windows portable support**:
protected filesystem stores still require a Windows ACL/reparse-point design,
process handling needs porting, and end-to-end native Windows validation has
not been performed. Use Linux/WSL2 for the application until those are completed.

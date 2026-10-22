# Web_Comfyui

A local front end for your own ComfyUI: image and video forms, a searchable workflow library, generation queues, and user-managed character, voice and media collections. Use the top-right **繁中 / English** control to switch the interface language on the documentation site or in the installed app.

[Project overview](https://ted-h-com.github.io/Web_Comfyui/) · [Interactive demo](https://ted-h-com.github.io/Web_Comfyui/demo/) · [Install locally](docs/installation.md) · [繁體中文安裝與操作](README.zh-TW.md)

The website root introduces the project and installation choices. Its **`./demo/` interactive demo is not a live generation service**: it uses the same interface and forms as the app, with 23 source-backed feature guides. Each tool has two help buttons: **查看流程與模型** explains its actual builder, models and defaults; **查看 ComfyUI 範本與節點** opens its node diagram. Diagrams distinguish actual ComfyUI graph structure from explanatory service-process diagrams.

Try the prefilled forms and use the existing **Generate** button to inspect a recorded example. All 23 tools and character creation retain their guides; features without a verified run explicitly show that no verified result is available. Editing a prompt or choosing a file does not rerun the recorded example. Click or drop a supported local file to preview it in your browser without uploading it. The site does not queue work, run models, or call providers.

Public result examples must come from actual workflow execution, with distinct input and output assets and a recorded GPU run. Illustrations used as reference inputs are not presented as generated results. Unverified video, voice, training and other features show an unavailable result instead of substitute media. [Demo provenance](webui/docs/examples/provenance.json) records the reviewed assets and execution evidence. Publication permits only explicitly pinned bytes and paths; personal/runtime media remains excluded. See [the site guide](docs/site.md).

The working application runs on **your computer**, with a separately installed ComfyUI. Models, custom nodes, accounts, personal media and API credentials are not bundled.

The demo's hardware selector offers no NVIDIA GPU, common 8/12/16/24/32 GB VRAM tiers, and two 24 GB cards. These are planning estimates, not measured minimums or benchmarks; two 24 GB cards are not treated as a pooled 48 GB device. Selecting a profile changes only the static demo's availability hints, never your installed app's configuration or permissions. Choosing **AutoIP** enables all 23 demo forms for exploration; real cloud support remains limited to available provider services and implemented integrations. The [cloud/demo guide](docs/autoip-cloud-demo.md) explains those boundaries and discloses referral code `TEDH2FND` used by the optional AutoIP registration link and QR code.

## Start locally

Use **Linux or WSL2 and Python 3.11+**. Under WSL2, run both applications in the same Linux distribution. Native Windows and macOS are not supported by this launcher.

**1. Start ComfyUI.** Follow the [official installation guide](https://docs.comfy.org/installation/manual_install) if needed. From its own environment, start it on loopback:

```bash
python main.py --listen 127.0.0.1 --port 8188
```

Confirm that **http://127.0.0.1:8188** opens the node editor. Keep this process running.

**2. Install Web_Comfyui in a separate terminal and environment.**

```bash
git clone https://github.com/TED-H-com/Web_Comfyui.git
cd Web_Comfyui
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-webui.txt
cp .env.example .env
chmod 600 .env
```

`python3 --version` must report 3.11 or newer. Edit `COMFY_HOME` in `.env` to match your ComfyUI directory; the default is `~/comfyui`. Then:

```bash
.venv/bin/python run.py --check
.venv/bin/python run.py
```

**3. Open http://127.0.0.1:8190.** Set a local administrator password of at least eight characters. First-time setup signs you in; there is no preset username or password.

`--check` reports basic dependency and ComfyUI connectivity status without installing software or running a model. A passing check does not qualify every workflow. See [installation and troubleshooting](docs/installation.md) if it fails. Ctrl+C stops the Web UI; ComfyUI is managed separately.

## Use the workflow library

Open **工作庫** and filter by category, source, or content type. The public catalog has 248 entries, including 46 downloadable graphs: 24 curated community imports and 22 baseline examples.

| Label | Meaning |
| --- | --- |
| 可下載工作流 | A local JSON is available to download. Visual graphs can also be loaded by an operator. |
| 方法與參數 | A recipe with settings and prerequisites; it is not an executable graph. |
| 上游參考 | A source link; the JSON is not bundled. |

Open a card, inspect its models/nodes/license, then choose **檢查 ComfyUI 節點與模型**. This compares registered nodes and recognized loader selections with ComfyUI’s reported model options. Missing dependencies and unknown checks are shown separately; a match does not verify weight integrity, memory capacity or render quality. **載入 ComfyUI** saves a visual graph into ComfyUI's `webui-library` workflow folder. Open it in the native editor, select your models and inputs, and run it there. Loading does not queue a job or install dependencies. Reimporting an identical saved graph succeeds; a different graph at the same destination is a conflict and is not overwritten. Follow the [first-workflow walkthrough](docs/getting-started.md).

The application’s generation forms usually compile their own graphs in Python. They are distinct from the community JSON collection; a similar workflow name does not make it the form’s template. Optional API/voice integrations remain available. Advanced H3, training, voice and orchestration paths need their own models, workers and local qualification. Features may remain unavailable until those prerequisites are met. [Workflow coverage and limits](docs/workflow-coverage.md).

## Your configuration and data

API features are optional. Configure your own supported provider accounts or `.env` credentials; imported API nodes may require separate setup in ComfyUI. No single key enables every workflow. [API setup](docs/autoip-api.md).

State defaults to `~/.local/share/web-comfyui`, cache to `~/.cache/web-comfyui`, and generated media to ComfyUI's configured output directory. Keep `.env`, credentials and personal data out of Git. The launcher reads only this checkout's `.env`, without executing shell commands or expanding `$VARIABLES`; process environment values take precedence.

Keep both listeners on loopback. The separate `install.sh` is an **advanced ComfyUI provisioner** that can change packages, nodes and workflow links; it is not needed for the installation above. Review [its scope](docs/installation.md#optional-advanced-provisioner) before using it.

WebUI code is GPL-3.0. Third-party workflows retain their own licenses: see [NOTICE.md](NOTICE.md) and [source coverage](docs/workflow-coverage.md). Model and runtime licenses remain separate.

# Your first workflow

Start after [installing Web_Comfyui](installation.md). Keep ComfyUI open at `http://127.0.0.1:8188` and Web_Comfyui at `http://127.0.0.1:8190`. The [online guides](https://ted-h-com.github.io/Web_Comfyui/) use the same app interface in read-only mode; your local application performs the work. For installation steps in Traditional Chinese, see [README.zh-TW.md](../README.zh-TW.md).

## 1. Sign in and choose a starting point

On first use, set a local administrator password of at least eight characters and choose **設定並進入**. Setup signs you in. On subsequent visits, use that same password. No external account or API key is needed to start browsing.

For a first render, choose a model you already have working in ComfyUI. A small image task is easier to inspect than a long video or a multi-stage training job. Start with one output and modest dimensions, then increase workload after checking the result.

You have two routes:

| Route | Use it when… | Where generation runs |
| --- | --- | --- |
| **創作** — generation forms | You want the application's existing controls for a supported model/task. | The form submits its supported job after its prerequisites pass. |
| **工作庫** — workflow library | You want to inspect or adapt a supplied ComfyUI graph. | You open the graph and queue it in the native ComfyUI editor. |

Before installing models, you can explore the online **創作** page. Click a tool name, **查看流程與模型**, or its generation button to read one of 23 source-backed feature guides. For example, **文生圖** explains the Z-Image Turbo Python builder, its Qwen text encoder, AE and optional DOPSD LoRA. Forms remain interactive for learning; online actions do not upload, queue or call providers.

Most actual generation forms build their own API graphs in Python. A community JSON with a similar title is a separate example, not automatically the form’s template.

The steps below use **工作庫**. A graph being in the library does not make an unrelated generation form ready, or enable a hidden mode.

## 2. Find a graph you can use

Open **工作庫**. Use the search box for a workflow, model or node name. The dropdowns filter by category, source and content type. Select **可下載工作流** first if you want an included JSON. The public catalog contains 248 entries and 46 downloads: 24 curated community imports plus 22 baseline examples.

| Label in the library | What is included |
| --- | --- |
| **已收錄 JSON** / **可下載工作流** | A local workflow file. Open the card for its download and, for visual graphs, operator loading action. |
| **方法與參數** | A recipe: settings, sequence, sources and prerequisites. It has no runnable graph attached. |
| **上游參考** | An author/source link. Get the original from the author if its terms and requirements suit your setup. |

Open a card and inspect **模型需求**, **節點需求**, **需求**, **授權** and **來源** where present. Model filenames may differ from those installed locally. Read the source instructions before substituting a different model or adapter. Source-only entries may require author license acceptance or contain old group nodes that need migration.

## 3. Check prerequisites

Choose **檢查 ComfyUI 節點與模型**. This reads the running ComfyUI’s `/object_info`, expands referenced active subgraphs, checks node names and compares recognized loader selections with the reported model options. It shows missing dependencies separately from checks it cannot complete. If ComfyUI is unreachable, start it and check the URL in `.env`.

Install missing node packs into **ComfyUI's environment**, following their own instructions. Download any needed models separately from their documented sources and select the matching files in the graph. Models, node packages and GPU dependencies are not installed when you load a workflow.

Unknown loaders, connected model choices or unavailable option schemas remain unverified; inspect them in the native editor. Even when all reported options match, the check does not establish weight integrity, complete widget/schema compatibility or enough GPU memory. Treat **not-gpu-qualified**, **runtime untested** and similar statuses literally: static inspection is not a successful render.

## 4. Load into ComfyUI

In the card, choose **載入 ComfyUI**, then **開啟 ComfyUI**. The first action writes the visual JSON into ComfyUI's user-data workflow folder. In the native editor's workflow sidebar, open the **webui-library** folder and select the newly added file.

**Loading does not queue a job.** A missing-node warning can accompany a successful file save; resolve those dependencies before executing. Loading the identical graph again succeeds and directs you to the existing copy. Different content at the same destination produces a conflict; the existing graph is not overwritten.

If the loading action is unavailable, check the entry type and your access. Only operators can use it, and it applies to visual graphs. **下載 JSON** remains the file-download route for included entries. Recipes and references use **閱讀來源** instead.

## 5. Set inputs, run and inspect

Inside the native editor:

1. Select installed checkpoints, encoders, VAEs and adapters. Match the source's model family and variant.
2. Replace example input filenames with your own uploaded images, video or audio. A filename in a template does not mean that media was bundled.
3. Edit the prompt and choose one image or a short clip for the first test. Check output dimensions, duration and filename prefix.
4. If the graph contains API nodes, configure your own endpoint/model/credentials before running it. An application `.env` key may not configure those nodes.
5. Queue the graph in ComfyUI. Watch its queue and terminal for errors, then inspect the saved output.

If a render fails, keep the error message and identify whether it names a missing file, missing node, incompatible input or memory shortage. Fix that cause before changing many sampling settings. For video, inspect both picture and audio timing; a successfully saved file alone is not a quality check.

ComfyUI's queue is the source of truth for jobs you start in its editor. WebUI's **任務** view manages its own application jobs; do not depend on library loading to create a tracked generation task.

## Choose the next guide

| Task | Guide |
| --- | --- |
| Understand available graphs, sources and redistribution limits | [Workflow coverage](workflow-coverage.md) and [community examples](community-workflows.md) |
| Explore local LLM prompting, staged H3 video, RTX finishing | [H3 / local LLM / RTX recipes](rtx-video-research.md) |
| Prepare a MOSS image LoRA experiment | [KohakuMoss setup and preflight](moss-training.md) |
| Connect an AutoIP account | [API setup and supported operations](autoip-api.md) |
| Fix environment, port or optional-worker issues | [Installation troubleshooting](installation.md#troubleshooting) |

Keep model downloads and optional worker setup specific to the task you choose. Voice, H3 production, training and automated pipelines have additional requirements; none inherit a previous installation's qualification. Use your own data and provider credentials, and remove credential values and personal inputs before sharing an edited workflow.

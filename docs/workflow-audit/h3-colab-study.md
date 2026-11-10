# H3 Colab 完整來源研究與整合設計

研究日期：**2026-11-10**。範圍包括 `dseditor/h3-colab-skill` 的全部追蹤檔案、三份相關 Colab 筆記本、Google 官方 CLI、必要節點、模型中繼資料與授權，以及 Web ComfyUI 的傳輸、恢復、取消與費用控制設計。**尚未存取真實 Google 帳號、驗證訂閱餘額、申請 GPU 或完成 Colab 生成。** 後文的來源檢查、離線契約測試及 SDK 匯入檢查，不構成實際 GPU 執行證據。

使用與安裝方式見 [Google Colab 與 H3](../colab.md)。下列技術研究保留英文、固定來源版本及行號，供維護者重現檢查；操作人員的本機路徑、帳號資訊及內部專案參照已移除。

Research date: 2026-11-10. The upstream study used source and public-document inspection. No upstream installers, notebooks, generation scripts or tests were executed. No account credentials, user media, live Google account information, GPU assignments or model weights were accessed. The later implementation checks described below used local fakes and SDK construction without Google requests. Application comparison baseline: [`95e2079ed3ae47f76ff67948f80673f410abd407`](https://github.com/TED-H-com/Web_Comfyui/tree/95e2079ed3ae47f76ff67948f80673f410abd407).

## Decision

Build an optional, account-scoped Colab executor inside Web_Comfyui's durable task system. Use Google's real `google-colab-cli` library transport with a pinned compatibility layer, and adapt the reviewed H3 graph and remote worker logic. Do not invoke the upstream `h3run.py` as an opaque, trusted job runner. Do not expose ComfyUI through an unauthenticated tunnel. A notebook link alone would not implement the requested integration.

This study evaluates Google AI Ultra and Pro entitlements without accessing any account. It does not establish an account’s active paid tier, available balance, accelerator allocation or region. Account selection and authentication are explicit application operations; a label such as “Ultra” is not a runtime capability probe.

## Pinned sources and inspection scope

| Source | Revision | Public source |
|---|---|---|
| dseditor/h3-colab-skill | `34ba92508ace42bc29548af7a9f07c27dcae5230` | [Pinned tree](https://github.com/dseditor/h3-colab-skill/tree/34ba92508ace42bc29548af7a9f07c27dcae5230) |
| Google CLI v0.7.4 | `66d7963611bcce2d272488e982ad38e519c062f0` | [Pinned tree](https://github.com/googlecolab/google-colab-cli/tree/66d7963611bcce2d272488e982ad38e519c062f0) |
| Google CLI main at inspection | `9d45981cb68c2ee44b94fe9fc0900da0d8da61af` | [Pinned tree](https://github.com/googlecolab/google-colab-cli/tree/9d45981cb68c2ee44b94fe9fc0900da0d8da61af) |
| dseditor/minimax-h3-colab | `44708d4aa17e30fc8b56be375387ba2106936a9c` | [Pinned tree](https://github.com/dseditor/minimax-h3-colab/tree/44708d4aa17e30fc8b56be375387ba2106936a9c) |
| T8mars/comfyui-minimax-h3-audio-T8 | `ee569dab831cf0d14bf8578215c7250874fad2f6` | [Pinned tree](https://github.com/T8mars/comfyui-minimax-h3-audio-T8/tree/ee569dab831cf0d14bf8578215c7250874fad2f6) |
| facok/comfyui-SelfLift | `835c3cf7919410f71f414688f049c6ca497789d0` | [Pinned tree](https://github.com/facok/comfyui-SelfLift/tree/835c3cf7919410f71f414688f049c6ca497789d0) |
| NikoDemon80/ComfyUI-H3-Motion-Context | `518c083d11d80fa5920222ad89889924ee4401c9` | [Pinned tree](https://github.com/NikoDemon80/ComfyUI-H3-Motion-Context/tree/518c083d11d80fa5920222ad89889924ee4401c9) |

Inspection also covered public Hugging Face model metadata, model cards, license text, and the full source of all three notebooks. The model revisions are listed below. No model binaries or generated media are included in this study.

Every tracked file in h3-colab-skill was inspected: README, INSTALL_FOR_AGENTS, localguide, LICENSE, git metadata files, SKILL, all three reference documents, all four remote Python files, all four local Python scripts, both OS installers, CLI patch script, workflow README, and all three API graphs. There are **no notebooks and no requirements file in h3-colab-skill**. Its dependency installs are encoded in Python/shell. The sibling minimax-h3-colab notebooks were also decoded and inspected; they are a separate interactive interface, not needed by the current skill runtime. Dependency node repositories were inspected for revision, packaging, requirements, license and node bindings; this is not an exhaustive audit of all their experimental features.

Source citations below use paths and exact lines at these revisions. Skill `remote/`, `scripts/`, `references/` and `workflows/` paths are relative to `h3-multimodal-colab/`; installer paths are relative to the repository root. Google CLI module paths are relative to `src/colab_cli/`. Bare `h3graph.py` and `remote_job.py` citations refer to the skill’s `remote/` files, while `h3run.py`, `colab_login.py` and `selftest_graph.py` refer to its `scripts/` files. Canonical primary source links: [skill source](https://github.com/dseditor/h3-colab-skill/tree/34ba92508ace42bc29548af7a9f07c27dcae5230), [Google CLI source](https://github.com/googlecolab/google-colab-cli/tree/66d7963611bcce2d272488e982ad38e519c062f0).

## Actual subscription and runtime constraints

Google announced Colab inclusion in paid AI plans on September 22, 2026, with Ultra gaining premium GPUs and background execution. Existing Colab subscriptions can stack with AI plan benefits. This is current functionality, so older statements that AI plans never include Colab are outdated. [Google announcement](https://developers.googleblog.com/colab-is-now-part-of-your-google-ai-plan/)

The current Google One comparison table lists 50 / 200 / 1,000 / 2,000 Colab compute units monthly for Plus / Pro / Ultra 5x / Ultra 20x. Its broad Ultra summary says 2,000; use the detailed plan table and the account's actual paid tier. Upstream's “Pro ~400 units” and unqualified “Plus is insufficient” are not authoritative current entitlement statements. Published allowances are not a measured balance. [Google AI plan comparison](https://one.google.com/about/google-ai-plans/)

Paid AI benefits apply to the same Google account; free trials and unsupported paid-Colab countries are excluded. Google AI and Colab-native grants stack on an account. No cited source establishes pooling between two Google accounts. Runtime availability varies; ordinary runtimes generally have a 12-hour maximum, Pro+ supports up to 24 hours with sufficient balance, and the Ultra announcement promises background execution without making an unlimited-runtime guarantee. Zero balance can end paid execution. Free/zero-balance runtimes prohibit primarily bypassing notebook UI, remote control and distributed workers; paid plans with positive balance remove those specific restrictions. All managed runtimes still prohibit abuse, unrelated file/web hosting, quota evasion with multiple accounts, and deepfakes. This does not classify every synthetic talking character as a prohibited deepfake. [Colab FAQ](https://research.google.com/colaboratory/faq.html)

Google now provides an official CLI explicitly intended for headless automation. Supported host platforms are Linux and macOS. The CLI is an authentic supported entry point, but its internal Python interfaces have no documented stable SDK contract. Its README's default-auth description does not match v0.7.4 implementation: that version defaults to OAuth2 (`cli.py:68–78`), not ADC. Pin implementation and supply explicit credentials. [Official CLI README](https://github.com/googlecolab/google-colab-cli/blob/66d7963611bcce2d272488e982ad38e519c062f0/README.md)

## What the upstream skill actually does

1. `scripts/h3run.py:260–280` parses jobs, infers GPU/resolution, checks a global token file and parses human-readable `colab usage`. It rejects a known balance below five units, but **continues if balance cannot be parsed**.
2. `h3run.py:283–294` enumerates named sessions, creates one if absent using `colab new`, and marks it busy. Names are timestamp based, without owner identity, manifest hash or locking.
3. `h3run.py:297–314` uploads remote_job.py, h3graph.py and three graph templates, a manifest, and each input to `/content/h3job`. A short `colab exec` invokes start.py.
4. `remote/start.py:6–16` uses pgrep to detect one shared runner; otherwise it starts a detached subprocess. `remote/status.py:5–15` reads status JSON, checks process liveness, and prints a sentinel plus JSON.
5. `remote/remote_job.py:102–121` checks GPU compute capability >=8, clones rolling ComfyUI and three node repositories, installs rolling CUDA 13 PyTorch and requirements, and writes a global setup marker.
6. `remote_job.py:126–134` downloads unpinned Hugging Face weights when the filename is absent. It does not verify a configured revision or digest.
7. `remote_job.py:148–156` starts ComfyUI bound to `127.0.0.1:8188`. No Toybox, Gradio, tunnel or public ComfyUI endpoint is needed.
8. `remote_job.py:489–523` runs jobs sequentially, writes atomic status, copies each result to `out/<job-id>.<ext>`, and optionally stops the batch on error. Individual generation failures do not crash the process.
9. `h3run.py:315–360` polls every 30 seconds, retrieves each result through the Google transport, then writes a local results summary. Success leaves a session idle for 15 minutes unless stop-after is selected; a detached **local** watchdog tries to stop it later (`:105–144,361–380`).

This is a genuine end-to-end transport implementation, but its durability/security assumptions are those of one trusted desktop agent, not a multi-user WebUI or scheduler.

## Capabilities and graph semantics

| Capability | Graph behavior and source |
|---|---|
| Text-to-video with generated sound | `h3graph.py:179–225,251–266`: `MiniMaxH3ImageToVideo`, no frame slots, then combined AV decode/SaveVideo |
| First/last frame | Same conditioning class; image-scale to target size; I2VA, L2VA or FL2VA; alignment prompt added (`:136–165,196–205,251–261`) |
| Reference video | `MiniMaxH3ReferenceToVideo`; images, videos (optional source audio), separate audio references (`:228–250`) |
| Extension | Last source frame becomes a reference; last 3 seconds conform to 24fps; MotionContext pins video/audio context; trim removes prefix; ffmpeg concatenates full source and new clip (`h3graph.py:268–281`; `remote_job.py:396–450`) |
| Text-to-image/image edit | P-H3 LoRA + separate encoder/VAE; <=8 pictures, first picture edited; 1K–2.7K long edge (`h3graph.py:295–324`) |
| TTS | 32×32 video latent with only audio decode; <=3 voice references; six-step sampler; output FLAC or WAV (`remote_job.py:453–483`) |

Video dimensions round to multiples of 32: “720P 16:9” becomes **1280×704**, and 480P portrait becomes 480×864 (`h3graph.py:73–85`; selftest:261). Display the actual dimensions; do not promise exact 1280×720. Duration is converted into frame counts satisfying the H3 frame arithmetic expression, so verify output duration rather than treating requested seconds as exact.

Reference input selection is important: video/ref uses only inputs mentioned by `@ImageN`, localized equivalents, or `<Picture N>` tags. Unmentioned uploaded media is dropped with a log warning (`h3graph.py:106–133`; `remote_job.py:413–414`). Mixed literal and @ notation can leave literal tags unremapped because literal conversion only runs when no @ mention exists. Require one canonical reference notation and validate every expected connection.

Fast video is 8 steps / transition 4; normal is 12/6. Three action LoRAs are removed when intensity is off. **Three other LoRAs, including MysticXXX, remain enabled for every video**, not an opt-in motion setting (`h3graph.py:28–35,283–291,401–406`). Names alone do not determine output content, but a product must explain that this is a derived community model recipe, not official base H3 defaults.

Graph validation checks dangling node links, forbidden UI-only/VHS classes, planned references, size, steps, seed, model chain and output wiring (`h3graph.py:327–456`). It does not validate node socket type compatibility against installed `/object_info`, model compatibility, numerical kernels or output quality. The selftest uses fake media sizes and skips TTS construction (`selftest_graph.py:51–60,136–161`); it is **not GPU validation**. The optional Toybox comparison AST-extracts and executes three functions (`:198–242`), so it was read, not run.

## Runtime dependencies, models and reproducibility

Local controller: Python 3.12+, google-colab-cli 0.7.4 (Apache-2.0), its dependencies including jupyter-kernel-client==0.9.0, google-auth, google-auth-oauthlib, requests, pydantic, typer and filelock (`CLI pyproject.toml:6–42`). Use a dedicated optional environment; the installer otherwise force-replaces a global uv tool, patches installed files, disables global update checks and overwrites every discovered agent skill directory (`install/install.sh:9–32`; PowerShell counterpart:9–35). None of those install side effects are needed for Web_Comfyui.

Remote dependencies are ComfyUI, CUDA 13 PyTorch, huggingface_hub/hf_xet, ffmpeg, image metadata support via Pillow/PyAV, and three node packs. The older notebook has a driver >=580 check and Toybox requires >=72GiB free disk; the current skill runner dropped both checks (`remote_job.py:102–121`; sibling Toybox notebook cell 1). Validate actual GPU name, compute capability, VRAM, RAM, free disk, CUDA/driver, torch version and needed node interfaces before a paid generation.

The default model set is 12 files (`remote_job.py:44–58`): PRISM int8 model; qwen3vl_32b_minimax_h3_nvfp4_awq; int8 ConvRot video VAE; fp32 audio VAE; pika flex; turbo hybrid; MysticXXX; Combat; Motion_Repair; SPEED; semantic bridge; latent upscaler. P-H3 adds three files (`:59–63`). The runner downloads action assets even for intensity-off jobs. Rough 50–60GB and timing claims are upstream measurements, not verified here. No model binaries were downloaded.

Public metadata revision snapshots:

| Model source | Hugging Face commit |
|---|---|
| MiniMaxAI/MiniMax-H3 | `0919fb684102ef115126612f3fbb84d1027e7317` |
| OliviaRossi/Minimax-h3_Singularity-x-10Eros-Max_PRISM | `f35f94f7a09d48f637649f665a4d468784187468` |
| Comfy-Org/MiniMax-H3 | `54472221d27c695fca9621b0c3ed52f06c4af707` |
| PrunaAI/P-H3-Image-Comfy | `44a0fb73f299da1bb715bdb5ff59ce3fd3c6f543` |
| dseditor/minimax-h3-studio-assets | `1feae43759ca3af96d528a4312c48665d9f88811` |

Each API metadata document includes file siblings and available LFS digests/sizes. Pin each required file and check its SHA256 before runtime use. A setup marker must be keyed by the complete dependency/model bundle digest, not merely `/content/.h3skill_setup_v1`.

T8 base requirements explicitly need no additional packages beyond ComfyUI; optional prompt-rewriter dependencies are separate and unused here. SelfLift and MotionContext have no requirements file at reviewed HEAD. Therefore the skill's failure to install custom requirements is presently not proof of a missing dependency for this exact graph, but rolling updates make that assumption unsafe.

## Licensing findings

The skill itself is MIT (`LICENSE:1–24`) and explicitly excludes model licenses. Retain its copyright/license when adapting substantial code. Google's CLI is Apache-2.0. T8 node code is GPL-3.0-or-later; MotionContext GPLv3. **SelfLift has no LICENSE or explicit license declaration in its tracked files at the pinned revision**; do not describe every runtime dependency as MIT or freely redistributable. Fetching an upstream dependency and redistributing its source are separate decisions; unresolved redistribution permission should be documented.

MiniMax's community license excludes EU, UK, South Korea and USA from its standard territory grant and provides a separate-license route. It includes restrictions on outputs outside the applicable territory, commercial UI naming and additional authorization above the stated revenue threshold. Its voluntary product attribution clause is not equivalent to a mandatory watermark on every video. Read the source license for the applicable territory and commercial terms. This study does not establish any operator’s location or individual licensing status; the upstream runtime itself does not implement a blanket license-attestation screen. [Official pinned license](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/0919fb684102ef115126612f3fbb84d1027e7317/LICENSE), [official licensing explanation](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/QA-about-License.md)

Comfy-Org inherits that model license. PRISM's card says licensing follows source models and provides no standalone complete license. Pruna's card specifies H3 weights plus Apache-2.0 components. The studio-assets mirror explicitly says each file retains its author's license and links original sources; it does not grant a blanket license (`studio-assets README:15–27`). Relevant sources are t8star turbo, lynaNSFW MysticXXX, JOKER141 Combat/Repair, siraxe sliders, t8star Semantic-Bridge, LBH latent-upscaler. Their individual derivative rights have not been conclusively established by the mirror metadata. Do not redistribute weights or claim license clearance for all models.

## Upstream failure modes that must not enter the adapter

| Finding | Exact evidence | Required behavior |
|---|---|---|
| Provisioning ambiguity can leak a GPU | new at `h3run.py:288` precedes try/finally at :296 | Persist allocation identity before request; reconcile before any further allocation |
| Local session name is not provider idempotency | CLI `commands/session.py:141–143` generates a fresh UUID for every new call | Persist UUID; use read-only lookup after uncertain result |
| Unknown balance fails open | `h3run.py:277–280` | Unknown balance/readiness prevents paid admission |
| Resume trusts stale/corrupt outputs | `h3run.py:211–240` accepts first matching filename, no request/content hash | Match immutable input/graph/seed bundle and verify result digest/media |
| Concurrent batches collide | fixed paths in h3run:297–314 and start/status | Per-job directory + runtime lease + atomic launch marker |
| New batch can poll an older batch | start:8–10 reports already-running; h3run:314 only prints response | Require acknowledgement matching job/request digest |
| Launch retry is not exactly once | `remote_exec` retries all code at :190–200; start pgrep check is non-atomic | Durable launch lock/manifest identity; reconcile uncertain exec |
| Download failure can still become success | `h3run.py:347–360` final retry only checks exists; completion doesn't require got set | Complete only after exact bytes/digest and decoder validation; retain GPU for bounded recovery |
| Stop errors are swallowed | `h3run.py:141–144,367–368,440–442` check=False then removes keep state | Persist cleanup-pending until assignment absence verified |
| Local watchdog cannot survive host/network outage | `references/troubleshooting.md:7–12`; watchdog local only | Remote deadline/cancel sentinel + durable controller reconciliation; never promise guaranteed release offline |
| Remote timeout leaves queued work | remote wait_history raises at :194 without cancel/interrupt; loop can continue | Remove owned queued prompt; interrupt only when this worker exclusively owns running prompt/runtime |
| “Failed batch shuts down” claim not always true | remote catches job failures and ends done/finished :516–523; h3run finished=True then idle retention | Separate transport done, generation failure, download done and cleanup state |
| User path can enter Python code | h3run:310 interpolates directory inside a quoted Python literal; base/f allows absolute/../ | Parent stages only validated owned assets under generated basenames; JSON data separate from code |
| Shared token/settings/history | CLI auth:54; common:57–68 | Explicit private account scope; no global HOME/token discovery |
| Fixed 5-CU threshold and local elapsed limit are not budget reservation | h3run:279,318; deadline starts after allocation | Enforce admission estimate + configured maximum CU/time, reconcile actual rate; include setup and retention |
| TTS metadata loses actual seed | remote:460 then returns only seconds :479–481; main stores missing info.seed :512 | Persist actual generated seed and prompt before submission |
| Extension can silently return only new segment | remote:443–446 emits warning but successful file | Distinct partial result/extension outcome; do not label concatenation complete |

## Google CLI transport and explicit account isolation

`--config` controls session metadata only (`cli.py:58–64`; `state.py:112–116`). The v0.7.4 token path is hardcoded (`auth.py:54`) and settings/history default globally (`common.py:57–68`). CLI usage/sessions/status have human-readable text, not a JSON mode (`commands/usage.py:20–33`; `commands/session.py:254–278,300–320`). `token.json` and sessions contain secrets, not browser-safe status.

Use a dedicated subprocess JSON bridge importing the pinned Python library. Explicitly load the configured account's OAuth credential JSON, refresh it through Google, atomically save it 0600, construct `AuthorizedSession` then `Client(Prod(), session)`. Do not invoke ADC discovery or change HOME. Do not share module-global State across users. Supply private per-account StateStore/HistoryLogger paths if using them; preferably keep the bridge's own bounded private runtime records.

OAuth scopes are openid/profile/email/cloud-platform/colaboratory/drive.file (`auth.py:44–51`). Native remote redirect is Google's registered HTTPS SDK landing page (`:54–94`); it is not the deprecated OOB URI. For a browser UI, retain PKCE verifier, random flow identity, state, creation/expiry privately; return only authorization URL/flow id. Receive the one-time code in a private POST/JSON stdin, never command argv, logs, browser storage, committed files or agent chat. Reject wrong/expired/reused flow. Token file presence alone is not authentication success. Upstream colab_login.py has PKCE but no TTL, filesystem mode enforcement or multi-account namespace (:57–75).

Library primitives:

| Operation | Exact primitive and caution |
|---|---|
| Account status | `Client.get_consumption_user_info()` (`client.py:246–250`), structured paid_compute_units_balance, consumption_rate_hourly, assignments_count (`consumption.py:31–44`); rate is account total |
| Allocate | `Client.assign(notebook_hash:UUID, Variant.GPU, Accelerator.*, shape)` (:265–287); first GET then conditional POST |
| Lookup only | `Client._get_assignment(...)` (:309–317), returns Assignment if present or GetAssignmentResponse if no existing assignment; **never call assign in lookup** |
| Reconcile/list | `Client.list_assignments()` (:252–255); returns endpoint, accelerator, shape, proxy token/url/expiry (:133–149), not local name |
| Stop | `Client.unassign(endpoint)` (:257–263); verify subsequent assignment absence; CLI stop also closes kernel (:324–348) |
| Execute | `ColabRuntime.execute_code(code,allow_stdin=False,timeout=...)` (`runtime.py:180–262`); returned outputs include errors and stream records; close channels with stop(False) after each short call |
| Upload/download | `ContentsClient` uses authenticated runtime proxy Jupyter Contents API (`contents.py:24–93`); base64 whole-file buffers; no default HTTP timeout or streaming bound |

`Client._issue_request` omits default network timeouts and DEBUG logs include request headers/response bodies (`client.py:196–227`). Supply finite connect/read timeouts and no debug logging. Upstream fixes body-less POST HTTP 411 by providing `{}` (`install/patch_colab_cli.py:16–19,39–45`). Confine that compatibility workaround to this pinned transport; do not mutate shared site-packages. Validate runtime-proxy URL scheme/host, disable redirects for credential-bearing transfers, bound bytes, and keep raw remote exceptions out of public errors. Runtime proxy token appears in query strings in ContentsClient, so never log raw URLs.

After ambiguous allocation, use the persisted UUID to GET the exact assignment. After ambiguous prompt submission, query owned queue/history/request record, never resubmit or send the same task to another provider. A missing runtime after a prior successful start is a lost runtime, not permission to regenerate automatically.

## Concrete Web_Comfyui integration

Use a dedicated Colab execution lane with durable task identity and explicit uncertain states. Preserve owner, account, request and content identities across admission, allocation, launch, output adoption and cleanup. The implementation is described in [the Colab guide](../colab.md) and [remote worker contract](../../scripts/colab_remote/README.md); the recommendations in this section are design requirements, not evidence that a real GPU run succeeded.

Recommended task record: owner/account ids; client request id; canonical manifest SHA256; input SHA256s/media metadata; graph/template/dependency hashes; explicit seed and dimensions; requested GPU; allocation UUID; private endpoint record reference; remote job id; Comfy prompt id; lease and timestamps; budget/time limits; output byte count/SHA256; cancellation and cleanup state; validation evidence. No raw tokens/proxy URLs in UI snapshots.

Flow: configured account -> read-only authenticated status -> bounded immutable job plan -> durable claim -> allocate once -> validate actual hardware and paid balance -> upload pinned worker/templates/input bundle -> bootstrap locked dependencies -> start local-only ComfyUI -> verify `/object_info` and model hashes -> atomically launch request -> poll via short exec -> download to private staging -> verify hash/container/media/dimensions -> import through existing owner-aware asset path -> stop owned runtime -> verify stopped. UI should separate “connected”, “ready for configured workload”, “GPU test passed” and “generation verified”.

Notebook bootstrap, if supplied for manual fallback, must use the **same locked worker and manifest schema**, with notebook cells only creating the directory, installing pinned dependencies and starting the bounded job. No second scheduler, broad Drive mount or unauthenticated tunnel. Browser notebook cells are not required for the preferred CLI route. A local helper can open the CLI's account-bound Colab runtime URL for diagnostics without exposing ComfyUI.

Inputs are selected through existing owned assets, checked for containment/digest/size/MIME, then staged to generated filenames. Only those files are uploaded; no sibling credential/project discovery. Store remote inputs under `/content/web-comfyui/<uuid>/input`. Keep session model cache under a bundle-hash namespace. Outputs are enumerated in an atomic result manifest with filename/size/SHA256/media kind/actual seed/prompt id. Large files need bounded/chunked transfer since CLI's native method buffers whole files; cap v1 input/output size and show actionable errors.

Cancellation first persists cancel_requested, then signals the per-job worker. The worker deletes only its queued prompt; if it owns the dedicated runtime it can interrupt the active prompt and terminate its processes. Then unassign and reconcile disappearance. A network failure remains cleanup_pending; do not say stopped. Restart recovery polls the existing runtime/request identity. The parent UI/controller may go away without erasing the job; no local watchdog alone can guarantee spend ends.

## Sibling notebooks: all cells reviewed

`H3_Basic_ComfyUI.ipynb` has 7 cells: introduction; environment checks/install; background ComfyUI; account-bound Colab proxy; unauthenticated cloudflared fallback; four base model downloads with optional broad Drive cache; diagnostics. It has no prepared graph or automation contract. It enables CORS and Manager with normal- security for manual node installation; do not copy those flags to the executor.

`H3_Digital_Human.ipynb` has 10 cells: same introduction/install/proxy/download/diagnostic foundation; adds workflow installation, one image upload, ipywidget prompt/duration/megapixel/seed form, direct `/prompt` POST and unbounded history poll with files.download. Generation is notebook-driven and does not persist an external durable request id. It is not the multimodal batch protocol.

`H3_Toybox.ipynb` has 6 cells: introduction; hardware/disk/install including additional prompt-enhancer sources; optional image/rewriter model download; ComfyUI+Toybox launch with Drive output persistence; unauthenticated cloudflared fallback; diagnostics. Default image and rewriter flags are enabled, unlike current skill. Default SAVE_TO_DRIVE=True requests broad Drive mount; SAVE_API_KEYS=False keeps secrets ephemeral, but enabling it writes key JSON into Drive. Its API-key-dependent prompt agent is not supplied by a consumer AI subscription alone. CLI executor needs none of this separate frontend/Drive/key persistence.

The three notebooks can be inspected cell by cell in the pinned notebook repository linked above. Notebook backups and current skill have materially different dependency and capability surfaces; do not replace cleaned API graphs with Toybox's raw WF.json, which retains UI-only and missing-node dependencies (`workflows/README.md:12–17`).

## Acceptance criteria and remaining unknowns

Offline tests should cover isolated account state; OAuth TTL/wrong/replayed flow; refresh errors; finite network timeout; malformed GPU fail-closed; deterministic allocation lookup without POST; ambiguous allocate/exec; remote job identity mismatch; cancel/stop verification; corrupt or oversized download; path traversal/symlinks; same job id with changed input; parent restart reconciliation; sanitization of exception tokens. Structural graph checks should cover all modes plus actual object_info snapshots; those still do not prove GPU generation.

Only after an explicit account is configured and a concrete job is submitted should a small synthetic-media run validate real L4/G4 availability, cold setup, CUDA kernels, generation, interruption, output transfer and verified release. Record actual rate/elapsed units, GPU, software/model hashes and output evidence. Until then expose the integration as configured/untested, not verified generation. No runtime or subscription claim in this report is based on account access.

Unresolved: account entitlements/balance, GPU allocation and driver availability, precise bundle compatibility on current Colab, full derivative model licensing, SelfLift redistribution permission, output quality and timing, behavior after real network loss. These do not prevent implementing and testing the transport/service with fakes; they prevent claiming live GPU validation.

## Initial implementation and additional transport validation

Implemented in this repository: `scripts/colab_bridge.py`, `requirements-colab.txt`, and `tests/test_colab_bridge.py`. The bridge uses one JSON stdin request/stdout response, explicit account directories, private atomic token/runtime records, expiring one-use PKCE login, GET-only allocation recovery, confirmed assignment removal, persisted kernel/session identity, bounded binary contents transfers, and idempotent creation of `/content` upload ancestors. Version 1 limits each transferred file to 64 MiB; the parent must independently verify downloaded size/hash and media validity. No bridge command uses command-line credential arguments, ADC, HOME overrides or global CLI configuration.

The pinned SDK itself also pins `jupyter-kernel-client==0.9.0`. In an isolated uv environment, `google-colab-cli==0.7.4` imported successfully and its OAuth flow object was constructed without initiating any Google request. Interface inspection found two otherwise hidden ambient HTTP sessions: `AuthorizedSession` automatically constructs a Requests session for 401 credential refresh, and `jupyter_kernel_client.manager.fetch` uses top-level Requests methods. The bridge now passes an explicit no-environment refresh transport and confines a Jupyter fetch adapter to the isolated subprocess. Finite connect/read timeouts are 10/60 seconds, OAuth token exchange is 30 seconds, and WebSocket connection default is 10 seconds with environment proxy discovery disabled. SDK startup retries and multiple sequential operations mean these are not total request deadlines; the controller must impose an operation deadline and treat killed mutations as uncertain.

Initial offline verification: `pytest-disktmp uv run --with pytest pytest -q tests/test_colab_bridge.py` passed **33 tests** on 2026-11-10. Tests include account isolation, login expiry/replay, durable ambiguous allocation, lookup without allocation, exact owned-runtime stop and pending cleanup, redacted execution output, kernel identity persistence, bounded atomic transfer, ancestor creation/conflicts, symlink/private-file rejection, explicit refresh/Jupyter transport and redirects. This is transport contract validation, not account access or GPU execution.

The current official [Colab runtime.unassign implementation](https://github.com/googlecolab/colabtools/blob/main/google/colab/runtime.py#L13-L35), inspected 2026-11-10, first posts to the local runtime management service from `TBE_RUNTIME_ADDR`, then requests browser/kernel disconnection. This makes a detached watchdog inheriting the runtime environment a plausible best-effort release mechanism even if its subsequent notebook UI RPC fails. The management request has no built-in finite timeout, so execute it in a subprocess with a deadline. A watchdog marker, process exit or attempted unassign is never proof of provider-side release; the parent still verifies the exact assignment is absent. Runtime behavior remains untested here.

Implemented `scripts/colab_watchdog.py` and `tests/test_colab_watchdog.py` as the early bootstrap for this fallback. Its generated code runs before input/dependency uploads, creates only the fixed UUID job directory, starts a detached process holding a per-job flock, and acknowledges matching job id, manifest SHA and absolute deadline only while that lock is held. Repeated bootstrap does not extend the deadline; stale markers alone do not count as readiness. The child waits for cancellation/deadline and makes at most three 20-second-bounded unassign attempts. The parent remains responsible for verified release. The initial combined bridge/watchdog test pass completed **48 tests** using disk-backed pytest temp. The real pinned SDK transport adapters were constructed and restored successfully in an isolated environment without sending requests.

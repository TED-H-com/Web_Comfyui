# Workflow coverage and sources

WebUI features and downloadable ComfyUI graphs are separate interfaces. The feature registry contains 23 entries, including library/editor tools and intentionally hidden modes. Most generation features construct their API graphs in Python; they do not depend on a recovered JSON file with a matching display name. Missing a model, custom node or runtime still blocks generation.

The [online site](https://ted-h-com.github.io/Web_Comfyui/) uses the actual app shell and forms with a read-only documentation adapter. Tool names, **查看流程與模型** and generation actions open 23 source-backed feature guides. These identify the real Python builder, required models and form defaults; downloadable reference JSONs are explicitly labelled. The public workflow library separately contains 248 entries, including 46 downloadable graphs: 24 curated community imports plus the 22 baseline examples audited below.

| Feature group | Implementation | Downloadable examples |
| --- | --- | --- |
| Z-Image, Flux Kontext, image composition, IPAdapter, InstantID | API builders in `webui/server.py` | Optional source-attributed examples in the workflow library; models remain user-installed. |
| Character photoshoot | API builder plus user-owned character data and qualified adapters | No character datasets or identity LoRAs included. |
| Wan image/video, SCAIL motion transfer, lip sync, face swap, loop | API builders in `webui/server.py` | Example presence does not enable hidden/disabled feature modes. |
| Speech, voice cloning, batch narration | Runtime/API builders and separate voice dependencies | No voice recordings, speaker identities or service credentials included. |
| H3 text, first/last-frame, multi-reference video | Native API compilation and readiness checks | Three official templates under `workflows/minimax_h3/official/`. |
| Qwen-Image 2.1 generation/editing | `webui/qwen_image21.py` compiles API graphs | Two official templates under `workflows/qwen_image21/`. |
| Voice library and post editor | Application tools | These registry entries are not standalone ComfyUI generation graphs. |

The three official H3 templates and two official Qwen templates were freshly fetched from pinned revisions of [Comfy-Org/workflow_templates](https://github.com/Comfy-Org/workflow_templates). They match the SHA256 values in this repository's model/workflow manifests. Their MIT license is included in [third_party/workflow_templates/LICENSE](../third_party/workflow_templates/LICENSE). Required native subgraph definitions remain intact. Their public upstream example input filenames are placeholders: upload your own inputs before running them. No upstream sample image/video files are bundled.

Sixteen legacy root-level examples were freshly fetched from [dseditor/ComfyuiWorkflows](https://github.com/dseditor/ComfyuiWorkflows/tree/b2ea26fa602d6eebadb1de1e4ccf1ecc91875806), then passed through the repository's sanitizer and graph checks. Original basenames are preserved. Embedded previews, cached metadata, credentials/private-path patterns and historical input/output filenames were removed or replaced; sample prompt text and cached text displays were replaced. Models, topology and sampler settings remain. The GPL-3.0 license and source links accompany these files. These are optional examples; old custom-node versions may require migration. API-assisted examples retain their integration nodes and require the user's own provider/model/credential settings.

The [Larry H3 turbo example](https://github.com/Larryvrh/ComfyUI-MiniMax-H3-Turbo/blob/546b5028f4934f5129eb6c7142c2f3e461dfddbf/example_workflows/minimax_h3_t2v_turbo.json) is included as a sanitized, statically checked upstream example with its Apache-2.0 license. Its adapters and nodes must be installed separately; no rendering claim is made.

The full audit is [imported_sources.json](../manifests/workflow_coverage/imported_sources.json): each distributed file has its pinned source, original and distributed hashes, license, modifications and validation status. It adds 22 graphs to the separately maintained community collection. This count describes this audit's files, not a guarantee of executable features.

## Source-only entries

These remain discoverable by source URL without being represented as ready local graphs:

- `AnimeArt`, `CoupleFull`, `clotheschangertiled`, `inpaintoutpaint`, `ipaclothes`, and `stablecascadecontrolnet`: their older `workflow/` group-node expansion needs a separate compatibility review. Removing their hidden definitions would break the graph.
- The pinned H3 Motion Context example: static inspection found duplicate/multiply connected links. A valid upstream graph or an explicitly reviewed repair is needed before import.
- Javawock H3 examples: no clear workflow redistribution grant was established at the reviewed revision. Local adaptations do not remove the source's license obligations.
- NativeAudio/music-video examples: licenses found inside individual custom-node directories do not establish a license for the top-level workflow collection.
- Servasyy/RunningHub examples: public article/export access alone does not establish redistribution rights.

The source index links each reviewed revision. To obtain a source-only graph for your own environment, open its source page, review the applicable terms, and use the author's download instructions. Inspect its node dependencies and input/output/API settings before importing. Gated content requires your own authorized account. This repository does not bypass those gates or bundle the associated weights/media.

## Verification boundary

This audit fetched JSON/license files only. It verified pinned hashes, parsed graphs, checked top-level links, scanned for credential/private-path patterns, and confirmed that all distributed paths and license files exist. Native official templates retain their upstream subgraphs; the checks do not prove installed node-schema compatibility inside them. No model download, service restart, GPU load or image/video generation was performed. Before calling a feature ready, install its compatible dependencies and run a short, user-owned-input qualification.

The local **檢查 ComfyUI 節點與模型** action checks active referenced subgraphs and compares recognized model-loader selections with ComfyUI’s reported options. Missing and unknown dependencies remain distinct. A matching option is not proof of weight integrity, runtime compatibility or GPU quality. Importing identical visual JSON again succeeds; different content at the same destination is a conflict and is not overwritten.

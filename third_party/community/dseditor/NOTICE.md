# dseditor community workflow attribution

The 24 JSON files in `workflows/community/dseditor/` derive from
[dseditor/ComfyuiWorkflows](https://github.com/dseditor/ComfyuiWorkflows/tree/b2ea26fa602d6eebadb1de1e4ccf1ecc91875806),
revision `b2ea26fa602d6eebadb1de1e4ccf1ecc91875806`.

They remain distributed under the upstream GNU General Public License version 3.
The complete, unmodified upstream license is in [LICENSE](LICENSE). This notice
does not relicense the surrounding application, custom nodes, or model weights.
The accompanying editable workflow JSON is the preferred source form for these
workflow modifications; the importer and manifest document how to reproduce it.

Modified on 2026-10-22: removed cached UI/subgraph metadata and media previews,
replaced local input/output filenames, removed private filesystem paths and
credential-shaped values, and normalized model path separators. Graph topology,
prompts, sampler settings and upstream model selections are retained. Every file
embeds source attribution and the original file's SHA-256. The catalog records
both upstream and sanitized file hashes.

No ecjojo workflow JSON is included. Source links and factual dependency metadata
in the catalog refer to `ecjojo/ComfyUI-Templates` at
`adc9ef7659db315053fd1b9863ca285fa3fcccb1`; that revision has no redistribution
license in its Git tree or README. An invitation to study a workflow does not
establish permission for this repository to redistribute its source.

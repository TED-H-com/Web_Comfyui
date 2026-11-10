# H3 remote worker provenance

`h3graph.py`, `upstream_runtime.py` and `workflows/*.json` derive from
[dseditor/h3-colab-skill](https://github.com/dseditor/h3-colab-skill/tree/34ba92508ace42bc29548af7a9f07c27dcae5230),
revision `34ba92508ace42bc29548af7a9f07c27dcae5230`, copyright 2026 dseditor
(DSLab). The complete MIT notice is in `LICENSE.upstream` and must accompany
redistributed copies.

Changes made on 2026-11-10: relocated the template directory and imports;
removed the upstream install/download/start/main entry points; retained its
graph construction and media preparation helpers. `remote_job.py` replaces
the controller, paths, model installation, submission, polling, cancellation
and output manifest handling. Runtime helpers are rebound to those checked
operations. The original TTS submission loop is replaced by the common durable
submission path. Graph templates retain their upstream bytes and recorded hashes.

`runtime.lock.json` records Git revisions, model repository revisions, LFS
digests/sizes and template digests. ComfyUI, T8, SelfLift and MotionContext source
is fetched at runtime; it is not copied into this directory. SelfLift declares
no redistribution license at its recorded revision. T8 and MotionContext carry
their own GPL licenses. Model weights retain their separate upstream licenses;
the MIT grant above does not apply to them.

The model set is the community PRISM/P-H3 recipe, including its always-on
community LoRAs. It is not the official base-model configuration. The MiniMax H3
community model license has territorial and commercial conditions; see the
[pinned source license](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/0919fb684102ef115126612f3fbb84d1027e7317/LICENSE).
PRISM derivatives and mirrored studio assets additionally retain their source
authors' terms. This bundle does not assert model-license clearance or infer
the operator's jurisdiction. No new attestation is invented by the worker.

Validation is CPU-only contract testing and pinned-source inspection. No GPU,
Colab assignment, installation, model download or generation has been performed
as part of this change. The selected CUDA 13 wheel versions exist, but the full
bundle has not been qualified on a live Colab GPU. ComfyUI's transitive Python
requirements are not fully hash-locked; installed versions are runtime evidence,
not a claim of a fully reproducible environment.

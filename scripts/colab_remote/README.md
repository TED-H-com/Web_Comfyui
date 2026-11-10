# Colab remote batch contract

The parent controller copies this directory to
`/content/web-comfyui/<UUID>/runner/`, writes the immutable `manifest.json` next
to it, and uploads only the declared files under `input/`. It launches a detached
process using `python runner/remote_job.py --job-dir <directory>
--manifest-sha256 <digest>`. This CLI does not allocate a runtime or discover
credentials. The parent owns account selection, paid admission, output download
and verified assignment release.

Import `validate_manifest(manifest, job_dir, check_files=True)` from
`scripts.colab_remote.remote_job` before staging. Persist its returned normalized
copy, then hash the exact JSON bytes uploaded. Defaults include deterministic
seeds derived from the immutable batch UUID and child ID. Input digests bind
the supplied content, and external paths are generated names under `input/`.

```json
{
  "schema_version": 1,
  "job_id": "00000000-0000-4000-8000-000000000001",
  "deadline_epoch": 1893456000,
  "stop_on_error": true,
  "input_files": [],
  "jobs": [{
    "id": "output", "type": "video", "mode": "text",
    "prompt": "A synthetic paper kite moves across a blue sky.",
    "seconds": 5, "resolution": "480P", "aspect": "16:9",
    "intensity": "off", "fast": true, "seed": 1
  }]
}
```

The example deadline is illustrative. The controller sets an actual deadline
covering allocation, setup, generation and bounded result retrieval. Execution
rejects elapsed deadlines and deadlines more than 24 hours away.

| Job | Accepted controls |
|---|---|
| All | `id`, `type`, `prompt`, `seed` |
| Image | `inputs` (at most 8 images), `aspect`, `size`: 1K/1.3K/1.6K/2K/2.7K |
| Video | `mode`: text/ref/frames; `seconds`: 2–15; `resolution`: 480P/720P/1080P; `aspect`; `intensity`: off/low/mid/high; `fast` boolean |
| Video ref | `inputs` images/video/audio, `extend` earlier video or input video |
| Video frames | `first` and/or `last` image |
| TTS | `seconds`: whole seconds 2–15; `voices`: at most 3 audio references |

An external media path needs a matching `input_files` item with exactly `path`,
`size` and lowercase SHA-256. The total input cap is 256 MiB. Prior outputs use
`@child-id`, must precede the referencing job and must have the correct media
type. Ref-mode video prompts must mention every supplied input using one
consistent notation, such as `@Image1`, `@Video1`, `@Audio1`. Unmentioned media
is rejected rather than silently dropped. Native templates round dimensions to
multiples of 32; for example 720P landscape is 1280×704.

`status.json` is written atomically with `job_id`, `manifest_sha256`, `phase`,
`done`, and a `jobs` map. Phases are starting, installing, running, completed,
failed, submission_uncertain, or cancelled. Each submission saves a graph hash
and immutable request identity **before** POST `/prompt`; the returned
`prompt_id` is saved **before** any history poll. A held OS lock makes a repeated
launch return an acknowledgement without starting another worker. A restarted
worker reuses its persisted prompt ID. An unknown response is reconciled against
owned history/queue identity; absence is never permission to submit again.
An incomplete helper bundle or invalid manifest produces a failed status bound
to the launch UUID and requested manifest hash before setup starts. Template
integrity is checked before dependency installation or model downloads. Public
model downloads disable implicit Hugging Face token use.

Successful `artifacts.json` contains `job_id`, `manifest_sha256` and `artifacts`
records with `path` relative to the job directory, `size`, `sha256`, `kind`,
`seed`, and `prompt_id`. The cap is 1 GiB per artifact. A failed batch may also
contain verified earlier outputs. The parent must check every downloaded
digest/size and decode/inspect the declared media before adopting it; the remote
manifest proves byte identity, not media validity or visual quality.

Writing `cancel.requested` requests cancellation. The worker checks it and its
deadline before submissions and during polling. A detached runtime-side
watchdog attempts `google.colab.runtime.unassign()` at cancellation/deadline,
then terminates its owned process group. That notebook RPC can fail outside a
live kernel or during a network outage. It is a best-effort fallback: the parent
must still stop and verify removal of the exact assignment. Do not report
cleanup complete solely from a local marker or a timeout.

ComfyUI stays on `127.0.0.1:8188` in the dedicated runtime. No tunnel, Drive mount,
global token discovery or sibling-project credential access is used. Dependency
setup is per job, in a venv, with fixed Git/model revisions and model integrity
checks. Colab's existing pip (22.3 or later) manages the job interpreter using
[`--python`](https://pip.pypa.io/en/stable/topics/python-option/); the venv is
created with `--without-pip` so a missing system `ensurepip` does not prevent
setup. Package installation targets only the job's venv, without apt or system
Python changes. No SelfLift code or model weights are redistributed here. See `NOTICE.md`
for licenses and the current static-only validation level.

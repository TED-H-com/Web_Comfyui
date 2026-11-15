# Provider response fixtures

The JSON files in `scripts/fixtures/` are synthetic. They keep the response shapes of the
provider GET endpoints that the offline tests need. Identifiers, hashes, sizes and times
are made up. They contain no account data, media names, prompts or credentials.

- `*-graph-schema.json`, `runninghub-object-info.json`, `../r34/runninghub-resize-node.json`:
  node input schemas. Model lists are trimmed to the names the graphs use; upload lists
  are empty.
- `runninghub-absence.json`: empty history and queue responses.
- `cloud-job.json`, `cloud-jobs.json`, `cloud-workflow.json`, `cloud-redirect.json`:
  Comfy Cloud job, listing, executed-graph and output-redirect shapes.
- `autoip-settlement.json`, `autoip-duration.json`, `../r31/autoip-voice-25-to-50.json`:
  AutoIP task, frame-count and pricing shapes.
- `colab-failure.json`: a failed Colab job with a released lease.

Tests label hostile redirect and error bodies as synthetic as well. Offline tests do not
establish visual quality.

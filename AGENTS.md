# Contributor instructions

This repository is a standalone WebUI for a separately installed local ComfyUI.
Keep the existing design tokens and interactions in DESIGN.md.

- Keep server defaults on loopback; never add deployment-specific accounts or URLs.
- Runtime accounts, credentials, character/voice assets, generated media and databases are local user data. Never commit them.
- The explicitly requested public documentation examples are the sole media exception: only the six reviewed files in `scripts/public_demo_assets.py`, with matching hashes and `webui/docs/examples/provenance.json`. Do not broaden this into a runtime/media directory exception.
- API integrations must use explicit user configuration. Do not discover credentials from sibling projects.
- Document new workflow sources, pinned revisions, dependencies, redistribution licenses and actual validation level.
- Never imply a static JSON check proves GPU generation works.
- Run tests using `pytest-disktmp uv run --with pytest pytest -q ...` when available. Otherwise use a unique disk directory under `~/agent-work/tmp/pytest/` as `--basetemp` and remove it after the run. Never put test data on tmpfs or `/dev/shm`.
- Before publishing, run `python3 scripts/check_public_release.py`, tests, and a secret scanner on the staged source and Git history.

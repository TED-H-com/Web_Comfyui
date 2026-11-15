ComfyUI-VideoHelperSuite: GPL-3.0.
The vhs-audio.patch is the maintainer's patch for videohelpersuite/utils.py on top of the pinned upstream commit.
Upstream: https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite
Provenance and both source hashes are recorded in registry.json.
Distributed to the Colab runtime to reproduce the pinned files.

BreezyVoice: Apache-2.0. breezy-frontend.patch and breezy-inference.patch are the maintainer's tracked-file patches for the pinned upstream commit. registry.json records the public base commit, upstream and modified SHA256, and local mtime; source provenance is a label only. Apply exclusively to that pinned checkout with strict pre/post hashes. The upstream checkout retains its LICENSE.

The VoxCPM wheel named in `voice-environments.json` is not distributed in this repository. Without it, `scripts/colab_session_voice.py` fails closed with `voice_env_mismatch`.

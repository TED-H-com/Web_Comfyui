# Licensing and source attribution

Web ComfyUI source code is distributed under GPL-3.0; see LICENSE. Each separately
identified third-party file keeps its original license and attribution.

The curated dseditor workflow collection is GPL-3.0. Its pinned source, modification
notices and full license are in `third_party/community/dseditor/` and
`manifests/community_workflows/catalog.json`.

The ecjojo collection is indexed through source links and dependency descriptions.
No ecjojo workflow JSON is redistributed because that repository does not declare
a redistribution license at the reviewed revision. Users can consult the upstream
workflows and obtain any needed permission from their author.

Article recipes describe concepts and parameter choices in our own words. They do
not reprint the articles, include article images, or package the authors' model
weights. Sources are linked on every recipe.

KohakuMossTrainer is an optional separate installation. Its own KohakuMoss license,
including branding requirements, applies to that software; this project's GPL
does not relicense it. Likewise ComfyUI extensions, NVIDIA runtime components and
model weights have their own licenses and requirements.

## Optional pose and costume references

Open Pose & Costume metadata is pinned to revision `16e378aaf0dd021b8e06001b4e1ed781580287ba`. No upstream images are bundled. Source and custom-asset terms, runtime fetching, and verification limits are documented in [character-portraits.md](docs/character-portraits.md).

## Optional Colab H3 worker

The graph/media helpers and three templates under `scripts/colab_remote/` derive
from dseditor/h3-colab-skill revision `34ba92508ace42bc29548af7a9f07c27dcae5230`
under MIT. Its complete notice and changes are in that directory's
`LICENSE.upstream` and `NOTICE.md`. Google Colab CLI is an optional Apache-2.0
dependency. No model weights or runtime node repositories are bundled; their
separate licenses and validation limits are documented in [the Colab guide](docs/colab.md).

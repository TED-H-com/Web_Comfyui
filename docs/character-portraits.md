# 角色搭配寫真 / Character portrait composer

The installed app's **角色搭配寫真** tool uses Qwen-Image 2.1 with one actual
character image, plus scene, clothing and pose descriptions. Select a local
character to bind its configured avatar on the server, or upload your own image.
Character-bound requests reject replacement images and follow the character's
anime/realistic classification. Existing legacy character routes remain valid.
Imported provider characters retain their existing provider workflow.

選擇「動畫／寫實」畫風，再選服裝與姿勢大分類。分類會隨機挑選細項；
點圖卡開啟置中小型預覽，可切換視角，再按「套用」。服裝搜尋、分頁與
隨機選項都依畫風分開；姿勢骨架共用。切換畫風會重新挑選服裝。

Outfit and pose cards supply text descriptions, **not garment images or OpenPose
conditioning**. The prompt requests one rendering style across the character,
body, clothing, props and background. Likeness, garment details and style
consistency are not guaranteed. This change has CPU and synthetic-browser tests;
it is not a new GPU-quality qualification or a recorded website demo. The static
website's existing 23 recorded/prepared examples are unchanged.

## Requirements and reference sources

Use the same nodes and model files as Qwen 2.1 instruction editing, pinned in
[`models.lock.json`](../manifests/qwen_image21/models.lock.json). No models or
personal media are included. Z-Image Turbo is not used because the current graph
has no identity-reference input.

The optional reference catalog comes from
[Open Pose & Costume](https://github.com/hongye007/open-pose-costume), pinned to
`16e378aaf0dd021b8e06001b4e1ed781580287ba` in
[`source.json`](../manifests/open_pose_costume/source.json). Catalog bytes are
SHA-256 checked before caching. Browsing downloads metadata and loads preview
images from the original public hosts; it does not bulk-import images or queue
work. Cache defaults to `WEB_COMFYUI_CACHE_DIR/reference-sources`, or
`~/.cache/web-comfyui/reference-sources`; `WEB_COMFYUI_REFERENCE_CACHE` overrides
that directory. No authentication credentials are required for these sources.

Source images are not redistributed here. Follow the upstream
[SOURCES.md](https://github.com/hongye007/open-pose-costume/blob/16e378aaf0dd021b8e06001b4e1ed781580287ba/SOURCES.md)
and [custom asset terms](https://github.com/hongye007/open-pose-costume/blob/16e378aaf0dd021b8e06001b4e1ed781580287ba/public/custom/LICENSE.txt).
Custom assets permit personal learning/noncommercial use and require separate
permission for other uses. Those terms do not become the application's GPL license.

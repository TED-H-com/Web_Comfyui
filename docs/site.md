# 維護 GitHub Pages 工作流說明

公開網站：https://ted-h-com.github.io/Web_Comfyui/

網站和本機 WebUI 共用 `webui/static/workflow-library.js`、`workflow-library.css`、既有 `styles.css` 與 `webui/workflow_guides.py` 產生的說明。網站只載入靜態目錄；本機 WebUI 仍透過需登入的 API 取得資料。

## 本機預覽

```bash
python3 scripts/build_docs.py
python3 scripts/check_public_release.py --root .site
python3 -m http.server 8000 --bind 127.0.0.1 --directory .site
```

開啟 `http://127.0.0.1:8000`。`.site` 是可重新產生的忽略目錄；建置器只會替換帶有自己建置標記的目錄，避免誤刪其他資料。

每個工作流可使用 `#/workflow/<catalog-id>` 分享，開始使用與授權頁分別為 `#/start`、`#/about`。所有網站資源使用相對路徑，因此可部署在 GitHub Pages 的 repo 子路徑下。

## 更新說明

- 在 `webui/workflow_guides.py` 維護用途、素材、方法與技巧。只從可確認的節點欄位抽取參數，不把提示詞、媒體路徑或憑證轉成教學資料。
- `manifests/community_workflows/catalog.json` 管理社群來源、授權與固定版本；`manifests/workflow_coverage/imported_sources.json` 記錄其他已授權收錄檔案與修改。
- 文章方法整理放在 `manifests/community_recipes/recipes.json`，不複製文章全文或圖片。未取得再散布授權的流程保留來源參考。
- 新增可下載 JSON 時必須同時提供授權檔、來源版本與檔案雜湊。工作流變更後應檢查拓樸與內容，再更新經核對的雜湊；不要為了讓測試通過就跳過核對。

建置器不掃描個人的新工作流；只匯出兩份來源清單允許的檔案。網站目錄包含共用介面、公開說明、核准的 JSON、來源紀錄及授權文字；不包含整份 repo、伺服器或使用者資料。

## 驗證與發布

```bash
pytest-disktmp uv run --python 3.12 --with pytest --with Pillow pytest -q tests/test_docs_site.py scripts/test_docs_browser.py scripts/test_workflow_library_browser.py
```

瀏覽器測試需要 `chromium`，測試 390、768、1440px 與 `/Web_Comfyui/` 子路徑。缺少瀏覽器時會標示略過；不能把略過視為已完成視覺驗證。

`.github/workflows/pages.yml` 在 `main` 更新時先檢查來源、執行發布邊界測試、建立 `.site` 並檢查產物，再上傳該目錄至 GitHub Pages。首次設定需在 repo 的 Pages 設定選擇 **GitHub Actions**；後續更新由 workflow 自動部署。

靜態檢查與瀏覽器測試不會執行 GPU 生成，也不代表上游模型、節點或服務仍可用。

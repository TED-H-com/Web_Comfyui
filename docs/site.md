# 維護 GitHub Pages 互動說明

公開網站：https://ted-h-com.github.io/Web_Comfyui/

網站使用真正的 `webui/static/index.html`、`app.js`、樣式與工作庫元件，保留本機 WebUI 的導覽、工具按鈕與表單。`webui/docs/docs.js` 加上唯讀文件轉接層：提供靜態資料，將生成、上傳與表單送出導向說明，封鎖 API 寫入，不連接本機 ComfyUI 或供應商。角色、素材、帳號與任務使用空白資料。

點 **文生圖** 等工具名稱、**查看流程與模型** 或生成按鈕，會顯示該功能真正使用的組圖程式、checkpoint／編碼器／VAE／LoRA、預設參數、操作步驟與前置需求。23 項功能說明對應實際 registry；隱藏、停用或角色限定的旗標仍保留。文件中可操作表單不代表功能已在使用者電腦上就緒。

**工作庫** 則提供獨立的社群流程、參考來源與方法配方。目前公開目錄共 248 項，包含 46 份可下載 JSON（24 份精選社群流程、22 份基礎範例）。大部分創作表單使用 Python 直接組圖；不把名稱相近的社群 JSON 當作該表單的實際模板。

## 本機預覽

```bash
python3 scripts/build_docs.py
python3 scripts/check_public_release.py --root .site
python3 -m http.server 8000 --bind 127.0.0.1 --directory .site
```

開啟 `http://127.0.0.1:8000`。`.site` 是可重新產生的忽略目錄；建置器只會替換帶有自己建置標記的目錄，避免誤刪其他資料。

功能說明可用 `#/studio/text2img?guide=1` 這類連結分享；工作流使用 `#/workflow/<catalog-id>`。舊的 `#/start` 與 `#/about` 會開啟安裝／文件說明。所有網站資源使用相對路徑，可部署在 GitHub Pages 的 repo 子路徑下。

## 資料如何產生

- `scripts/app_guide_catalog.py` 靜態解析 `webui/server.py`、H3 product/native 與 Qwen 模組的 AST，取得工作室、23 項 feature、完整欄位、選項、圖示、模型載入器與預設值。它不匯入或執行伺服器，不讀環境變數、憑證、模型或使用者資料。語音欄位的環境設定只使用程式中的文字預設值。
- `manifests/app_guides/catalog.json` 維護每個實際 feature 的用途、流程、技巧與來源對應；產物為 `app-guide.json`。程式 builder 與官方參考 JSON 分別標示，無固定 checkpoint 的語音／API 工具不虛構模型路徑。
- 建置器先檢查抽取器明列的 `SOURCE_FILES`；抽取器拒絕未列出的路徑與 symlink。只允許特定 AST 常數與純資料操作，未知語法會失敗，不使用 `eval`／`exec`。
- `webui/workflow_guides.py` 產生工作庫的用途、素材、方法與技巧。只從可確認欄位抽取參數，不把提示詞、媒體路徑或憑證轉成教學資料。工作庫資料輸出為 `catalog.json`。
- `manifests/community_workflows/catalog.json` 管理社群來源、授權與固定版本；`manifests/workflow_coverage/imported_sources.json` 記錄其他已授權收錄檔案與修改。文章配方放在 `manifests/community_recipes/recipes.json`，不複製文章全文或圖片。

新增下載 JSON 時，同時提供授權檔、來源版本與檔案雜湊。工作流變更後先核對拓樸與內容，再更新雜湊。未取得再散布授權的流程只保留來源參考。建置器不掃描個人的新工作流，只匯出來源清單核准的檔案；產物不包含伺服器原始碼、整份 repo 或使用者資料。

## 驗證與發布

```bash
pytest-disktmp uv run --python 3.12 --with pytest --with Pillow pytest -q scripts/test_app_guide_catalog.py tests/test_docs_site.py scripts/test_docs_browser.py scripts/test_workflow_library_browser.py
```

瀏覽器測試需要 `chromium`，涵蓋 390、768、1440px 與 `/Web_Comfyui/` 子路徑。缺少瀏覽器時會標示略過；不能把略過視為已完成視覺驗證。檢查實際工具按鈕與表單說明、模型資訊、唯讀操作和工作庫導覽。

`.github/workflows/pages.yml` 在 `main` 更新時先檢查來源、執行發布邊界測試、建立 `.site` 並檢查產物，再上傳至 GitHub Pages。首次設定需在 repo 的 Pages 設定選擇 **GitHub Actions**；後續由 workflow 自動部署。

文件網站不做即時依賴檢查。本機 **檢查 ComfyUI 節點與模型** 才會讀取使用者 ComfyUI 回報的節點與模型選項；缺少與未知狀態分別顯示。靜態檢查、選項匹配與瀏覽器測試都不代表 GPU 生成成功。

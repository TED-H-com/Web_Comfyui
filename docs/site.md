# 維護 GitHub Pages 互動說明

公開首頁：[專案介紹與安裝入口](https://ted-h-com.github.io/Web_Comfyui/)。互動示範：[https://ted-h-com.github.io/Web_Comfyui/demo/](https://ted-h-com.github.io/Web_Comfyui/demo/)。首頁與示範分開；`./demo/` 不是實際生成服務，真正使用請依 [安裝指南](installation.md) 設定自己的環境。

示範頁使用真正的 `webui/static/index.html`、`app.js`、樣式與工作庫元件，保留本機 WebUI 的導覽、工具按鈕與表單。公開文件站與安裝後的 WebUI 都可用右上角 **繁中 / English** 切換介面語言。`webui/docs/docs.js` 提供靜態資料、封鎖 API 寫入；網站不連接本機 ComfyUI 或供應商。角色、素材與任務清單從空白開始，文件模式的合成登入狀態不建立真實帳戶。

每個工具有兩個說明按鈕：**查看流程與模型** 顯示真正使用的組圖程式、checkpoint／編碼器／VAE／LoRA、預設參數、操作步驟與前置需求；**查看 ComfyUI 範本與節點** 開啟可互動的節點檢視。可抽取的 ComfyUI 節點與連線會依來源呈現；語音、API 或其他服務流程使用明確標示的處理流程示意，不宣稱它們是可執行的 ComfyUI 圖。23 項功能說明對應實際 registry；隱藏、停用或角色限定的旗標仍保留。

## 在原有表單體驗範例

23 項工具與建立角色都保留預填表單與說明，每個工具都有可體驗的結果。已實跑的工具顯示對應紀錄；其餘顯示「預製介面範例 · 非此工作流實測」，清楚標記素材真正的來源引擎。預製範例不證明所選工作流已執行，也不代表其品質。修改提示詞或選項只用來熟悉介面，不會重新生成。

按下生成前只在上傳區顯示參考素材，結果區保持隱藏；重設或更換參考檔案會清除舊結果。指令改圖在生成後並排顯示青綠白原隊服與紅色上衣、深藍短褲的實測成品，清楚標示修改前後。

文生圖與其他已實跑的範例按下生成後，會先顯示約 2.4 秒的百分比進度，再開啟成果；進度明確標為模擬，不代表即時 GPU 狀態。系統設定減少動態效果時會快速完成。參考圖融合改用 SAM3 分割原人物，再合成夜間球場背景，保留人物原貌；[實際模型、節點與像素比對](subject-blend.md) 有獨立 GPU 紀錄。此合成不會自動補光或新增陰影。

**建立角色** 是不需要 GPU 的流程模擬。範例資料標明「尚未建立」；填入名字、描述與照片後，模擬進度完成會關閉表單並進入暫存工作室。返回列表可重新開啟角色或建立另一個。工作室分開呈現本次輸入與晴晴的預製換裝、寫真候選及影片成果；自選照片不會套用到這些固定案例，也不保證寫真候選與原圖完全同臉。角色與獨立的本機照片 URL 只保留在頁面記憶體，離開表單不會清除；刪除、清除全部或重新整理後移除。不寫入帳戶或資料庫，不宣稱訓練過模型。

點選或拖入支援的檔案，可在瀏覽器預覽自己的圖片、影片或音訊。預覽使用暫時的 blob URL，不上傳、不讀取其他本機檔案，也不寫入帳戶或素材庫。替換、清除或離開表單時會釋放預覽 URL。密碼欄位停用；文件模式的草稿與偏好只放在頁面記憶體。可操作的表單與模擬進度不表示依賴已安裝或 GPU 已完成生成。

參考素材與實跑成果分開呈現。實跑紀錄包含真正的工作流、輸入／輸出雜湊、時間與硬體；預製介面範例則另外標明，不能當作所選模型的實測證據。單次實跑只證明記錄中的設定成功，不是所有選項或硬體的效能保證。

女聲使用本機 VoxCPM2 的九段真實合成音訊，包含迎賓、元氣主持、溫柔故事、成熟播報、英文迎賓、三段簡報與合成參考聲音延續。全部為 CPU 執行，未讀取私人錄音；訊號與自動逐字稿檢查有獨立紀錄，沒有宣稱代理已人工試聽。聲音庫可逐一播放、選擇後帶到語音或聲音參考表單；此頁只保留記憶體選擇，不會實際複製使用者音色。逐字稿是實際說出的內容，不因介面切換英文而更換音訊。

對嘴與虛構角色換臉有新 ComfyUI 執行紀錄；首尾幀採 Wan2.1 I2V 480p 變體，記錄明示它與表單原 SCAIL 預設的差異。循環影片由 VHS 處理，沒有擴散模型。H3、Qwen 與部分企劃入口展示具名替代引擎的預製案例，不冒稱 H3 或 Qwen 產出。

**工作庫** 則提供獨立的社群流程、參考來源與方法配方。目前公開目錄共 248 項，包含 46 份可下載 JSON（24 份精選社群流程、22 份基礎範例）。大部分創作表單使用 Python 直接組圖；不把名稱相近的社群 JSON 當作該表單的實際模板。

## 硬體選單與雲端示範

硬體選單提供無 NVIDIA GPU、8／12／16／24／32 GB，以及雙 24 GB 等規劃級距。顯存需求是依預設 builder 整理的估計，不是偵測結果、最低需求實測或效能基準；解析度、精度、影格數、參考素材、卸載方式及系統記憶體都會影響實際需求。這些 builder 沒有自動跨卡切分模型，雙 24 GB 以單卡 24 GB 計，不加總成 48 GB。

選擇只影響靜態示範中的操作提示與範例可用性，不修改安裝後 WebUI 的設定、不啟用原本停用的 API，也不因選擇無 GPU 而停用使用者的本機服務。**AutoIP** 選項開放全部 23 項示範表單供探索；不代表供應商已支援每個本機工作流、節點或訓練器。實際雲端能力與目前七項已實作 API 整合分開核對。首頁的選用註冊連結與 QR code 使用聯盟推薦碼 `TEDH2FND`，詳見 [服務界線與推薦揭露](autoip-cloud-demo.md)。

## 本機預覽

```bash
python3 scripts/build_docs.py
python3 scripts/check_public_release.py --root .site
python3 -m http.server 8000 --bind 127.0.0.1 --directory .site
```

開啟 `http://127.0.0.1:8000/` 查看專案首頁；互動示範位於 **`http://127.0.0.1:8000/demo/`**。`.site` 是可重新產生的忽略目錄；建置器只會替換帶有自己建置標記的目錄，避免誤刪其他資料。

功能說明可用 `demo/#/studio/text2img?guide=1` 這類連結分享；工作流使用 `demo/#/workflow/<catalog-id>`。舊的 `#/start` 與 `#/about` 會開啟安裝／文件說明。所有網站資源使用相對路徑，可部署在 GitHub Pages 的 repo 子路徑下。

## 資料如何產生

- `scripts/app_guide_catalog.py` 靜態解析 `webui/server.py`、H3 product/native 與 Qwen 模組的 AST，取得工作室、23 項 feature、完整欄位、選項、圖示、模型載入器與預設值。它不匯入或執行伺服器，不讀環境變數、憑證、模型或使用者資料。語音欄位的環境設定只使用程式中的文字預設值。
- `manifests/app_guides/catalog.json` 維護每個實際 feature 的用途、流程、技巧與來源對應；產物為 `app-guide.json`。程式 builder 與官方參考 JSON 分別標示，無固定 checkpoint 的語音／API 工具不虛構模型路徑。
- `scripts/app_graph_catalog.py` 從核准來源建立節點／流程檢視資料；`scripts/demo_catalog.py` 為 23 項 feature 與建立角色提供繁中／英文提示詞、來源步驟、實跑成果或未驗證狀態與揭露文字。`webui/docs/tour.js` 將範例接到原有表單，`webui/docs/explorers.js` 顯示節點與實跑紀錄；它們不執行生成。
- 建置器先檢查抽取器明列的 `SOURCE_FILES`；抽取器拒絕未列出的路徑與 symlink。只允許特定 AST 常數與純資料操作，未知語法會失敗，不使用 `eval`／`exec`。
- `webui/workflow_guides.py` 產生工作庫的用途、素材、方法與技巧。只從可確認欄位抽取參數，不把提示詞、媒體路徑或憑證轉成教學資料。工作庫資料輸出為 `catalog.json`。
- `manifests/community_workflows/catalog.json` 管理社群來源、授權與固定版本；`manifests/workflow_coverage/imported_sources.json` 記錄其他已授權收錄檔案與修改。文章配方放在 `manifests/community_recipes/recipes.json`，不複製文章全文或圖片。

新增下載 JSON 時，同時提供授權檔、來源版本與檔案雜湊。工作流變更後先核對拓樸與內容，再更新雜湊。未取得再散布授權的流程只保留來源參考。建置器不掃描個人的新工作流，只匯出來源清單核准的檔案；產物不包含伺服器原始碼、整份 repo 或使用者資料。

## 示範媒體的發布邊界

公開範例是使用者明確要求的文件示範例外，存放在 `webui/docs/examples/`，建置時只複製指定檔案到 `assets/examples/`。[provenance.json](../webui/docs/examples/provenance.json) 記錄素材來源、轉換與最終 SHA-256；實跑成果另有工作流及輸入／輸出的執行證據。參考圖來源與 GPU 生成來源分別記錄。發布的 WebP 圖片不含 EXIF 或文字中繼資料；不公開私有路徑、帳戶、模型權重或憑證。

`scripts/public_demo_assets.py` 固定經審核的檔名與雜湊。發布檢查只允許完全符合雜湊的檔案出現在 `webui/docs/examples/` 或產物的 `assets/examples/`；改名、改內容、未列出的媒體、symlink 與個人／執行期路徑仍會被拒絕。這不是對圖片或影片副檔名的全面開放。`provenance.json` 與發布允許清單需一併審核，不能只更新來源紀錄就讓其他媒體通過。

## 驗證與發布

```bash
pytest-disktmp uv run --python 3.12 --with pytest --with Pillow --with playwright pytest -q scripts/test_app_guide_catalog.py scripts/test_app_graph_catalog.py scripts/test_demo_catalog.py tests/test_demo_publication.py tests/test_docs_site.py scripts/test_docs_browser.py scripts/test_workflow_library_browser.py
```

瀏覽器測試需要 `chromium`；整站測試另用 Playwright 設定真實的 390、768、1440px viewport，驗證 `/Web_Comfyui/` 專案首頁與 `/Web_Comfyui/demo/` 示範子路徑。整站測試也支援 `uv run --with playwright playwright install chromium` 安裝的瀏覽器。缺少瀏覽器或 Playwright 時會標示略過；不能把略過視為已完成視覺驗證。檢查兩個說明按鈕、節點檢視、原有生成按鈕的行內結果、角色建立、語言切換、預覽檔案不外送、重設／離頁取消，以及工作庫導覽。媒體測試另外核對固定雜湊、路徑、EXIF／文字中繼資料與 symlink 拒絕行為。

`.github/workflows/pages.yml` 在 `main` 更新時先檢查來源、執行發布邊界測試、建立 `.site` 並檢查產物，再上傳至 GitHub Pages。首次設定需在 repo 的 Pages 設定選擇 **GitHub Actions**；後續由 workflow 自動部署。

文件網站不做即時依賴檢查。本機 **檢查 ComfyUI 節點與模型** 才會讀取使用者 ComfyUI 回報的節點與模型選項；缺少與未知狀態分別顯示。靜態檢查、選項匹配與瀏覽器測試都不代表 GPU 生成成功。

## 已記錄的 GPU 範例

目前 [來源紀錄](../webui/docs/examples/provenance.json) 包含 RTX 5090 上完成的 8 次執行，示範頁選用其中 6 項功能的成果：文生圖、指令改圖、參考圖融合、臉部參考肖像、圖生影片與舞蹈／動作轉移。實際模型與設定以各筆紀錄中的工作流為準；這些單次範例不是全部功能已完成驗證的宣告。

人物參考為原創生成的虛構成年女性。動作轉移使用的 `cheer-motion-source.mp4` 也是由 Wan 實際生成的側步／抬手應援動作影片，再輸入 SCAIL 工作流；它不是下載的真人表演素材或單張圖片的平移縮放動畫。影片海報由對應輸出影片擷取。素材的來源雜湊、發布版本雜湊與執行關聯都保留在來源紀錄中。

## 在自己的 GPU 重跑公開紀錄

先依安裝指南準備 ComfyUI，以及紀錄所用的模型與 custom nodes。`scripts/replay_recorded_demo.py` 只讀取公開來源紀錄與固定雜湊素材，不搜尋帳號、憑證或個人檔案。從 `provenance.json` 的 `runs` 選取一個 `id`，先驗證：

```bash
python3 scripts/replay_recorded_demo.py <run-id>
```

預設只檢查與列出紀錄，不連線、不複製檔案、不執行 GPU。要實際提交給自己的本機 ComfyUI，明確加入 `--run`；有參考輸入的紀錄還須指定自己的 ComfyUI `input` 目錄：

```bash
python3 scripts/replay_recorded_demo.py <run-id> --run --input-dir ~/comfyui/input
```

預設連線為 `http://127.0.0.1:8188`；`--comfy-url` 只接受數字形式的 HTTP loopback 位址，不接受帳密、外部位址或重新導向。核准素材會複製至指定目錄中的 `web-demo-replay/`，遇到同名但不同內容的檔案會停止。腳本只回報提交取得的 `prompt_id`，完成與否請在 ComfyUI 查看；它不宣稱佇列提交就是生成成功。重跑前會確認輸入雜湊與原始執行一致；成果可能轉成公開用格式，與 ComfyUI 原始輸出分別記錄雜湊。即使使用同一份輸入，重跑結果也不保證逐像素相同。模型與節點版本、執行硬體也可能影響結果。

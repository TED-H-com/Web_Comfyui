# Comfy Cloud 與 RunningHub 託管算力

研究與協定檢查日期：2026-11-10。這兩個選項使用同一個生產表單，但執行的是管理者明確設定的遠端工作流程。此版本已完成合成素材、注入 HTTP 回應，以及本機 TLS 伺服器的離線驗證；尚未登入真實供應商帳戶、確認付費權益、部署工作流程或執行付費 GPU 工作。既有本機、Colab、AutoIP 的說明見 [算力選擇](compute-platforms.md)。

使用「平台設定」為自己的登入身分填入 API key、啟用平台並匯入配方。API key 是只寫欄位，回應只顯示是否已設定；留空保留，明確清除才刪除。各登入身分的配方、憑證、工作與成果分開存放。管理 API 的授權由 WebUI 處理；後端不搜尋其他專案或環境中的憑證。

Comfy 接受 `https://cloud.comfy.org`，或已建立的 `https://<deployment>.run.comfy.app`。專用部署必須先準備 Build、Release 與 Deployment，包含模型、節點及 Python 相依套件；目前官方說明要求 Standard 或更高付費方案。設定網址不會替使用者部署或啟動 GPU。[Comfy API 部署說明](https://support.comfy.org/articles/2703236295-comfy-api-deploy-your-comfyui-workflow-as-an-api)

RunningHub 使用 `https://www.runninghub.ai`。先在供應商平台儲存並成功手動執行自己的工作流程，取得 workflow ID 與各節點的輸入欄位。API key 的資格、資源及餘額仍由供應商判定；本介面中的「已設定」並不代表已驗證帳戶或節點可用。[RunningHub 錯誤碼說明](https://www.runninghub.ai/runninghub-api-doc-en/doc-8287467)

## 配方與映射

配方匯入的是 JSON 陣列。每個功能最多一份配方，包含：

| 欄位 | 用途 |
| --- | --- |
| `feature_id` | 與生產表單的功能 ID 完全相同，例如 `text2img` |
| `recipe_id`, `label` | 此配方的版本識別碼與可讀名稱 |
| `output_kind` | `image`、`video` 或 `audio` |
| `workflow` | Comfy 的 **API 格式**圖，不是含 `nodes` / `links` 的編輯器匯出格式 |
| `workflow_id` | RunningHub 上自己的工作流程 ID |
| `bindings` | `source` → `node_id` / `input` 的明確映射 |
| `fixed_settings` | 宣告此工作流程固定採用的表單值；使用者自訂其他值就拒絕 |
| `estimated_cost` | 管理者設定的單次費用估計，必須為正數 |
| `partner_nodes` | Comfy 選用。明確允許將 API key 提供給 Partner Nodes，預設關閉 |
| `instance_type` | RunningHub 選用：`default`、`plus`、`ultra`，預設 `default` |

`source` 支援 `prompt`、`settings.<欄位>`、`files.<素材欄位>`。`prompt` 會沿用生產功能的角色身分、畫風與 H3 結構化描述規則，不會把內部角色識別碼或授權收據當提示詞送出。角色／畫風請求必須使用完整 `prompt` 映射。每個實際素材欄位都必須映射，不能默默遺失第二張參考圖。所有其他創作設定也必須映射或與 `fixed_settings` 中的明確值一致。這些宣告需要與遠端工作流程的真正行為一致；成功下載媒體不能證明模型品質或每個節點的語意。

映射保留數值與布林型別。`settings.seed` 是例外的已定義轉換：十進位種子文字轉成整數，`-1` 以本次請求 UUID 衍生 uint64 種子，同一請求重連不會改變。解析度不自動縮小、負向提示詞不自動刪除、不同供應商的模型不被視為相同。

以下是可匯入的 **Comfy API 格式範例**，採用標準 SDXL 節點。它沒有在遠端執行驗證。先將 `ckpt_name` 改成自己部署中確實存在且有權使用的模型檔名；Cloud 也必須支援這些節點及模型。此範例映射 `text2img` 的實際寬高、步數、負向提示、張數與種子，不代表原本本機的模型或畫質。

```json
[
  {
    "feature_id": "text2img",
    "recipe_id": "owned-sdxl-text2img.v1",
    "label": "自有 SDXL 遠端配方（需先驗證）",
    "output_kind": "image",
    "estimated_cost": 20,
    "fixed_settings": {"useDopsd": false},
    "bindings": [
      {"source": "prompt", "node_id": "6", "input": "text"},
      {"source": "settings.negative", "node_id": "7", "input": "text"},
      {"source": "settings.width", "node_id": "5", "input": "width"},
      {"source": "settings.height", "node_id": "5", "input": "height"},
      {"source": "settings.batchSize", "node_id": "5", "input": "batch_size"},
      {"source": "settings.steps", "node_id": "3", "input": "steps"},
      {"source": "settings.cfg", "node_id": "3", "input": "cfg"},
      {"source": "settings.seed", "node_id": "3", "input": "seed"}
    ],
    "workflow": {
      "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
      "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
      "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["4", 1]}},
      "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["4", 1]}},
      "3": {"class_type": "KSampler", "inputs": {"seed": 0, "steps": 20, "cfg": 7, "sampler_name": "euler", "scheduler": "normal", "denoise": 1, "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
      "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
      "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "WebComfy", "images": ["8", 0]}}
    }
  }
]
```

RunningHub 可以匯入同一個圖後，使用相同的 `bindings`、`fixed_settings` 與功能欄位，移除 `workflow` 並加入自己的 `workflow_id`。節點 ID 若在平台被改變，映射也必須一起改。`instance_type: "plus"` / `"ultra"` 只選擇官方所列的 48 / 84 GB 資源，不保證會員、容量或餘額可用。圖片修改配方還必須增加例如 `{"source":"files.image","node_id":"10","input":"image"}`，而遠端圖中的節點 10 必須是接受該圖片的實際節點。[RunningHub 工作流程提交協定](https://www.runninghub.ai/runninghub-api-doc-en/api-425761093)

第一次只可手動選擇。只有同一網址、憑證版本與完整配方成功產生並下載驗證成果後，才會提供 `auto_eligible: true`。更換 API key、網址或配方會失去該驗證資格。供應商網站上的工作流程或專用部署若被外部修改，WebUI 無法自動察覺；管理者必須更新 `recipe_id`，再手動驗證。這個標記表示傳輸與媒體驗證成功，不是品質認證或帳戶餘額保證。

## 額度、期限與恢復

費用維度分開：Comfy 為 `max_comfy_credits`，RunningHub 為 `max_runninghub_coins`，另外必須設定 `max_minutes`。這裡檢查的是配方的 `estimated_cost`，不是供應商即時報價，也不是硬性帳單上限。Comfy 的部署 GPU 時間包括啟動、模型載入及暖機待命；模型儲存也可能持續收費。RunningHub 回傳的 `consumeCoins` 與 `chargedPoint` 是不同欄位，本程式不假裝它們能互換。實際計價應以自己的供應商儀表板為準。[Comfy 官方計價](https://comfy.org/pricing/)、[RunningHub 成果欄位](https://www.runninghub.ai/runninghub-api-doc-en/api-425761034)

工作使用不可變的本機請求 UUID，另存供應商回傳的遠端 ID。送出前先持久化狀態；若提交的回應遺失，保留 `submission_uncertain`，不自動重送、不切換到另一個平台。這種工作仍占用同時執行名額。

Comfy 的 `Idempotency-Key` 是「重複時拒絕」，不是回放原本回應，且只保留 24 小時。專用部署支援 request label 與工作查詢，可在失去回應時找回同一工作。Comfy Cloud 不接受非空 label、目前也不支援工作清單；RunningHub 工作流程提交沒有已確認的客戶端去重欄位。後兩者若連遠端 ID 都沒有收到，需要登入供應商儀表板確認或取消工作，不能以新的請求冒險重送。此版本不接受無法證明與原請求相關的手填遠端 ID。[Comfy v2 提交與工作清單規格](https://docs.comfy.org/openapi-v2.yaml)

WebUI 存活時，背景程序會輪詢既有工作，重啟後恢復，超過期限會要求取消。取消回應不會立刻當成已停止，必須讀到供應商的終止狀態。網路中斷、撤銷 key、服務停機、未知遠端 ID 都會影響取消能力；重新設定有效 key 可恢復已綁定工作的查詢。取消期間已完成的成果仍會下載驗證。關閉或刪除遠端部署以及暖機／儲存費用，必須在供應商平台管理。

## 傳輸與成果限制

輸入合計最多 32 MiB，RunningHub 每個檔案另外限制為 30 MB；最多 8 個素材欄位。輸入先以 Pillow 或 ffprobe 檢查，再上傳。Comfy 上傳採用 `/api/v2/assets` multipart，檔案必須放在最後一個 part，圖內使用 `core/ASSET` 參考。RunningHub 使用 `/task/openapi/upload` 的檔案名稱，再置入 `nodeInfoList`。[Comfy 素材規格](https://docs.comfy.org/api-reference/v2/assets/upload-an-asset-single-call-multipart)、[RunningHub 上傳規格](https://www.runninghub.ai/runninghub-api-doc-en/api-425761099)

RunningHub 輪詢 `/task/openapi/outputs`：804 表示執行中，813 表示排隊中，805 表示中斷或狀態錯誤；官方整合指南已註明舊 `/status` 不再維護。未知回應不當作成功或允許重送。[官方整合範例](https://www.runninghub.ai/runninghub-api-doc-en/doc-8287469)

每次最多收集 16 個同類成果，合計最多 64 MiB。支援 PNG/JPEG/WebP、MP4/MOV/WebM、WAV/MP3/FLAC/M4A；其他輸出、混合媒體、ZIP 或文字成果目前會停留在成果收集中，不能當成已完成。每個檔案必須下載完整、通過圖片解碼或影音串流檢查，再計算 SHA-256。Comfy 已知大小也必須一致。瀏覽器與下一個流程步驟只取得需要擁有者授權的本機 URL，不取得 key 或供應商簽名網址。成果取用時再次檢查大小與 SHA-256。

所有外連使用有限期限 HTTPS，不讀取環境代理，不自動重試 POST。供應商 API 只可連到官方設定網址；成果下載可跟隨有限次公開 HTTPS 轉址，跨來源不帶 Authorization。DNS 回應必須全為公開位址，連線固定使用已檢查的 IP 並驗證原始主機名稱的 TLS 憑證。憑證、工作狀態與素材檔案採私有目錄及 `0600` 檔案權限。

## 維護者驗證

```sh
pytest-disktmp uv run --with pytest --with pillow pytest -q \
  tests/test_hosted_compute.py tests/test_hosted_compute_http.py
```

HTTP fixture 測試包括兩種上傳／提交格式、重啟、提交不確定性、專用部署查回、期限、取消與晚到成果、寫入中的取消、私有成果、檔案變更與憑證更新。TLS 測試只使用磁碟臨時憑證與 loopback 合成伺服器，檢查真正的 chunked framing、截斷、大小限制、有限 timeout 及 DNS 私有位址拒絕。沒有測試會登入帳戶或執行遠端工作。

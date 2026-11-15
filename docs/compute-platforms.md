# 統一運算平台與多步製作

所有操作都在 Web UI。生成表單提供本機、自動安排、Google Colab、AutoIP、Comfy Cloud、RunningHub。選擇平台前會檢查此功能、設定、素材、帳號、容量與費用門檻；列出平台不代表每個功能都已完成遠端部署。

## 如何使用

1. 在生成表單選擇「執行方式」。本機沿用既有工作流；Colab 使用已整合的 PRISM / P-H3 配方；AutoIP 使用已支援的服務契約。
2. 管理介面開啟「運算平台」。每位已登入的使用者連接自己的金鑰，並選擇要使用哪些路線；金鑰留在本機狀態目錄，不會回填到瀏覽器。連線探測只檢查金鑰、不跑工作：目前涵蓋 AutoIP 與 RunningHub，Comfy Cloud 還沒有這支探測。舊的配方路徑仍可選內建配方；RunningHub 的 workflow-ID 配方要先在其工作台儲存並成功手動執行，再填入 ID，不要求公開發布。圖束路線則把同一張圖送到 Comfy Cloud 的 `/api/v2` 或 RunningHub 的 `/proxy-plus/`，不必先取得 workflow ID。Google Colab 帳號管理仍在同一個 Web。
3. 每個託管配方必須先手動選用平台，取得下載並驗證成功的成果，才會納入自動安排。變更金鑰、端點或配方會重新要求驗證。
4. 創作頁或側欄開啟「多步製作」，使用三條快速流程，或自行加入製作步驟、選擇各步平台、上傳素材或綁定前一步的成果。可選同一平台，也可跨平台。流程不接受任意程式或節點圖，只能使用平台已開放的生成功能。
5. 每一步的進度、成果、取消與失敗重試都留在此頁。關閉瀏覽器後，已接受的流程會繼續；重新開啟可讀取原紀錄。
6. 「預設安排」可設定帳號預設與各功能的平台。單次手動選擇優先；更改預設不會移動已接受的任務，也不會使原提交識別碼被重新派送。
7. 「可用功能」列出各功能的五平台對應與缺件原因。這是已實作／已設定的清單，沒有為每個模型啟動 GPU，也不等同即時帳號、節點或容量檢查。

## 工作流與算力的界線

本機與可部署的 Comfy 工作流可以使用相同圖形；Colab、AutoIP 可能使用不同實作。系統保留語意與參考素材的綁定，不會把不相容參數默默丟掉。只有明確設定的固定值或節點映射才允許通過託管配方檢查。未部署的模型、自訂節點、聲音或角色契約仍會顯示不可用。

兩條圖片流程以一張圖片作為影片步驟的輸入。文字生圖尺寸明確為 1024 × 1024。影片可選本機 / Colab 工作流的 124、226、362 幀；AutoIP 的秒數與解析度另有明示欄位，只有該步驟實際選中 AutoIP 時套用。動畫、寫實分類會保留至下一步。第三條「設計聲音 → 影片對嘴」先以 VoxCPM Voice Design 生成描述式合成聲音，再交給上傳的真人影片對嘴；不使用真人聲音參考，也不宣稱任何角色身份。各步仍需通過實際模型、配方與輸入相容性檢查。

跨步素材會驗證圖片或影音串流、凍結內容雜湊並檢查輸入類型；每個檔案上限 64 MiB、圖片上限 4000 萬像素、影音最長一小時。只有已確認失敗或拒絕的步驟可以改平台或調整預算重試，會建立新的執行識別碼並保留先前嘗試與依賴素材證明。提交結果不確定的步驟不能藉由重試切換平台。

自訂流程可以把圖片、音訊及影片接到相容的輸入欄位；串接前會驗證實際格式與內容雜湊。獨立的步驟可交由不同平台執行；有依賴的步驟只會在前置成果確認完成後派送。既有獨立批次語音與私人 Production Studio 保留原入口。

## 任務與素材的持久化

- 每個流程有不可變 UUID 與計畫雜湊；每一步、每次明確失敗重試各有固定 UUID。
- 複合流程不會重跑已完成的前置步驟。重試時必須沿用原中間成果的 SHA-256、類型與素材識別。只有已確認失敗或拒絕的步驟可以改選平台／預算，並另建一次明確的重試紀錄。
- 遠端成果先下載、驗證格式、保存至本機；圖片、音訊或影片再複製成擁有者專屬、內容雜湊固定的輸入檔，交給下一步。瀏覽器不能提供任意下載網址或伺服器路徑。交接檔案上限 64 MiB；音訊／影片須經串流探測，最長一小時。
- 提交結果不明時只查詢原任務；不自動換平台或重送付費工作。Comfy Cloud 與 RunningHub 某些失去回覆的提交無法自動恢復，會保留「提交狀態待確認」。
- 背景步驟重新讀取目前帳號權限，不保留 HTTP handler、登入 cookie 或另一使用者的執行上下文。
- 本機一般任務完成時，會保存擁有者、原任務編號、精確輸出檔名及內容雜湊。ComfyUI 重啟後仍可讀取已驗證的成果；任務同時不在歷史、佇列與持久紀錄時，顯示「提交待確認」，不猜測檔名或自動重跑。
- Web 送出本機任務至保存歸屬紀錄的期間，與 H3／語音工作程序的記憶體整理共用鎖。整理前必須確認佇列空閒並保存已完成成果；無法驗證時保留現有程序。這個協調只涵蓋使用相同鎖的呼叫端，其他直接操作 ComfyUI 的程式不會自動受到保護。

狀態位於 Colab 資料目錄的上一層：`compute-routing.sqlite3`、`compute-pipelines.sqlite3`、`compute-preferences.sqlite3`、`hosted-compute/`。其內容包含私人任務與連線設定，不可提交至 Git。

本機成果收據同樣位於該目錄下的 `compute-local-results/`。維護鎖預設為 `~/.local/share/castle-comfy-web/comfy-admission.lock`；若設定 `CASTLE_COMFY_ADMISSION_LOCK`，Web 與所有本機工作程序必須使用同一個絕對路徑。Web 啟動時會在鎖旁寫入權限為 `0600` 的程序與路徑設定，供整理前的檢查程序使用；其中不包含供應商金鑰，過期 Web 程序的設定不會被接受。語音的 `CASTLE_GPU_QUEUE_SCRIPT` 覆寫值也應指向本版本的 `scripts/run_comfy_gpu_queue.py`。

## 費用與驗證範圍

各平台費用單位分開檢查，不把 Colab 運算單位、AutoIP 點數、Comfy 點數、RunningHub 金幣視為同一貨幣。舊的 Comfy Cloud／RunningHub 配方路徑仍以設定的配方估計值與執行期限作為准入條件；這不是供應商保證的帳單硬上限。圖束路線則要先有報價才算就緒，見下一節。取消已開始的工作仍可能有費用。

合成測試覆蓋 HTTP 權限、UI、五平台選擇、下載與驗證、重啟、重複送出、並行 worker、取消和續跑。這些測試不申請真實 GPU、不使用真實帳號金鑰，也不代表五條路線的每個模型已完成實際生成。託管 API 契約與配方格式見 [託管運算設定](hosted-compute.md)。

## 目前各路線

本機是對照路線。目錄裡的每個工作流都在使用者自己的 ComfyUI 上跑。託管路線的執行時間估計來自使用者自己已完成的本機執行，或由擁有者輸入的估計；儲存庫不附帶測量資料。

Google Colab 工作階段：每個已連接的 Google 帳號開一個工作階段（所選等級的 GPU，仍看 Colab 當時有沒有機器）。它用 `scripts/colab_session_overlays/` 裡釘選的獨立 CPython、覆疊與版本啟動環境，上傳輸入、執行工作、送心跳，並在閒置、故障上限或狀態遺失時關閉。Web 沒在跑時，`scripts/colab_lease_sweeper.py` 配合 systemd 使用者單元 `scripts/systemd/web-comfyui-colab-lease-sweeper.service` 與 `.timer`（每 5 分鐘）釋放租約。單元假設簽出位於 `~/Web_Comfyui`（Web 使用其 `.venv`），控制器虛擬環境位於 `~/.local/share/web-comfyui/colab-controller`；路徑不同時請編輯單元。狀態目錄採用 Web 的預設值，可用 `CASTLE_COLAB_ROOT` 覆寫。安裝方式見 [Colab](colab.md)。2026-11-13 已在 A100 工作階段完成真實帳號的舞蹈與影片替換。語音還不能用：語音環境在 Colab 上仍建置失敗，此儲存庫不附帶釘選的 VoxCPM wheel，語音環境以 `voice_env_mismatch` 失敗關閉。

AutoIP：派送前向報價 API 取點數報價，完成後結算。2026-11-11 與 2026-11-12 已完成真實帳號的舞蹈生成。和本機的身份相似度依角色而異。七個媒體操作由 CI 的契約與合成測試覆蓋。

Comfy Cloud：自備 API 金鑰。同一份圖束經 `/api/v2` 的工作與素材 API 送出，並做 object-info 就緒檢查。2026-11-11 與 2026-11-12 已完成真實帳號的舞蹈工作。API 把結算回報為待處理，介面因此顯示估計，直到供應商回報實際費用。

RunningHub：自備會員 API 金鑰。同一張圖經 RunningHub 相容 ComfyUI 的 API 執行（`/proxy-plus/` 的 prompt、object info、上傳、history、queue、interrupt），並查帳戶狀態。2026-11-12 的真實帳號校正產出 8／8。和本機的身份相似度，寫實角色從接近到明顯較低都有，動漫角色會往寫實的臉偏移，因此還沒有和本機對齊。推薦連結見 [RunningHub 推薦連結](runninghub-referral.md)。

## 估計與實際費用

Comfy Cloud 的估計對照官方點數說明 <https://support.comfy.org/articles/5846341390-how-credits-work-in-comfy> 與定價頁 <https://www.comfy.org/pricing>。RunningHub 用公開工作流作者所說的約 60 POINT／4 分鐘作包絡（<https://www.runninghub.ai/post/2015763807888478210>）；這支 API 沒有成文費率。AutoIP 的數字來自分派前的報價 API。

圖束路線在有報價之前不算就緒，原因碼是 `parity_probe_quote_required`（`webui/compute_parity/hosted_readiness.py`）。報價來自這位使用者自己的本機同款執行；沒有的話，由擁有者輸入 `runninghub_points` 或 `comfy_cloud_credits`，標成「擁有者估計，非官方報價」。實際費用只在供應商回報後出現。

## 供應商主控台

每位已登入的使用者在主控台連接自己的金鑰，並選擇要開哪些路線。金鑰留在本機狀態目錄。連線探測不跑工作：RunningHub 查帳戶狀態，AutoIP 查帳戶與餘額；Comfy Cloud 尚無這支探測。

離線升級請先停止 Web，先乾跑再套用。

`webui/provider_console_migration.py` 不連網。旗標：`--state-root`、`--vault-root`、`--identity-key`、可重複的 `--owner`、`--backup`。`--dry-run`、`--apply`、`--rollback` 互斥；都沒給時走乾跑。`--rollback` 使用 `--backup`。

`webui/provider_console_backfill.py` 需要 `--state-root`。加上 `--dry-run` 只報告；不加上就寫入。先乾跑。

## 上游來源 / Upstream sources

下列呼叫只列主機與路徑。查詢字串不列入。RunningHub 把金鑰拼進路徑的寫法不展開，只記 `/proxy-plus/`。

### 本機 / Local

| | |
| --- | --- |
| 官方 | <https://docs.comfy.org/installation/manual_install> |
| API | 使用者自己的 ComfyUI。預設位址字串是 `127.0.0.1:8188`，另有 `/history/{id}` 與 `/queue` |
| 此程式呼叫 | 沒有固定的公開主機。執行器使用使用者設定的 ComfyUI |

### Google Colab

| | |
| --- | --- |
| 官方 | <https://colab.research.google.com> ；方案說明見 [Colab](colab.md) 所引的 Google 頁 |
| 此程式呼叫 | `accounts.google.com/`、`accounts.google.com/.well-known/openid-configuration`、`openidconnect.googleapis.com/v1/userinfo`、`www.googleapis.com/auth/userinfo.profile`、`/auth/userinfo.email`、`/auth/cloud-platform`、`/auth/colaboratory`、`/auth/drive.file`、`sdk.cloud.google.com/applicationdefaultauthcode.html`、執行階段主機 `colab.research.google.com` 或 `*.googleusercontent.com` 的 `/api/contents/{path}` |
| 工作階段安裝 | `pypi.org/simple`、`download.pytorch.org/whl/cu128` |

釘選的上游（`scripts/colab_session_overlays/`）：

| 專案 | 釘選 | 授權 |
| --- | --- | --- |
| VideoHelperSuite | `4ee72c065db22c9d96c2427954dc69e7b908444b`（`registry.json`） | GPL-3.0（`NOTICE.md`） |
| BreezyVoice | `d592c9d3e8927a0f53f68616387060dcd32a05ea`（`registry.json`） | Apache-2.0（`NOTICE.md`） |
| ComfyUI-KJNodes | `6ab7e8130e449ed2c0037589bcf84146ceb7fc9c`（`comfy-node-supplements.json`） | pin 檔沒有記錄授權或倉庫網址 |
| VoxCPM | `30118a33522ef2d8698fca9bebac7224af242395`（`NOTICE`、`voice-environments.json`） | Apache-2.0。公開樹不附帶該 wheel |
| python-build-standalone | 20260602。`interpreters.comfy`：CPython 3.12.13，`cpython-3.12.13+20260602-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz`，SHA256 `191b5188b42886fb8a14968d714571e8f3d1cef92ac7ad4c7e24cc4d0929b194`。`interpreters.voice`：CPython 3.11.15，`cpython-3.11.15+20260602-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz`，SHA256 `4b79bcb83a22961c8972f8d1d4780bd8925f0b52688d95b090a488f31c8b1139`。發布頁是 `https://github.com/astral-sh/python-build-standalone/releases/download/20260602/`（`voice-environments.json`） | pin 檔沒有另記授權 |

### AutoIP

| | |
| --- | --- |
| 官方 | <https://autoip.app/> |
| API 文件 | <https://autoip.app/api/v1/openapi.yaml>、金鑰頁 <https://autoip.app/dashboard/api-keys> |
| 此程式呼叫 | 基底 `https://autoip.app/api/v1`。POST `/images/text-to-image`、`/images/image-to-image`、`/videos/image-to-video`、`/videos/text-to-video`、`/videos/lip-sync`、`/videos/dance`、`/videos/multi-subject-reference`。GET `/dance-templates`、`/me`、`/credits/balance`、`/pricing`、`/tasks`、`/tasks/{task_uuid}`、`/tasks/{task_uuid}/outputs/{index}` |

### Comfy Cloud

| | |
| --- | --- |
| 官方 | <https://cloud.comfy.org>，或已建立的 `https://<deployment>.run.comfy.app` |
| API 文件 | <https://docs.comfy.org/openapi-v2.yaml>、素材上傳說明、點數說明 <https://support.comfy.org/articles/5846341390-how-credits-work-in-comfy>、定價 <https://www.comfy.org/pricing> |
| 此程式呼叫 | POST `/api/v2/assets`、POST `/api/v2/jobs`、GET `/api/v2/jobs`、GET `/api/v2/jobs/{id}`、GET `/api/object_info`、GET `/api/v2/jobs/{id}/workflow`、POST `/api/v2/jobs/{id}/cancel`、GET `/api/v2/assets/{uuid}/content`、GET `/api/billing/usage/timeseries`。下載主機 `cdn.comfy.org`、`comfy-hub-assets.comfy.org`、`storage.googleapis.com` |

### RunningHub

| | |
| --- | --- |
| 官方 | <https://www.runninghub.ai> |
| API 文件 | 工作流程部署 `doc-8287465`、錯誤碼 `doc-8287467`、Advanced API `api-425761093`、帳號欄位 `api-425761030`、上傳 `api-425761099`。相容 API 指南 `doc-8287466` |
| 此程式呼叫 | 舊 workflow-ID 路徑：POST `/task/openapi/upload`、`/task/openapi/create`、`/task/openapi/outputs`、`/task/openapi/{operation}`。圖束路徑：POST `www.runninghub.ai/uc/openapi/accountStatus`；`www.runninghub.ai/proxy-plus/` 下的 `/object_info`、`/upload/image`、`/prompt`、`/history`、`/queue`、`/history/{id}`、`/interrupt`、`/view`；POST `www.runninghub.ai/openapi/v2/query`。轉址主機 `rh-images.xiaoyaoyou.com`、`rh-hk-images.xiaoyaoyou.com` |
| 估計出處 | <https://www.runninghub.ai/post/2015763807888478210>（約 60 POINT／4 分鐘的公開作者包絡，不是這支 API 的費率表） |


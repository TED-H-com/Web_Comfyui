# Web_Comfyui

在自己的電腦上，用網頁介面操作 ComfyUI 生圖、影片、工作流、角色、聲音與素材。公開文件站與安裝後的 WebUI 都可用右上角 **繁中 / English** 切換介面語言。角色、帳號與素材庫從空白開始，需要 API 的功能可設定自己的帳號與金鑰。

[專案介紹](https://ted-h-com.github.io/Web_Comfyui/) · [互動示範](https://ted-h-com.github.io/Web_Comfyui/demo/) · [English](README.md) · [安裝設定完整參考](docs/installation.md)

網站首頁是專案介紹與安裝入口；**`./demo/` 是互動示範，不是實際生成服務**。示範使用與本機 WebUI 相同的介面、導覽與表單。每個工具有兩個說明按鈕：**查看流程與模型** 說明實際組圖程式、模型與預設值；**查看 ComfyUI 範本與節點** 開啟節點圖。圖中會區分可對照的 ComfyUI 圖結構與服務處理流程示意，不把流程示意當成可執行節點圖。

23 項工具與建立角色都保留預填表單與操作說明。可修改文字，或點選／拖入自己的檔案，只在瀏覽器預覽；示範按鈕顯示已記錄的實跑成果，尚未驗證的功能則明確顯示「尚無實跑成果」。成果在按下生成後才出現；指令改圖會並排顯示「青綠白隊服 → 紅上衣、深藍短褲」的修改前後對照。建立角色則會依名字、描述與參考照片組成暫存的示範角色卡，並提供其他工作流的示範入口，不保存資料或訓練模型。修改輸入不會重新生成，也不會改變固定的實跑紀錄。網站不排入生成佇列、不執行 GPU 模型生成、不上傳素材、不呼叫供應商 API。

成果範例必須來自對應工作流的實際執行，並記錄輸入、不同的輸出、GPU 與執行證據。參考插圖只作為輸入，不充當生成成果；尚未實跑的影片、語音、訓練及其他功能不以替代媒體展示成果。[示範來源紀錄](webui/docs/examples/provenance.json) 保留公開素材與執行證據；發布只開放經審核的指定路徑與固定雜湊，個人與執行期媒體仍不納入。詳見 [文件站說明](docs/site.md)。

示範的硬體選單提供無 NVIDIA GPU、8／12／16／24／32 GB 常見顯存級距，以及雙 24 GB 顯卡。這是規劃估計，不是最低需求實測或效能測試；雙 24 GB 不視為合併的 48 GB。切換只影響示範中的可用性提示，不偵測顯卡，也不更動安裝後 WebUI 的設定或停用本機功能。選 **AutoIP** 可探索全部 23 項示範表單，不代表每個本機節點、訓練器或功能都已有雲端 API 對應。選用的註冊連結與 QR code 帶推薦碼 `TEDH2FND`；服務界線與聯盟推薦揭露見 [AutoIP 雲端與示範說明](docs/autoip-cloud-demo.md)。

## 先準備兩套環境

| 項目 | 負責什麼 | 預設網址 |
| --- | --- | --- |
| ComfyUI | 載入模型、節點編輯與實際生成 | http://127.0.0.1:8188 |
| Web_Comfyui | 操作表單、工作庫、角色／媒體管理 | http://127.0.0.1:8190 |

Web_Comfyui 目前支援 **Linux 或 WSL2、Python 3.11 以上**。Windows 使用者請在同一套 WSL2 Linux 中安裝並執行這兩個服務；此啟動器不支援直接從原生 Windows 或 macOS 啟動。瀏覽器可以在 Windows 開啟上述本機網址。

WebUI 本身只需輕量的 Python 環境與 Pillow；生成所需的 GPU、記憶體、模型與 custom nodes 由 ComfyUI 工作流決定。能打開網頁，不代表電腦能跑所有影片模型。

### 1. 安裝並啟動 ComfyUI

如果已經有正常運作的 ComfyUI，沿用它即可。尚未安裝時，依 [ComfyUI 官方安裝文件](https://docs.comfy.org/installation/manual_install) 選擇符合硬體的依賴。

在 **ComfyUI 的目錄與 Python 環境** 執行：

```bash
python main.py --listen 127.0.0.1 --port 8188
```

如果你的環境使用 `.venv`，可改用 `.venv/bin/python main.py --listen 127.0.0.1 --port 8188`。確認 **http://127.0.0.1:8188** 能打開節點編輯器，保留這個終端機。

### 2. 安裝 Web_Comfyui

另開一個 Linux／WSL2 終端機：

```bash
git clone https://github.com/TED-H-com/Web_Comfyui.git
cd Web_Comfyui
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-webui.txt
cp .env.example .env
chmod 600 .env
```

`python3 --version` 必須顯示 3.11 或更新版本。若建立 venv 時缺少 `venv`／`ensurepip`，先安裝該 Linux 發行版對應的 Python venv 套件。

用文字編輯器打開剛建立的 `.env`，調整這三項：

```dotenv
COMFY_HOME=~/comfyui
COMFYUI_URL=http://127.0.0.1:8188
COMFYUI_BROWSER_URL=http://127.0.0.1:8188
```

`COMFY_HOME` 必須是實際的 ComfyUI 安裝目錄，不是 Web_Comfyui 的目錄。可使用 `~` 或絕對路徑；不要寫 `$HOME/comfyui`，因為 `.env` 不會展開環境變數。Linux 路徑區分大小寫，`ComfyUI` 與 `comfyui` 是不同目錄。先保持 API 金鑰欄位空白也能啟動。

一般安裝不需要執行根目錄的 `install.sh`。那是進階 ComfyUI 環境部署腳本，會修改套件、節點與部分設定，請先閱讀 [適用範圍](docs/installation.md#optional-advanced-provisioner)。

### 3. 檢查與開啟

在 Web_Comfyui 目錄執行：

```bash
.venv/bin/python run.py --check
```

輸出是 JSON。基本條件應為：

- `python_supported`、`platform_supported`：`true`。
- `dependencies.pillow`：`true`。
- `comfy_api_reachable`：`true`。
- `comfy_directory_exists`：確認本機 ComfyUI 路徑是否填對；這項不參與退出碼的成功判斷。

`ffmpeg`、`ffprobe`、`yt_dlp` 或 API 設定為 `false`，表示相關選用功能尚未備妥，不必為了瀏覽工作庫一次裝齊所有工具。`--check` 不安裝套件、不下載模型、不執行生成；通過檢查也不代表所有工作流已就緒。

接著啟動：

```bash
.venv/bin/python run.py
```

開啟 **http://127.0.0.1:8190**。第一次會顯示「設定本機管理密碼」，輸入至少八個字元後按「設定並進入」，即可登入。沒有預設帳號或密碼，也不需要先註冊外部服務。下次使用相同密碼登入即可。

## 第一次使用：從工作庫選一個工作流

1. 開啟 **工作庫**，先將收錄狀態切換為 **可下載工作流**。
2. 搜尋自己已安裝的模型，或依分類、來源篩選。點開卡片查看模型、節點、授權與來源。
3. 按 **檢查 ComfyUI 節點與模型**。檢查會核對節點名稱及已辨識載入器的模型選項，並分別標示缺少與尚無法確認的項目。若缺少節點，回到 ComfyUI 的環境安裝所需套件，依套件說明重啟 ComfyUI，再重新檢查。
4. 按 **載入 ComfyUI**，再按 **開啟 ComfyUI**。從原生編輯器的工作流側欄，找到 `webui-library` 資料夾並打開剛加入的檔案。
5. 在原生編輯器選擇已安裝的模型，替換示範圖片／音檔，填入自己的提示詞。先用一張圖片或短片、小尺寸試跑，再逐步增加負載。

**載入只存入 JSON，不會排入生成佇列、不會下載權重，也不會自動安裝節點。** 重複載入相同內容會成功並提示開啟既有檔案；同一目的檔若有不同內容則回報衝突，不會覆寫。 只有操作員能使用載入按鈕。若沒有這個按鈕，可先下載 JSON；API 格式與視覺格式的匯入方式也不同。

| 收錄狀態 | 內容 | 可以怎麼用 |
| --- | --- | --- |
| 可下載工作流／已收錄 JSON | 專案內有工作流檔案 | 下載，或將視覺工作流載入原生編輯器 |
| 方法與參數 | 教學整理的參數、流程與前置需求 | 照需求配置，不是可以直接執行的 JSON |
| 上游參考 | 作者來源，沒有隨專案附檔 | 開啟來源，確認授權與依賴後自行取得 |

目前公開工作庫共 248 項，其中 46 份可下載 JSON：24 份精選社群流程與 22 份基礎範例。創作表單多半由 Python 直接組圖，與社群 JSON 分別管理；名稱相近不代表表單載入該 JSON。

「已核對依賴」只代表節點與可辨識的模型選項符合 ComfyUI 回報。未知載入器、連線控制的模型選項等會標示尚無法確認；權重完整性、版本、輸入格式、VRAM 與實際畫質仍需自行驗證。詳細操作見 [第一個工作流](docs/getting-started.md)，各檔案的授權與驗證範圍見 [工作流來源與涵蓋範圍](docs/workflow-coverage.md)。

## 其他功能在哪裡

| 頁面 | 用途與前置條件 |
| --- | --- |
| 創作 | 使用既有生成表單；需備妥表單要求的模型與節點 |
| 角色 | 建立自己的角色與參考素材；專用身份生成還需要相應模型與驗證 |
| 媒體庫 | 查看、分類與整理本機作品 |
| 資源 | 設定支援的外部供應商帳號與金鑰 |
| 任務 | 查看 WebUI 管理的工作狀態；在原生 ComfyUI 執行的工作仍以原生佇列為準 |

語音、KohakuMoss 訓練、進階 H3 與自動化流程有各自的 worker／模型／授權及驗證條件，並非安裝 WebUI 後就全部可執行。有些模式保留在程式中但預設隱藏或停用，工作庫載入不會改變這些設定。

## API、資料與每天啟動

需要文字生成或外部供應商時，再設定自己的 API。`.env` 的 `OPENAI_API_KEY`／`GEMINI_API_KEY` 用於已實作的應用程式整合；匯入的 API 工作流可能需要在 ComfyUI 節點中另填端點、模型和金鑰。AutoIP 設定與支援範圍見 [API 文件](docs/autoip-api.md)。方法與參數卡片中提到的端點範例，不代表相應服務已安裝。

金鑰不要寫入分享用工作流 JSON、截圖或 Git。外部 API 會接收你送出的文字／素材，也可能依供應商規則計費；本機模式和外部服務可依用途自行選擇。

| 資料 | 預設位置 |
| --- | --- |
| 帳號、角色、聲音、應用資料庫 | `~/.local/share/web-comfyui` |
| 快取與磁碟暫存 | `~/.cache/web-comfyui` |
| 生成圖片與影片 | ComfyUI 的 `output` 目錄，或你設定的輸出位置 |

升級或搬移前，保留 `.env` 並備份自己的資料目錄與 ComfyUI 素材。這些內容不要提交到公開儲存庫。

平常只要先啟動 ComfyUI，再從 Web_Comfyui 目錄執行 `.venv/bin/python run.py`；不用每次重新建立 venv 或複製 `.env`。修改 `.env` 後重啟 WebUI。關閉 WebUI 終端機的 Ctrl+C 不會停止另一個 ComfyUI 服務。

遇到問題先看 [安裝與疑難排解](docs/installation.md#troubleshooting)。進階主題集中在 [互動指南](https://ted-h-com.github.io/Web_Comfyui/demo/)、[KohakuMoss LoRA](docs/moss-training.md) 與 [H3／RTX／本機 LLM](docs/rtx-video-research.md)。

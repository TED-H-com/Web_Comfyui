# Web_Comfyui

用自己的電腦與模型運行 ComfyUI，再透過這套 WebUI 操作生圖、影片、角色、聲音、素材與工作流。介面主要使用繁體中文，公開版從空白的帳號、角色與素材庫開始。需要 API 的功能可設定自己的金鑰。

## 安裝

目前支援 Linux 或 Windows WSL2，Python 3.11 以上。ComfyUI 需另外安裝與啟動；模型與 custom nodes 安裝在 ComfyUI 的環境中。

1. 依 [ComfyUI 官方說明](https://docs.comfy.org/installation/manual_install) 安裝 ComfyUI，並在該環境啟動 `python main.py --listen 127.0.0.1`。
2. 安裝 WebUI：

```bash
git clone https://github.com/TED-H-com/Web_Comfyui.git
cd Web_Comfyui
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-webui.txt
cp .env.example .env
chmod 600 .env
```

3. 如果 ComfyUI 不在 `~/comfyui`，在 `.env` 填入 `COMFY_HOME` 的實際絕對路徑。連線位址預設是 `http://127.0.0.1:8188`。
4. 啟動：

```bash
.venv/bin/python run.py --check
.venv/bin/python run.py
```

開啟 **http://127.0.0.1:8190**，首次使用設定管理密碼。兩個服務都維持本機監聽。WebUI 不附模型、不代填 API 金鑰，也不會在啟動時下載大型權重。

## 使用工作庫

**工作庫** 收錄可下載 JSON、上游參考與教學參數方案。可按分類／來源搜尋，檢查目前 ComfyUI 的節點，查看模型、授權與來源。已收錄的視覺工作流可按「載入 ComfyUI」，接著從原生編輯器的 `webui-library` 資料夾開啟，設定輸入素材與模型後執行。

初版附 40 個工作流 JSON 與 8 組教學整理方案，涵蓋圖片編輯、背景／材質、Flux、SDXL、Qwen、H3、MOSS 與 RTX 影片處理。模型和節點需求各有不同；JSON 與節點檢查通過不代表已完成 GPU 生成測試。

**角色** 管理自己的角色和參考素材；**媒體庫** 分類、搜尋與預覽作品；**創作** 使用既有工具表單；**資源** 設定自己的外部供應商帳號。語音、訓練與進階影片功能需要額外 worker、模型或環境，未就緒的工具會顯示缺少的條件。

## API 與資料

AutoIP 在資源頁連接自己的帳號與金鑰；OpenAI／Gemini 等設定見 `.env.example`。金鑰留在自己的環境或本機私有資料目錄，不放進工作流 JSON。AutoIP 新版秒數和圖片模式已更新，完整支援範圍見 [AutoIP 文件](docs/autoip-api.md)。

帳號、角色、聲音與資料庫預設存在 `~/.local/share/web-comfyui`，快取存在 `~/.cache/web-comfyui`；生成檔案留在自己的 ComfyUI output。這些資料不屬於 Git 追蹤內容。

詳細說明：[安裝與需求](docs/installation.md)、[工作流來源與授權](docs/workflow-coverage.md)、[MOSS LoRA](docs/moss-training.md)、[H3／RTX／本機 LLM](docs/rtx-video-research.md)。

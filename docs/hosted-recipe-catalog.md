# 內建託管配方目錄

2026-11-10：目錄提供 **31 個配方、12 種生產功能**，使用本專案已有的原生圖建構器與表單契約。管理介面可以挑選範本、確認模型／節點需求、填入估計費用後儲存；不必手寫 JSON。Comfy 使用 API 格式圖，RunningHub 則先在工作台匯入並儲存同一個圖、成功試跑，再填入自己的 workflow ID；不必公開到社群。連線、預算、恢復與成果限制見 [託管運算設定](hosted-compute.md)。

驗證標記 `contract_tested` 表示：使用合成設定檢查輸入映射、原生圖一致性、表單預設值與失敗行為。**沒有驗證真實供應商帳戶、遠端模型、節點安裝、GPU 記憶體或生成品質。** 自動安排仍要求該使用者、網址、憑證版本及完整配方曾有下載並驗證的成功成果。

## 可選配方

| 功能 | 配方 | 素材與保留內容 |
| --- | --- | --- |
| `text2img` | Z-Image Turbo | 原生模型、Qwen 文字編碼器、AE；精確寬高、負向提示、步數、CFG、張數、種子；固定不啟用 DOPSD |
| `img2img` | FLUX.1 Kontext | 恰好 `image`；原生縮放、參考 latent、Guidance、Denoise、採樣設定 |
| `qwen21-text2img` | Qwen-Image 2.1 | 九種原生比例及 1／2／4 MP；負向提示、步數、CFG、種子 |
| `qwen21-edit` | Qwen-Image 2.1，1–4 張參考 | 依序 `image`、`image2`、`image3`、`image4`，選定範本需要恰好該數量；保留 1024／1536／2048 編輯解析度與負向提示 |
| `character-photoshoot` | Qwen-Image 2.1 角色寫真 | 一張伺服器授權的角色圖，沿用原生身分與動畫／寫實畫風提示；不把角色 ID 或授權收據放進提示詞 |
| `img2video` | Wan 2.1 I2V + LightX2V LoRA | `image`；寬高、幀數、FPS、Shift、步數、負向提示與種子 |
| `h3-text-video` | MiniMax H3 FL2VA Stock | 三段原生文字描述、六種畫布、124–362 的合法 17k+5 幀數、有聲 MP4 |
| `h3-image-video` | MiniMax H3 FL2VA Stock，首格／尾格／首尾格 | `image`、`last_frame` 或兩者；提示中的時間與圖像對齊使用同一個幀數 |
| `h3-reference-video` | MiniMax H3 Ref2VA Stock，15 個素材組合 | 1–8 張圖；1–3 支影片；1–3 段音訊；或 1 圖＋1 影＋1 音。圖／影／音引用順序沿用原生編譯器 |
| `lipsync` | Wav2Lip 上傳素材改編 | `video`＋`voice`；sequential／repetitive、讀取上限、臉部偵測批次，沿用 30 FPS 有聲 MP4 |
| `video-loop` | VHS 上傳素材改編 | `video`；保留來源影格率、重複次數、正反播放及讀取上限；與原工具相同，不輸出音訊 |
| `dance` | SCAIL Animate 上傳素材改編 | `reference`＋`video`；保留姿態、遮罩、採樣、來源音訊與 81–161 幀；固定 Animate 與 81 幀 context |

每個功能目前儲存一份配方。Qwen 的參考數量、H3 的素材組合有多個明確範本，請選擇符合實際輸入者；不會為了套用範本而丟掉附件。H3 原生最多可接受更多素材，但託管服務目前有 8 檔、合計 32 MiB 的傳輸上限，所以沒有提供超出此上限的範本。未列的混合組合也會拒絕，管理者可另行設定經審查的完整映射。

目錄中的 `estimated_cost: 1` 只是讓配方結構完整的占位值，**不是費用報價**。導引設定要求自行填入費用，不能用占位值當成已知成本。模型、冷啟動、輸出時長及供應商資源選項都會影響費用；本機硬體或已安裝模型也不代表遠端已有相同資源。

## 語意與預設值

`bindings` 的 `default` 只能用於 `settings.<欄位>`，且只在欄位不存在時套用。空字串、`null`、`false`、零或自訂值都不會被預設值覆蓋。所有實際提供的創作設定仍須映射或等於明確的 `fixed_settings`，否則拒絕。

`value_map` 是有限的 `from` / `to` 清單。比較包含 JSON 型別；未知值沒有 fallback。例如：

```json
{
  "source": "settings.resolution",
  "node_id": "4",
  "input": "resolution",
  "default": "1024",
  "value_map": [
    {"from": "1024", "to": 1024},
    {"from": "1536", "to": 1536},
    {"from": "2048", "to": 2048}
  ]
}
```

Qwen 的比例標籤、MP 數字與編輯解析度，以及 H3 的畫布寬高與幀數，都有明確的映射。它們不使用模糊最接近值。H3 同時接收原生合法幀數整數與相同的十進位字串，但 `124.0`、`123`、空值、布林值都不會冒充合法選項。舊 `quality` 時長別名沒有納入這份範本，請使用目前表單的 `frames`。

種子沿用託管轉接器的 uint64 規則：`-1` 根據本次請求 UUID 生成一次固定種子；明確輸入的最大 uint64 十進位字串不經 JavaScript 浮點數轉換。重新查詢或恢復同一工作不會更換種子。

一般 H3 表單的 `visualStyle` 只加入畫風描述，不會把一般素材冒充成角色。真正綁定角色的請求仍使用原生人物身分、動作與畫風規則；前端畫風不能覆寫角色已綁定的分類。

## 影片路徑改編的來源證據

原本三個影片工具使用本機 `VHS_LoadVideoPath`。遠端配方明確改成 `VHS_LoadVideo`，讓上傳素材在供應商的 input 命名空間解析；其他處理連線不變。這三份配方有 `adaptation: "upload_backed_video_loader"` 標記，不聲稱與本機載入方式完全相同。

已檢查本專案固定的 VideoHelperSuite `4ee72c065db22c9d96c2427954dc69e7b908444b`：

- `LoadVideoUpload` 使用 `folder_paths.get_annotated_filepath` 解析上傳檔名，呼叫共用 `load_video`，並用 `exists_annotated_filepath` 驗證；輸出為 IMAGE、INT、AUDIO、VHS_VIDEOINFO。[原始碼 L423–473](https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite/blob/4ee72c065db22c9d96c2427954dc69e7b908444b/videohelpersuite/load_video_nodes.py#L423)
- `LoadVideoPath` 具有相同解碼參數及輸出插槽，額外接受路徑／URL。目錄中的改編圖不保留這項路徑能力。[原始碼 L476–520](https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite/blob/4ee72c065db22c9d96c2427954dc69e7b908444b/videohelpersuite/load_video_nodes.py#L476)
- 節點名稱 `VHS_LoadVideo` 對應上傳型別；`VHS_VideoInfo` 的第 5 個零基輸出是 loaded FPS，因此循環工具沿用原本的速度連線。[節點對照](https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite/blob/4ee72c065db22c9d96c2427954dc69e7b908444b/videohelpersuite/nodes.py#L1035)、[FPS 輸出](https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite/blob/4ee72c065db22c9d96c2427954dc69e7b908444b/videohelpersuite/nodes.py#L879)

Comfy v2 的 `core/ASSET` 參考放在原本檔名的位置；RunningHub 的 `fileName` 則放入相同節點欄位。檔案仍先通過 WebUI 的擁有者授權、大小與媒體驗證。這是傳輸和圖契約的來源分析，尚未證明特定供應商會提供相容的 VHS、Wav2Lip、SAM3 或 SCAIL 節點。[Comfy AssetReference](https://docs.comfy.org/openapi-v2.yaml)、[RunningHub 上傳](https://www.runninghub.ai/runninghub-api-doc-en/api-425761099)

Wav2Lip 的內部 checkpoint 與人臉偵測依賴沒有在本專案 registry 中固定版本；它們必須由遠端部署者明確安裝及驗證。此配方接受已授權解析的 `voice` 素材，包含直接上傳，以及 WebUI 先核對擁有者後從聲音庫解析的音檔。WebUI 會移除僅供本機使用的 `voiceId`，不將聲音庫識別碼送往遠端；直接呼叫配方而仍帶非空 `voiceId` 時會拒絕。SCAIL 的 Replace 分段分支及非 81 幀 context 尚未納入，不會自動切換成 Animate。

## 來源、依賴與未提供功能

本目錄的原生建構器來源基準為 `01850c166af1356e2d13348a6bdfb628eeedc0ab`。Z-Image、Kontext、Wan 與三份影片改編的圖有測試對照選定的純函式 AST；測試不匯入 HTTP server，不執行其初始化或讀取使用者資料。Qwen、H3 圖則直接呼叫無副作用的 [Qwen 編譯器](../webui/qwen_image21.py)、[H3 原生建構器](../webui/minimax_h3_native.py)，提示詞再使用 [H3 產品契約](../webui/minimax_h3_product.py)。各範本回傳來源檔案、模型名、節點名及圖 SHA-256。

Qwen 模型鎖定於 `Comfy-Org/Qwen-Image-2.1@ace0edeb3791a594ddfa36ed5f41a178a394e921`，節點基準為 ComfyUI `c194dd00cd42aa18d9dbf27d977bf6b85d9ea565`。H3 模型基準為 `Comfy-Org/MiniMax-H3@014cd40f7e177756c6b2473c0d93b1c89a790dd2`，節點基準為 ComfyUI `c2bcbecd82ec5ae66594340b395c24ef0217b238`。詳細檔案與雜湊以 [Qwen manifest](../manifests/qwen_image21/models.lock.json)、[H3 manifest](../manifests/minimax_h3/models.lock.json)、[H3 節點鎖定](../manifests/minimax_h3/tutorial_stack.lock.json) 為準。Z-Image、Kontext、Wan 的既有 builder 只固定檔名，沒有宣稱完整權重雜湊鎖定。

此目錄程式沿用本專案 GPL-3.0 授權；沒有包入模型、第三方節點程式、個人素材或上游範例影像。Qwen／H3 原始官方範本的 MIT 歸屬記錄見 [工作流覆蓋](workflow-coverage.md)，模型與各自訂節點有獨立條款。H3 模型授權的既有來源記錄見 [H3 研究](workflow-audit/h3-colab-study.md)。目錄不表示模型已獲授權部署。

`unsupported` 會逐一說明其餘功能的技術原因：角色 LoRA／InstantID 私有檔案與生成後身分檢查、尚未映射的合成／多角度處理、語音服務編排，或根本不是生成工具。公開 API 已停用的首尾幀舊工具與換臉工具也不會因目錄出現而被重新開啟；它們原本的動畫 WebP 輸出不能冒充目前的 MP4 影片成果契約。

## 離線驗證

```sh
pytest-disktmp uv run --with pytest --with pillow pytest -q \
  tests/test_compute_recipe_catalog.py tests/test_hosted_compute.py \
  tests/test_hosted_compute_http.py
```

測試使用實際表單預設值、每個素材組合、多圖順序、自訂負向提示、全部 Qwen 比例／MP、H3 原生畫布／幀數、角色 guardrail、上傳型影片改編與惡意／錯誤映射。沒有供應商或 GPU 呼叫。

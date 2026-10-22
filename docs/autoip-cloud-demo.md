# AutoIP 雲端服務與互動示範 / Cloud service and demo

查核日期：2026-10-22。這份文件區分 AutoIP 的服務、WebUI 已完成的串接，以及本站的靜態示範。

## 想直接使用雲端生成

AutoIP 的官方網站提供圖片、影片、角色多角度及聲音克隆等線上創作功能；使用這些託管服務不需要自行安裝模型或準備 GPU。實際可用功能、點數與方案條件以 AutoIP 當前帳戶及官方說明為準。[官方網站](https://autoip.app/)

**推廣揭露：以下連結與 QR code 使用推薦碼 `TEDH2FND`，屬於聯盟推薦入口；推薦方可能獲得獎勵。** 本頁不承諾折扣、贈點或費用不變。也可直接前往不含推薦碼的 [AutoIP 首頁](https://autoip.app/)。

[透過推薦連結註冊 AutoIP](https://autoip.app/register?ref=TEDH2FND&utm_source=affiliate&utm_medium=referral&utm_campaign=TEDH2FND)

![AutoIP 推薦註冊 QR code；推薦碼 TEDH2FND](../webui/docs/autoip-referral.svg)

QR code 與上方連結編碼相同的完整網址。SVG 使用原生向量圖形，沒有腳本、外部圖片、追蹤像素或自動跳轉。只有開啟連結或掃碼後，瀏覽器才會前往 AutoIP；目的站的隱私及服務條款由 AutoIP 提供。

## 三種範圍不能混為一談

| 範圍 | 能做什麼 | 使用條件 |
| --- | --- | --- |
| 本站互動示範 | 所有展示功能可操作表單、預覽本機檔案，按示範按鈕查看已記錄的實跑成果或尚未驗證狀態 | 不上傳、不執行 GPU 或 API 生成；開啟所有示範不表示所有實際服務都已串接 |
| 自行安裝 WebUI | 控制使用者自己安裝的 ComfyUI，並使用已實作的外部供應商整合 | 本機工作流仍需要相應模型、節點、硬體或工具；各功能檢查自己的依賴 |
| AutoIP 雲端服務 | 使用 AutoIP 提供的線上工具或其公開 API | 自己的帳戶、適用方案與點數；API 還需要自己的金鑰，不能在靜態示範頁輸入 |

本站只將具有工作流執行證據的素材標為實跑成果；尚未驗證的功能顯示無成果可用。參考插圖不作為模型輸出，鏡頭動畫與逐字稿也不作為影片／語音生成證明。本機 GPU 執行紀錄不代表 AutoIP 已成功執行同一工作流。見 [素材與執行來源](../webui/docs/examples/provenance.json) 及 [示範站說明](site.md)。

## API 與目前整合的界線

官方首頁列出 MCP 的七類媒體工具，並說明 API 金鑰及訂閱條件。[官方首頁的 MCP 說明](https://autoip.app/)、[官方串接教學](https://autoip.app/blog/claude-code)、[API 金鑰管理](https://autoip.app/dashboard/api-keys)

本專案已實作七個媒體生成操作：文生圖、圖生圖、文生影片、圖生影片、多參考影片、對嘴與舞蹈影片。這是程式契約及合成測試覆蓋的範圍，並非本次使用真實帳戶執行成功的宣告。詳細端點、選項及限制見 [AutoIP integration](autoip-api.md)。

使用者提供的 2026-10-22 **AutoIP Public API Skill** 另描述圖片反推提示詞、圖片融合、多角度、貼文、關鍵幀影片、角色跳舞、影片修復、語音克隆、提示詞擴寫，以及角色／提示詞／聲音庫管理。這些上游能力並未因此自動出現在本 WebUI 的已實作操作中；逐項整合狀態見上述文件。

該契約提供的完整規格位置是 [OpenAPI YAML](https://autoip.app/api/v1/openapi.yaml)。本次未取得該網址內容，不能把它當作已重新核對的即時規格；使用的是提供的文件與本專案現有實作，沒有讀取帳戶金鑰或呼叫付費服務。

官方頁面與這份 API 契約沒有列出任意 ComfyUI 工作流上傳執行、自訂節點安裝，或通用 LoRA／模型訓練端點。因此**不能宣稱訂閱 AutoIP 就能執行本 WebUI 的每個本機節點、訓練器或選項**。這是現有文件尚未證實的能力，並非斷言供應商永遠不提供。角色庫管理、多角度生成與聲音克隆也不等同於任意模型訓練。

需要 API 時，請在自己安裝的 WebUI 依 [連線說明](autoip-api.md#connect-your-own-account) 設定帳戶。可用性還受操作、帳戶、方案、點數及即時價格矩陣約束；不要從示範表單是否開啟推論帳戶已獲授權。

## English

AutoIP offers hosted creation tools, while this site demonstrates WebUI interactions and recorded workflow results. The demo does not upload selected files, call generation APIs, or run a GPU. Results require actual execution evidence; unverified features explicitly have no result. A local GPU run does not establish an AutoIP API result.

**Referral disclosure:** the registration link and QR code contain affiliate referral code `TEDH2FND`. The referrer may receive a reward. No discount or credit offer is promised. You can instead visit the [AutoIP homepage without referral parameters](https://autoip.app/).

The installed WebUI currently implements seven AutoIP media operations. The supplied API contract documents additional provider capabilities that are not yet implemented here. Neither the reviewed public page nor that contract establishes support for arbitrary ComfyUI graphs, custom nodes, or general LoRA training. Enabling every static demo therefore does not establish equivalent cloud-service support.

Real generation requires a suitable provider account and configuration, or the local dependencies for a local workflow. See the [integration contract and implementation boundaries](autoip-api.md) before connecting your own account.

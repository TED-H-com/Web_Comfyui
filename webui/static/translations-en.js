/* Reviewed UI phrases only. Unknown text is left unchanged; no network translation. */
(() => {
  'use strict';
  const pairs = `
角色|Characters
角色卡片牆|Character gallery
選一個角色開始創作。|Choose a character to start creating.
+ 新角色|+ New character
創作|Create
專案|Projects
媒體庫|Media library
工作庫|Workflows
資源|Resources
任務|Tasks
管理|Manage
更多|More
帳號|Account
主要導覽|Main navigation
服務正常|Service available
服務異常|Service unavailable
互動說明|Interactive guide
設定密碼|Set password
設定本機管理密碼|Set local admin password
第一次進入先綁定這個介面的密碼。|Set a password for this interface on your first visit.
進入角色工作室，讓每一次生成都有清楚身分與安全邊界。|Enter the character studio with clear identity and access controls for each generation.
密碼|Password
再次確認|Confirm password
進入|Enter
設定並進入|Set password and enter
登入|Sign in
登出|Sign out
管理員|Administrator
操作員|Operator
一般使用者|Member
使用者|User
設定|Settings
名稱|Name
照片|Photos
建立|Create
取消|Cancel
儲存|Save
刪除|Delete
編輯|Edit
關閉|Close
返回|Back
完成|Done
確認|Confirm
重新整理|Refresh
重試|Retry
下載|Download
上傳|Upload
複製|Copy
預覽|Preview
查看|View
開啟|Open
顯示|Show
隱藏|Hide
搜尋|Search
全部|All
上一頁|Previous
下一頁|Next
上一步|Back
下一步|Next
載入中…|Loading…
載入中...|Loading…
儲存中…|Saving…
處理中…|Processing…
準備中|Preparing
尚未設定|Not configured
未設定|Not configured
可用|Available
不可用|Unavailable
已停用|Disabled
啟用|Enable
停用|Disable
建立實驗角色|Create an experimental character
新增角色|Add character
建立角色|Create character
角色創作工作室|Character studio
角色工作室|Character studio
角色設定|Character settings
角色資料|Character details
角色素材|Character assets
角色造型庫|Character wardrobe
角色管理|Manage characters
個人角色|My characters
實驗角色|Experimental character
私人實驗角色|Private experimental character
真人|Realistic
動漫|Anime
未知|Unknown
待補資料|Details needed
需補素材|Assets needed
素材已就緒|Assets ready
本機可用|Available locally
未設定一句話定位|No short description
請輸入角色名稱。|Enter a character name.
請至少選一張照片。|Select at least one photo.
最多一次上傳 6 張照片。|Upload up to six photos at a time.
檔案太大|File is too large
不支援的圖片格式|Unsupported image format
實驗角色已建立。|Experimental character created.
尚未選擇圖片|No image selected
選擇圖片|Choose image
選擇檔案|Choose file
選擇角色|Choose character
目前角色|Current character
最近作品|Recent work
最近輸出|Recent outputs
生成|Generate
開始生成|Generate
生成圖片|Generate image
生成影片|Generate video
生成語音|Generate speech
生成失敗|Generation failed
生成中|Generating
生成中…|Generating…
生成設定|Generation settings
進階設定|Advanced settings
基本設定|Basic settings
必填|Required
可選|Optional
查看流程與模型|View workflow and models
查看 ComfyUI 範本與節點|View ComfyUI template and nodes
查看ComfyUI 範本與節點|View ComfyUI template and nodes
流程與模型|Workflow and models
模型需求|Required models
節點需求|Required nodes
前置需求|Prerequisites
需求|Requirements
來源|Sources
授權|License
模型與檔案|Models and files
操作步驟|Steps
使用技巧|Techniques
用途|Purpose
先準備什麼|What to prepare
會怎麼跑|How it works
使用什麼技巧|Techniques used
調整與排錯|Tuning and troubleshooting
圖內參數|Graph parameters
技術資料與來源|Technical details and sources
可下載工作流|Downloadable workflows
已收錄 JSON|Included JSON
方法與參數|Recipes and settings
上游參考|Upstream references
全部分類|All categories
全部來源|All sources
全部類型|All types
搜尋工作流|Search workflows
搜尋名稱、用途、模型或節點…|Search names, purposes, models or nodes…
載入 ComfyUI|Load into ComfyUI
開啟 ComfyUI|Open ComfyUI
下載 JSON|Download JSON
閱讀來源|Read source
檢查 ComfyUI 節點與模型|Check ComfyUI nodes and models
重新載入|Reload
檢查節點中…|Checking nodes…
部分模型仍需核對|Some models still need checking
已核對依賴，GPU 待驗證|Dependencies checked; GPU validation pending
目前缺少的模型|Currently missing models
目前缺少的節點|Currently missing nodes
沒有符合條件的工作流。|No workflows match these filters.
依用途選擇工作流，檢查模型與節點，再送到自己的 ComfyUI。|Choose a workflow by purpose, check its models and nodes, then load it into your ComfyUI.
先了解用途、執行步驟與技巧，再選擇適合自己的工作流。|Read the purpose, steps and techniques, then choose a workflow that fits your task.
已收錄 JSON；請在自己的 ComfyUI 檢查模型與輸入。尚未完成 GPU 生成驗證。|JSON is included. Check models and inputs in your ComfyUI. GPU generation has not been validated.
這是方法與參數說明，需要依自己的環境建立流程；未附可直接執行的 JSON。|This is a recipe and settings guide. Build the workflow for your environment; no executable JSON is included.
此項目提供上游來源；未附 JSON。請先閱讀原作者說明與授權。|This entry links to the upstream source without bundling JSON. Read the author’s instructions and license.
沒有缺少節點|No missing nodes
未宣告再散布授權|No redistribution license declared
AI 圖片工作室|Image studio
AI 影像工作室|Video studio
聲音工作室|Voice studio
AI 文字工作室|Text studio
文生圖|Text to image
AI 指令改圖|Instruction-based image editing
動漫場景寫真|Anime scene composition
AI 參考圖融合|Reference image blending
真人臉參考肖像|Face-reference portraits
角色身份寫真|Character identity portraits
AI 圖生影片|Image to video
首尾幀影片|First/last-frame video
AI 跳舞影片|Dance video
真人對嘴工具|Live-action lip sync
AI 換人|Video face swap
影片循環接龍|Video loop
聲音生成|Speech generation
簡報配音|Presentation voiceover
聲音克隆|Voice cloning
整合語音生成|Integrated speech generation
聲音庫|Voice library
貼文生成器|Post generator
文字生成有聲影片|Text to video with audio
首／尾格有聲影片|First/last-frame video with audio
多參考有聲影片|Multi-reference video with audio
Qwen 2.1 文生圖|Qwen 2.1 text to image
Qwen 2.1 指令改圖|Qwen 2.1 image editing
文生圖 · 指令改圖 · 參考融合 · 真人臉多角度|Text to image · Image editing · Reference blending · Face-reference portraits
圖生影片 · 跳舞 · 真人對嘴 · 影片循環|Image to video · Dance · Lip sync · Video loops
聲音生成 · 簡報配音 · 聲音庫|Speech generation · Presentation voiceover · Voice library
Flux Kontext 重新生成式編輯|Regenerative editing with Flux Kontext
角色原圖合成 · 不會轉成真人|Original character compositing · Keeps the illustrated style
保留人物原貌 · AI 去背合成|Keep the original subject · AI background compositing
人物原圖|Original subject image
背景參考圖|Background reference
保留的主體|Subject to keep
主體偵測門檻|Subject detection threshold
邊緣羽化（像素，0 為關閉）|Edge feathering (pixels, 0 = off)
只縮放裁切背景，不重畫人物或補光。person 可能漏掉動漫角色的書包、手腳與髮尾；主體文字可包含道具，例如 person holding a bag，仍需檢查遮罩。羽化預設關閉；開啟會混合邊緣與背景，不能補回漏切部位。|Only the background is resized and cropped; the subject is not redrawn or relit. The person label can miss anime props, limbs and hair. Try including the prop, such as person holding a bag, and inspect the mask. Feathering is off by default; enabling it blends edges with the background and cannot recover missing regions.
AI 辨識人物後保留遮罩內的原始像素，只縮放裁切背景。輸出維持人物原圖尺寸；不重畫臉、不換裝，也不自動補光或生成陰影。去背邊緣需檢查。|AI segments the subject and preserves original pixels inside the mask. Only the background is resized and cropped. Output matches the subject image dimensions, without redrawing faces, changing clothes, relighting, or generating shadows. Check the cutout edges.
InstantID · 依同一張真人正臉生成候選肖像|InstantID · Generate portrait candidates from one real face photo
角色卡原圖雙重身份鎖定 · SDXL Beta|Dual identity guidance from the character profile · SDXL Beta
SCAIL 姿態驅動|SCAIL pose-driven motion
非角色綁定 · Wav2Lip 真人臉口型同步|Independent tool · Wav2Lip for real faces
ReActor 影片換臉|ReActor video face swap
重複或正反播放短片（輸出不含原音訊）|Repeat or reverse short clips (output has no original audio)
依目前入口提供角色聲音或聲音庫克隆|Character voice or library cloning, depending on the entry point
Custom English 批次配音|Custom English batch voiceover
舊入口：請改用「聲音生成」的聲音庫 Clone|Legacy entry: use library cloning in Speech generation
BreezyVoice 台灣國語 · VoxCPM Custom English / 多語|BreezyVoice Taiwanese Mandarin · VoxCPM Custom English / multilingual
參考音訊管理|Reference audio management
姊妹語氣社群文案|Social posts with selectable writing styles
MiniMax H3 FL2VA · 三欄原生提示詞|MiniMax H3 FL2VA · Three native prompt sections
MiniMax H3 Ref2VA · 圖片／影片／音訊|MiniMax H3 Ref2VA · Image / video / audio references
Qwen-Image-2.1 · 中文排版與原生 2K|Qwen-Image-2.1 · Chinese typography and native 2K
Qwen-Image-2.1 · 多圖參考|Qwen-Image-2.1 · Multiple image references
想生成什麼？|What would you like to generate?
想拍什麼場景？|What scene would you like?
正向提示詞|Positive prompt
負向提示詞|Negative prompt
畫面比例|Aspect ratio
步數|Steps
張數|Image count
DOPSD 白幕風格|DOPSD white-backdrop style
種子（-1 隨機）|Seed (-1 for random)
修改指令（英文最佳）|Edit instruction (English works best)
修改指令|Edit instruction
參考強度|Reference strength
場景／角度描述|Scene / camera angle
臉部一致性|Face consistency
候選張數|Candidate count
動態描述|Motion description
影格數|Frame count
過場描述|Transition description
模式|Mode
角色描述|Character description
姿態強度|Pose strength
影格對應|Frame alignment
最多讀取影格數|Maximum frames to read
臉部偵測批次|Face detection batch size
臉部修復|Face restoration
臉部偵測|Face detection
影格率 (fps)|Frame rate (fps)
最多影格數|Maximum frames
重複次數|Repeat count
正反來回（正放後倒放，減少接點跳動）|Ping-pong (play forward then reverse to reduce jumps)
聲音路徑|Voice route
角色聲音|Character voice
聲音庫參考音訊|Voice-library reference audio
聲音庫引擎|Voice-library engine
高品質 ASR Gate（較慢，會等 GPU，逐段檢查失敗重跑）|ASR quality gate (slower; waits for GPU and retries failed chunks)
語音版本|Speech engine
模型|Model
語音引擎|Speech engine
BreezyVoice 聲音|BreezyVoice voice
VoxCPM 模式|VoxCPM mode
語言|Language
口音 / 方言|Accent / dialect
聲紋 Gate（有 reference 時低於相似度會自動重跑）|Speaker-similarity gate (retry when below threshold with a reference)
語氣 / 風格控制|Tone / style control
參考音訊逐字稿（Ultimate Clone 才需要）|Reference transcript (Ultimate Clone only)
要生成的文字|Text to speak
推理步數|Inference steps
哪位姊妹來寫|Writing backend / style
平台|Platform
語調風格|Tone
文章長度|Post length
畫布|Canvas
幀數（17k+5）|Frame count (17k+5)
加速模式|Acceleration
輸出大小|Output size
參考圖片|Reference image
核准角色造型|Approved character image
核准動漫場景|Approved anime scene
角色臉部參考|Character face reference
起始圖片|Starting image
首幀|First frame
尾幀|Last frame
角色參考圖|Character reference image
動作驅動影片|Motion-driving video
人物影片|Person video
上傳語音（未選聲音庫時使用）|Upload speech (when no library voice is selected)
目標影片|Target video
來源臉部|Source face
短影片|Short video
上傳參考音訊（未選聲音庫時使用）|Upload reference audio (when no library voice is selected)
參考音訊（Reference / Ultimate 模式使用）|Reference audio (Reference / Ultimate modes)
首格圖片|First-frame image
尾格圖片|Last-frame image
聲音庫語音|Voice-library audio
動畫模式(AI 生成背景,比例跟參考圖)|Animate (AI-generated background, reference-image aspect ratio)
順序對應|Sequential alignment
影片隨語音長度伸縮|Adjust video duration to match the speech
循環播放|Loop playback
影片重複到語音結束|Repeat video until the speech ends
清晰銳利|Clear and sharp
自然柔和|Soft and natural
不修復|No restoration
保留原始臉部|Keep the original face
已訓練的角色聲音(台灣國語),選人即說|Configured character voice (Taiwanese Mandarin); select a speaker
聲音庫克隆|Library voice cloning
用 聲音庫 的參考音克隆音色,逐字稿自動帶入|Clone from voice-library audio; load its transcript automatically
台灣腔|Taiwanese Mandarin
BreezyVoice reference clone|BreezyVoice reference clone
多語 F5|Multilingual F5
要說英日等外語選這個|Choose this for English, Japanese and other languages
國語通用|General Mandarin
F5-TTS・多語音色克隆|F5-TTS · Multilingual voice cloning
國語台灣口音|Taiwanese Mandarin accent
BreezyVoice・道地台灣腔|BreezyVoice · Taiwanese Mandarin
F5v1（多語 / 中英）|F5v1 (multilingual / Chinese-English)
F5 Base（英文）|F5 Base (English)
E2-TTS（英文）|E2-TTS (English)
日文|Japanese
日本語|Japanese
西班牙文|Spanish
法文|French
德文|German
義大利文|Italian
印地文|Hindi
泰文|Thai
台灣國語|Taiwanese Mandarin
BreezyVoice · operator / AI-Sister 本機聲音|BreezyVoice · Configured local voices
Custom English / 多語|Custom English / multilingual
VoxCPM2 · 口音 / LoRA / reference clone|VoxCPM2 · Accent / LoRA / reference clone
LoRA + 乾淨 reference，不讓模型自由猜聲音|LoRA plus a clean reference to guide the voice
不用參考音，靠描述生成聲音|Create a voice from a description without reference audio
只用參考音（免逐字稿）|Use reference audio without a transcript
真人參考音 + 逐字稿（最像本人）|Reference voice plus transcript for closer voice matching
自動（依文字內容）|Automatic (based on the text)
自動 / 保持文字自然口音|Automatic / natural accent for the text
中文 / Mandarin|Chinese / Mandarin
美式標準英文|Standard American English
國際清晰英文|Clear international English
英式英文|British English
澳洲英文|Australian English
印度英文|Indian English
普通話|Standard Mandarin
粵語 / Cantonese|Cantonese
閩南語 / 台語風格|Taiwanese Hokkien style
四川話|Sichuan dialect
吳語|Wu dialect
東北話|Northeastern Chinese
河南話|Henan dialect
陝西話|Shaanxi dialect
山東話|Shandong dialect
天津話|Tianjin dialect
Claude（溫穩・邏輯硬）|Claude (calm and logical)
ChatGPT（冷靜・精準）|ChatGPT (concise and precise)
Gemini（博學・愛比喻）|Gemini (informative and illustrative)
Grok 風格（Claude 本機模擬）|Grok style (simulated by local Claude)
小紅書|Xiaohongshu
親切溫暖|Warm and friendly
專業可信|Professional
幽默風趣|Humorous
走心共鳴|Heartfelt
促銷 CTA|Promotional CTA
短文（1–2 句）|Short (1–2 sentences)
中等（約一段）|Medium (about one paragraph)
長文（2–4 段）|Long (2–4 paragraphs)
原生直式 9:16|Native portrait 9:16
原生橫式 16:9|Native landscape 16:9
原生方形 1:1|Native square 1:1
預覽直式 9:16|Preview portrait 9:16
預覽橫式 16:9|Preview landscape 16:9
預覽方形 1:1|Preview square 1:1
Stock · 官方 20 步|Stock · Official 20 steps
SageAttention · 20 步|SageAttention · 20 steps
1 MP · 正方形 1024 × 1024|1 MP · Square 1024 × 1024
2 MP · 更多細節|2 MP · More detail
4 MP · 正方形 2048 × 2048|4 MP · Square 2048 × 2048
約 1 MP · 依原圖比例|About 1 MP · Original aspect ratio
約 2.25 MP · 依原圖比例|About 2.25 MP · Original aspect ratio
約 4 MP · 依原圖比例|About 4 MP · Original aspect ratio
女性人像|Woman portrait
男性人像|Man portrait
風景|Landscape
商品|Product
寵物|Pet
場景合成|Scene composition
雙人構圖|Two-person composition
服裝靈感|Clothing inspiration
故事場景|Story scene
創意混搭|Creative mix
輕微擺動|Subtle motion
轉頭微笑|Turn and smile
鏡頭推近|Camera push-in
風吹髮絲|Wind-blown hair
弱|Low
強|High
寬鬆|Loose
緊跟|Follow closely
接近原圖|Close to the original
大幅變化|Larger changes
提示詞建議使用英文，畫面模型對英文較敏感。|English prompts are recommended for this image model.
提示詞建議使用英文，影片模型對英文較敏感。|English prompts are recommended for this video model.
支援中文與英文；要呈現的文字請用引號標明。|Supports Chinese and English. Put text you want rendered in quotation marks.
例如：午後窗邊的一杯咖啡，溫暖自然光，生活攝影|Example: a cup of coffee by a window, warm afternoon light, lifestyle photography
例如：一座雲海中的城堡，晨光照亮塔樓，海報標題為「雲上之城」。|Example: a castle above the clouds, morning light on its towers, poster title “Castle in the Clouds”.
保留 <image1> 的構圖，把背景改成黃昏的海邊；可用 <image2> 指定參考風格。|Keep the composition of <image1>, change the background to a beach at sunset, and use <image2> as a style reference.
依時間順序描述畫面、動作、鏡頭、對白與可見文字。|Describe visuals, action, camera movement, dialogue and visible text in chronological order.
環境音、物理聲響、對白以外的場景聲音；沒有可填 N/A。|Ambient and physical sounds other than dialogue; enter N/A if none.
觀眾聽見、場景內角色聽不見的配樂；不要配樂可填 None.|Background music heard by the audience, not by characters; enter None. for no music.
參考音訊實際說的那段話|The exact words spoken in the reference audio
參考音訊實際說的那段話；聲音庫有 metadata 時會自動帶入|The exact reference transcript; loaded automatically when voice-library metadata is available
參考音訊實際講的英文逐字稿|The exact English transcript of the reference audio
想用這個音色說的內容；長文會自動切段後合併|Text to speak in this voice; long text is split and merged automatically
打字，讓她用這個聲音說出來；長文會自動切段後合併|Enter speech text; long text is split and merged automatically
例如：calm, confident, standard American English, natural meeting voice|Example: calm, confident, standard American English, natural meeting voice
輸入要用 VoxCPM 說出的文字|Enter the text for VoxCPM to speak
工作佇列|Job queue
進行中|Active
排隊中|Queued
執行中|Running
已完成|Completed
已取消|Cancelled
失敗|Failed
中止|Stop
清除|Clear
清除已完成|Clear completed
沒有進行中的任務|No active tasks
目前沒有任務|No tasks right now
最近任務|Recent tasks
任務詳情|Task details
任務記錄|Task history
供應商|Providers
供應商設定|Provider settings
新增供應商|Add provider
新增帳號|Add account
連線|Connection
測試連線|Test connection
連線成功|Connected
連線失敗|Connection failed
API 金鑰|API key
金鑰|API key
端點|Endpoint
預設模型|Default model
本機|Local
外部服務|External service
可用資源|Available resources
重新檢查|Check again
資源管理|Resource management
尚未連接|Not connected
尚未登入|Not signed in
已連接|Connected
使用自己的 API|Use your own API
圖片|Images
影片|Videos
音訊|Audio
文字|Text
素材庫|Asset library
素材|Assets
作品|Outputs
下載原始檔|Download original
上傳圖片|Upload image
上傳影片|Upload video
上傳音訊|Upload audio
從網址下載|Download from URL
貼上網址|Paste a URL
貼上文字|Paste text
上傳 PPTX|Upload PPTX
開始配音|Start voiceover
批次完成後才能下載|Download after the batch completes
無法載入|Unable to load
沒有資料|No data
尚無作品|No outputs yet
尚無角色|No characters yet
先選擇角色|Choose a character first
照片與影片|Photos and videos
儲存設定|Save settings
返回角色|Back to characters
返回工作庫|Back to workflows
來源與授權|Sources and licenses
完整安裝步驟|Full installation steps
公開 Repo|Public repository
互動說明與安裝|Interactive guide and installation
分享這個功能的說明|Share this feature guide
分享連結|Share link
複製連結|Copy link
安裝說明|Installation guide
未執行 GPU 生成|GPU generation was not run
示範案例|Demo scenario
示範輸入|Demo input
示範結果|Demo result
模擬生成|Simulate generation
模擬進度|Simulated progress
重新播放|Replay
資料夾|Folder
備註|Notes
狀態|Status
時間|Time
大小|Size
檔案|File
選取|Select
取消選取|Deselect
匯入|Import
匯出|Export
開始|Start
停止|Stop
登入後繼續|Sign in to continue
請在本機 WebUI 設定|Configure this in your local WebUI
上傳一張角色參考圖與一段動作影片，系統會依動作重新生成角色舞蹈 MP4。背景會重新生成，不保留原影片場景；輸出會沿用動作影片的原音訊。|Upload a character image and a motion video to generate a dance MP4. The background is regenerated; the motion video's original audio is retained.
上傳一段目標影片與一張來源臉部照片，ReActor 會逐格把影片中的臉換成來源臉，可選 GFPGAN／CodeFormer 修復臉部，輸出為動畫 webp。|Upload a target video and source face. ReActor swaps faces frame by frame, with optional GFPGAN or CodeFormer restoration, and outputs animated WebP.
依圖片 1 的比例輸出；參考圖請按順序加入，指令以 <image1> 至 <image4> 指定。|Output uses image 1's aspect ratio. Add references in sequence and refer to them as <image1> through <image4>.
只接受單一、清楚、近正面的真人臉照片；動漫角色、插畫、多人照與遮擋臉孔不適用。|Use one clear, nearly frontal real face. Anime, illustrations, group photos and obscured faces are not supported.
可直接使用最多 9 圖、3 影片、3 音訊；六段提示詞與上傳 reference tag 必須一致。|Use up to 9 images, 3 videos and 3 audio clips. The six prompt sections must match the uploaded reference tags.
可直接描述中文海報與排版；PNG 輸出支援透明背景。|Describe Chinese posters and typography directly. PNG output supports transparent backgrounds.
可直接測試，不綁角色；Stock、Sage、EasyCache、TeaCache 都是明確選項。|Use without a bound character. Stock, Sage, EasyCache and TeaCache are explicit choices.
同一個介面整合兩條後端：台灣國語走 BreezyVoice；Custom English / 多語 clone 走 VoxCPM2。VoxCPM 的 Custom English Clone 會同時套 設定的 LoRA 與參考音訊逐字稿，並用聲紋 Gate 避免漂成別人的聲音。|Taiwanese Mandarin uses BreezyVoice; Custom English and multilingual cloning use VoxCPM2. Custom English Clone combines the configured LoRA and reference transcript with a speaker-similarity gate.
寫實角色由伺服器使用角色卡原圖做 InstantID + 換臉雙重鎖定，並在輸出前檢查人物與身份；動漫角色使用既有角色 LoRA。瀏覽器不能替換身份圖、模型或觸發詞。|For realistic characters, the server uses the profile image with InstantID and face swap, then checks the person and identity. Anime characters use their bound LoRA. The browser cannot replace identity inputs, models or trigger words.
從角色頁進入時只提供該角色已驗證的聲音；從一般工具進入時只提供聲音庫克隆。長文會自動分段後合併。|The character entry uses that character's verified voice; the general tool uses library cloning. Long text is split and merged automatically.
快速延伸：丟一句話幫你擴寫成完整貼文；進階模式：自選語調、長度、關鍵字。Claude／Grok 走本機生成，Gemini／ChatGPT 走各自的 API（金鑰由本機環境變數或明確設定的檔案提供）。|Expand an idea into a post, or choose tone, length and keywords. Claude and Grok style use local generation; Gemini and ChatGPT use their APIs with your locally configured credentials.
把短影片重複接成 MP4；可選正放後倒放來減少接點跳動。這個版本不保留原音訊，也不保證每支影片都能完全無縫。從角色作品接續時，輸出會列為一般工具作品。|Repeat a short clip into an MP4. Forward/reverse playback can reduce jumps. Original audio is omitted and seamless results are not guaranteed. Outputs are listed as general-tool work.
會依參考圖重新生成新畫面，不保證保留同一張臉、服裝或人物身分；不作為角色合照或精準換裝入口。|Creates a new image from references. Faces, clothing and identity may change; this is not an exact clothing-transfer or character-group-photo tool.
獨立工具：只適用可清楚偵測臉部的真人影片，不會把人物換成某個角色，也不會驗證影片中的人是 operator。角色頁只會開放具備可驗證角色影片與聲音來源的專屬版本。|Independent tool for real videos with detectable faces. It does not replace or verify identity. Character-specific versions require verified video and voice sources.
管理上傳的參考音訊，供聲音克隆使用。|Manage uploaded reference audio for voice cloning.
管理員工具：貼上長文或上傳 PPTX 備忘稿，一次生成多段 Custom English 配音。|Administrator tool: paste long text or upload PPTX speaker notes to generate multiple Custom English voiceover segments.
舊入口保留給直接連結與回溯測試。日常使用請走「聲音生成」並選聲音庫 Clone，避免同一件事出現兩個入口。|This legacy entry remains for direct links and regression checks. Use library cloning in Speech generation for everyday work.
角色與服裝像素保持不變，只把核准的透明角色圖放進核准的動漫場景。|Preserves character and clothing pixels by compositing the approved transparent character image onto an approved anime scene.
這是 AI 重新生成，不是逐像素修圖；人物臉孔、服裝細節與透明背景可能改變。需要角色一致性時，請從角色頁使用已驗證功能。|This regenerates the image rather than editing individual pixels. Faces, clothing details and transparency may change. Use verified character-page features when identity consistency is required.
首格、尾格皆可單獨使用；至少上傳一張。角色入口仍會套用動漫／真人 guardrail。|Use a first frame, last frame or both; upload at least one. Character entries still apply their anime or realistic-character constraints.
生成資源|Generation resources
先看清楚每個來源現在能不能用；未通過的項目一律不會被自動選中。|Check each source's availability. Sources that have not passed checks are not selected automatically.
金鑰採只寫保存，介面與 API 都不提供讀回。|Credentials are write-only; neither the UI nor API returns their values.
連接 AutoIP|Connect AutoIP
匯入角色與作品|Import characters and media
帳號識別碼|Account identifier
新 API Key|New API key
Key 不會寫進瀏覽器儲存空間；送出後欄位立即清空。|The key is not saved in browser storage. The field clears immediately after submission.
金鑰只寫保存、不提供讀回；按一次就會接著完成零扣點連線、帳號與價格檢查。|The key is stored write-only. Submission checks connectivity, account status and prices without spending credits.
AutoIP 連線與啟用|AutoIP connection and activation
查看上次同步|View last sync
AutoIP 歷史與模板|AutoIP history and templates
AutoIP 輸出品質紀錄（選用）|AutoIP output-quality records (optional)
請先重新執行零扣點 AutoIP 連線檢查。|Run the no-credit AutoIP connection check again first.
目前沒有同一設定的兩個可比較輸出；這項紀錄可以略過。|No two comparable outputs share the same settings. You can skip this record.
保存這兩個品質參考|Save these two quality references
ClipMode 離線匯入|ClipMode offline import
下載打包清單範本|Download package inventory template
內含檔案|Bundled files
僅遠端索引|Remote references only
來源關係|Source relationships
未分類阻擋|Blocked by missing classification
重複|Duplicates
角色分類|Character classification
角色預覽|Character preview
衝突|Conflicts
全部角色|All characters
全部用途|All purposes
這個分類目前沒有素材|No assets in this category
調整角色或來源篩選，或稍後再回來查看。|Change the character or source filters, or check again later.
正在整理素材…|Organizing assets…
載入更多|Load more
先選角色類型|Choose a character type
再選資源範圍|Choose the resource scope
角色快速篩選|Quick character filter
外部目錄唯讀|External catalog is read-only
本機素材|Local assets
外部素材|External assets
聲音|Audio
未命名文字|Untitled text
1. 貼一次 Key|1. Paste the key once
2. 正常生成|2. Generate normally
只寫保存並建立你的私人帳號設定。|Store the key write-only and create your private account settings.
輪替後會直接重新檢查並啟用。|Rotating the key reruns checks and activation.
連線通過後，直接選擇功能、查看即時報價並確認生成。|Once connected, choose a feature, review the current quote and confirm generation.
只讀取帳號狀態、剩餘點數與價格表；檢查本身不生成、不上傳、不扣點，通過後會啟用正常生成。|Reads account status, remaining credits and prices. The check does not generate, upload or spend credits; passing enables normal generation.
只同步 task 狀態和模板名稱／長度；不生成、不下載、不扣點。模板影片下載並納入私人媒體庫後才會出現在可選清單。|Syncs task status and template names/durations without generating, downloading or spending credits. Downloaded templates become selectable after import into your private media library.
下載後仍不綁角色、不判斷動漫或真人|Downloading does not bind a character or classify it as anime or realistic
AutoIP 目前沒有回傳舞蹈模板。|AutoIP has not returned any dance templates.
待私有化 · 不可選|Private import required · Not selectable
可把同一設定的兩個結果存成私人品質參考；這不會決定 AutoIP 能不能使用。|Save two outputs with matching settings as private quality references. This does not determine AutoIP availability.
只有按下「確認匯入媒體庫」才會寫入。整個流程不呼叫 ClipMode、不花點；暫存有期限，取消後立即失效。|Nothing is saved until you confirm media-library import. This flow does not call ClipMode or spend credits. Temporary data expires and cancellation invalidates it.
確認匯入媒體庫|Confirm media-library import
畫面只列出前 12 位角色；總數以上方統計為準。|Only the first 12 characters are shown; the totals above include all entries.
衝突清單已限量顯示；請修正來源檔後重新預覽。|The conflict list is truncated. Fix the source file and preview again.
只處理目前看得到的本機作品；逐一驗證檔案與角色，不會上傳或花點。|Processes only visible local outputs, validating files and characters without uploads or credit charges.
同名角色也不會混在一起；每張卡都標示 Castle 或外部來源。|Characters with identical names stay separate; each card identifies its local or external source.

模型版本|Model version
功能說明|Feature guide
就緒|Ready
缺件|Missing dependencies
工具|Tool
待開放|Not yet available
尚未就緒|Not ready
服務連線異常|Service connection error
搜尋創作工具|Search creation tools
沒有符合條件的工具。|No tools match your search.
改用一般工具|Use the general tool
這個角色不能使用這項工具，已停止送出。|This tool is unavailable for this character. Submission is blocked.
動漫角色|Anime characters
動漫與插畫風格角色|Anime and illustrated characters
真人角色|Realistic characters
真人與寫實風格角色|Real people and realistic-style characters
缺少角色素材或尚待確認分類|Missing character assets or classification
上傳照片建立私人角色|Upload photos to create a private character
前往角色素材|Open character assets
補角色素材|Add character assets
查看素材|View assets
角色與素材分類|Character and asset categories
採集圖片與影片|Collect images and videos
外部角色與資源|External characters and resources
使用者管理|User management
工作流監控|Workflow monitor
開啟 ComfyUI 節點編輯器 ↗|Open ComfyUI node editor ↗
顯示名稱|Display name
權限|Permissions
新增使用者|Add user
重設密碼|Reset password
設定權限|Assigned role
目前權限|Current role
建立時間|Created
操作|Actions
更新|Refresh
更新佇列|Refresh queue
查詢|Search
重跑|Rerun
標籤|Tags
核准|Approve
退回|Reject
批次加入|Add in bulk
提交條件|Submission requirements
查看技術狀態|View technical status
進階操作與新工作流|Advanced actions and new workflow
送出 Mock|Submit mock
送出 DAG|Submit DAG
← 總覽|← Overview
← 執行紀錄|← Runs
先看整體狀態，再進入執行紀錄與詳細資料。|Review the overall status, then inspect individual runs and details.
沒有符合條件的 artifact。|No artifacts match the filters.
還沒有 artifact。|No artifacts yet.
還沒有 event。|No events yet.
目前沒有 workflow run。|No workflow runs yet.
沒有任務紀錄|No task history
我的任務|My tasks
全部任務|All tasks
最近 48 小時|Last 48 hours
目前沒有進行中的任務。|No active tasks.
最近沒有完成或失敗的任務。|No recent completed or failed tasks.
任務服務暫時無法連線，草稿與目前畫面已保留。|The task service is temporarily unavailable. Your draft and current view are preserved.
重新連線|Reconnect
目前沒有執行中或排隊中的 ComfyUI job。|No running or queued ComfyUI jobs.
外部|External
建立平台帳號後，仍需把 Email 加入 Cloudflare Access 政策。|After creating an account, add its email to your Cloudflare Access policy if applicable.
管理 Castle 平台帳號、角色與初次密碼設定狀態。|Manage platform accounts, roles and initial password setup.
選擇符合 Castle 公開格式、由你建立或由服務提供的 JSON／ZIP。Castle 會先建立短效私有暫存，再讓你核對差異；不接受 Cookies、瀏覽器工作階段或私有接口。|Choose a JSON or ZIP you created or received in the supported public format. Review changes in short-lived private staging. Cookies, browser sessions and private interfaces are not accepted.
還沒有 Castle 匯出包？用本機檔案安全打包|No export package yet? Package your local files
先下載清單範本，填入你已下載的角色與媒體；再於 Web_Comfyui 執行下列工具。工具不會登入 ClipMode，也不會讀 Cookies。|Download the inventory template, list your downloaded characters and media, then run the tool in Web_Comfyui. It does not sign into ClipMode or read cookies.
連線、帳號與價格相容性檢查仍在有效期限內；AutoIP 可直接列入正常生成規劃。|Connection, account and price checks are current. AutoIP can be included in normal generation planning.

設定你的登入密碼|Set your sign-in password
信箱驗證已通過，第一次進入請設定 Castle 平台密碼。|Your email is verified. Set your platform password on this first visit.
第一次啟用主控台，請先建立本機管理密碼。|Create a local administrator password to start using the console.
輸入平台密碼|Enter your platform password
本機管理登入|Local administrator sign-in
Cloudflare Access 已確認你的信箱，請輸入 Castle 平台密碼。|Cloudflare Access verified your email. Enter your platform password.
這是 local-workstation 的 ComfyUI 管理入口。|This is the local workstation's ComfyUI administration interface.
密碼至少 8 個字。|Use a password with at least 8 characters.
兩次輸入的密碼不一致。|The passwords do not match.
登入中…|Signing in…
設定中…|Setting up…

互動說明版 · 點功能或生成按鈕，查看實際模型、template 與技巧。|Interactive guide · Choose a tool or Generate to explore its actual models, templates and techniques.
安裝與使用說明|Installation and usage
創作工具|Creation tools
切換 ▾|Switch ▾
幫我想|Brainstorm
加強描述|Enhance description
隨機提示詞|Random prompt
✨ 幫我想|✨ Brainstorm
✨ 加強描述|✨ Enhance description
✨ 隨機提示詞|✨ Random prompt
說明資料載入失敗，請重新整理頁面。|Guide data could not load. Reload the page.
目前沒有可用的創作工具。|No creation tools are currently available.
查看一般創作工具|Browse creation tools
已切換到一般創作工具。|Switched to creation tools.
目前角色：|Current character:\u0020
以匯入後的實際節點設定為準。|Use the actual node settings after import.
全部內容|All content
工作流來源|Workflow source
工作流分類|Workflow category
已列出的節點與模型選項都存在；仍需確認素材、版本相容性與 GPU 容量。|The listed nodes and model options are available. Check input assets, version compatibility and GPU capacity before running.
已確認部分依賴；仍有模型選項需在原生圖中核對。|Some dependencies are confirmed. Check the remaining model options in the native graph.
從下方來源查看工作流的用途與設定。|See the source below for this workflow's purpose and settings.
找不到這個工作流，請從工作庫搜尋。|This workflow was not found. Search the workflow library.
搜尋用途、技巧、模型或節點|Search purposes, techniques, models or nodes
收錄狀態|Availability
教學整理|Tutorial notes
方案設定|Recipe settings
有節點或模型尚未具備，請先補齊下列項目。|Some nodes or models are missing. Install the following requirements first.
正在加入 ComfyUI…|Adding to ComfyUI…
此工作流的說明連結|Guide link for this workflow
沒有符合條件的工作流。請調整搜尋或分類。|No workflows match. Adjust the search or category.
環境需求|Environment requirements
節點已具備，模型待確認|Nodes available; models need checking
請依節點說明放置|Use the location specified by the node documentation
讀取失敗|Could not load
預期結果：|Expected result:\u0020
驗證紀錄|Validation record
，可按上方按鈕重試。|. Use the button above to retry.

還沒有角色。先建立一個實驗角色，再開始創作。|No characters yet. Create an experimental character to get started.
Castle 自動影片工作室|Castle automated video studio
從一個構想做到完整影片|Turn an idea into a complete video
選角色、說故事；Castle 自動整理分鏡並選好可用資源，你只要決定何時開始試做。|Choose characters and describe your story. Castle prepares the storyboard and available resources; you decide when to try a shot.
最短只要兩次點擊|Get started in as few as two clicks
準備試做 → 立即試做；中間檢查全部自動完成|Prepare a trial → Try now; the checks in between run automatically
背景音樂庫目前無法安全載入。|The background music library cannot be loaded safely right now.
填構想與角色|Add your idea and characters
按一次準備試做|Select Prepare a trial
Castle 自動準備|Castle prepares the plan
整理故事板與可用方式|Organize the storyboard and available methods
立即試做|Try now
選一個鏡頭直接送出生成|Choose a shot and submit it for generation
用簡單方式開始|Start with a simple plan
填好構想、角色與段落後，按一下準備試做；Castle 會自動整理分鏡與可用方式。|Add your idea, characters and story beats, then select Prepare a trial. Castle organizes the shots and available methods.
單支影片|Single video
一次完成一個主題|Complete one topic at a time
系列內容|Series
共用角色與風格聖經|Share characters and a style bible
短劇|Short drama
依場景與角色狀態延續|Continue from the scene and character states
單支影片：一個清楚重點，先做短而完整。|Single video: start with one clear focus and keep it short and complete.
專案名稱|Project name
一句話構想|One-sentence idea
觀眾|Audience
比例|Aspect ratio
公開平台會自動使用正確比例；私人預覽可自由選擇。|Public platforms use their required aspect ratio automatically; private previews let you choose.
背景音樂（選用）|Background music (optional)
從統一媒體庫選擇|Choose from the unified media library
媒體庫目前沒有可用音訊；仍可先製作無背景音樂版本。|No audio is available in the media library. You can create a version without background music.
音量|Volume
會保留角色說話與環境聲，再統一做社群音量標準化。|Character speech and ambient sound are preserved, then normalized for social platforms.
選角色|Choose characters
目前沒有已分類且有 profile 圖的角色。|No categorized characters with profile images are available.
故事段落|Story beats
＋ 新增段落|＋ Add a beat
移除|Remove
這一段發生什麼？|What happens in this beat?
角色動作|Character action
鏡頭|Shot
秒數|Duration in seconds
聲音／對白|Sound / dialogue
效果音（選用）|Sound effects (optional)
可搜尋、預聽並疊加多段自己的音訊；每段可設定不同進場時間。|Search, preview and layer your own audio clips, with a separate start time for each.
＋ 加一段效果音|＋ Add a sound effect
這一段誰出場？|Who appears in this beat?
專案角色預設都會加入這一段；若某位不出場，再點一下將他排除。動漫與真人請拆成不同鏡頭。|All project characters are included by default. Select a character again to exclude them. Keep anime and real people in separate shots.
先在上方選擇專案角色。|Choose project characters above first.
尚未選擇這一段的出場角色|No characters selected for this beat
準備試做|Prepare a trial
進階：完整專案製作、逐鏡審核與剪輯|Advanced: full production, shot review and editing
繼續既有製作|Continue an existing production
直接從你的最近製作續作；也可以輸入專案 ID。載入清單不會執行、排隊或扣點。|Resume a recent production or enter a project ID. Loading the list does not run jobs, queue work or spend credits.
取回最新進度|Refresh progress
匯入完整 Production Pack JSON|Import a complete Production Pack JSON
1. 放入專案規格|1. Add the project specification
可直接放 production pack；介面只替你包上 dry-run 外層，原始內容仍由伺服器嚴格檢查。|Paste a production pack directly. The interface wraps it in a dry-run request; the server still validates the original content strictly.
上限 2.06 MiB|Limit: 2.06 MiB
選擇 JSON 檔案|Choose a JSON file
也可以直接貼上 JSON|Or paste JSON directly
檢查規劃（不執行）|Check the plan (without running)
角色創作資源中心|Character creation resources
同一個角色、同一份素材，在本機 5090、AutoIP 與離線匯入之間保持清楚來源與安全邊界。|Keep character and asset provenance clear across the local 5090, AutoIP and offline imports, with separate access boundaries.
預設安全模式|Safe mode by default
不會自動上傳、不會自動花點，也不會讀取網站 Cookies。|No automatic uploads, spending or website cookie access.
重新整理狀態|Refresh status
暫時無法取得|Temporarily unavailable
我的 5090|My 5090
狀態資料目前無法讀取，所有寫入與付費動作保持關閉。|Status information is unavailable. All write and paid actions remain disabled.
不會執行生成|Generation is disabled
需要 ClipMode JSON／ZIP 匯出包；Castle 不會讀取瀏覽器 Cookie。|A ClipMode JSON/ZIP export is required. Castle does not read browser cookies.
MiniMAX H3 目前不會執行|MiniMAX H3 is currently disabled
沒有另行取得適用授權前，這項功能不會下載模型或執行。|This feature will not download models or run until the applicable license has been obtained separately.
○ 授權紀錄待完成|○ License record pending
○ 鎖定模型待完成|○ Pinned model pending
○ 原生節點待完成|○ Native nodes pending
○ 功能技術相容性待完成|○ Feature compatibility pending
授權限制中|License restricted
儲存並啟用 AutoIP|Save and enable AutoIP
請先安全儲存 AutoIP API Key。|Securely save your AutoIP API key first.
檢查並啟用 AutoIP|Check and enable AutoIP
同步歷史與舞蹈模板（零扣點）|Sync history and dance templates (no credits spent)
1 選擇匯出包|1 Choose an export
2 安全分析|2 Analyze safely
3 核對差異|3 Review changes
4 匯入媒體庫|4 Import into the media library
拖放 ClipMode 匯出包到這裡|Drop a ClipMode export here
或點一下選擇 JSON／ZIP（上限 64 MiB）|Or select a JSON/ZIP file (up to 64 MiB)
分析並建立暫存|Analyze and stage
核對最後差異|Review final changes
取消這次匯入|Cancel this import
統一媒體庫|Unified media library
Castle 角色與外部角色分開識別；外部目錄目前只供整理與查看，不會直接進入生成。|Castle and external characters are identified separately. The external catalog is currently for organization and viewing; it does not feed directly into generation.
插畫|Illustration
未分類|Uncategorized
全部資源|All resources
Castle 與外部目錄|Castle and external catalogs
Castle 本機|Local Castle
我的 5090 作品|My 5090 creations
外部目錄|External catalog
唯讀角色與素材|Read-only characters and assets
全|All
兩個目錄|Both catalogs
這個類型與範圍目前沒有角色。|No characters match this type and scope.
單張或多張參考圖都從這裡生成；系統會自動選擇相符路線，當下價格只作紀錄。|Generate from one or more reference images here. The system selects a matching route; the current price is recorded for reference.
尚未選擇參考圖|No reference images selected
從媒體庫選擇一張或多張已驗證圖片後即可生成。|Select one or more verified images from the media library to generate.
查看全部|View all
點擊或拖曳上傳|Click or drag to upload
從素材庫選擇|Choose from the asset library
方式一：上傳檔案|Method 1: upload a file
方式二：從網址下載|Method 2: download from a URL
方式三：fb_crawl 工作區影片|Method 3: fb_crawl workspace video
使用|Use
讀取失敗：此資料由安裝後的本機服務提供；說明站沒有個人資料。|Could not load: this data comes from your installed local service. The guide site contains no personal data.
方式四：Google Drive 登入 / 分享連結|Method 4: Google Drive sign-in / shared link
登入 Google Drive|Sign in to Google Drive
Drive 狀態讀取失敗：此資料由安裝後的本機服務提供；說明站沒有個人資料。|Could not load Drive status: this data comes from your installed local service. The guide site contains no personal data.
或貼上分享連結|Or paste a shared link
請從聲音庫選擇或上傳一段語音。|Choose a voice from the library or upload a speech clip.
聲音方式|Voice method
聲音庫克隆|Voice library cloning
預設下載格式|Default download format
未壓縮・適合剪輯|Uncompressed · suitable for editing
檔案較小・通用|Smaller file · widely supported
720p 黑底影片・直接上傳|720p video on black · ready to upload
之後按下載時會優先使用這個格式；作品詳細裡仍可隨時改選 WAV、MP3 或 MP4。|Downloads will use this format by default. You can still choose WAV, MP3 or MP4 in the result details.
克隆方式|Cloning method
台灣腔走 BreezyVoice；要說英文、日文等外語請選多語 F5。兩者都需要參考音訊的逐字稿。|Use BreezyVoice for a Taiwanese accent and multilingual F5 for English, Japanese and other languages. Both require a transcript of the reference audio.
歷史批次|Previous batches
目前沒有歷史批次。|No previous batches.
歷史批次讀取失敗：此資料由安裝後的本機服務提供；說明站沒有個人資料。|Could not load previous batches: this data comes from your installed local service. The guide site contains no personal data.
長文貼進來,空行分段;每段一個音檔|Paste long text; blank lines separate paragraphs, each producing one audio file
抽出每頁備忘稿(notes),一頁一段|Extract speaker notes, with one segment per slide
檢查段落|Check segments
參考音來源|Reference audio source
從聲音庫選|Choose from the voice library
上傳新音訊|Upload new audio
文字內容|Text content
來源 / 備註|Source / notes
參考音訊逐字稿|Reference audio transcript
音訊檔（mp3 / wav / m4a / ogg / flac）|Audio file (mp3 / wav / m4a / ogg / flac)
聲音庫還是空的，上傳第一段參考音訊吧。|The voice library is empty. Upload your first reference clip.
快速延伸|Quick expansion
進階模式|Advanced mode
一句話|One sentence
目標平台|Target platform
至少選一個參考|Select at least one reference

`;
  const map = Object.fromEntries(pairs.trim().split('\n').map(line => {const i=line.indexOf('|');return [line.slice(0,i),line.slice(i+1)];}));
  window.WebComfyEnglish = Object.freeze(map);
  if (window.WebComfyI18n) window.WebComfyI18n.register(map);
})();

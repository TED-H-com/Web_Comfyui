(() => {
  "use strict";

  const SCHEMA_VERSION = 1;
  const PROVIDERS_ENDPOINT = "/api/providers";
  const CHARACTERS_ENDPOINT = "/api/provider-characters";
  const ASSETS_ENDPOINT = "/api/assets";
  const LOCAL_HISTORY_ENDPOINT = "/api/history?max_items=500&scope=own";
  const CLIPMODE_PREVIEW_ENDPOINT = "/api/providers/clipmode/imports/preview";
  const CLIPMODE_STAGE_ENDPOINT = "/api/providers/clipmode/imports/stages";
  const CLIPMODE_PREVIEW_MAX_BYTES = 64 * 1024 * 1024;
  const CLASSIFICATION_CHARACTER_PAGE_SIZE = 200;
  const CLASSIFICATION_CHARACTER_MAX_PAGES = 8;
  const GENERATION_ENDPOINT = "/api/generations";
  const PLAN_ENDPOINT = "/api/intents/plan";
  const PROJECT_STATUS_REFRESH_MS = 5000;
  const INTENT_STATUS_STATES = Object.freeze([
    "pending", "preparing", "submission_uncertain", "submitted", "queued", "running",
    "completed", "failed", "cancelled", "timeout", "abandoned",
  ]);
  const TERMINAL_INTENT_STATES = Object.freeze([
    "completed", "failed", "cancelled", "timeout", "abandoned",
  ]);
  const MEDIA_KINDS = Object.freeze([
    Object.freeze({ id: "image", label: "圖片" }),
    Object.freeze({ id: "video", label: "影片" }),
    Object.freeze({ id: "audio", label: "聲音" }),
    Object.freeze({ id: "text", label: "文字" }),
  ]);
  const MEDIA_ROLE_OPTIONS = Object.freeze({
    image: Object.freeze([
      ["unclassified", "未分類"], ["profile", "角色 Profile"], ["outfit", "服裝參考"],
      ["reference", "角色／構圖參考"], ["background", "背景"], ["prop", "道具"], ["storyboard", "分鏡"], ["finished", "生成成品"],
    ]),
    video: Object.freeze([
      ["unclassified", "未分類"], ["dance_template", "跳舞模板"], ["motion_reference", "動作參考"],
      ["source_clip", "原始片段"], ["finished", "完成影片"],
    ]),
    audio: Object.freeze([["unclassified", "由聲音用途分類"]]),
    text: Object.freeze([
      ["unclassified", "未分類"], ["prompt", "提示詞"], ["script", "腳本"], ["subtitle", "字幕"],
    ]),
  });
  const VISUAL_CLASS_FILTERS = Object.freeze([
    Object.freeze({ id: "all", label: "全部角色" }),
    Object.freeze({ id: "anime", label: "動漫" }),
    Object.freeze({ id: "realistic", label: "真人" }),
    Object.freeze({ id: "illustration", label: "插畫" }),
    Object.freeze({ id: "unknown", label: "未分類" }),
  ]);
  const CATALOG_SCOPES = Object.freeze([
    Object.freeze({ id: "all", label: "全部資源", detail: "Castle 與外部目錄" }),
    Object.freeze({ id: "castle", label: "Castle 本機", detail: "我的 5090 作品" }),
    Object.freeze({ id: "provider", label: "外部目錄", detail: "唯讀角色與素材" }),
  ]);
  const PROVIDER_META = Object.freeze({
    local_comfy: Object.freeze({ label: "我的 5090", badge: "Local", tone: "local" }),
    autoip: Object.freeze({ label: "AutoIP", badge: "AutoIP", tone: "autoip" }),
    clipmode: Object.freeze({ label: "ClipMode", badge: "ClipMode", tone: "clipmode" }),
  });
  const STATUS_COPY = Object.freeze({
    ready: Object.freeze({ label: "可以使用", detail: "已通過必要檢查，可列入明確的生成規劃。", tone: "ready" }),
    account_not_configured: Object.freeze({ label: "尚未設定", detail: "還沒有可用的服務帳號。", tone: "attention" }),
    needs_credential: Object.freeze({ label: "需要新金鑰", detail: "請由管理員設定已輪替的新 API Key。", tone: "attention" }),
    configured_disabled: Object.freeze({ label: "已設定但停用", detail: "資料已保存，現在不會呼叫這個服務。", tone: "muted" }),
    credential_rotation_required: Object.freeze({ label: "必須先輪替金鑰", detail: "現有金鑰不會被啟用；請換成未曝光的新金鑰。", tone: "blocked" }),
    health_unverified: Object.freeze({ label: "等待連線驗證", detail: "尚未證明服務健康，因此不會送出工作。", tone: "attention" }),
    pricing_unavailable: Object.freeze({ label: "費用尚未確認", detail: "拿不到可信報價時，付費操作會保持關閉。", tone: "attention" }),
    account_selection_required: Object.freeze({ label: "需要選擇帳號", detail: "有多個帳號，系統不會自行猜測要使用哪一個。", tone: "attention" }),
    runtime_not_ready: Object.freeze({ label: "本機尚未就緒", detail: "模型、節點或執行環境仍有必要項目未完成。", tone: "attention" }),
    models_not_ready: Object.freeze({ label: "模型尚未完整", detail: "MiniMax H3 的鎖定模型尚未全部通過檔案大小與 SHA-256 驗證，因此不會執行。", tone: "attention" }),
    contract_not_ready: Object.freeze({ label: "部分功能尚未就緒", detail: "至少一種 H3 功能的精確模型、節點或工作流相容性尚未通過。", tone: "attention" }),
    needs_supported_contract: Object.freeze({ label: "僅能離線匯入", detail: "目前沒有經驗證的官方 API 契約，不會使用 Cookies 或網站私有接口。", tone: "muted" }),
    offline_export_required: Object.freeze({ label: "等待匯出包", detail: "請從 ClipMode 準備 JSON／ZIP 匯出包；不會使用 Cookies 或網站私有接口。", tone: "attention" }),
    offline_import_ready: Object.freeze({ label: "離線匯入已就緒", detail: "可匯入角色、profile photo、圖片／影片／聲音／文字、媒體版本與來源關係。", tone: "ready" }),
    license_blocked: Object.freeze({ label: "授權限制中", detail: "沒有另行取得適用授權前，這項功能不會下載模型或執行。", tone: "blocked" }),
    unavailable: Object.freeze({ label: "暫時無法取得", detail: "狀態資料目前無法讀取，所有寫入與付費動作保持關閉。", tone: "blocked" }),
  });
  const INTENT_STATUS_COPY = Object.freeze({
    pending: Object.freeze({ label: "等待處理", detail: "Castle 已建立工作，正在等待安全執行。", tone: "attention" }),
    preparing: Object.freeze({ label: "準備中", detail: "Castle 正在準備已確認的工作，尚未證明供應商已收到。", tone: "attention" }),
    submission_uncertain: Object.freeze({ label: "送出狀態不確定", detail: "供應商是否收到工作目前無法確定。請勿重新送出，以免重複扣點或產生重複工作。", tone: "blocked" }),
    submitted: Object.freeze({ label: "已送出", detail: "Castle 的持久紀錄顯示工作已送交供應商。", tone: "attention" }),
    queued: Object.freeze({ label: "供應商排隊中", detail: "供應商已接收工作，目前正在佇列中等待。", tone: "attention" }),
    running: Object.freeze({ label: "生成中", detail: "供應商正在處理這份工作。", tone: "attention" }),
    completed: Object.freeze({ label: "供應商已完成", detail: "供應商已完成，但輸出尚未經 Castle 驗證或納入媒體庫。", tone: "ready" }),
    failed: Object.freeze({ label: "工作失敗", detail: "工作已停止；這裡不提供重試或重新送出，避免重複執行。", tone: "blocked" }),
    cancelled: Object.freeze({ label: "工作已取消", detail: "工作已停止；這裡不提供重新送出。", tone: "muted" }),
    timeout: Object.freeze({ label: "工作逾時", detail: "工作已逾時；先查明供應商狀態，不要直接重新送出。", tone: "blocked" }),
    abandoned: Object.freeze({ label: "停止追蹤", detail: "Castle 已停止追蹤這份工作；請勿從這張卡重新送出。", tone: "blocked" }),
  });

  function node(tag, options = {}, children = []) {
    const element = document.createElement(tag);
    if (options.className) element.className = options.className;
    if (options.text !== undefined) element.textContent = String(options.text);
    if (options.type) element.type = options.type;
    if (options.id) element.id = options.id;
    if (options.hidden) element.hidden = true;
    for (const [name, value] of Object.entries(options.attributes || {})) {
      if (value !== undefined && value !== null) element.setAttribute(name, String(value));
    }
    for (const child of children) {
      if (child) element.append(child);
    }
    return element;
  }

  function safeText(value, fallback = "", maximum = 500) {
    if (typeof value !== "string") return fallback;
    const result = value.trim();
    return result && Number.isSafeInteger(maximum) && maximum > 0 && result.length <= maximum
      ? result : fallback;
  }

  function safeId(value) {
    const normalized = safeText(value).toLowerCase();
    return /^[a-z0-9][a-z0-9._:-]{0,127}$/.test(normalized) ? normalized : "";
  }

  function pathId(value) {
    const normalized = safeId(value);
    if (!normalized) throw new TypeError("Invalid typed path identifier");
    return normalized;
  }

  function safeIntentId(value) {
    return typeof value === "string" && /^intent:[0-9a-f]{32}$/.test(value) ? value : "";
  }

  function safeDigestId(value, prefix) {
    if (typeof value !== "string") return "";
    const match = /^([a-z]+):([0-9a-f]{32})$/.exec(value);
    return match && match[1] === prefix ? value : "";
  }

  function safeArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function safeOptionScalar(value) {
    if (typeof value === "boolean") return value;
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string" && value.length > 0 && value.length <= 100
      && !Array.from(value).some((character) => character.charCodeAt(0) < 32)) return value;
    return null;
  }

  function hasExactKeys(value, keys) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return false;
    const observed = Object.keys(value).sort();
    const expected = [...keys].sort();
    return observed.length === expected.length
      && observed.every((key, index) => key === expected[index]);
  }

  function safeCount(value, maximum = 1000000) {
    return Number.isInteger(value) && value >= 0 && value <= maximum ? value : 0;
  }

  function safeSha256(value) {
    return typeof value === "string" && /^[0-9a-f]{64}$/.test(value) ? value : "";
  }

  function safeOpaqueToken(value, maximum = 8192) {
    return typeof value === "string"
      && value.length >= 40
      && value.length <= maximum
      && /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(value)
      ? value
      : "";
  }

  function containsSensitiveField(value, depth = 0) {
    if (depth > 16) return true;
    if (Array.isArray(value)) return value.some((item) => containsSensitiveField(item, depth + 1));
    if (!value || typeof value !== "object") return false;
    return Object.entries(value).some(([key, child]) => {
      const normalized = String(key).toLowerCase().replace(/[^a-z0-9]+/g, "_");
      if (
        [
          "api_key", "apikey", "authorization", "bearer", "cookie", "cookies",
          "credential", "credential_value", "password", "private_key", "refresh_token",
          "secret", "secret_key", "secret_value", "session", "session_cookie", "token",
          "webhook_secret", "xsrf_token",
        ].includes(normalized)
        || normalized.endsWith("_api_key")
        || normalized.endsWith("_cookie")
        || normalized.endsWith("_password")
        || normalized.endsWith("_secret")
        || normalized.endsWith("_token")
      ) return true;
      return containsSensitiveField(child, depth + 1);
    });
  }

  function sameOriginMediaUrl(value) {
    const candidate = safeText(value);
    if (!candidate || !candidate.startsWith("/") || candidate.startsWith("//")) return "";
    try {
      const parsed = new URL(candidate, window.location.origin);
      return parsed.origin === window.location.origin ? `${parsed.pathname}${parsed.search}` : "";
    } catch (_error) {
      return "";
    }
  }

  function localHistoryMediaUrl(value) {
    const candidate = sameOriginMediaUrl(value);
    if (!candidate) return "";
    try {
      const parsed = new URL(candidate, window.location.origin);
      return parsed.pathname === "/api/view" ? candidate : "";
    } catch (_error) {
      return "";
    }
  }

  function providerProfileMediaUrl(value, profileAssetId) {
    const candidate = sameOriginMediaUrl(value);
    const expectedAssetId = safeId(profileAssetId);
    if (!candidate || !expectedAssetId) return "";
    try {
      const parsed = new URL(candidate, window.location.origin);
      const match = /^\/api\/assets\/([^/]+)\/preview$/.exec(parsed.pathname);
      if (!match || parsed.search) return "";
      return safeId(decodeURIComponent(match[1])) === expectedAssetId ? candidate : "";
    } catch (_error) {
      return "";
    }
  }

  function providerAssetPreviewUrl(value, assetId) {
    const candidate = sameOriginMediaUrl(value);
    const expectedAssetId = safeId(assetId);
    if (!candidate || !expectedAssetId) return "";
    try {
      const parsed = new URL(candidate, window.location.origin);
      const match = /^\/api\/assets\/([^/]+)\/(?:preview|source)$/.exec(parsed.pathname);
      if (!match || parsed.search) return "";
      return safeId(decodeURIComponent(match[1])) === expectedAssetId ? candidate : "";
    } catch (_error) {
      return "";
    }
  }

  function stableHash32(value, seed) {
    let hash = seed >>> 0;
    for (let index = 0; index < value.length; index += 1) {
      hash ^= value.charCodeAt(index);
      hash = Math.imul(hash, 16777619) >>> 0;
    }
    return hash.toString(16).padStart(8, "0");
  }

  function deterministicLocalAssetId(viewType, subfolder, filename) {
    const identity = `${viewType}\u0000${subfolder}\u0000${filename}`;
    return `local_comfy:${stableHash32(identity, 2166136261)}${stableHash32(identity, 2654435769)}`;
  }

  function localHistorySourceLocator(raw) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
    const values = {
      filename: raw.filename,
      subfolder: raw.subfolder,
      type: raw.type,
      promptId: raw.promptId,
    };
    if (
      typeof values.filename !== "string"
      || !values.filename
      || values.filename.length > 500
      || values.filename.includes("\u0000")
      || typeof values.subfolder !== "string"
      || values.subfolder.length > 500
      || values.subfolder.includes("\u0000")
      || typeof values.type !== "string"
      || !["input", "output", "temp"].includes(values.type)
      || typeof values.promptId !== "string"
      || (values.promptId.startsWith("local-")
        ? !/^local-[A-Za-z0-9_-]{6,80}$/.test(values.promptId)
        : !/^[A-Za-z0-9_.:-]{1,128}$/.test(values.promptId))
    ) return null;
    return Object.freeze({
      filename: values.filename,
      subfolder: values.subfolder,
      type: values.type,
      promptId: values.promptId,
    });
  }

  function trustedLocalHistoryAttribution(raw) {
    const provenance = raw && raw.provenance && typeof raw.provenance === "object"
      && !Array.isArray(raw.provenance)
      ? raw.provenance
      : {};
    const character = raw && raw.character && typeof raw.character === "object"
      && !Array.isArray(raw.character)
      ? raw.character
      : {};
    const characterId = safeId(character.id || raw.characterId || raw.character_id);
    const provenanceCharacterId = safeId(provenance.characterId);
    const visualClass = safeText(raw.visualClass || raw.visual_class);
    const provenanceVisualClass = safeText(provenance.visualClass);
    const trusted = raw.provenanceTrusted === true
      && raw.characterAttribution === "server"
      && provenance.schema === "castle.output-provenance.v1"
      && Boolean(characterId)
      && characterId === provenanceCharacterId
      && ["anime", "realistic", "illustration"].includes(visualClass)
      && visualClass === provenanceVisualClass;
    return Object.freeze({
      characterId: trusted ? characterId : "",
      visualClass: trusted ? visualClass : "unknown",
    });
  }

  function canonicalJson(value) {
    if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
    if (value && typeof value === "object") {
      return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
    }
    return JSON.stringify(value);
  }

  function newGenerationRequestId() {
    const bytes = new Uint8Array(16);
    if (window.crypto && typeof window.crypto.getRandomValues === "function") {
      window.crypto.getRandomValues(bytes);
    } else {
      for (let index = 0; index < bytes.length; index += 1) {
        bytes[index] = Math.floor(Math.random() * 256);
      }
    }
    return `generation:${[...bytes]
      .map((value) => value.toString(16).padStart(2, "0"))
      .join("")}`;
  }

  async function deterministicIntentIdForPlan(planId) {
    const normalized = safeId(planId);
    if (!normalized || !window.crypto?.subtle || typeof TextEncoder !== "function") return "";
    try {
      const digest = await window.crypto.subtle.digest(
        "SHA-256",
        new TextEncoder().encode(normalized),
      );
      const hex = [...new Uint8Array(digest)]
        .map((value) => value.toString(16).padStart(2, "0"))
        .join("");
      return `intent:${hex.slice(0, 32)}`;
    } catch (_error) {
      return "";
    }
  }

  function planResponseMatchesRequest(data, request) {
    if (!data || !request || typeof data !== "object" || typeof request !== "object") return false;
    const intent = data.intent;
    const decision = data.decision;
    const quote = data.quote;
    const spendGuard = data.spend_guard;
    if (!intent || !decision || !quote || !spendGuard) return false;
    if (intent.capability !== request.capability || intent.visual_class !== request.visual_class) return false;
    if (canonicalJson(data.options || {}) !== canonicalJson(request.options || {})) return false;
    if (request.creative_instruction) {
      const observedInstruction = data.creative_instruction;
      if (!observedInstruction || typeof observedInstruction !== "object") return false;
      const expectedInstruction = request.creative_instruction;
      for (const key of ["schema_version", "shot_id", "frame_intent", "observable_action", "camera", "audio_intent", "language"]) {
        if (observedInstruction[key] !== expectedInstruction[key]) return false;
      }
      if (!/^[0-9a-f]{64}$/.test(String(observedInstruction.content_hash || ""))) return false;
    } else if (data.creative_instruction != null) return false;
    const expectedCharacters = safeArray(request.characters).map((item) => [
      safeId(item && item.character_id),
      safeText(item && item.visual_class),
    ]);
    const observedCharacters = safeArray(intent.characters).map((item) => [
      safeId(item && item.character_id),
      safeText(item && item.visual_class),
    ]);
    if (canonicalJson(observedCharacters) !== canonicalJson(expectedCharacters)) return false;
    const expectedAssets = safeArray(request.assets).map((item) => [
      safeId(item && item.asset_id),
      safeText(item && item.kind),
      safeText(item && item.visual_class),
      safeArray(item && item.character_ids).map(safeId),
    ]);
    const observedAssets = safeArray(intent.assets).map((item) => [
      safeId(item && item.asset_id),
      safeText(item && item.kind),
      safeText(item && item.visual_class),
      safeArray(item && item.character_ids).map(safeId),
    ]);
    if (canonicalJson(observedAssets) !== canonicalJson(expectedAssets)) return false;
    const constrainedProvider = safeId(request.provider_id);
    if (constrainedProvider && safeId(decision.selected_provider_id) !== constrainedProvider) return false;
    if (safeId(quote.provider_id) !== safeId(decision.selected_provider_id)) return false;
    if (quote.capability !== intent.capability) return false;
    if (spendGuard.max_credits !== (request.max_credits ?? null)) return false;
    return spendGuard.quoted_credits === quote.credits && spendGuard.currency === "provider_credits";
  }

  function publicErrorMessage(error) {
    if (error && error.name === "AbortError") return "";
    if (error && error.publicMessage) return error.publicMessage;
    return "目前連不上 Castle 服務，請稍後再試。";
  }

  async function requestJson(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      cache: "no-store",
      ...options,
      headers: {
        Accept: "application/json",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(options.headers || {}),
      },
    });
    let data = null;
    try {
      data = await response.json();
    } catch (_error) {
      data = null;
    }
    if (!response.ok) {
      if (response.status === 401) {
        window.dispatchEvent(new CustomEvent("castle-auth-required"));
      }
      const message = safeText(data && data.error && data.error.message);
      const error = new Error("Castle request failed");
      error.publicMessage = message || "操作沒有完成，Castle 沒有變更任何設定。";
      error.status = response.status;
      throw error;
    }
    if (!data || typeof data !== "object" || Array.isArray(data)) {
      const error = new Error("Castle response was invalid");
      error.publicMessage = "Castle 回傳的資料格式不正確，已停止後續操作。";
      throw error;
    }
    return data;
  }

  function statusCopy(status) {
    return STATUS_COPY[safeText(status)] || Object.freeze({
      label: "需要確認",
      detail: "這個狀態尚未被介面識別，因此不會自動啟用。",
      tone: "attention",
    });
  }

  function providerMeta(providerId) {
    return PROVIDER_META[providerId] || Object.freeze({
      label: providerId || "未知來源",
      badge: "Provider",
      tone: "unknown",
    });
  }

  function visualClassLabel(value) {
    return {
      anime: "動漫角色",
      realistic: "真實人物",
      illustration: "插畫角色",
      unknown: "尚未分類",
    }[value] || "尚未分類";
  }

  function handoffActionLabel(actionId) {
    return {
      "h3-image-video": "讓圖片動起來",
      "h3-reference-video": "依照參考做影片",
      "image-video": "讓圖片動起來",
    }[actionId] || "接著製作";
  }

  function capabilityLabel(capability) {
    return {
      image_to_video: "參考圖影片",
      image_to_image: "用圖片生成新圖片",
      text_to_image: "用文字生成圖片",
      text_to_video: "用文字生成影片",
      lip_sync: "對嘴影片",
      dance: "跳舞影片",
      multi_subject_video: "參考圖影片",
      text_to_speech: "文字轉語音",
      voice_clone: "模仿音色",
    }[capability] || "生成內容";
  }

  function mediaKindLabel(kind) {
    return { image: "圖片", video: "影片", audio: "聲音", text: "文字" }[kind] || "素材";
  }

  function intentAssetLabel(asset) {
    const assetId = safeId(asset?.asset_id);
    const kind = mediaKindLabel(safeId(asset?.kind));
    if (assetId.startsWith("provider-version:")) return `${kind}（媒體庫版本已鎖定）`;
    if (assetId.startsWith("production-media:")) return `${kind}（作品版本已鎖定）`;
    if (assetId.startsWith("castle-character:") && assetId.endsWith(":avatar")) return "角色大頭貼";
    return kind;
  }

  function optionSummaryNodes(options) {
    if (!options || typeof options !== "object" || Array.isArray(options)) return [];
    return Object.entries(options).map(([key, value]) => node("div", {}, [
      node("dt", { text: handoffOptionLabel(key) }),
      node("dd", { text: handoffOptionValueLabel(key, value) }),
    ]));
  }

  function handoffOptionLabel(key) {
    return {
      aspect: "畫面比例",
      aspect_ratio: "畫面比例",
      duration_seconds: "影片秒數",
      mode: "生成模式",
      motion_profile: "動作風格",
      quality: "品質",
      resolution: "解析度",
      size: "輸出尺寸",
      strength: "變化強度",
    }[key] || key;
  }

  function handoffOptionValueLabel(key, value) {
    const labels = {
      aspect: { portrait: "直式", landscape: "橫式", square: "正方形" },
      aspect_ratio: { "16:9": "橫式 16:9", "9:16": "直式 9:16" },
      mode: { anime: "動漫", standard: "標準", hd_4k: "高畫質", v2: "V2", v3: "V3" },
      motion_profile: {
        gentle: "柔和動作", expressive: "明顯動作",
        natural: "自然動作", presenter: "講解／主持",
      },
      quality: { quality: "品質優先" },
    };
    return labels[key]?.[value] || value;
  }

  function untitledMediaLabel(kind) {
    return {
      image: "未命名圖片",
      video: "未命名影片",
      audio: "未命名聲音",
      text: "未命名文字",
    }[kind] || "未命名內容";
  }

  function characterInitials(value) {
    const label = safeText(value, "?");
    return Array.from(label).slice(0, 2).join("").toUpperCase() || "?";
  }

  function normalizeProviderCharacter(raw) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw) || containsSensitiveField(raw)) return null;
    const characterId = safeId(raw.character_id);
    const visualClass = safeText(raw.visual_class);
    if (!characterId || !["anime", "realistic", "illustration", "unknown"].includes(visualClass)) return null;
    const sourceProviderIds = [...new Set(safeArray(raw.source_provider_ids).map(safeId).filter(Boolean))];
    const profileAssetId = safeId(raw.profile_asset_id);
    return Object.freeze({
      scope: "provider",
      id: characterId,
      label: safeText(raw.display_name, characterId),
      visualClass,
      avatarUrl: providerProfileMediaUrl(raw.profile_url, profileAssetId),
      profileAssetId,
      sourceProviderIds,
      catalogOnly: raw.catalog_only === true || visualClass === "unknown",
      generationEnabled: false,
      generationBlocker: safeId(raw.generation_blocker),
      updatedAt: safeText(raw.updated_at),
    });
  }

  function normalizeAsset(raw, catalogScope = "provider") {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
    const assetId = safeId(raw.asset_id || raw.assetId || raw.id);
    const kind = safeText(raw.kind);
    if (!assetId || !MEDIA_KINDS.some((item) => item.id === kind)) return null;
    const providerId = safeId(raw.provider_id || raw.providerId || raw.origin) || "unknown";
    const visualClass = ["anime", "realistic", "illustration", "unknown"].includes(raw.visual_class)
      ? raw.visual_class
      : "unknown";
    const characterIds = safeArray(raw.character_ids || raw.characterIds)
      .map(safeId)
      .filter(Boolean);
    const catalog = raw.catalog && typeof raw.catalog === "object" && !Array.isArray(raw.catalog)
      ? raw.catalog : {};
    const audioRole = ["unclassified", "music", "sound_effect", "voice", "ambience"].includes(catalog.audio_role)
      ? catalog.audio_role : "unclassified";
    const mediaRoles = (MEDIA_ROLE_OPTIONS[kind] || []).map(([value]) => value);
    const mediaRole = mediaRoles.includes(catalog.media_role) ? catalog.media_role : "unclassified";
    const catalogRevision = Number.isSafeInteger(catalog.revision) && catalog.revision >= 0
      ? catalog.revision : 0;
    const metadata = raw.metadata && typeof raw.metadata === "object" && !Array.isArray(raw.metadata)
      ? raw.metadata : {};
    const localHistoryKey = safeId(raw.localHistoryKey || metadata.local_history_key);
    const classificationAvailable = raw.classification_available === true
      && catalogScope !== "castle" && providerId === "autoip"
      && ["image", "video"].includes(kind) && visualClass === "unknown"
      && characterIds.length === 0 && raw.source_available === true
      && raw.preparation_available === true && Boolean(safeId(raw.asset_version_id));
    return Object.freeze({
      assetId,
      kind,
      catalogScope: catalogScope === "castle" ? "castle" : "provider",
      providerId,
      visualClass,
      characterIds: [...new Set(characterIds)],
      title: safeText(raw.title || raw.display_name || raw.name, untitledMediaLabel(kind)),
      catalogDisplayName: safeText(catalog.display_name, "", 160),
      audioRole,
      mediaRole,
      catalogRevision,
      createdAt: safeText(raw.created_at || raw.createdAt),
      previewUrl: catalogScope === "castle"
        ? sameOriginMediaUrl(raw.preview_url || raw.previewUrl || raw.thumbnail_url || raw.thumbnailUrl)
        : providerAssetPreviewUrl(
          raw.preview_url || raw.source_url || raw.previewUrl || raw.sourceUrl || raw.thumbnail_url || raw.thumbnailUrl,
          assetId,
        ),
      excerpt: safeText(raw.excerpt || raw.text_preview || raw.summary),
      state: raw.state === "imported" && raw.source_available === true
        ? "imported"
        : "remote_only",
      sourceAvailable: raw.source_available === true,
      previewAvailable: raw.preview_available === true,
      assetVersionId: safeId(raw.asset_version_id),
      preparationAvailable: raw.preparation_available === true,
      classificationAvailable,
      generationDetailAvailable: raw.generation_detail_available === true,
      localHistoryKey,
      canGenerate: false,
    });
  }

  function normalizeAssetGenerationDetail(raw, asset) {
    if (!raw || raw.schema_version !== "castle.asset-generation-detail.response.v1"
      || raw.ok !== true || raw.asset_id !== asset?.assetId || containsSensitiveField(raw)) return null;
    if (raw.available === false) return Object.freeze({ available: false });
    if (raw.available !== true || !raw.workflow || typeof raw.workflow !== "object") return null;
    const capability = safeId(raw.workflow.capability);
    const providerId = safeId(raw.workflow.provider);
    if (!capability || !providerId) return null;
    const characters = safeArray(raw.characters).slice(0, 16).map((item) => ({
      id: safeId(item?.id), name: safeText(item?.name, safeId(item?.id), 160),
    })).filter((item) => item.id);
    const prompt = {};
    for (const key of ["frame_intent", "observable_action", "camera", "audio_intent", "language", "shot_id"]) {
      const value = safeText(raw.prompt?.[key], "", 4000);
      if (value) prompt[key] = value;
    }
    const options = {};
    for (const [key, value] of Object.entries(raw.options || {})) {
      const safeValue = safeOptionScalar(value);
      if (safeValue !== null) options[key] = safeValue;
    }
    const credit = (value) => Number.isSafeInteger(value) && value >= 0 ? value : null;
    return Object.freeze({
      available: true,
      capability,
      providerId,
      module: safeText(raw.workflow.module, "", 240),
      characters,
      prompt: Object.freeze(prompt),
      options: Object.freeze(options),
      usage: Object.freeze({
        authorized: credit(raw.usage?.authorized_credits),
        charged: credit(raw.usage?.charged_credits),
        remaining: credit(raw.usage?.remaining_credits),
      }),
      state: safeId(raw.state),
      createdAt: safeText(raw.created_at),
      updatedAt: safeText(raw.updated_at),
    });
  }

  function normalizeAssetHandoff(raw, asset) {
    if (!raw || raw.schema_version !== "castle.media-asset-handoff.response.v1"
      || raw.ok !== true || containsSensitiveField(raw) || !asset
      || raw.asset?.asset_id !== asset.assetId
      || raw.asset?.asset_version_id !== asset.assetVersionId
      || !safeId(raw.asset?.planner_asset_id)
      || raw.asset?.kind !== asset.kind || raw.asset?.visual_class !== asset.visualClass
      || JSON.stringify(raw.asset?.character_ids) !== JSON.stringify(asset.characterIds)
      || !Array.isArray(raw.candidates) || raw.candidates.length > 64
      || raw.authority?.owner_scoped !== true
      || raw.authority?.asset_version_server_resolved !== true
      || raw.authority?.profile_catalog_server_resolved !== true
      || raw.authority?.provider_submission_authorized !== false
      || raw.authority?.gpu_execution_authorized !== false
      || raw.authority?.credits_authorized !== 0) return null;
    const candidates = [];
    for (const item of raw.candidates) {
      const profileKey = safeText(item?.profile_key);
      const providerId = safeId(item?.provider_id);
      const variantId = safeId(item?.variant_id);
      const actionId = safeId(item?.action_id);
      const featureId = safeId(item?.feature_id);
      const capability = safeId(item?.capability);
      const expectedMaxCredits = providerId === "local_comfy"
        ? 0
        : (providerId === "autoip" ? null : undefined);
      const reasons = safeArray(item?.unavailable_reason_codes).map(safeId).filter(Boolean);
      const rawOptions = item?.option_values;
      const optionValues = {};
      if (!rawOptions || typeof rawOptions !== "object" || Array.isArray(rawOptions)
        || Object.keys(rawOptions).length > 16) return null;
      for (const [rawKey, rawValues] of Object.entries(rawOptions)) {
        const key = safeId(rawKey);
        const values = safeArray(rawValues).map(safeOptionScalar);
        if (!key || !values.length || values.length > 16
          || values.some((value) => value === null)
          || new Set(values.map(canonicalJson)).size !== values.length) return null;
        optionValues[key] = Object.freeze(values);
      }
      if (!profileKey || !providerId || !variantId || !actionId || !featureId || !capability
        || expectedMaxCredits === undefined
        || item?.visual_class !== asset.visualClass || typeof item?.selectable !== "boolean"
        || (item.selectable && (reasons.length || !item.planner_request))
        || (!item.selectable && item.planner_request !== null)) return null;
      let plannerRequest = null;
      if (item.selectable) {
        const request = item.planner_request;
        const characterBound = asset.characterIds.length > 0;
        if (!request || request.schema_version !== 1
          || request.provider_id !== providerId || request.visual_class !== asset.visualClass
          || request.max_credits !== expectedMaxCredits || !Array.isArray(request.assets)
          || request.assets.length !== 1
          || request.assets[0]?.asset_id !== raw.asset.planner_asset_id
          || request.assets[0]?.kind !== asset.kind
          || JSON.stringify(request.assets[0]?.character_ids) !== JSON.stringify(asset.characterIds)
          || containsSensitiveField(request)) return null;
        if (characterBound) {
          if (request.profile_key !== profileKey
            || request.action_id !== actionId || request.feature_id !== featureId) return null;
        } else if (request.profile_key !== undefined
          || request.action_id !== undefined || request.feature_id !== undefined) return null;
        if (!request.options || typeof request.options !== "object" || Array.isArray(request.options)
          || Object.keys(request.options).length !== Object.keys(optionValues).length
          || Object.entries(optionValues).some(([key, values]) => !values.some(
            (value) => canonicalJson(value) === canonicalJson(request.options[key])
          ))) {
          return null;
        }
        plannerRequest = JSON.parse(JSON.stringify(request));
      }
      candidates.push(Object.freeze({
        profileKey, providerId, variantId, actionId, featureId, capability,
        selectable: item.selectable, reasons: Object.freeze(reasons),
        optionValues: Object.freeze(optionValues), plannerRequest,
      }));
    }
    return Object.freeze(candidates);
  }

  function normalizeLocalHistoryAsset(raw) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw) || raw.hidden) return null;
    const kind = typeof raw.kind === "string" ? raw.kind : "";
    if (!["image", "video", "audio"].includes(kind)) return null;
    const filename = safeText(raw.filename);
    const subfolder = safeText(raw.subfolder);
    const viewType = safeText(raw.type, "output");
    const previewUrl = localHistoryMediaUrl(raw.url);
    if (!filename || !previewUrl) return null;
    const attribution = trustedLocalHistoryAttribution(raw);
    const asset = normalizeAsset({
      asset_id: deterministicLocalAssetId(viewType, subfolder, filename),
      kind,
      provider_id: "local_comfy",
      visual_class: attribution.visualClass,
      character_ids: attribution.characterId ? [attribution.characterId] : [],
      title: filename,
      preview_url: previewUrl,
      can_generate: false,
    }, "castle");
    if (!asset) return null;
    return Object.freeze({
      ...asset,
      localSourceLocator: localHistorySourceLocator(raw),
    });
  }

  function normalizeClipModePreview(raw) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
    if (
      raw.schema_version !== SCHEMA_VERSION
      || raw.provider_id !== "clipmode"
      || raw.mode !== "offline_preview"
      || raw.confirmation_available !== false
      || raw.provider_transport_performed !== false
      || raw.source_derived_records_mutated !== false
      || raw.source_persisted !== false
      || raw.archive_extracted !== false
      || raw.credits_spent !== 0
    ) return null;
    const source = raw.source && typeof raw.source === "object" && !Array.isArray(raw.source)
      ? raw.source
      : {};
    const account = raw.account && typeof raw.account === "object" && !Array.isArray(raw.account)
      ? raw.account
      : {};
    const plan = raw.plan && typeof raw.plan === "object" && !Array.isArray(raw.plan)
      ? raw.plan
      : {};
    const summary = raw.summary && typeof raw.summary === "object" && !Array.isArray(raw.summary)
      ? raw.summary
      : {};
    const normalizedClasses = (value) => {
      const counts = value && typeof value === "object" && !Array.isArray(value) ? value : {};
      return Object.freeze({
        anime: safeCount(counts.anime),
        realistic: safeCount(counts.realistic),
        illustration: safeCount(counts.illustration),
        unknown: safeCount(counts.unknown),
      });
    };
    const actionCounts = plan.counts && typeof plan.counts === "object" && !Array.isArray(plan.counts)
      ? plan.counts
      : {};
    const sourceKind = ["json", "zip"].includes(source.source_kind) ? source.source_kind : "";
    const countKeys = ["create", "update", "noop", "conflict"];
    if (
      !sourceKind
      || typeof raw.ready !== "boolean"
      || countKeys.some((key) => safeCount(actionCounts[key]) !== actionCounts[key])
      || raw.ready !== (actionCounts.conflict === 0)
      || (raw.ready && (account.scope_matches !== true || account.scope_bound !== true))
    ) return null;
    const characters = safeArray(raw.characters).slice(0, 100).map((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return null;
      const visualClass = safeText(item.visual_class);
      const recordId = safeId(item.record_id);
      if (!recordId || !["anime", "realistic", "illustration", "unknown"].includes(visualClass)) return null;
      return Object.freeze({
        recordId,
        displayName: safeText(item.display_name, "未命名角色"),
        visualClass,
        catalogOnly: item.catalog_only === true,
        hasProfileMedia: item.has_profile_media === true,
      });
    }).filter(Boolean);
    const conflicts = safeArray(raw.conflicts).slice(0, 50).map((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return null;
      const code = safeId(item.code);
      const resource = safeId(item.resource);
      const message = safeText(item.message);
      return code && resource && message ? Object.freeze({ code, resource, message }) : null;
    }).filter(Boolean);
    const truncated = raw.truncated && typeof raw.truncated === "object" && !Array.isArray(raw.truncated)
      ? raw.truncated
      : {};
    const accountId = safeId(account.account_id);
    const providerScope = safeId(account.provider_scope);
    const planHash = safeSha256(plan.plan_hash);
    const sourceHash = safeSha256(source.semantic_sha256);
    if (!accountId || !providerScope || !planHash || !sourceHash) return null;
    return Object.freeze({
      ready: raw.ready === true,
      source: Object.freeze({
        kind: sourceKind,
        exportId: safeText(source.export_id, "未命名匯出"),
        exportedAt: safeText(source.exported_at),
        semanticSha256: sourceHash,
      }),
      account: Object.freeze({
        accountId,
        providerScope,
        scopeMatches: account.scope_matches === true,
        scopeBound: account.scope_bound === true,
      }),
      plan: Object.freeze({
        planHash,
        counts: Object.freeze({
          create: safeCount(actionCounts.create),
          update: safeCount(actionCounts.update),
          noop: safeCount(actionCounts.noop),
          conflict: safeCount(actionCounts.conflict),
        }),
      }),
      summary: Object.freeze({
        characters: safeCount(summary.characters),
        media: safeCount(summary.media),
        lineage: safeCount(summary.lineage),
        duplicates: safeCount(summary.duplicates),
        characterClasses: normalizedClasses(summary.character_visual_classes),
        mediaKinds: Object.freeze({
          image: safeCount(summary.media_kinds && summary.media_kinds.image),
          video: safeCount(summary.media_kinds && summary.media_kinds.video),
          audio: safeCount(summary.media_kinds && summary.media_kinds.audio),
          text: safeCount(summary.media_kinds && summary.media_kinds.text),
        }),
      }),
      characters,
      conflicts,
      truncated: Object.freeze({
        characters: truncated.characters === true,
        conflicts: truncated.conflicts === true,
      }),
    });
  }

  function normalizeOwnerImportStage(raw) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
    const stageId = typeof raw.stage_id === "string" && /^stage_[0-9a-f]{32}$/.test(raw.stage_id)
      ? raw.stage_id
      : "";
    const stageToken = safeOpaqueToken(raw.stage_token, 256);
    const review = raw.default_review && typeof raw.default_review === "object"
      && !Array.isArray(raw.default_review) ? raw.default_review : {};
    const summary = review.summary && typeof review.summary === "object"
      && !Array.isArray(review.summary) ? review.summary : {};
    const suggested = raw.suggested_decision;
    const suggestedProfiles = suggested && typeof suggested === "object"
      && !Array.isArray(suggested) && Array.isArray(suggested.profile_selections)
      ? suggested.profile_selections : null;
    const normalizedProfiles = suggestedProfiles && suggestedProfiles.map((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return null;
      const sourceCharacterKey = safeId(item.source_character_key);
      const mediaRecordId = safeId(item.media_record_id);
      if (!sourceCharacterKey || !mediaRecordId) return null;
      return Object.freeze({
        source_character_key: sourceCharacterKey,
        media_record_id: mediaRecordId,
      });
    });
    if (
      raw.schema_version !== SCHEMA_VERSION
      || !stageId
      || !stageToken
      || !["application/json", "application/zip"].includes(raw.content_type)
      || safeCount(raw.byte_size, CLIPMODE_PREVIEW_MAX_BYTES) !== raw.byte_size
      || raw.confirmation_available !== false
      || raw.provider_transport_performed !== false
      || raw.credits_spent !== 0
      || review.schema_version !== SCHEMA_VERSION
      || review.provider_id !== "clipmode"
      || review.mode !== "decision_substrate"
      || review.decision_validated !== true
      || review.store_snapshot_validated !== false
      || review.confirmation_available !== false
      || review.commit_available !== false
      || review.provider_store_mutated !== false
      || review.source_persisted !== false
      || review.media_materialized !== false
      || review.provider_transport_performed !== false
      || review.credits_spent !== 0
      || !suggested
      || suggested.schema_version !== SCHEMA_VERSION
      || !Array.isArray(suggested.character_mappings)
      || suggested.character_mappings.length !== 0
      || !normalizedProfiles
      || normalizedProfiles.some((item) => !item)
      || new Set(normalizedProfiles.map((item) => item.source_character_key)).size
        !== normalizedProfiles.length
    ) return null;
    return Object.freeze({
      stageId,
      stageToken,
      expiresAt: safeCount(raw.expires_at, Number.MAX_SAFE_INTEGER),
      byteSize: raw.byte_size,
      suggestedDecision: Object.freeze({
        schema_version: SCHEMA_VERSION,
        character_mappings: Object.freeze([]),
        profile_selections: Object.freeze(normalizedProfiles),
      }),
      summary: Object.freeze({
        characters: safeCount(summary.characters),
        media: safeCount(summary.media),
        lineage: safeCount(summary.lineage),
        bundledMedia: safeCount(summary.bundled_media),
        remoteOnlyMedia: safeCount(summary.remote_only_media),
        unknownClassBlockers: safeCount(summary.unknown_class_blockers),
      }),
    });
  }

  function normalizeOwnerImportPlan(raw) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
    const decisionToken = safeOpaqueToken(raw.decision_token);
    const summary = raw.summary && typeof raw.summary === "object" && !Array.isArray(raw.summary)
      ? raw.summary : {};
    if (
      raw.schema_version !== SCHEMA_VERSION
      || raw.provider_id !== "clipmode"
      || raw.mode !== "store_snapshot_plan"
      || raw.decision_validated !== true
      || raw.store_snapshot_validated !== true
      || raw.confirmation_available !== true
      || raw.commit_available !== true
      || raw.provider_store_mutated !== false
      || raw.filesystem_mutated !== false
      || raw.media_materialized !== false
      || typeof raw.profile_rendition_available !== "boolean"
      || raw.provider_transport_performed !== false
      || raw.credits_spent !== 0
      || !decisionToken
    ) return null;
    return Object.freeze({
      decisionToken,
      expiresAt: safeCount(raw.expires_at, Number.MAX_SAFE_INTEGER),
      summary: Object.freeze({
        characters: safeCount(summary.characters),
        projections: safeCount(summary.projections),
        versions: safeCount(summary.versions),
        lineage: safeCount(summary.lineage),
        profileSelections: safeCount(summary.profile_selections),
      }),
      profileRenditionAvailable: raw.profile_rendition_available === true,
    });
  }

  function normalizeOwnerImportReceipt(raw) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
    const receiptId = typeof raw.receipt_id === "string" && /^receipt:[0-9a-f]{32}$/.test(raw.receipt_id)
      ? raw.receipt_id : "";
    const counts = raw.membership_counts && typeof raw.membership_counts === "object"
      && !Array.isArray(raw.membership_counts) ? raw.membership_counts : {};
    if (
      raw.schema_version !== SCHEMA_VERSION
      || !receiptId
      || raw.catalog_state !== "available"
      || raw.provider_transport_performed !== false
      || raw.credits_spent !== 0
    ) return null;
    return Object.freeze({
      receiptId,
      replayed: raw.replayed === true,
      counts: Object.freeze(Object.fromEntries(
        Object.entries(counts).slice(0, 20).map(([key, value]) => [safeId(key), safeCount(value)])
          .filter(([key]) => key),
      )),
    });
  }

  function normalizeAutoIPProbe(raw, expectedAccountId) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
    const reasons = safeArray(raw.reason_codes).map(safeId).filter(Boolean).slice(0, 20);
    const health = raw.health && typeof raw.health === "object" && !Array.isArray(raw.health)
      ? raw.health : {};
    const availableCreditsValid = raw.available_credits === null
      || (Number.isInteger(raw.available_credits) && raw.available_credits >= 0);
    if (
      raw.schema_version !== SCHEMA_VERSION
      || raw.provider_id !== "autoip"
      || safeId(raw.account_id) !== expectedAccountId
      || health.provider_id !== "autoip"
      || typeof raw.ready !== "boolean"
      || !availableCreditsValid
      || typeof health.provider_reachable !== "boolean"
      || typeof health.api_healthy !== "boolean"
      || typeof health.authenticated !== "boolean"
      || typeof raw.generation_enabled !== "boolean"
      || raw.generation_enabled !== raw.ready
      || raw.provider_write_performed !== false
      || raw.credits_spent !== 0
      || (raw.ready === true && (
        raw.compatibility_receipt?.schema_version !== "castle.autoip-compatibility-receipt.v1"
        || !/^autoip-probe:[0-9a-f]{32}$/.test(raw.compatibility_receipt?.receipt_id || "")
        || raw.compatibility_receipt?.persisted !== true
      ))
    ) return null;
    return Object.freeze({
      ready: raw.ready,
      reasons: Object.freeze(reasons),
      reachable: health.provider_reachable,
      healthy: health.api_healthy,
      authenticated: health.authenticated,
      availableCredits: raw.available_credits === null
        ? null : safeCount(raw.available_credits, Number.MAX_SAFE_INTEGER),
      pricingVerified: raw.pricing !== null,
      receiptId: raw.compatibility_receipt?.receipt_id || null,
    });
  }

  function normalizeAutoIPCatalog(raw, expectedAccountId) {
    const authority = raw?.authority;
    const history = raw?.history;
    if (
      !raw || raw.schema_version !== "castle.autoip-catalog.response.v1" || raw.ok !== true
      || raw.provider_id !== "autoip" || safeId(raw.account_id) !== expectedAccountId
      || !history || !Array.isArray(history.tasks) || !Array.isArray(raw.dance_templates)
      || authority?.owner_scoped !== true
      || authority?.compatibility_receipt_checked !== true
      || authority?.provider_write_performed !== false || authority?.credits_spent !== 0
      || authority?.provider_urls_disclosed !== false || authority?.templates_selectable !== false
      || typeof history.complete !== "boolean"
      || !Number.isInteger(history.pages_fetched) || history.pages_fetched < 1 || history.pages_fetched > 20
    ) return null;
    const tasks = history.tasks.map((item) => {
      const taskId = safeText(item?.task_uuid, "", 64);
      const status = safeId(item?.status);
      if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(taskId)
        || !["created", "submitted", "pending", "queued", "processing", "running", "completed", "failed", "cancelled", "timeout"].includes(status)) return null;
      if (typeof item.adopted !== "boolean") return null;
      return Object.freeze({ taskId, status, adopted: item.adopted });
    });
    const templates = raw.dance_templates.map((item) => {
      if (!Number.isInteger(item?.template_id) || item.template_id < 1
        || !safeText(item?.name, "", 200) || !Number.isInteger(item.duration_seconds)
        || item.duration_seconds < 1 || item.duration_seconds > 3600
        || item.preview_available !== false || item.runtime_ready !== false || item.selectable !== false) return null;
      return Object.freeze({ id: item.template_id, name: safeText(item.name, "", 200), duration: item.duration_seconds });
    });
    if (tasks.some((item) => !item) || templates.some((item) => !item)) return null;
    return Object.freeze({
      syncedAt: safeText(raw.synced_at, "", 40),
      complete: history.complete,
      total: history.total === null ? null : safeCount(history.total, Number.MAX_SAFE_INTEGER),
      pagesFetched: history.pages_fetched,
      tasks: Object.freeze(tasks), templates: Object.freeze(templates),
    });
  }

  function normalizeAutoIPHistoryAdoption(raw, expectedAccountId, expectedTaskId) {
    if (
      !raw || raw.schema_version !== "castle.autoip-history-adoption.response.v2" || raw.ok !== true
      || raw.provider_id !== "autoip" || safeId(raw.account_id) !== expectedAccountId
      || raw.task_uuid !== expectedTaskId || raw.classification_inferred !== false
      || raw.provider_write_performed !== false || raw.credits_spent !== 0
      || !Array.isArray(raw.assets) || !raw.assets.length || raw.assets.length > 16
      || containsSensitiveField(raw)
    ) return null;
    const assets = raw.assets.map((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)
        || !Array.isArray(item.character_ids)) return null;
      const projectionId = safeId(item?.projection_id);
      const versionId = safeId(item?.asset_version_id);
      const visualClass = safeId(item?.visual_class);
      const rawCharacters = safeArray(item?.character_ids);
      const characterIds = [...new Set(rawCharacters.map(safeId).filter(Boolean))].sort();
      const known = ["anime", "realistic", "illustration"].includes(visualClass);
      if (!projectionId || !versionId || (!known && visualClass !== "unknown")
        || characterIds.length !== rawCharacters.length || characterIds.length > 4
        || (known && (!characterIds.length || item?.classification_required !== false))
        || (!known && (characterIds.length || item?.classification_required !== true))) return null;
      return Object.freeze({
        projectionId, versionId, visualClass,
        characterIds: Object.freeze(characterIds),
        classificationRequired: !known,
      });
    });
    if (assets.some((item) => !item)) return null;
    const projectionIds = assets.map((item) => item.projectionId);
    const versionIds = assets.map((item) => item.versionId);
    if (new Set(projectionIds).size !== assets.length
      || new Set(versionIds).size !== assets.length) return null;
    return Object.freeze(assets);
  }

  function normalizeAutoIPGoldenCandidates(raw, expectedAccountId) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)
      || raw.schema_version !== SCHEMA_VERSION || safeId(raw.account_id) !== expectedAccountId
      || typeof raw.compatibility_current !== "boolean"
      || raw.provider_transport_performed !== false || raw.credits_spent !== 0
      || !Array.isArray(raw.candidates)) return null;
    const candidates = raw.candidates.map((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return null;
      const projectionId = safeId(item.projection_id);
      const cellId = safeId(item.cell_id);
      const variantId = safeId(item.variant_id);
      const capability = safeId(item.capability);
      const visualClass = safeText(item.visual_class);
      const kind = safeText(item.media_kind);
      if (!projectionId || !cellId || !variantId || !capability
        || !["anime", "realistic"].includes(visualClass)
        || !["image", "video", "audio", "text"].includes(kind)) return null;
      return Object.freeze({ projectionId, cellId, variantId, capability, visualClass, kind,
        characterIds: Object.freeze(safeArray(item.character_ids).map(safeId).filter(Boolean)),
        previewUrl: providerAssetPreviewUrl(item.preview_url, projectionId) });
    });
    if (candidates.some((item) => !item)) return null;
    return Object.freeze({ compatibilityCurrent: raw.compatibility_current, candidates: Object.freeze(candidates) });
  }

  function normalizeIntentStatus(raw, expected) {
    const exactFields = [
      "schema_version", "intent_id", "provider_id", "capability", "visual_class", "state",
      "terminal", "retry_available", "transport_may_have_occurred",
      "provider_receipt_available", "output_verified", "credits_authorized", "credits_charged",
      "remaining_credits", "created_at", "updated_at", "status_check_transport_performed",
      "status_check_credits_spent",
    ];
    if (!hasExactKeys(raw, exactFields) || !expected) return null;
    const intentId = safeIntentId(raw.intent_id);
    const providerId = safeId(raw.provider_id);
    const capability = safeId(raw.capability);
    const visualClass = safeText(raw.visual_class);
    const state = safeText(raw.state);
    const createdAt = safeText(raw.created_at);
    const updatedAt = safeText(raw.updated_at);
    const terminal = TERMINAL_INTENT_STATES.includes(state);
    const creditsAuthorized = raw.credits_authorized;
    const creditsCharged = raw.credits_charged;
    const remainingCredits = raw.remaining_credits;
    if (
      raw.schema_version !== SCHEMA_VERSION
      || !intentId
      || raw.intent_id !== intentId
      || intentId !== expected.intentId
      || !providerId
      || raw.provider_id !== providerId
      || providerId !== expected.providerId
      || !capability
      || raw.capability !== capability
      || capability !== expected.capability
      || !["anime", "realistic", "illustration"].includes(visualClass)
      || raw.visual_class !== visualClass
      || visualClass !== expected.visualClass
      || !INTENT_STATUS_STATES.includes(state)
      || raw.state !== state
      || raw.terminal !== terminal
      || raw.retry_available !== false
      || typeof raw.transport_may_have_occurred !== "boolean"
      || typeof raw.provider_receipt_available !== "boolean"
      || typeof raw.output_verified !== "boolean"
      || (raw.output_verified && state !== "completed")
      || !Number.isSafeInteger(creditsAuthorized)
      || creditsAuthorized < 0
      || creditsAuthorized !== expected.creditsAuthorized
      || (creditsCharged !== null && (!Number.isSafeInteger(creditsCharged) || creditsCharged < 0))
      || (remainingCredits !== null && (!Number.isSafeInteger(remainingCredits) || remainingCredits < 0))
      || !createdAt
      || raw.created_at !== createdAt
      || !updatedAt
      || raw.updated_at !== updatedAt
      || raw.status_check_transport_performed !== false
      || raw.status_check_credits_spent !== 0
      || (raw.provider_receipt_available && !raw.transport_may_have_occurred)
      || (state === "pending"
        && (raw.transport_may_have_occurred || raw.provider_receipt_available))
      || (state !== "pending" && state !== "abandoned"
        && !raw.transport_may_have_occurred)
      || (["submitted", "queued", "running", "completed", "failed", "timeout"].includes(state)
        && !raw.provider_receipt_available)
    ) return null;
    return Object.freeze({
      intentId,
      providerId,
      capability,
      visualClass,
      state,
      terminal,
      retryAvailable: false,
      transportMayHaveOccurred: raw.transport_may_have_occurred,
      providerReceiptAvailable: raw.provider_receipt_available,
      outputVerified: raw.output_verified,
      creditsAuthorized,
      creditsCharged,
      remainingCredits,
      createdAt,
      updatedAt,
      statusChecked: true,
    });
  }

  function normalizeConfirmationResponse(raw, expected) {
    const exactFields = [
      "schema_version", "plan_id", "intent_id", "decision_id", "outbox",
      "client_request_id", "state", "credits_authorized", "credits_spent",
      "transport_dispatched", "next_action",
    ];
    const exactOutboxFields = [
      "outbox_id", "intent_id", "decision_id", "account_id", "client_request_id",
      "state", "attempt_count", "leased", "created_at", "updated_at",
    ];
    if (!hasExactKeys(raw, exactFields) || !expected) return null;
    const outbox = raw.outbox;
    if (!hasExactKeys(outbox, exactOutboxFields)) return null;
    const intentId = safeIntentId(raw.intent_id);
    const decisionId = safeDigestId(raw.decision_id, "route");
    const outboxId = safeDigestId(outbox.outbox_id, "outbox");
    const clientRequestId = safeDigestId(raw.client_request_id, "request");
    const accountId = safeId(outbox.account_id);
    const createdAt = safeText(outbox.created_at);
    const updatedAt = safeText(outbox.updated_at);
    if (
      raw.schema_version !== SCHEMA_VERSION
      || raw.plan_id !== expected.planId
      || !intentId
      || raw.intent_id !== intentId
      || !decisionId
      || raw.decision_id !== decisionId
      || !outboxId
      || outbox.outbox_id !== outboxId
      || !clientRequestId
      || raw.client_request_id !== clientRequestId
      || outbox.intent_id !== intentId
      || outbox.decision_id !== decisionId
      || outbox.client_request_id !== clientRequestId
      || !accountId
      || outbox.account_id !== accountId
      || accountId !== expected.selectedAccountId
      || raw.state !== "pending"
      || outbox.state !== "pending"
      || outbox.attempt_count !== 0
      || outbox.leased !== false
      || !createdAt
      || outbox.created_at !== createdAt
      || !updatedAt
      || outbox.updated_at !== updatedAt
      || updatedAt !== createdAt
      || raw.credits_authorized !== expected.credits
      || raw.credits_spent !== 0
      || raw.transport_dispatched !== false
      || raw.next_action !== "provider_worker_claim_outbox"
    ) return null;
    return Object.freeze({ intentId });
  }

  function assetMatchesFilter(asset, { kind, characterScope, characterId, visualClass, origin, catalogRole }) {
    if (!asset || asset.kind !== kind) return false;
    if (characterScope !== "all" && asset.catalogScope !== characterScope) return false;
    if (characterId && !asset.characterIds.includes(characterId)) return false;
    if (visualClass !== "all" && asset.visualClass !== visualClass) return false;
    if (catalogRole && (asset.kind === "audio" ? asset.audioRole : asset.mediaRole) !== catalogRole) return false;
    return !origin || asset.providerId === origin;
  }

  function mergeAssets(...groups) {
    const byIdentity = new Map();
    const historyIdentities = new Map();
    for (const asset of groups.flat()) {
      if (!asset) continue;
      const identity = `${asset.catalogScope}\u0000${asset.providerId}\u0000${asset.assetId}`;
      const matchingIdentity = asset.localHistoryKey
        ? historyIdentities.get(asset.localHistoryKey)
        : null;
      if (matchingIdentity) {
        const previous = byIdentity.get(matchingIdentity);
        const canonical = asset.catalogScope === "provider" ? asset : previous;
        const local = asset.catalogScope === "castle" ? asset : previous;
        byIdentity.set(matchingIdentity, Object.freeze({
          ...canonical,
          localSourceLocator: local?.localSourceLocator || canonical?.localSourceLocator || null,
        }));
        continue;
      }
      if (!byIdentity.has(identity)) byIdentity.set(identity, asset);
      if (asset.localHistoryKey) historyIdentities.set(asset.localHistoryKey, identity);
    }
    return [...byIdentity.values()];
  }

  class CastleProviderFabric extends HTMLElement {
    constructor() {
      super();
      this._state = {
        providers: [],
        features: {},
        providerCharacters: [],
        assets: [],
        nextCharacterCursor: "",
        nextCursor: "",
        kind: "image",
        characterScope: "all",
        characterId: "",
        visualClass: "all",
        origin: "",
        catalogRole: "",
        providerError: "",
        characterError: "",
        assetError: "",
        loadingProviders: false,
        loadingCharacters: false,
        loadingAssets: false,
        pendingIntent: null,
        generationRequestId: "",
        plan: null,
        planMessage: "",
        confirmedMessage: "",
        confirmationUncertainMessage: "",
        confirmationUncertainPlanId: "",
        lastIntent: null,
        lastIntentMessage: "",
        lastIntentError: false,
        lastIntentRefreshing: false,
        planning: false,
        clipModePreview: null,
        clipModeMessage: "",
        clipModeError: false,
        clipModePreviewing: false,
        clipModeFileName: "",
        clipModeFileBytes: 0,
        clipModeStage: null,
        clipModePlan: null,
        clipModeReceipt: null,
        clipModeWorking: "",
        autoipProbe: null,
        autoipProbeMessage: "",
        autoipProbing: false,
        autoipCredentialSaving: false,
        autoipCatalog: null,
        autoipCatalogLoading: false,
        autoipCatalogMessage: "",
        autoipHistoryAdopting: "",
        autoipHistoryAdopted: [],
        autoipGoldenCandidates: null,
        autoipGoldenSelected: [],
        autoipGoldenLoading: false,
        autoipGoldenMessage: "",
        localBulkAdopting: false,
        localBulkProgress: "",
      };
      this._characters = [];
      this._providerRequest = null;
      this._characterRequest = null;
      this._assetRequest = null;
      this._classificationCharacterCache = new Map();
      this._classificationCharacterRequests = new Map();
      this._assetHandoffs = new Map();
      this._assetGenerationDetails = new Map();
      this._localAdoptions = new Map();
      this._credentialField = null;
      this._accountField = null;
      this._statusRegion = null;
      this._planRequest = null;
      this._intentRevision = 0;
      this._confirming = false;
      this._intentStatusRequest = null;
      this._intentStatusRevision = 0;
      this._clipModeFile = null;
      this._clipModeRequest = null;
      this._clipModeRevision = 0;
      this._mediaOnly = false;
      this._lockedProviderScope = false;
      this._projectQuickRun = false;
      this._projectStatusTimer = null;
      this._technicalReadinessTimers = [];
    }

    connectedCallback() {
      if (this.dataset.providerFabricReady === "true") {
        this.render();
        return;
      }
      this.dataset.providerFabricReady = "true";
      this.classList.add("provider-fabric");
      this.render();
      if (!this._mediaOnly && !this._projectQuickRun) {
        this.refreshProviders();
        // H3 verifies large pinned model files in the background after a
        // service restart.  These are read-only inventory refreshes so the
        // owner sees readiness as soon as that technical audit completes.
        this._technicalReadinessTimers = [20000, 60000, 120000].map((delay) =>
          window.setTimeout(() => {
            if (this.isConnected && !this._mediaOnly) this.refreshProviders();
          }, delay)
        );
      }
      if (!this._projectQuickRun) {
        this.refreshCharacters();
        this.refreshAssets();
      }
      if (this._projectQuickRun && this._state.lastIntent && !this._state.lastIntent.terminal) {
        this._scheduleProjectStatusRefresh({ delay: 0 });
      }
    }

    disconnectedCallback() {
      this._stopProjectStatusRefresh();
      for (const timer of this._technicalReadinessTimers) window.clearTimeout(timer);
      this._technicalReadinessTimers = [];
      if (this._providerRequest) this._providerRequest.abort();
      if (this._characterRequest) this._characterRequest.abort();
      if (this._assetRequest) this._assetRequest.abort();
      this._cancelPlanRequest();
      this._cancelIntentStatusRequest();
      this._invalidateClipModePreview({ clearFile: true });
      this._clearCredentialField();
      this._discardPlan();
      delete this.dataset.providerFabricReady;
    }

    get isAdmin() {
      return this.hasAttribute("admin") || this.getAttribute("role-mode") === "admin";
    }

    get isOperator() {
      const roleMode = this.getAttribute("role-mode");
      return this.hasAttribute("operator") || roleMode === "operator" || roleMode === "admin";
    }

    get mediaOnly() {
      return this._mediaOnly;
    }

    configure(options = {}) {
      if (!options || typeof options !== "object" || Array.isArray(options)) return;
      this._mediaOnly = options.mediaOnly === true;
      this._lockedProviderScope = this._mediaOnly || options.lockedProviderScope === true;
      const initialKind = safeText(options.initialKind);
      if (MEDIA_KINDS.some((item) => item.id === initialKind)) this._state.kind = initialKind;
      if (this._lockedProviderScope) {
        this._state.characterScope = "provider";
        this._state.characterId = "";
        if (this._state.origin === "local_comfy") this._state.origin = "";
      }
      if (Array.isArray(options.characters)) {
        this._characters = options.characters
          .map((item) => ({
            scope: "castle",
            id: safeId(item && (item.id || item.character_id)),
            label: safeText(item && (item.label || item.display_name || item.name)),
            visualClass: ["anime", "realistic", "illustration", "unknown"].includes(item && item.visualClass)
              ? item.visualClass
              : "unknown",
            avatarUrl: sameOriginMediaUrl(item && item.avatarUrl),
          }))
          .filter((item) => item.id)
          .slice(0, 500);
      }
      const requestedCharacter = safeId(options.characterId);
      const requestedScope = safeText(options.characterScope);
      if (
        requestedCharacter
        && ["castle", "provider"].includes(requestedScope)
        && (!this._lockedProviderScope || requestedScope === "provider")
      ) {
        this._state.characterId = requestedCharacter;
        this._state.characterScope = requestedScope;
      }
      if (this._lockedProviderScope) {
        this._state.characterScope = "provider";
        if (requestedScope !== "provider") this._state.characterId = "";
      }
      if (options.isAdmin === true) this.setAttribute("admin", "");
      if (options.isAdmin === false) this.removeAttribute("admin");
      if (options.isOperator === true) this.setAttribute("operator", "");
      if (options.isOperator === false) this.removeAttribute("operator");
      if (!this.isAdmin) this._invalidateClipModePreview({ clearFile: true });
      if (options.pendingIntent) {
        this._projectQuickRun = options.autoStartPendingIntent === true;
        const prepared = this.preparePaidAction(options.pendingIntent);
        if (prepared && options.autoStartPendingIntent === true) {
          const pendingIntent = this._state.pendingIntent;
          const revision = this._intentRevision;
          queueMicrotask(() => {
            if (
              !this.isConnected
              || this._mediaOnly
              || revision !== this._intentRevision
              || this._state.pendingIntent !== pendingIntent
            ) return;
            this._requestPlan({ autoConfirm: true });
          });
        }
        return;
      }
      this.render();
    }

    focusSection(section) {
      if (!this.isAdmin || this._mediaOnly) return false;
      if (section === "autoip") {
        requestAnimationFrame(() => {
          const form = this.querySelector('[aria-labelledby="provider-fabric-credential-title"]');
          const account = this.querySelector("#provider-fabric-account");
          const credential = this.querySelector("#provider-fabric-credential");
          const input = account && !account.value ? account : credential;
          if (!form || !input) return;
          form.scrollIntoView({ behavior: "smooth", block: "center" });
          input.focus({ preventScroll: true });
          form.classList.add("is-entry-highlighted");
          form.addEventListener(
            "animationend",
            () => form.classList.remove("is-entry-highlighted"),
            { once: true },
          );
        });
        return true;
      }
      if (section !== "clipmode") return false;
      requestAnimationFrame(() => {
        const fieldset = this.querySelector('[data-provider-section="clipmode"]');
        const input = this.querySelector("#provider-fabric-clipmode-file");
        if (!fieldset || !input) return;
        fieldset.scrollIntoView({ behavior: "smooth", block: "start" });
        input.focus({ preventScroll: true });
        fieldset.classList.add("is-entry-highlighted");
        fieldset.addEventListener(
          "animationend",
          () => fieldset.classList.remove("is-entry-highlighted"),
          { once: true },
        );
      });
      return true;
    }

    preparePaidAction(intent) {
      if (this._mediaOnly) return false;
      if (this._confirming) {
        this._state.planMessage = this._projectQuickRun
          ? "正在建立這個鏡頭，請稍候。"
          : "正在確認上一份報價，完成前不會切換生成內容。";
        this.render();
        return false;
      }
      if (this._state.confirmationUncertainPlanId) {
        this._state.planMessage = this._projectQuickRun
          ? "Castle 正在核對剛才的工作，你不需要再按一次。"
          : "Castle 正在核對剛才的工作；核對完成前不需要再次操作。";
        this.render();
        return false;
      }
      this._discardPlan();
      if (!intent || typeof intent !== "object" || Array.isArray(intent)) {
        this._state.planMessage = "缺少可以規劃的生成內容。";
        this.render();
        return false;
      }
      const visualClass = safeText(intent.visual_class || intent.visualClass);
      if (visualClass === "unknown" || !["anime", "realistic", "illustration"].includes(visualClass)) {
        this._state.planMessage = "角色尚未分類，只能整理到媒體庫，不能進入生成。";
        this.render();
        return false;
      }
      if (containsSensitiveField(intent)) {
        this._state.planMessage = "生成內容不可包含金鑰、Cookies 或其他登入憑證。";
        this.render();
        return false;
      }
      let clone;
      try {
        clone = JSON.parse(JSON.stringify(intent));
      } catch (_error) {
        this._state.planMessage = "生成內容格式不正確。";
        this.render();
        return false;
      }
      this._state.pendingIntent = clone;
      this._state.generationRequestId = newGenerationRequestId();
      this._state.confirmedMessage = "";
      this._state.planMessage = this._projectQuickRun
        ? "鏡頭資料已收到，Castle 會自動開始製作。"
        : "內容已準備好；按生成後會直接排入工作。";
      this.render();
      return true;
    }

    async refreshProviders() {
      if (this._mediaOnly) return;
      if (this._providerRequest) this._providerRequest.abort();
      const controller = new AbortController();
      this._providerRequest = controller;
      this._state.loadingProviders = true;
      this._state.providerError = "";
      this.render();
      try {
        const data = await requestJson(PROVIDERS_ENDPOINT, { signal: controller.signal });
        if (controller.signal.aborted || this._providerRequest !== controller) return;
        this._state.providers = safeArray(data.providers).filter(
          (item) => item && typeof item === "object" && safeId(item.provider_id),
        );
        this._state.features = data.features && typeof data.features === "object" && !Array.isArray(data.features)
          ? data.features
          : {};
      } catch (error) {
        if (controller.signal.aborted || this._providerRequest !== controller) return;
        this._state.providers = [];
        this._state.features = {};
        this._state.providerError = publicErrorMessage(error);
      } finally {
        if (this._providerRequest === controller) {
          this._state.loadingProviders = false;
          this._providerRequest = null;
          this.render();
        }
      }
    }

    async refreshCharacters({ append = false } = {}) {
      if (this._characterRequest) this._characterRequest.abort();
      const controller = new AbortController();
      this._characterRequest = controller;
      const visualClass = this._state.visualClass;
      const existing = append ? [...this._state.providerCharacters] : [];
      const cursor = append ? this._state.nextCharacterCursor : "";
      this._state.loadingCharacters = true;
      this._state.characterError = "";
      if (!append) {
        this._state.providerCharacters = [];
        this._state.nextCharacterCursor = "";
      }
      this.render();
      try {
        const url = new URL(CHARACTERS_ENDPOINT, window.location.origin);
        if (visualClass !== "all") url.searchParams.set("visualClass", visualClass);
        if (append && cursor) url.searchParams.set("cursor", cursor);
        const data = await requestJson(`${url.pathname}${url.search}`, { signal: controller.signal });
        if (controller.signal.aborted || this._characterRequest !== controller) return;
        if (data.provider_transport_performed !== false || data.credits_spent !== 0) {
          throw Object.assign(new Error("Unsafe character catalog response"), {
            publicMessage: "角色目錄沒有證明這次只做唯讀整理，已拒絕顯示。",
          });
        }
        const incoming = safeArray(data.characters).map(normalizeProviderCharacter).filter(Boolean);
        const byIdentity = new Map();
        for (const character of [...existing, ...incoming]) {
          if (!byIdentity.has(character.id)) byIdentity.set(character.id, character);
        }
        this._state.providerCharacters = [...byIdentity.values()];
        this._state.nextCharacterCursor = safeText(data.next_cursor);
      } catch (error) {
        if (controller.signal.aborted || this._characterRequest !== controller) return;
        if (append) {
          // A transient load-more failure must not strand the remaining
          // owner-scoped roster. Keep both the trusted page and its cursor so
          // the same explicit action can be retried.
          this._state.providerCharacters = existing;
          this._state.nextCharacterCursor = cursor;
        } else {
          this._state.providerCharacters = [];
          this._state.nextCharacterCursor = "";
        }
        this._state.characterError = publicErrorMessage(error);
      } finally {
        if (this._characterRequest === controller) {
          this._state.loadingCharacters = false;
          this._characterRequest = null;
          this.render();
        }
      }
    }

    _abortCatalogReads({ characters = true } = {}) {
      if (characters && this._characterRequest) this._characterRequest.abort();
      if (this._assetRequest) this._assetRequest.abort();
      if (characters) this._characterRequest = null;
      this._assetRequest = null;
      if (characters) this._state.loadingCharacters = false;
      this._state.loadingAssets = false;
    }

    _changeCatalogContext({ characterScope, characterId = "", visualClass, origin, catalogRole, reloadCharacters = false }) {
      const nextScope = this._lockedProviderScope
        ? "provider"
        : (["all", "castle", "provider"].includes(characterScope)
          ? characterScope
          : this._state.characterScope);
      const nextVisualClass = VISUAL_CLASS_FILTERS.some((item) => item.id === visualClass)
        ? visualClass
        : this._state.visualClass;
      const nextOrigin = origin === undefined ? this._state.origin : safeId(origin);
      const roleOptions = this._state.kind === "audio"
        ? ["unclassified", "music", "sound_effect", "voice", "ambience"]
        : (MEDIA_ROLE_OPTIONS[this._state.kind] || []).map(([value]) => value);
      const nextCatalogRole = catalogRole === undefined
        ? this._state.catalogRole
        : (roleOptions.includes(catalogRole) ? catalogRole : "");
      const nextCharacterId = nextScope === "all" ? "" : safeId(characterId);
      this._abortCatalogReads({ characters: reloadCharacters });
      this._discardPlan();
      this._state.characterScope = nextScope;
      this._state.characterId = nextCharacterId;
      this._state.visualClass = nextVisualClass;
      this._state.origin = nextOrigin;
      this._state.catalogRole = nextCatalogRole;
      if (nextScope === "castle" && !["", "local_comfy"].includes(this._state.origin)) this._state.origin = "";
      if (nextScope === "provider" && this._state.origin === "local_comfy") this._state.origin = "";
      this._state.assets = [];
      this._state.nextCursor = "";
      this._state.assetError = "";
      this._state.planMessage = "";
      this._state.confirmedMessage = "";
      this.render();
      if (reloadCharacters) this.refreshCharacters();
      this.refreshAssets();
    }

    async refreshAssets({ append = false } = {}) {
      if (this._assetRequest) this._assetRequest.abort();
      const controller = new AbortController();
      this._assetRequest = controller;
      const filter = Object.freeze({
        kind: this._state.kind,
        characterScope: this._state.characterScope,
        characterId: this._state.characterId,
        visualClass: this._state.visualClass,
        origin: this._state.origin,
        catalogRole: this._state.catalogRole,
      });
      const existingAssets = append ? [...this._state.assets] : [];
      const cursor = append ? this._state.nextCursor : "";
      const loadLocalHistory = !append
        && filter.characterScope !== "provider"
        && (!filter.origin || filter.origin === "local_comfy");
      const loadRemoteCatalog = filter.characterScope !== "castle"
        && filter.origin !== "local_comfy"
        && (!append || Boolean(cursor));
      this._state.loadingAssets = true;
      this._state.assetError = "";
      if (!append) {
        this._state.assets = [];
        this._state.nextCursor = "";
      }
      this.render();
      try {
        const reads = [];
        if (loadLocalHistory) {
          reads.push(requestJson(LOCAL_HISTORY_ENDPOINT, { signal: controller.signal })
            .then((data) => ({ source: "local", data }))
            .catch((error) => ({ source: "local", error })));
        }
        if (loadRemoteCatalog) {
          const url = new URL(ASSETS_ENDPOINT, window.location.origin);
          url.searchParams.set("kind", filter.kind);
          if (filter.characterScope === "provider" && filter.characterId) {
            url.searchParams.set("characterId", filter.characterId);
          }
          if (filter.visualClass !== "all") url.searchParams.set("visualClass", filter.visualClass);
          if (filter.origin) url.searchParams.set("origin", filter.origin);
          if (filter.catalogRole) url.searchParams.set("catalogRole", filter.catalogRole);
          if (append && cursor) url.searchParams.set("cursor", cursor);
          reads.push(requestJson(`${url.pathname}${url.search}`, { signal: controller.signal })
            .then((data) => ({ source: "remote", data }))
            .catch((error) => ({ source: "remote", error })));
        }
        const results = await Promise.all(reads);
        if (controller.signal.aborted || this._assetRequest !== controller) return;
        let localAssets = [];
        let remoteAssets = [];
        let remoteResult = null;
        const errors = [];
        for (const result of results) {
          if (result.error) {
            const message = publicErrorMessage(result.error);
            if (message) errors.push(message);
            continue;
          }
          if (result.source === "local") {
            localAssets = safeArray(result.data.images)
              .map(normalizeLocalHistoryAsset)
              .filter((asset) => assetMatchesFilter(asset, filter));
          } else if (result.source === "remote") {
            remoteResult = result.data;
            if (
              remoteResult.provider_transport_performed !== false
              || remoteResult.credits_spent !== 0
            ) {
              throw Object.assign(new Error("Unsafe asset catalog response"), {
                publicMessage: "外部素材目錄沒有證明這次只做唯讀整理，已拒絕顯示。",
              });
            }
            remoteAssets = safeArray(result.data.assets || result.data.items)
              .map(normalizeAsset)
              .filter((asset) => assetMatchesFilter(asset, filter));
          }
        }
        this._state.assets = mergeAssets(existingAssets, localAssets, remoteAssets);
        if (remoteResult) {
          this._state.nextCursor = safeText(remoteResult.next_cursor || remoteResult.nextCursor);
        } else if (!append) {
          this._state.nextCursor = "";
        }
        this._state.assetError = [...new Set(errors)].join(" ");
      } catch (error) {
        if (controller.signal.aborted || this._assetRequest !== controller) return;
        this._state.assetError = publicErrorMessage(error);
      } finally {
        if (this._assetRequest === controller) {
          this._state.loadingAssets = false;
          this._assetRequest = null;
          this.render();
        }
      }
    }

    render() {
      if (!this.isConnected) return;
      if (this._mediaOnly) {
        const root = node("div", { className: "provider-fabric__inner provider-fabric__inner--media-only" });
        this._statusRegion = node("div", {
          className: "provider-fabric__live",
          attributes: { role: "status", "aria-live": "polite", "aria-atomic": "true" },
        });
        root.append(this._statusRegion, this._renderMediaLibrary());
        this.replaceChildren(root);
        this.setAttribute("aria-labelledby", "provider-fabric-media");
        return;
      }
      const headingId = "provider-fabric-heading";
      const root = node("div", { className: "provider-fabric__inner" });
      root.append(
        node("header", { className: "provider-fabric__hero" }, [
          node("div", {}, [
            node("p", {
              className: "provider-fabric__eyebrow",
              text: this._projectQuickRun ? "Castle 專案試做" : "Castle Provider Fabric",
            }),
            node("h1", {
              id: headingId,
              text: this._projectQuickRun ? "正在製作這個鏡頭" : "角色創作資源中心",
            }),
            node("p", {
              className: "provider-fabric__lead",
              text: this._projectQuickRun
                ? "Castle 會自動選擇可用方式並建立工作；留在這裡看進度即可。"
                : "同一個角色、同一份素材，在本機 5090、AutoIP 與離線匯入之間保持清楚來源與安全邊界。",
            }),
          ]),
          node("div", { className: "provider-fabric__safety", attributes: { role: "note" } }, [
            node("strong", { text: this._projectQuickRun ? "已交給 Castle" : "預設安全模式" }),
            node("span", {
              text: this._projectQuickRun
                ? "不需要另外操作，也不需要再次按生成。"
                : "不會自動上傳、不會自動花點，也不會讀取網站 Cookies。",
            }),
          ]),
        ]),
      );
      this._statusRegion = node("div", {
        className: "provider-fabric__live",
        attributes: { role: "status", "aria-live": "polite", "aria-atomic": "true" },
      });
      root.append(this._statusRegion);
      if (this._projectQuickRun) root.append(this._renderPaidAction());
      else root.append(this._renderProviders(), this._renderMediaLibrary(), this._renderPaidAction());
      this.replaceChildren(root);
      this.setAttribute("aria-labelledby", headingId);
    }

    _renderProviders() {
      const section = node("section", {
        className: "provider-fabric__section",
        attributes: { "aria-labelledby": "provider-fabric-providers" },
      });
      const refresh = node("button", {
        className: "provider-fabric__button provider-fabric__button--quiet",
        text: this._state.loadingProviders ? "更新中…" : "重新整理狀態",
        type: "button",
        attributes: { disabled: this._state.loadingProviders ? "" : null },
      });
      refresh.addEventListener("click", () => this.refreshProviders());
      section.append(node("div", { className: "provider-fabric__section-head" }, [
        node("div", {}, [
          node("h2", { id: "provider-fabric-providers", text: "生成資源" }),
          node("p", { text: "先看清楚每個來源現在能不能用；未通過的項目一律不會被自動選中。" }),
        ]),
        refresh,
      ]));
      if (this._state.providerError) {
        section.append(node("div", {
          className: "provider-fabric__notice provider-fabric__notice--error",
          text: this._state.providerError,
          attributes: { role: "alert" },
        }));
      }
      const providerById = new Map(this._state.providers.map((item) => [safeId(item.provider_id), item]));
      const cards = node("div", { className: "provider-fabric__provider-grid" });
      for (const providerId of ["local_comfy", "autoip", "clipmode"]) {
        const provider = providerById.get(providerId) || {
          provider_id: providerId,
          status: this._state.loadingProviders ? "unavailable" : "unavailable",
          execution_enabled: false,
        };
        cards.append(this._renderProviderCard(provider));
      }
      section.append(cards, this._renderH3Feature());
      if (this.isAdmin) section.append(this._renderCredentialForm());
      if (this.isAdmin) section.append(this._renderAutoIPProbe());
      if (this.isAdmin) section.append(this._renderAutoIPCatalog());
      if (this.isAdmin) section.append(this._renderClipModeImport());
      return section;
    }

    _renderProviderCard(provider) {
      const providerId = safeId(provider.provider_id) || "unknown";
      const meta = providerMeta(providerId);
      const status = statusCopy(provider.status);
      const enabled = provider.execution_enabled === true;
      const card = node("article", {
        className: `provider-fabric__provider provider-fabric__provider--${meta.tone}`,
        attributes: { "data-provider": providerId },
      });
      card.append(
        node("div", { className: "provider-fabric__provider-head" }, [
          node("span", { className: `provider-fabric__badge provider-fabric__badge--${meta.tone}`, text: meta.badge }),
          node("span", {
            className: `provider-fabric__status provider-fabric__status--${status.tone}`,
            text: status.label,
          }),
        ]),
        node("h3", { text: meta.label }),
        node("p", { text: status.detail }),
        node("p", {
          className: "provider-fabric__availability",
          text: enabled ? "可納入生成路由" : "不會執行生成",
        }),
      );
      if (providerId === "autoip" && provider.credential_readback === false) {
        card.append(node("p", {
          className: "provider-fabric__write-only",
          text: "金鑰採只寫保存，介面與 API 都不提供讀回。",
        }));
        if (
          provider.compatibility_receipt_current === true
          && provider.compatibility_receipt?.schema_version === "castle.autoip-compatibility-receipt.v1"
          && /^autoip-probe:[0-9a-f]{32}$/.test(provider.compatibility_receipt.receipt_id || "")
        ) card.append(node("p", {
          className: "provider-fabric__write-only",
          text: "連線、帳號與價格相容性檢查仍在有效期限內；AutoIP 可直接列入正常生成規劃。",
        }));
      }
      if (providerId === "autoip" && !safeId(provider.account_id)) {
        const connect = node("button", {
          className: "provider-fabric__button provider-fabric__button--quiet",
          type: "button",
          text: "連接 AutoIP",
        });
        connect.addEventListener("click", () => this.querySelector("#provider-fabric-credential-title")?.scrollIntoView({ behavior: "smooth", block: "center" }));
        card.append(connect);
      }
      if (providerId === "clipmode") card.append(node("p", {
        className: "provider-fabric__write-only",
        text: provider.status === "offline_import_ready"
          ? "支援 castle.clipmode-export.v1；Live sync 維持關閉，匯入不會呼叫 ClipMode。"
          : "需要 ClipMode JSON／ZIP 匯出包；Castle 不會讀取瀏覽器 Cookie。",
      }));
      if (providerId === "clipmode") {
        const importButton = node("button", {
          className: "provider-fabric__button provider-fabric__button--quiet",
          type: "button",
          text: "匯入角色與作品",
        });
        importButton.addEventListener("click", () => this.querySelector("#provider-fabric-clipmode")?.scrollIntoView({ behavior: "smooth", block: "start" }));
        card.append(importButton);
      }
      return card;
    }

    _renderH3Feature() {
      const raw = this._state.features.minimax_h3;
      const feature = raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
      const blocked = feature.license_blocked !== false || feature.status === "license_blocked";
      const status = blocked ? statusCopy("license_blocked") : statusCopy(feature.status || "runtime_not_ready");
      const gates = [
        ["授權紀錄", feature.authorization_recorded === true],
        ["鎖定模型", feature.models_ready === true],
        ["原生節點", feature.runtime_ready === true],
        ["功能技術相容性", feature.contracts && Object.values(feature.contracts).every((value) => value === true)],
      ];
      return node("article", {
        className: `provider-fabric__feature-callout provider-fabric__feature-callout--${status.tone}`,
        attributes: { role: blocked ? "alert" : "status" },
      }, [
        node("div", {}, [
          node("span", { className: "provider-fabric__badge provider-fabric__badge--h3", text: "MiniMax H3" }),
          node("h3", { text: blocked ? "MiniMAX H3 目前不會執行" : "MiniMAX H3 狀態" }),
          node("p", { text: status.detail }),
          node("ul", { className: "provider-fabric__h3-gates" }, gates.map(([label, ready]) =>
            node("li", { text: `${ready ? "✓" : "○"} ${label}${ready ? "已通過" : "待完成"}` })
          )),
        ]),
        node("span", {
          className: `provider-fabric__status provider-fabric__status--${status.tone}`,
          text: status.label,
        }),
      ]);
    }

    _renderCredentialForm() {
      const current = this._state.providers.find((item) => item.provider_id === "autoip") || {};
      const currentAccountId = safeId(current.account_id);
      const form = node("form", {
        className: "provider-fabric__credential",
        attributes: { "aria-labelledby": "provider-fabric-credential-title", novalidate: "" },
      });
      const accountId = node("input", {
        attributes: {
          id: "provider-fabric-account",
          inputmode: "latin",
          autocomplete: "off",
          maxlength: "128",
          placeholder: "例如：local-autoip",
          required: "",
          value: currentAccountId || "local-autoip",
        },
      });
      const credential = node("input", {
        attributes: {
          id: "provider-fabric-credential",
          type: "password",
          autocomplete: "new-password",
          spellcheck: "false",
          minlength: "12",
          maxlength: "4096",
          placeholder: "貼上 AutoIP API Key",
          required: "",
        },
      });
      this._accountField = accountId;
      this._credentialField = credential;
      form.append(
        node("div", { className: "provider-fabric__form-copy" }, [
          node("h3", { id: "provider-fabric-credential-title", text: "連接 AutoIP" }),
          node("p", { text: "金鑰只寫保存、不提供讀回；按一次就會接著完成零扣點連線、帳號與價格檢查。" }),
        ]),
        node("div", { className: "provider-fabric__form-grid" }, [
          node("label", {}, [node("span", { text: "帳號識別碼" }), accountId]),
          node("label", {}, [node("span", { text: "新 API Key" }), credential]),
        ]),
        node("div", { className: "provider-fabric__form-actions" }, [
          node("p", { text: "Key 不會寫進瀏覽器儲存空間；送出後欄位立即清空。" }),
          node("button", {
            className: "provider-fabric__button",
            type: "submit",
            text: this._state.autoipCredentialSaving ? "正在連接…" : "儲存並啟用 AutoIP",
            attributes: { disabled: this._state.autoipCredentialSaving ? "" : null },
          }),
        ]),
        node("ol", { className: "provider-fabric__onboarding-steps" }, [
          node("li", {}, [node("strong", { text: "1. 貼一次 Key" }), node("span", { text: currentAccountId ? "輪替後會直接重新檢查並啟用。" : "只寫保存並建立你的私人帳號設定。" })]),
          node("li", {}, [node("strong", { text: "2. 正常生成" }), node("span", { text: "連線通過後，直接選擇功能、查看即時報價並確認生成。" })]),
        ]),
      );
      form.addEventListener("submit", (event) => this._submitCredential(event));
      return form;
    }

    async _submitCredential(event) {
      event.preventDefault();
      if (
        !this.isAdmin || !this._credentialField || !this._accountField
        || this._state.autoipCredentialSaving
      ) return;
      const accountId = safeId(this._accountField.value);
      let credentialValue = this._credentialField.value;
      if (!accountId) {
        this._announce("帳號識別碼只能使用小寫英數、點、底線、冒號或連字號。", true);
        this._clearCredentialField();
        return;
      }
      if (typeof credentialValue !== "string" || credentialValue.length < 12 || credentialValue.length > 4096) {
        this._announce("請輸入有效的新 API Key。", true);
        this._clearCredentialField();
        return;
      }
      let requestBody = JSON.stringify({
        schema_version: SCHEMA_VERSION,
        provider_id: "autoip",
        account_id: accountId,
        display_name: "AutoIP",
        api_base: "https://autoip.app/api/v1",
        credential_value: credentialValue,
      });
      this._clearCredentialField();
      credentialValue = "";
      this._state.autoipCredentialSaving = true;
      this._state.autoipProbeMessage = "正在只寫保存 Key，接著會自動做零扣點連線檢查…";
      this.render();
      try {
        await requestJson(`/api/providers/autoip/accounts/${pathId(accountId)}/credential`, {
          method: "PUT",
          body: requestBody,
        });
        requestBody = "";
        await this.refreshProviders();
        this._state.autoipCredentialSaving = false;
        await this._requestAutoIPProbe(accountId);
      } catch (error) {
        this._state.autoipProbeMessage = publicErrorMessage(error);
      } finally {
        requestBody = "";
        this._state.autoipCredentialSaving = false;
        this._clearCredentialField();
        this.render();
      }
    }

    _clearCredentialField() {
      if (!this._credentialField) return;
      this._credentialField.value = "";
    }

    _renderAutoIPProbe() {
      const provider = this._state.providers.find((item) => item.provider_id === "autoip") || {};
      const accountId = safeId(provider.account_id);
      const probe = this._state.autoipProbe;
      const section = node("section", {
        className: "provider-fabric__credential",
        attributes: { "aria-labelledby": "provider-fabric-autoip-probe-title" },
      });
      const button = node("button", {
        className: "provider-fabric__button provider-fabric__button--quiet",
        type: "button",
        text: this._state.autoipProbing ? "正在檢查…" : "檢查並啟用 AutoIP",
        attributes: { disabled: !accountId || this._state.autoipProbing ? "" : null },
      });
      button.addEventListener("click", () => this._requestAutoIPProbe(accountId));
      section.append(
        node("div", { className: "provider-fabric__form-copy" }, [
          node("h3", { id: "provider-fabric-autoip-probe-title", text: "AutoIP 連線與啟用" }),
          node("p", { text: "只讀取帳號狀態、剩餘點數與價格表；檢查本身不生成、不上傳、不扣點，通過後會啟用正常生成。" }),
        ]),
        node("div", { className: "provider-fabric__form-actions" }, [
          node("p", { text: accountId ? "已找到你的 AutoIP 帳號。" : "請先安全儲存 AutoIP API Key。" }),
          button,
        ]),
      );
      if (this._state.autoipProbeMessage) {
        section.append(node("div", {
          className: `provider-fabric__notice${probe && !probe.ready ? " provider-fabric__notice--error" : ""}`,
          text: this._state.autoipProbeMessage,
          attributes: { role: probe && !probe.ready ? "alert" : "status" },
        }));
      }
      return section;
    }

    async _requestAutoIPProbe(accountId) {
      if (!this.isAdmin || !safeId(accountId) || this._state.autoipProbing) return;
      this._state.autoipProbing = true;
      this._state.autoipProbe = null;
      this._state.autoipProbeMessage = "正在執行不花點的相容性檢查…";
      this.render();
      try {
        const data = await requestJson(
          `/api/providers/autoip/accounts/${pathId(accountId)}/probe`,
          { method: "POST", body: JSON.stringify({ schema_version: SCHEMA_VERSION }) },
        );
        const probe = normalizeAutoIPProbe(data, accountId);
        if (!probe) throw Object.assign(new Error("Unsafe AutoIP probe response"), {
          publicMessage: "AutoIP 檢查結果缺少完整技術證明，因此沒有啟用。",
        });
        this._state.autoipProbe = probe;
        this._state.autoipProbeMessage = probe.ready
          ? `連線、帳號與價格表均通過；目前剩餘 ${probe.availableCredits ?? "未知"} 點，AutoIP 已可正常使用。`
          : `檢查尚未通過：${probe.reasons.join("、") || "狀態不完整"}。請修正 key、帳號或連線後再試。`;
        if (probe.ready) await this.refreshProviders();
      } catch (error) {
        this._state.autoipProbeMessage = publicErrorMessage(error);
      } finally {
        this._state.autoipProbing = false;
        this.render();
      }
    }

    _renderAutoIPCatalog() {
      const provider = this._state.providers.find((item) => item.provider_id === "autoip") || {};
      const accountId = safeId(provider.account_id);
      const compatible = provider.compatibility_receipt_current === true;
      const catalog = this._state.autoipCatalog;
      const section = node("section", {
        className: "provider-fabric__credential",
        attributes: { "aria-labelledby": "provider-fabric-autoip-catalog-title" },
      });
      const load = node("button", {
        className: "provider-fabric__button provider-fabric__button--quiet", type: "button",
        text: "查看上次同步", attributes: { disabled: !accountId || this._state.autoipCatalogLoading ? "" : null },
      });
      load.addEventListener("click", () => this._loadAutoIPCatalog(accountId, false));
      const sync = node("button", {
        className: "provider-fabric__button", type: "button",
        text: this._state.autoipCatalogLoading ? "同步中…" : "同步歷史與舞蹈模板（零扣點）",
        attributes: { disabled: !accountId || !compatible || this._state.autoipCatalogLoading ? "" : null },
      });
      sync.addEventListener("click", () => this._loadAutoIPCatalog(accountId, true));
      section.append(node("div", { className: "provider-fabric__form-copy" }, [
        node("h3", { id: "provider-fabric-autoip-catalog-title", text: "AutoIP 歷史與模板" }),
        node("p", { text: "只同步 task 狀態和模板名稱／長度；不生成、不下載、不扣點。模板影片下載並納入私人媒體庫後才會出現在可選清單。" }),
      ]), node("div", { className: "provider-fabric__form-actions" }, [load, sync]));
      if (this._state.autoipCatalogMessage) section.append(node("p", {
        className: `provider-fabric__notice${catalog ? "" : " provider-fabric__notice--error"}`,
        text: this._state.autoipCatalogMessage,
      }));
      if (!catalog) return section;
      const counts = new Map();
      for (const item of catalog.tasks) counts.set(item.status, (counts.get(item.status) || 0) + 1);
      section.append(node("div", { className: "provider-fabric__catalog-summary" }, [
        node("strong", { text: `${catalog.total ?? catalog.tasks.length} 筆歷史工作` }),
        node("span", { text: catalog.complete ? "完整同步" : `部分同步 · ${catalog.pagesFetched} 頁` }),
        node("span", { text: [...counts.entries()].map(([status, count]) => `${status} ${count}`).join(" · ") || "沒有工作" }),
      ]));
      const completed = catalog.tasks.filter((item) => item.status === "completed").slice(0, 100);
      if (completed.length) section.append(node("div", { className: "provider-fabric__catalog-list" }, completed.map((item) => {
        const adopted = item.adopted || this._state.autoipHistoryAdopted.includes(item.taskId);
        const adopting = this._state.autoipHistoryAdopting === item.taskId;
        const button = node("button", {
          className: "provider-fabric__button provider-fabric__button--quiet", type: "button",
          text: adopting ? "驗證並下載中…" : adopted ? "已收進媒體庫" : "收進媒體庫（不自動分類）",
          attributes: { disabled: adopting || adopted || this._state.autoipHistoryAdopting ? "" : null },
        });
        button.addEventListener("click", () => this._adoptAutoIPHistoryTask(accountId, item.taskId));
        return node("article", { className: "provider-fabric__catalog-row" }, [
          node("div", {}, [node("strong", { text: `已完成工作 · ${item.taskId.slice(0, 8)}…${item.taskId.slice(-6)}` }),
            node("span", { text: "下載後仍不綁角色、不判斷動漫或真人" })]), button,
        ]);
      })));
      if (!catalog.templates.length) section.append(node("p", { className: "provider-fabric__empty", text: "AutoIP 目前沒有回傳舞蹈模板。" }));
      else section.append(node("div", { className: "provider-fabric__catalog-list" }, catalog.templates.slice(0, 100).map((template) =>
        node("article", { className: "provider-fabric__catalog-row" }, [
          node("div", {}, [node("strong", { text: template.name }), node("span", { text: `${template.duration} 秒` })]),
          node("span", { className: "provider-fabric__status provider-fabric__status--muted", text: "待私有化 · 不可選" }),
        ])
      )));
      return section;
    }

    async _loadAutoIPCatalog(accountId, synchronize) {
      if (!this.isAdmin || !safeId(accountId) || this._state.autoipCatalogLoading) return;
      this._state.autoipCatalogLoading = true;
      this._state.autoipCatalogMessage = synchronize ? "正在零扣點同步 AutoIP 歷史與模板…" : "正在讀取上次同步…";
      this.render();
      try {
        const suffix = synchronize ? "/catalog/sync" : "/catalog";
        const data = await requestJson(`/api/providers/autoip/accounts/${pathId(accountId)}${suffix}`,
          synchronize ? { method: "POST", body: JSON.stringify({ schema_version: SCHEMA_VERSION }) } : {});
        const catalog = normalizeAutoIPCatalog(data, accountId);
        if (!catalog) throw Object.assign(new Error("Unsafe AutoIP catalog"), { publicMessage: "AutoIP 同步結果缺少安全證明，因此沒有載入。" });
        this._state.autoipCatalog = catalog;
        this._state.autoipCatalogMessage = `已整理 ${catalog.tasks.length} 筆 task 與 ${catalog.templates.length} 個模板；模板目前仍不可選。`;
      } catch (error) {
        this._state.autoipCatalog = null;
        this._state.autoipCatalogMessage = publicErrorMessage(error);
      } finally {
        this._state.autoipCatalogLoading = false;
        this.render();
      }
    }

    async _adoptAutoIPHistoryTask(accountId, taskId) {
      if (!this.isAdmin || !safeId(accountId) || this._state.autoipHistoryAdopting
        || !/^[0-9a-f-]{36}$/.test(taskId)) return;
      this._state.autoipHistoryAdopting = taskId;
      this._state.autoipCatalogMessage = "正在從固定 AutoIP 輸出端點下載、驗證檔案並收進私人媒體庫…";
      this.render();
      try {
        const data = await requestJson(
          `/api/providers/autoip/accounts/${pathId(accountId)}/catalog/tasks/${pathId(taskId)}/adopt`,
          { method: "POST", body: JSON.stringify({ schema_version: SCHEMA_VERSION, confirmed: true }) },
        );
        const assets = normalizeAutoIPHistoryAdoption(data, accountId, taskId);
        if (!assets) throw Object.assign(new Error("Unsafe history adoption"), {
          publicMessage: "AutoIP 歷史輸出沒有通過完整性與分類邊界，因此未載入。",
        });
        this._state.autoipHistoryAdopted = [...new Set([...this._state.autoipHistoryAdopted, taskId])];
        const pending = assets.filter((item) => item.classificationRequired).length;
        this._state.autoipCatalogMessage = `已收進 ${assets.length} 個媒體版本；本次沒有自動分類，${pending} 個仍待人工整理，既有分類已保留。`;
        await this.refreshAssets();
      } catch (error) {
        this._state.autoipCatalogMessage = publicErrorMessage(error);
      } finally {
        this._state.autoipHistoryAdopting = "";
        this.render();
      }
    }

    _renderAutoIPGoldenReview() {
      const provider = this._state.providers.find((item) => item.provider_id === "autoip") || {};
      const accountId = safeId(provider.account_id);
      const result = this._state.autoipGoldenCandidates;
      const section = node("section", {
        className: "provider-fabric__credential provider-fabric__golden-review",
        attributes: { "aria-labelledby": "provider-fabric-autoip-golden-title" },
      });
      const refresh = node("button", {
        className: "provider-fabric__button provider-fabric__button--quiet", type: "button",
        text: this._state.autoipGoldenLoading ? "正在整理…" : "整理品質參考",
        attributes: { disabled: !accountId || this._state.autoipGoldenLoading ? "" : null },
      });
      refresh.addEventListener("click", () => this._loadAutoIPGoldenCandidates(accountId));
      section.append(node("div", { className: "provider-fabric__form-copy" }, [
        node("h3", { id: "provider-fabric-autoip-golden-title", text: "AutoIP 輸出品質紀錄（選用）" }),
        node("p", { text: "可把同一設定的兩個結果存成私人品質參考；這不會決定 AutoIP 能不能使用。" }),
      ]), node("div", { className: "provider-fabric__form-actions" }, [refresh]));
      if (this._state.autoipGoldenMessage) section.append(node("p", {
        className: `provider-fabric__notice${result ? "" : " provider-fabric__notice--error"}`,
        text: this._state.autoipGoldenMessage,
      }));
      if (!result) return section;
      if (!result.compatibilityCurrent) {
        section.append(node("p", { className: "provider-fabric__notice provider-fabric__notice--error", text: "請先重新執行零扣點 AutoIP 連線檢查。" }));
        return section;
      }
      const groups = new Map();
      for (const item of result.candidates) {
        if (!groups.has(item.cellId)) groups.set(item.cellId, []);
        groups.get(item.cellId).push(item);
      }
      const eligible = [...groups.entries()].filter(([, items]) => items.length >= 2);
      if (!eligible.length) {
        section.append(node("p", { className: "provider-fabric__empty", text: "目前沒有同一設定的兩個可比較輸出；這項紀錄可以略過。" }));
        return section;
      }
      for (const [cellId, items] of eligible) {
        const group = node("fieldset", { className: "provider-fabric__golden-group" });
        group.append(node("legend", { text: `${visualClassLabel(items[0].visualClass)} · ${items[0].capability.replaceAll("_", " ")}` }));
        const grid = node("div", { className: "provider-fabric__asset-grid" });
        for (const item of items) {
          const selected = this._state.autoipGoldenSelected.includes(item.projectionId);
          const card = node("article", { className: `provider-fabric__asset provider-fabric__golden-candidate${selected ? " is-selected" : ""}` });
          const preview = node("div", { className: "provider-fabric__asset-preview" });
          if (item.kind === "video" && item.previewUrl) preview.append(node("video", { attributes: { src: item.previewUrl, controls: "", preload: "metadata", playsinline: "" } }));
          else if (item.kind === "image" && item.previewUrl) preview.append(node("img", { attributes: { src: item.previewUrl, alt: "", loading: "lazy" } }));
          else preview.append(node("span", { className: "provider-fabric__asset-placeholder", text: item.kind }));
          const choose = node("button", { className: "provider-fabric__button provider-fabric__button--quiet", type: "button", text: selected ? "取消選取" : "選這個結果" });
          choose.addEventListener("click", () => this._toggleAutoIPGoldenCandidate(cellId, item.projectionId, items));
          card.append(preview, node("div", { className: "provider-fabric__asset-body" }, [node("p", { text: selected ? "已選為比較結果" : items[0].variantId }), choose]));
          grid.append(card);
        }
        group.append(grid);
        if (this._state.autoipGoldenSelected.length === 2 && this._state.autoipGoldenSelected.every((id) => items.some((item) => item.projectionId === id))) {
          group.append(this._renderAutoIPGoldenApproval(accountId));
        }
        section.append(group);
      }
      return section;
    }

    _toggleAutoIPGoldenCandidate(cellId, projectionId, groupItems) {
      let selected = this._state.autoipGoldenSelected.filter((id) => groupItems.some((item) => item.projectionId === id));
      selected = selected.includes(projectionId) ? selected.filter((id) => id !== projectionId) : [...selected, projectionId].slice(-2);
      this._state.autoipGoldenSelected = selected;
      this.render();
    }

    _renderAutoIPGoldenApproval(accountId) {
      const form = node("form", { className: "provider-fabric__golden-approval", attributes: { novalidate: "" } });
      const gates = [["format", "格式可正常使用"], ["rights_consent", "權利與同意已確認"], ["visual_class", "動漫／真人分類正確"], ["identity", "角色身分一致"], ["outfit", "服裝符合選擇"]];
      for (const [gate, label] of gates) form.append(node("label", { className: "provider-fabric__consent" }, [node("input", { attributes: { type: "checkbox", name: gate, required: "" } }), node("span", { text: label })]));
      const phrase = node("input", { attributes: { autocomplete: "off", placeholder: "輸入 I_APPROVE_BOTH_AUTOIP_OUTPUTS", required: "" } });
      const submit = node("button", { className: "provider-fabric__button provider-fabric__button--danger", type: "submit", text: "保存這兩個品質參考" });
      form.append(phrase, submit);
      form.addEventListener("submit", (event) => { event.preventDefault(); this._submitAutoIPGolden(accountId, form, phrase.value); });
      return form;
    }

    async _loadAutoIPGoldenCandidates(accountId) {
      if (!this.isAdmin || !safeId(accountId) || this._state.autoipGoldenLoading) return;
      this._state.autoipGoldenLoading = true; this._state.autoipGoldenMessage = "正在從統一媒體庫核對 task 與輸出來源…"; this.render();
      try {
        const data = await requestJson(`/api/providers/autoip/accounts/${pathId(accountId)}/golden-candidates`);
        const normalized = normalizeAutoIPGoldenCandidates(data, accountId);
        if (!normalized) throw Object.assign(new Error("Unsafe golden candidates"), { publicMessage: "候選輸出證據不完整，因此沒有建立品質紀錄。" });
        this._state.autoipGoldenCandidates = normalized; this._state.autoipGoldenSelected = [];
        this._state.autoipGoldenMessage = `已找到 ${normalized.candidates.length} 個可追溯輸出。`;
      } catch (error) { this._state.autoipGoldenCandidates = null; this._state.autoipGoldenMessage = publicErrorMessage(error); }
      finally { this._state.autoipGoldenLoading = false; this.render(); }
    }

    async _submitAutoIPGolden(accountId, form, phrase) {
      const gates = ["format", "rights_consent", "visual_class", "identity", "outfit"];
      if (phrase !== "I_APPROVE_BOTH_AUTOIP_OUTPUTS" || !gates.every((gate) => form.elements[gate]?.checked)) {
        this._state.autoipGoldenMessage = "請完成五項確認並輸入完整核准字串。"; this.render(); return;
      }
      const evidence = Object.fromEntries(gates.map((gate) => [gate, this._state.autoipGoldenSelected.map((id) => `operator-ui:${gate}:${id}`)]));
      try {
        const data = await requestJson(`/api/providers/autoip/accounts/${pathId(accountId)}/goldens`, { method: "POST", body: JSON.stringify({ schema_version: 1, projection_ids: this._state.autoipGoldenSelected, gate_evidence: evidence, review_evidence_ids: this._state.autoipGoldenSelected.map((id) => `operator-ui:review:${id}`), acceptance_phrase: phrase }) });
        if (!/^golden:autoip:[0-9a-f]{32}$/.test(data.golden_id || "") || data.activatable !== true) throw new Error("Unsafe golden response");
        this._state.autoipGoldenMessage = "私人品質參考已保存；AutoIP 是否可用仍只看 key、帳號、API 與目前相容性。"; this._state.autoipGoldenSelected = [];
        await this._loadProviders();
      } catch (error) { this._state.autoipGoldenMessage = publicErrorMessage(error); this.render(); }
    }

    _renderClipModeImport() {
      const fieldset = node("fieldset", {
        className: "provider-fabric__clipmode",
        attributes: {
          id: "provider-fabric-clipmode",
          "aria-describedby": "provider-fabric-clipmode-help",
          "data-provider-section": "clipmode",
        },
      });
      const fileInput = node("input", {
        className: "provider-fabric__clipmode-native-file",
        attributes: {
          id: "provider-fabric-clipmode-file",
          type: "file",
          accept: ".json,.zip,application/json,application/zip",
        },
      });
      const dropZone = node("label", {
        className: "provider-fabric__clipmode-dropzone",
        attributes: { for: "provider-fabric-clipmode-file" },
      }, [
        fileInput,
        node("span", { className: "provider-fabric__clipmode-drop-icon", text: "⇧" }),
        node("strong", {
          text: this._state.clipModeFileName ? "更換匯出包" : "拖放 ClipMode 匯出包到這裡",
        }),
        node("span", {
          text: this._state.clipModeFileName
            ? `${this._state.clipModeFileName} · ${this._formatBytes(this._state.clipModeFileBytes)}`
            : "或點一下選擇 JSON／ZIP（上限 64 MiB）",
        }),
      ]);
      const previewButton = node("button", {
        className: "provider-fabric__button",
        type: "button",
        text: this._state.clipModeWorking === "stage"
          ? "正在安全分析…"
          : (this._state.clipModeStage ? "重新分析檔案" : "分析並建立暫存"),
        attributes: {
          disabled: !this._clipModeFile || this._state.clipModeWorking ? "" : null,
        },
      });
      const confirmButton = node("button", {
        className: "provider-fabric__button",
        type: "button",
        text: this._state.clipModeWorking === "confirm" ? "正在匯入…" : "確認匯入媒體庫",
        attributes: {
          disabled: !this._state.clipModePlan || this._state.clipModeWorking ? "" : null,
          "aria-describedby": "provider-fabric-clipmode-confirm-help",
        },
      });
      const planButton = node("button", {
        className: "provider-fabric__button provider-fabric__button--quiet",
        type: "button",
        text: this._state.clipModeWorking === "plan" ? "正在核對差異…" : "核對最後差異",
        attributes: {
          disabled: !this._state.clipModeStage || this._state.clipModeReceipt
            || this._state.clipModeWorking ? "" : null,
        },
      });
      const cancelButton = node("button", {
        className: "provider-fabric__button provider-fabric__button--quiet",
        type: "button",
        text: this._state.clipModeWorking === "cancel" ? "正在取消…" : "取消這次匯入",
        attributes: {
          disabled: !this._state.clipModeStage || this._state.clipModeReceipt
            || this._state.clipModeWorking ? "" : null,
        },
      });
      fileInput.addEventListener("change", () => this._selectClipModeFile(fileInput));
      dropZone.addEventListener("dragover", (event) => {
        event.preventDefault();
        if (!this._state.clipModeWorking) dropZone.classList.add("is-dragging");
      });
      dropZone.addEventListener("dragleave", () => dropZone.classList.remove("is-dragging"));
      dropZone.addEventListener("drop", (event) => {
        event.preventDefault();
        dropZone.classList.remove("is-dragging");
        if (!this._state.clipModeWorking) this._selectClipModeFile(event.dataTransfer);
      });
      previewButton.addEventListener("click", () => this._requestClipModeStage());
      planButton.addEventListener("click", () => this._requestClipModePlan());
      confirmButton.addEventListener("click", () => this._confirmClipModeImport());
      cancelButton.addEventListener("click", () => this._cancelClipModeImport());
      fieldset.append(
        node("legend", { text: "ClipMode 離線匯入" }),
        node("p", {
          id: "provider-fabric-clipmode-help",
          text: "選擇符合 Castle 公開格式、由你建立或由服務提供的 JSON／ZIP。Castle 會先建立短效私有暫存，再讓你核對差異；不接受 Cookies、瀏覽器工作階段或私有接口。",
        }),
        node("details", { className: "provider-fabric__clipmode-pack-help" }, [
          node("summary", { text: "還沒有 Castle 匯出包？用本機檔案安全打包" }),
          node("p", {
            text: "先下載清單範本，填入你已下載的角色與媒體；再於 Web_Comfyui 執行下列工具。工具不會登入 ClipMode，也不會讀 Cookies。",
          }),
          node("a", {
            className: "provider-fabric__button provider-fabric__button--quiet",
            text: "下載打包清單範本",
            attributes: {
              href: "/static/clipmode-pack-input.example.json",
              download: "clipmode-pack-input.example.json",
            },
          }),
          node("code", {
            text: "python3 scripts/pack_clipmode_export.py --inventory INVENTORY.json --media-root MEDIA_DIR --output clipmode-export.zip",
          }),
        ]),
        node("ol", { className: "provider-fabric__clipmode-steps", attributes: { "aria-label": "ClipMode 匯入進度" } }, [
          ["選擇匯出包", Boolean(this._clipModeFile)],
          ["安全分析", Boolean(this._state.clipModeStage)],
          ["核對差異", Boolean(this._state.clipModePlan || this._state.clipModeReceipt)],
          ["匯入媒體庫", Boolean(this._state.clipModeReceipt)],
        ].map(([label, done], index) => node("li", {
          className: done ? "is-done" : "",
          text: `${done ? "✓" : index + 1} ${label}`,
        }))),
        dropZone,
        node("div", { className: "provider-fabric__clipmode-actions" }, [
          previewButton,
          planButton,
          confirmButton,
          cancelButton,
        ]),
        node("p", {
          id: "provider-fabric-clipmode-confirm-help",
          className: "provider-fabric__clipmode-boundary",
          text: "只有按下「確認匯入媒體庫」才會寫入。整個流程不呼叫 ClipMode、不花點；暫存有期限，取消後立即失效。",
        }),
      );
      if (this._state.clipModeFileName) {
        fieldset.append(node("p", {
          className: "provider-fabric__clipmode-file",
          text: `已選擇：${this._state.clipModeFileName}（${this._formatBytes(this._state.clipModeFileBytes)}）`,
        }));
      }
      if (this._state.clipModeMessage) {
        fieldset.append(node("div", {
          className: `provider-fabric__notice${this._state.clipModeError ? " provider-fabric__notice--error" : ""}`,
          text: this._state.clipModeMessage,
          attributes: { role: this._state.clipModeError ? "alert" : "status" },
        }));
      }
      if (this._state.clipModePreview) {
        fieldset.append(this._renderClipModePreviewResult(this._state.clipModePreview));
      }
      if (this._state.clipModeStage) {
        fieldset.append(this._renderOwnerImportReview());
      }
      return fieldset;
    }

    _selectClipModeFile(input) {
      if (this._mediaOnly || !this.isAdmin) return;
      this._invalidateClipModePreview({ clearFile: true });
      const file = input && input.files && input.files[0];
      if (!file) {
        this._state.clipModeMessage = "尚未選擇檔案。";
        this.render();
        return;
      }
      const name = safeText(file.name);
      const extension = name.toLowerCase().endsWith(".json")
        ? "json"
        : (name.toLowerCase().endsWith(".zip") ? "zip" : "");
      if (!name || !extension) {
        this._state.clipModeMessage = "只接受副檔名為 .json 或 .zip 的離線匯出檔。";
        this._state.clipModeError = true;
        this.render();
        return;
      }
      if (!Number.isInteger(file.size) || file.size <= 0 || file.size > CLIPMODE_PREVIEW_MAX_BYTES) {
        this._state.clipModeMessage = "檔案必須大於 0，且不可超過 64 MiB。";
        this._state.clipModeError = true;
        this.render();
        return;
      }
      this._clipModeFile = file;
      this._state.clipModeFileName = name;
      this._state.clipModeFileBytes = file.size;
      this._state.clipModeMessage = "檔案已選好；下一步先分析內容並建立短效私有暫存。";
      this.render();
    }

    _clipModeDecision() {
      const suggested = this._state.clipModeStage && this._state.clipModeStage.suggestedDecision;
      return suggested ? {
        schema_version: SCHEMA_VERSION,
        character_mappings: [],
        profile_selections: suggested.profile_selections.map((item) => ({ ...item })),
      } : { schema_version: SCHEMA_VERSION, character_mappings: [], profile_selections: [] };
    }

    async _requestClipModeStage() {
      const file = this._clipModeFile;
      if (this._mediaOnly || !this.isAdmin || !file || this._clipModeRequest) return;
      const revision = this._clipModeRevision;
      const controller = new AbortController();
      this._clipModeRequest = controller;
      this._state.clipModePreviewing = true;
      this._state.clipModePreview = null;
      this._state.clipModeStage = null;
      this._state.clipModePlan = null;
      this._state.clipModeReceipt = null;
      this._state.clipModeWorking = "stage";
      this._state.clipModeMessage = "正在驗證檔案並建立只屬於你的短效暫存…";
      this._state.clipModeError = false;
      this.render();
      try {
        const contentType = file.name.toLowerCase().endsWith(".zip")
          ? "application/zip"
          : "application/json";
        const data = await requestJson(CLIPMODE_STAGE_ENDPOINT, {
          method: "POST",
          body: file,
          headers: { "Content-Type": contentType },
          signal: controller.signal,
        });
        if (
          controller.signal.aborted
          || this._clipModeRequest !== controller
          || this._clipModeRevision !== revision
          || this._clipModeFile !== file
        ) return;
        const staged = normalizeOwnerImportStage(data);
        if (!staged) {
          throw Object.assign(new Error("Unsafe ClipMode stage response"), {
            publicMessage: "後端沒有提供完整的安全暫存證明，已停止流程。",
          });
        }
        this._state.clipModeStage = staged;
        this._state.clipModeMessage = "分析完成，尚未寫入媒體庫。請核對數量，再建立最後差異計畫。";
      } catch (error) {
        if (
          controller.signal.aborted
          || this._clipModeRequest !== controller
          || this._clipModeRevision !== revision
          || this._clipModeFile !== file
        ) return;
        this._state.clipModeStage = null;
        this._state.clipModeMessage = publicErrorMessage(error);
        this._state.clipModeError = true;
      } finally {
        if (this._clipModeRequest === controller) {
          this._clipModeRequest = null;
          this._state.clipModePreviewing = false;
          this._state.clipModeWorking = "";
          this.render();
        }
      }
    }

    async _requestClipModePlan() {
      const staged = this._state.clipModeStage;
      if (!staged || this._state.clipModeWorking || !this.isAdmin) return;
      this._state.clipModeWorking = "plan";
      this._state.clipModePlan = null;
      this._state.clipModeMessage = "正在依目前媒體庫重新核對角色、版本與血緣關係…";
      this.render();
      try {
        const data = await requestJson(`${CLIPMODE_STAGE_ENDPOINT}/${pathId(staged.stageId)}/plan`, {
          method: "POST",
          body: JSON.stringify({
            schema_version: SCHEMA_VERSION,
            stage_token: staged.stageToken,
            decision: this._clipModeDecision(),
          }),
        });
        if (this._state.clipModeStage !== staged) return;
        const plan = normalizeOwnerImportPlan(data);
        if (!plan) throw Object.assign(new Error("Unsafe import plan response"), {
          publicMessage: "最後差異計畫缺少安全證明，因此沒有開放確認。",
        });
        this._state.clipModePlan = plan;
        this._state.clipModeMessage = "最後差異已鎖定；確認後才會一次寫入角色與媒體庫。";
      } catch (error) {
        if (this._state.clipModeStage !== staged) return;
        this._state.clipModeMessage = publicErrorMessage(error);
        this._state.clipModeError = true;
      } finally {
        this._state.clipModeWorking = "";
        this.render();
      }
    }

    async _confirmClipModeImport() {
      const staged = this._state.clipModeStage;
      const plan = this._state.clipModePlan;
      if (!staged || !plan || this._state.clipModeWorking || !this.isAdmin) return;
      this._state.clipModeWorking = "confirm";
      this._state.clipModeMessage = "正在以單一交易寫入角色、素材與來源關係…";
      this.render();
      try {
        const data = await requestJson(`${CLIPMODE_STAGE_ENDPOINT}/${pathId(staged.stageId)}/confirm`, {
          method: "POST",
          body: JSON.stringify({
            schema_version: SCHEMA_VERSION,
            stage_token: staged.stageToken,
            decision_token: plan.decisionToken,
            decision: this._clipModeDecision(),
            confirmed: true,
          }),
        });
        if (this._state.clipModeStage !== staged) return;
        const receipt = normalizeOwnerImportReceipt(data);
        if (!receipt) throw Object.assign(new Error("Unsafe import receipt"), {
          publicMessage: "匯入結果無法驗證，請勿重送；請用收據查詢狀態。",
        });
        this._state.clipModeReceipt = receipt;
        this._state.clipModeMessage = receipt.replayed
          ? "這次匯入先前已完成；已安全取回同一張收據，沒有重複寫入。"
          : "匯入完成；角色與素材已加入統一媒體庫。";
        this._state.clipModePlan = null;
        await Promise.all([this.refreshCharacters(), this.refreshAssets()]);
        this.dispatchEvent(new CustomEvent("castle-provider-catalog-changed", {
          bubbles: true,
          composed: true,
          detail: Object.freeze({ providerId: "clipmode" }),
        }));
      } catch (error) {
        if (this._state.clipModeStage !== staged) return;
        this._state.clipModeMessage = publicErrorMessage(error);
        this._state.clipModeError = true;
      } finally {
        this._state.clipModeWorking = "";
        this.render();
      }
    }

    async _cancelClipModeImport() {
      const staged = this._state.clipModeStage;
      if (!staged || this._state.clipModeWorking || !this.isAdmin) return;
      this._state.clipModeWorking = "cancel";
      this.render();
      try {
        await requestJson(`${CLIPMODE_STAGE_ENDPOINT}/${pathId(staged.stageId)}`, {
          method: "DELETE",
          body: JSON.stringify({ schema_version: SCHEMA_VERSION, stage_token: staged.stageToken }),
        });
        if (this._state.clipModeStage !== staged) return;
        this._invalidateClipModePreview({ clearFile: false });
        this._state.clipModeMessage = "這次暫存已取消，沒有寫入媒體庫。";
      } catch (error) {
        if (this._state.clipModeStage !== staged) return;
        this._state.clipModeMessage = publicErrorMessage(error);
        this._state.clipModeError = true;
      } finally {
        this._state.clipModeWorking = "";
        this.render();
      }
    }

    _invalidateClipModePreview({ clearFile = false } = {}) {
      this._clipModeRevision += 1;
      if (this._clipModeRequest) this._clipModeRequest.abort();
      this._clipModeRequest = null;
      this._state.clipModePreviewing = false;
      this._state.clipModePreview = null;
      this._state.clipModeStage = null;
      this._state.clipModePlan = null;
      this._state.clipModeReceipt = null;
      this._state.clipModeWorking = "";
      this._state.clipModeMessage = "";
      this._state.clipModeError = false;
      if (clearFile) {
        this._clipModeFile = null;
        this._state.clipModeFileName = "";
        this._state.clipModeFileBytes = 0;
      }
    }

    _renderOwnerImportReview() {
      const staged = this._state.clipModeStage;
      const plan = this._state.clipModePlan;
      const receipt = this._state.clipModeReceipt;
      const summary = staged.summary;
      const result = node("section", {
        className: `provider-fabric__clipmode-result${receipt ? " is-ready" : ""}`,
        attributes: { "aria-label": "ClipMode 匯入檢查" },
      });
      result.append(
        node("div", { className: "provider-fabric__clipmode-result-head" }, [
          node("div", {}, [
            node("h3", { text: receipt ? "匯入完成" : (plan ? "等待你的最後確認" : "暫存分析完成") }),
            node("p", { text: receipt ? `收據 ${receipt.receiptId}` : "尚未呼叫外部服務，也沒有花費點數" }),
          ]),
          node("span", {
            className: `provider-fabric__status provider-fabric__status--${receipt || plan ? "ready" : "attention"}`,
            text: receipt ? "已進入媒體庫" : (plan ? "可以確認" : "尚未寫入"),
          }),
        ]),
        node("dl", { className: "provider-fabric__clipmode-counts" }, [
          node("div", {}, [node("dt", { text: "角色" }), node("dd", { text: summary.characters })]),
          node("div", {}, [node("dt", { text: "素材" }), node("dd", { text: summary.media })]),
          node("div", {}, [node("dt", { text: "內含檔案" }), node("dd", { text: summary.bundledMedia })]),
          node("div", {}, [node("dt", { text: "僅遠端索引" }), node("dd", { text: summary.remoteOnlyMedia })]),
          node("div", {}, [node("dt", { text: "來源關係" }), node("dd", { text: summary.lineage })]),
          node("div", {}, [node("dt", { text: "未分類阻擋" }), node("dd", { text: summary.unknownClassBlockers })]),
        ]),
      );
      if (plan) {
        result.append(node("p", {
          className: "provider-fabric__clipmode-proof",
          text: `最後計畫：${plan.summary.characters} 個角色、${plan.summary.projections} 個素材、${plan.summary.versions} 個版本。確認權限會自動過期。`,
        }));
        if (plan.summary.profileSelections) {
          result.append(node("p", {
            className: "provider-fabric__clipmode-proof",
            text: `將使用匯出檔明確指定的 ${plan.summary.profileSelections} 張角色照片；Castle 會先移除 metadata、縮圖並重編碼，再設為角色頭像。`,
          }));
        }
      }
      if (receipt) {
        const imported = Object.values(receipt.counts).reduce((total, value) => total + value, 0);
        result.append(node("p", {
          className: "provider-fabric__clipmode-proof",
          text: `收據已驗證，共記錄 ${imported} 筆成員關係；重複提交保護：${receipt.replayed ? "已啟用並攔截重送" : "已啟用"}。`,
        }));
      }
      return result;
    }

    _renderClipModePreviewResult(preview) {
      const result = node("section", {
        className: `provider-fabric__clipmode-result${preview.ready ? " is-ready" : " has-conflicts"}`,
        attributes: { "aria-labelledby": "provider-fabric-clipmode-result-title" },
      });
      result.append(
        node("div", { className: "provider-fabric__clipmode-result-head" }, [
          node("div", {}, [
            node("h3", {
              id: "provider-fabric-clipmode-result-title",
              text: preview.ready ? "預覽通過" : "需要先處理衝突",
            }),
            node("p", {
              text: `${preview.source.exportId} · ${preview.source.kind.toUpperCase()} · scope ${preview.account.providerScope}`,
            }),
          ]),
          node("span", {
            className: `provider-fabric__status provider-fabric__status--${preview.ready ? "ready" : "attention"}`,
            text: preview.ready ? "唯讀 dry-run 完成" : `${preview.plan.counts.conflict} 個衝突`,
          }),
        ]),
        node("dl", { className: "provider-fabric__clipmode-counts" }, [
          node("div", {}, [node("dt", { text: "角色" }), node("dd", { text: preview.summary.characters })]),
          node("div", {}, [node("dt", { text: "圖片" }), node("dd", { text: preview.summary.mediaKinds.image })]),
          node("div", {}, [node("dt", { text: "影片" }), node("dd", { text: preview.summary.mediaKinds.video })]),
          node("div", {}, [node("dt", { text: "聲音" }), node("dd", { text: preview.summary.mediaKinds.audio })]),
          node("div", {}, [node("dt", { text: "文字" }), node("dd", { text: preview.summary.mediaKinds.text })]),
          node("div", {}, [node("dt", { text: "重複" }), node("dd", { text: preview.summary.duplicates })]),
        ]),
        node("div", { className: "provider-fabric__clipmode-classes" }, [
          node("strong", { text: "角色分類" }),
          ...["anime", "realistic", "illustration", "unknown"].map((visualClass) => node("span", {
            className: `provider-fabric__class${visualClass === "unknown" ? " provider-fabric__class--unknown" : ""}`,
            text: `${visualClassLabel(visualClass)} ${preview.summary.characterClasses[visualClass]}`,
          })),
        ]),
      );
      if (preview.characters.length) {
        const characterList = node("ul", { className: "provider-fabric__clipmode-character-list" });
        for (const character of preview.characters.slice(0, 12)) {
          characterList.append(node("li", {}, [
            node("strong", { text: character.displayName }),
            node("span", {
              text: `${visualClassLabel(character.visualClass)}${character.hasProfileMedia ? " · 有 profile 圖" : " · 無 profile 圖"}`,
            }),
          ]));
        }
        result.append(
          node("h4", { text: "角色預覽" }),
          characterList,
        );
        if (preview.characters.length > 12 || preview.truncated.characters) {
          result.append(node("p", {
            className: "provider-fabric__clipmode-more",
            text: "畫面只列出前 12 位角色；總數以上方統計為準。",
          }));
        }
      }
      if (preview.conflicts.length) {
        const conflictList = node("ul", { className: "provider-fabric__clipmode-conflicts" });
        for (const conflict of preview.conflicts.slice(0, 10)) {
          conflictList.append(node("li", {}, [
            node("code", { text: conflict.code }),
            node("span", { text: conflict.message }),
          ]));
        }
        result.append(node("h4", { text: "衝突" }), conflictList);
        if (preview.conflicts.length > 10 || preview.truncated.conflicts) {
          result.append(node("p", {
            className: "provider-fabric__clipmode-more",
            text: "衝突清單已限量顯示；請修正來源檔後重新預覽。",
          }));
        }
      }
      result.append(node("p", {
        className: "provider-fabric__clipmode-proof",
        text: `預計新增 ${preview.plan.counts.create}、更新 ${preview.plan.counts.update}、不變 ${preview.plan.counts.noop}。本次寫入 0、花費 0 點。`,
      }));
      return result;
    }

    _formatBytes(value) {
      const bytes = safeCount(value, CLIPMODE_PREVIEW_MAX_BYTES);
      if (bytes < 1024) return `${bytes} B`;
      if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
      return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
    }

    _renderMediaLibrary() {
      const section = node("section", {
        className: "provider-fabric__section",
        attributes: { "aria-labelledby": "provider-fabric-media" },
      });
      section.append(node("div", { className: "provider-fabric__section-head" }, [
        node("div", {}, [
          node("h2", { id: "provider-fabric-media", text: this._mediaOnly ? "外部目錄" : "統一媒體庫" }),
          node("p", {
            text: this._mediaOnly
              ? "唯讀查看 AutoIP 與 ClipMode 已整理的角色素材；不會呼叫供應商、不會花點，也不會進入生成。"
              : "Castle 角色與外部角色分開識別；外部目錄目前只供整理與查看，不會直接進入生成。",
          }),
        ]),
      ]));
      section.append(this._renderCatalogControls(), this._renderCharacterBrowser());
      const tabs = node("div", {
        className: "provider-fabric__tabs provider-fabric__media-tabs",
        attributes: { role: "tablist", "aria-label": "媒體類型" },
      });
      for (const kind of MEDIA_KINDS) {
        const active = kind.id === this._state.kind;
        const tab = node("button", {
          className: `provider-fabric__tab${active ? " is-active" : ""}`,
          text: kind.label,
          type: "button",
          attributes: {
            role: "tab",
            "aria-selected": active ? "true" : "false",
            "aria-controls": "provider-fabric-assets-panel",
            id: `provider-fabric-tab-${kind.id}`,
            tabindex: active ? "0" : "-1",
          },
        });
        tab.addEventListener("click", () => this._selectMediaKind(kind.id));
        tabs.append(tab);
      }
      tabs.addEventListener("keydown", (event) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        const buttons = [...tabs.querySelectorAll('[role="tab"]')];
        if (!buttons.length) return;
        event.preventDefault();
        const current = Math.max(0, buttons.indexOf(document.activeElement));
        const index = event.key === "Home" ? 0
          : (event.key === "End" ? buttons.length - 1
            : (current + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) % buttons.length);
        buttons.forEach((button, buttonIndex) => {
          button.setAttribute("tabindex", buttonIndex === index ? "0" : "-1");
        });
        buttons[index].focus();
      });
      const characterSelect = node("select", { attributes: { id: "provider-fabric-character-filter" } });
      characterSelect.append(node("option", { text: "全部角色", attributes: { value: "" } }));
      const selectedReference = this._state.characterId
        ? `${this._state.characterScope}:${this._state.characterId}`
        : "";
      const localCharacters = this._visibleLocalCharacters();
      const providerCharacters = this._visibleProviderCharacters();
      const characterGroups = this._lockedProviderScope
        ? [["provider", "外部目錄角色（唯讀）", providerCharacters]]
        : [
          ["castle", "Castle 本機角色", localCharacters],
          ["provider", "外部目錄角色（唯讀）", providerCharacters],
        ];
      for (const [scope, label, entries] of characterGroups) {
        if (!entries.length) continue;
        const group = node("optgroup", { attributes: { label } });
        for (const character of entries) {
          const value = `${scope}:${character.id}`;
          group.append(node("option", {
            text: character.label || character.id,
            attributes: { value, selected: value === selectedReference ? "" : null },
          }));
        }
        characterSelect.append(group);
      }
      characterSelect.addEventListener("change", () => {
        const match = /^(castle|provider):(.+)$/.exec(characterSelect.value);
        this._changeCatalogContext({
          characterScope: match ? match[1] : this._state.characterScope,
          characterId: match ? safeId(match[2]) : "",
          visualClass: this._state.visualClass,
          reloadCharacters: false,
        });
      });
      const originSelect = node("select", { attributes: { id: "provider-fabric-origin-filter" } });
      const originOptions = this._lockedProviderScope
        ? [
          ["", "全部外部來源"],
          ["autoip", "AutoIP"],
          ["clipmode", "ClipMode 匯入"],
        ]
        : [
          ["", "全部來源"],
          ["local_comfy", "我的 5090"],
          ["autoip", "AutoIP"],
          ["clipmode", "ClipMode 匯入"],
        ];
      for (const [value, label] of originOptions) {
        originSelect.append(node("option", {
          text: label,
          attributes: { value, selected: value === this._state.origin ? "" : null },
        }));
      }
      originSelect.addEventListener("change", () => {
        const origin = safeId(originSelect.value);
        const characterScope = origin === "local_comfy"
          ? "castle"
          : (["autoip", "clipmode"].includes(origin) ? "provider" : this._state.characterScope);
        this._changeCatalogContext({
          characterScope,
          characterId: characterScope === this._state.characterScope ? this._state.characterId : "",
          visualClass: this._state.visualClass,
          origin,
          reloadCharacters: characterScope !== this._state.characterScope,
        });
      });
      const roleSelect = node("select", { attributes: { id: "provider-fabric-role-filter" } });
      const roleOptions = this._state.kind === "audio"
        ? [["unclassified", "未分類"], ["music", "背景音樂"], ["sound_effect", "效果音"], ["voice", "語音／人聲"], ["ambience", "環境聲"]]
        : MEDIA_ROLE_OPTIONS[this._state.kind];
      roleSelect.append(node("option", { text: "全部用途", attributes: { value: "" } }));
      for (const [value, label] of roleOptions) roleSelect.append(node("option", {
        text: label, attributes: { value, selected: value === this._state.catalogRole ? "" : null },
      }));
      roleSelect.addEventListener("change", () => this._changeCatalogContext({
        characterScope: this._state.characterScope,
        characterId: this._state.characterId,
        visualClass: this._state.visualClass,
        catalogRole: safeId(roleSelect.value),
      }));
      section.append(
        tabs,
        node("div", { className: "provider-fabric__filters" }, [
          node("label", {}, [node("span", { text: "角色" }), characterSelect]),
          node("label", {}, [node("span", { text: "來源" }), originSelect]),
          node("label", {}, [node("span", { text: "用途" }), roleSelect]),
        ]),
      );
      const visibleLocal = this._state.assets.filter(
        (asset) => asset.catalogScope === "castle" && asset.localSourceLocator,
      );
      if (visibleLocal.length) {
        const adoptVisible = node("button", {
          className: "provider-fabric__button provider-fabric__button--quiet provider-fabric__adopt-visible",
          type: "button",
          text: this._state.localBulkAdopting
            ? (this._state.localBulkProgress || "正在整理這一頁…")
            : `把這頁 ${visibleLocal.length} 個作品加入統一資源`,
          attributes: { disabled: this._state.localBulkAdopting ? "" : null },
        });
        adoptVisible.addEventListener("click", () => this._adoptVisibleLocalHistory(visibleLocal));
        section.append(node("div", { className: "provider-fabric__bulk-action" }, [
          node("p", { text: "只處理目前看得到的本機作品；逐一驗證檔案與角色，不會上傳或花點。" }),
          adoptVisible,
        ]));
      }
      if (this._state.assetError) {
        section.append(node("div", {
          className: "provider-fabric__notice provider-fabric__notice--error",
          text: this._state.assetError,
          attributes: { role: "alert" },
        }));
      }
      const panel = node("div", {
        id: "provider-fabric-assets-panel",
        className: "provider-fabric__asset-panel",
        attributes: {
          role: "tabpanel",
          "aria-labelledby": `provider-fabric-tab-${this._state.kind}`,
          tabindex: "0",
        },
      });
      const grid = node("div", { className: "provider-fabric__asset-grid" });
      for (const asset of this._state.assets) grid.append(this._renderAsset(asset));
      if (!this._state.assets.length && !this._state.loadingAssets) {
        grid.append(node("div", { className: "provider-fabric__empty" }, [
          node("strong", { text: "這個分類目前沒有素材" }),
          node("span", { text: "調整角色或來源篩選，或稍後再回來查看。" }),
        ]));
      }
      panel.append(grid);
      if (this._state.loadingAssets) panel.append(node("p", { className: "provider-fabric__loading", text: "正在整理素材…" }));
      if (this._state.nextCursor && !this._state.loadingAssets) {
        const more = node("button", {
          className: "provider-fabric__button provider-fabric__button--quiet provider-fabric__load-more",
          type: "button",
          text: "載入更多",
        });
        more.addEventListener("click", () => this.refreshAssets({ append: true }));
        panel.append(more);
      }
      section.append(panel);
      return section;
    }

    _selectMediaKind(kind) {
      if (!MEDIA_KINDS.some((item) => item.id === kind) || this._state.kind === kind) return;
      if (this._mediaOnly) {
        this.dispatchEvent(new CustomEvent("castle-provider-media-kind-change", {
          bubbles: true,
          composed: true,
          detail: Object.freeze({ kind }),
        }));
        return;
      }
      if (this._assetRequest) this._assetRequest.abort();
      this._assetRequest = null;
      this._discardPlan();
      this._state.kind = kind;
      this._state.catalogRole = "";
      this._state.assets = [];
      this._state.nextCursor = "";
      this.refreshAssets();
    }

    _visibleLocalCharacters() {
      const visualClass = this._state.visualClass;
      return this._characters
        .filter((item) => visualClass === "all" || item.visualClass === visualClass)
        .sort((left, right) => left.label.localeCompare(right.label, "zh-Hant"));
    }

    _visibleProviderCharacters() {
      const visualClass = this._state.visualClass;
      return this._state.providerCharacters
        .filter((item) => visualClass === "all" || item.visualClass === visualClass)
        .sort((left, right) => left.label.localeCompare(right.label, "zh-Hant"));
    }

    _renderCatalogControls() {
      const controls = node("div", { className: "provider-fabric__catalog-controls" });
      const visualTabs = node("div", {
        className: "provider-fabric__visual-tabs",
        attributes: { role: "group", "aria-label": "角色類型" },
      });
      for (const option of VISUAL_CLASS_FILTERS) {
        const active = option.id === this._state.visualClass;
        const button = node("button", {
          className: `provider-fabric__visual-tab${active ? " is-active" : ""}`,
          text: option.label,
          type: "button",
          attributes: {
            "aria-pressed": active ? "true" : "false",
            "data-visual-class": option.id,
          },
        });
        button.addEventListener("click", () => {
          if (active) return;
          const selected = this._state.characterScope === "castle"
            ? this._characters.find((item) => item.id === this._state.characterId)
            : this._state.providerCharacters.find((item) => item.id === this._state.characterId);
          const keepCharacter = selected && (option.id === "all" || selected.visualClass === option.id);
          this._changeCatalogContext({
            characterScope: this._state.characterScope,
            characterId: keepCharacter ? this._state.characterId : "",
            visualClass: option.id,
            reloadCharacters: true,
          });
        });
        visualTabs.append(button);
      }
      controls.append(node("div", { className: "provider-fabric__catalog-control" }, [
        node("span", { className: "provider-fabric__control-label", text: "先選角色類型" }),
        visualTabs,
      ]));
      if (!this._lockedProviderScope) {
        const scopeTabs = node("div", {
          className: "provider-fabric__scope-tabs",
          attributes: { role: "group", "aria-label": "資源範圍" },
        });
        for (const option of CATALOG_SCOPES) {
          const active = option.id === this._state.characterScope;
          const button = node("button", {
            className: `provider-fabric__scope-tab${active ? " is-active" : ""}`,
            type: "button",
            attributes: { "aria-pressed": active ? "true" : "false", "data-catalog-scope": option.id },
          }, [
            node("strong", { text: option.label }),
            node("span", { text: option.detail }),
          ]);
          button.addEventListener("click", () => {
            if (active) return;
            this._changeCatalogContext({
              characterScope: option.id,
              characterId: "",
              visualClass: this._state.visualClass,
              reloadCharacters: true,
            });
          });
          scopeTabs.append(button);
        }
        controls.append(node("div", { className: "provider-fabric__catalog-control" }, [
          node("span", { className: "provider-fabric__control-label", text: "再選資源範圍" }),
          scopeTabs,
        ]));
      } else {
        controls.classList.add("provider-fabric__catalog-controls--locked");
      }
      return controls;
    }

    _renderCharacterBrowser() {
      const browser = node("div", {
        className: "provider-fabric__character-browser",
        attributes: { "aria-labelledby": "provider-fabric-character-browser-title" },
      });
      browser.append(node("div", { className: "provider-fabric__character-browser-head" }, [
        node("div", {}, [
          node("h3", { id: "provider-fabric-character-browser-title", text: "角色快速篩選" }),
          node("p", { text: "同名角色也不會混在一起；每張卡都標示 Castle 或外部來源。" }),
        ]),
        node("span", {
          className: "provider-fabric__read-only-pill",
          text: "外部目錄唯讀",
        }),
      ]));
      const rail = node("div", {
        className: "provider-fabric__character-rail",
        attributes: { role: "group", "aria-label": "角色快速篩選" },
      });
      const showLocal = this._state.characterScope !== "provider";
      const showProvider = this._state.characterScope !== "castle";
      const characters = [
        ...(showLocal ? this._visibleLocalCharacters() : []),
        ...(showProvider ? this._visibleProviderCharacters() : []),
      ];
      const allActive = !this._state.characterId;
      const allButton = node("button", {
        className: `provider-fabric__character-card provider-fabric__character-card--all${allActive ? " is-active" : ""}`,
        type: "button",
        attributes: { "aria-pressed": allActive ? "true" : "false" },
      }, [
        node("span", { className: "provider-fabric__character-avatar provider-fabric__character-avatar--all", text: "全" }),
        node("strong", { text: "全部角色" }),
        node("small", { text: this._state.characterScope === "all" ? "兩個目錄" : "目前範圍" }),
      ]);
      allButton.addEventListener("click", () => {
        if (allActive) return;
        this._changeCatalogContext({
          characterScope: this._state.characterScope,
          characterId: "",
          visualClass: this._state.visualClass,
          reloadCharacters: false,
        });
      });
      rail.append(allButton);
      for (const character of characters) {
        const active = character.scope === this._state.characterScope && character.id === this._state.characterId;
        const sourceLabels = character.scope === "provider"
          ? character.sourceProviderIds.map((id) => providerMeta(id).label)
          : [];
        const card = node("button", {
          className: `provider-fabric__character-card provider-fabric__character-card--${character.scope}${active ? " is-active" : ""}`,
          type: "button",
          attributes: {
            "aria-pressed": active ? "true" : "false",
            "data-character-scope": character.scope,
            "data-character-id": character.id,
          },
        });
        card.append(
          this._renderCharacterAvatar(character),
          node("strong", { text: character.label || character.id }),
          node("small", {
            text: character.scope === "castle"
              ? `Castle · ${visualClassLabel(character.visualClass)}`
              : `${sourceLabels.join(" + ") || "外部目錄"} · ${character.catalogOnly ? "待分類" : "唯讀"}`,
          }),
        );
        card.addEventListener("click", () => {
          if (active) return;
          this._changeCatalogContext({
            characterScope: character.scope,
            characterId: character.id,
            visualClass: this._state.visualClass,
            origin: character.scope === "castle"
              ? (this._state.origin === "local_comfy" ? "local_comfy" : "")
              : (this._state.origin === "local_comfy" ? "" : this._state.origin),
            reloadCharacters: false,
          });
        });
        rail.append(card);
      }
      if (!characters.length && !this._state.loadingCharacters) {
        rail.append(node("div", { className: "provider-fabric__character-empty", text: "這個類型與範圍目前沒有角色。" }));
      }
      browser.append(rail);
      if (this._state.loadingCharacters) {
        browser.append(node("p", { className: "provider-fabric__loading", text: "正在整理你的外部角色…" }));
      }
      if (this._state.characterError) {
        browser.append(node("div", {
          className: "provider-fabric__notice provider-fabric__notice--error",
          text: this._state.characterError,
          attributes: { role: "alert" },
        }));
      }
      if (this._state.nextCharacterCursor && !this._state.loadingCharacters) {
        const more = node("button", {
          className: "provider-fabric__button provider-fabric__button--quiet provider-fabric__character-more",
          type: "button",
          text: "載入更多外部角色",
        });
        more.addEventListener("click", () => this.refreshCharacters({ append: true }));
        browser.append(more);
      }
      return browser;
    }

    _renderCharacterAvatar(character) {
      const fallback = () => node("span", {
        className: `provider-fabric__character-avatar provider-fabric__character-avatar--${character.scope}`,
        text: characterInitials(character.label || character.id),
      });
      if (!character.avatarUrl) return fallback();
      const image = node("img", {
        className: `provider-fabric__character-avatar provider-fabric__character-avatar--${character.scope}`,
        attributes: { src: character.avatarUrl, alt: "", loading: "lazy", decoding: "async" },
      });
      image.addEventListener("error", () => image.replaceWith(fallback()), { once: true });
      return image;
    }

    async _saveAssetCatalog(asset, form) {
      const data = new FormData(form);
      const button = form.querySelector('button[type="submit"]');
      if (button) button.disabled = true;
      try {
        const raw = await requestJson(`/api/assets/${pathId(asset.assetId)}/catalog`, {
          method: "PUT",
          body: JSON.stringify({
            schema_version: "castle.asset-catalog-annotation.v2",
            display_name: String(data.get("display_name") || "").trim() || null,
            audio_role: String(data.get("audio_role") || "unclassified"),
            media_role: String(data.get("media_role") || "unclassified"),
            expected_revision: asset.catalogRevision,
          }),
        });
        if (
          raw?.schema_version !== "castle.asset-catalog-annotation.response.v2"
          || raw.ok !== true || raw.asset_id !== asset.assetId
          || raw.provider_transport_performed !== false || raw.credits_spent !== 0
          || !raw.catalog || !Number.isSafeInteger(raw.catalog.revision)
          || raw.catalog.revision !== asset.catalogRevision + 1
          || containsSensitiveField(raw)
        ) throw new Error("Unsafe asset catalog response");
        this._announce("媒體名稱與用途已保存。", false);
        await this.refreshAssets();
      } catch (error) {
        this._announce(publicErrorMessage(error), true);
        if (button) button.disabled = false;
      }
    }

    async _classificationCharactersForVisualClass(visualClass) {
      if (!["anime", "realistic", "illustration"].includes(visualClass)) {
        throw new Error("Unsafe classification visual class");
      }
      const cached = this._classificationCharacterCache.get(visualClass);
      if (cached) return cached;
      const inFlight = this._classificationCharacterRequests.get(visualClass);
      if (inFlight) return inFlight;

      const pending = (async () => {
        const byIdentity = new Map();
        const seenCursors = new Set();
        let cursor = "";
        for (let page = 0; page < CLASSIFICATION_CHARACTER_MAX_PAGES; page += 1) {
          const url = new URL(CHARACTERS_ENDPOINT, window.location.origin);
          url.searchParams.set("visualClass", visualClass);
          if (cursor) url.searchParams.set("cursor", cursor);
          const raw = await requestJson(`${url.pathname}${url.search}`);
          const hasNextCursor = Object.prototype.hasOwnProperty.call(raw, "next_cursor");
          const nextCursor = raw.next_cursor === null ? "" : safeOpaqueToken(raw.next_cursor);
          if (
            raw.schema_version !== SCHEMA_VERSION
            || raw.provider_transport_performed !== false || raw.credits_spent !== 0
            || !Array.isArray(raw.characters)
            || raw.characters.length > CLASSIFICATION_CHARACTER_PAGE_SIZE
            || !hasExactKeys(raw.filters, ["visualClass"])
            || raw.filters.visualClass !== visualClass
            || !hasNextCursor || (raw.next_cursor !== null && !nextCursor)
            || containsSensitiveField(raw)
          ) throw new Error("Unsafe classification character catalog");
          const characters = raw.characters.map(normalizeProviderCharacter);
          if (characters.some(
            (item) => !item || item.visualClass !== visualClass || item.catalogOnly,
          )) throw new Error("Unsafe classification character catalog");
          for (const character of characters) {
            if (!byIdentity.has(character.id)) byIdentity.set(character.id, character);
          }
          if (!nextCursor) {
            const completed = Object.freeze([...byIdentity.values()]);
            this._classificationCharacterCache.set(visualClass, completed);
            return completed;
          }
          if (seenCursors.has(nextCursor)) {
            throw new Error("Unsafe classification character cursor loop");
          }
          seenCursors.add(nextCursor);
          cursor = nextCursor;
        }
        throw new Error("Unsafe classification character page limit");
      })();
      this._classificationCharacterRequests.set(visualClass, pending);
      try {
        return await pending;
      } finally {
        if (this._classificationCharacterRequests.get(visualClass) === pending) {
          this._classificationCharacterRequests.delete(visualClass);
        }
      }
    }

    async _loadAssetClassificationCharacters(visualClass, select, button, message) {
      select.replaceChildren();
      button.disabled = true;
      message.textContent = "正在載入同類型角色…";
      try {
        const characters = await this._classificationCharactersForVisualClass(visualClass);
        if (!select.isConnected || select.form?.elements.visual_class?.value !== visualClass) return;
        for (const character of characters) {
          select.append(node("option", {
            text: character.label || character.id,
            attributes: { value: character.id },
          }));
        }
        button.disabled = !select.options.length;
        message.textContent = select.options.length
          ? "可選 1–4 個同類型角色；按住 Ctrl／⌘ 可複選。"
          : "目前沒有已完成此類型分類的角色，素材會繼續維持未分類。";
      } catch (error) {
        if (!select.isConnected || select.form?.elements.visual_class?.value !== visualClass) return;
        message.textContent = publicErrorMessage(error);
      }
    }

    async _classifyAsset(asset, form) {
      if (!asset?.classificationAvailable || !asset.assetVersionId) return;
      const data = new FormData(form);
      const visualClass = String(data.get("visual_class") || "");
      const characterIds = [...new Set(
        data.getAll("character_ids").map((item) => safeId(item)).filter(Boolean),
      )].sort();
      const confirmed = data.get("confirmed") === "yes";
      const button = form.querySelector('button[type="submit"]');
      if (!["anime", "realistic", "illustration"].includes(visualClass)
        || !characterIds.length || characterIds.length > 4 || !confirmed) {
        this._announce("請選擇正確類型、1–4 個同類型角色，並確認這是不可逆決定。", true);
        return;
      }
      if (button) button.disabled = true;
      try {
        const raw = await requestJson(
          `/api/assets/${pathId(asset.assetId)}/classification`,
          {
            method: "PUT",
            body: JSON.stringify({
              schema_version: "castle.asset-classification.v1",
              asset_version_id: asset.assetVersionId,
              visual_class: visualClass,
              character_ids: characterIds,
              confirmed: true,
            }),
          },
        );
        const returnedCharacters = safeArray(raw?.character_ids).map(safeId).filter(Boolean).sort();
        if (
          raw?.schema_version !== "castle.asset-classification.response.v1"
          || raw.ok !== true || raw.asset_id !== asset.assetId
          || raw.asset_version_id !== asset.assetVersionId
          || !safeId(raw.classification_id)?.startsWith("assetclass:")
          || raw.classification_version !== 1 || raw.visual_class !== visualClass
          || JSON.stringify(returnedCharacters) !== JSON.stringify(characterIds)
          || typeof raw.replayed !== "boolean"
          || raw.provider_transport_performed !== false
          || raw.provider_write_performed !== false || raw.credits_spent !== 0
          || raw.generation_authorized !== false || containsSensitiveField(raw)
        ) throw new Error("Unsafe asset classification response");
        this._announce("素材類型與角色已鎖定；系統仍會另外檢查權利、模型與執行條件。", false);
        await this.refreshAssets();
      } catch (error) {
        this._announce(publicErrorMessage(error), true);
        if (button) button.disabled = false;
      }
    }

    _renderAsset(asset) {
      const provider = providerMeta(asset.providerId);
      const catalogOnly = asset.visualClass === "unknown";
      const localSourceLocator = asset.catalogScope === "castle" ? asset.localSourceLocator : null;
      const card = node("article", { className: `provider-fabric__asset provider-fabric__asset--${asset.catalogScope}` });
      const media = node("div", { className: "provider-fabric__asset-preview" });
      const placeholder = () => node("span", {
        className: "provider-fabric__asset-placeholder",
        text: { image: "圖片", video: "影片", audio: "聲音", text: "文字" }[asset.kind],
      });
      if (asset.previewUrl && asset.kind === "image") {
        const image = node("img", {
          attributes: { src: asset.previewUrl, alt: "", loading: "lazy", decoding: "async" },
        });
        image.addEventListener("error", () => image.replaceWith(placeholder()), { once: true });
        media.append(image);
      } else if (asset.previewUrl && asset.kind === "video") {
        media.append(node("video", {
          attributes: { src: asset.previewUrl, controls: "", preload: "metadata", playsinline: "" },
        }));
      } else if (asset.previewUrl && asset.kind === "audio") {
        media.append(node("audio", { attributes: { src: asset.previewUrl, controls: "", preload: "none" } }));
      } else if (asset.kind === "text" && asset.excerpt) {
        media.append(node("p", { text: asset.excerpt }));
      } else {
        media.append(placeholder());
      }
      const imported = asset.catalogScope === "provider" && asset.state === "imported";
      const canHandoff = asset.catalogScope === "provider" && asset.sourceAvailable
        && asset.preparationAvailable && Boolean(asset.assetVersionId)
        && asset.visualClass !== "unknown";
      const handoff = this._assetHandoffs.get(asset.assetId);
      const generationDetail = this._assetGenerationDetails.get(asset.assetId);
      const adoption = this._localAdoptions.get(asset.assetId);
      const action = node("button", {
        className: "provider-fabric__button provider-fabric__button--quiet",
        type: "button",
        text: localSourceLocator
          ? "查看作品與接續功能"
          : (canHandoff
            ? (handoff?.loading ? "檢查可用功能中…" : "用這個素材做下一步")
          : (imported
            ? "已匯入 Castle · 可在媒體庫預覽"
            : (catalogOnly
            ? "完成分類後才可使用"
            : (asset.providerId === "clipmode" ? "尚未匯入 Castle" : "尚未匯入，不能生成")))),
        attributes: { disabled: localSourceLocator || (canHandoff && !handoff?.loading) ? null : "" },
      });
      action.addEventListener("click", () => {
        if (canHandoff) {
          this._loadAssetHandoff(asset);
          return;
        }
        if (!localSourceLocator) return;
        this.dispatchEvent(new CustomEvent("castle-local-history-open", {
          bubbles: true,
          composed: true,
          detail: localSourceLocator,
        }));
        this._announce("已送出作品確認；系統只會開啟目前帳號仍可存取的版本。", false);
      });
      const adopt = localSourceLocator ? node("button", {
        className: "provider-fabric__button",
        type: "button",
        text: adoption?.loading ? "正在驗證並加入…" : (adoption?.done ? "已加入統一資源" : "加入統一資源"),
        attributes: { disabled: adoption?.loading || adoption?.done ? "" : null },
      }) : null;
      if (adopt) adopt.addEventListener("click", () => this._adoptLocalHistory(asset));
      const generationButton = asset.generationDetailAvailable ? node("button", {
        className: "provider-fabric__button provider-fabric__button--quiet",
        type: "button",
        text: generationDetail?.loading ? "讀取生成資料中…" : "查看怎麼做的",
        attributes: { disabled: generationDetail?.loading ? "" : null },
      }) : null;
      if (generationButton) generationButton.addEventListener("click", () => {
        if (generationDetail?.detail) {
          this._assetGenerationDetails.delete(asset.assetId);
          this.render();
          return;
        }
        this._loadAssetGenerationDetail(asset);
      });
      const handoffPanel = node("div", { className: "provider-fabric__asset-handoff" });
      if (handoff?.error) handoffPanel.append(node("p", { text: handoff.error }));
      if (handoff?.candidates) {
        const selectable = handoff.candidates.filter((item) => item.selectable);
        if (!selectable.length) {
          handoffPanel.append(node("p", {
            text: "目前沒有技術狀態與輸入契約都相符的模組；不會改用錯誤的動漫／真人模型。",
          }));
        }
        for (const candidate of selectable) {
          const controls = node("div", { className: "provider-fabric__handoff-options" });
          const selections = {};
          for (const [key, values] of Object.entries(candidate.optionValues)) {
            const select = node("select", {
              attributes: { "aria-label": handoffOptionLabel(key) },
            });
            values.forEach((value, index) => {
              select.append(node("option", {
                text: handoffOptionValueLabel(key, value), attributes: { value: index },
              }));
            });
            selections[key] = select;
            controls.append(node("label", {}, [
              node("span", { text: handoffOptionLabel(key) }), select,
            ]));
          }
          const use = node("button", {
            className: "provider-fabric__button", type: "button",
            text: `${handoffActionLabel(candidate.actionId)} · ${providerMeta(candidate.providerId).label}`,
          });
          use.addEventListener("click", () => {
            const plannerRequest = JSON.parse(JSON.stringify(candidate.plannerRequest));
            for (const [key, select] of Object.entries(selections)) {
              plannerRequest.options[key] = candidate.optionValues[key][Number(select.value)];
            }
            this.dispatchEvent(new CustomEvent("castle-provider-asset-handoff", {
              bubbles: true, composed: true, detail: plannerRequest,
            }));
          });
          handoffPanel.append(node("div", {
            className: "provider-fabric__handoff-candidate",
          }, [controls, use]));
        }
      }
      let catalogForm = null;
      if (asset.catalogScope === "provider") {
        const name = node("input", { attributes: {
          name: "display_name", maxlength: "160", value: asset.catalogDisplayName,
          placeholder: "自訂好記的名稱",
        } });
        const role = asset.kind === "audio"
          ? node("select", { attributes: { name: "audio_role" } }, [
            ["unclassified", "未分類"], ["music", "背景音樂"],
            ["sound_effect", "效果音"], ["voice", "語音／人聲"], ["ambience", "環境聲"],
          ].map(([value, text]) => node("option", {
            text, attributes: { value, selected: value === asset.audioRole ? "" : null },
          })))
          : node("select", { attributes: { name: "media_role" } },
            MEDIA_ROLE_OPTIONS[asset.kind].map(([value, text]) => node("option", {
              text, attributes: { value, selected: value === asset.mediaRole ? "" : null },
            })));
        const hiddenRole = node("input", { attributes: {
          type: "hidden", name: asset.kind === "audio" ? "media_role" : "audio_role", value: "unclassified",
        } });
        catalogForm = node("form", { className: "provider-fabric__asset-catalog" }, [
          node("strong", { text: "媒體庫整理" }),
          node("label", {}, [node("span", { text: "顯示名稱" }), name]),
          node("label", {}, [node("span", { text: asset.kind === "audio" ? "聲音用途" : "素材用途" }), role]),
          hiddenRole,
          node("button", { className: "provider-fabric__button provider-fabric__button--quiet", type: "submit", text: "保存分類" }),
        ]);
        catalogForm.addEventListener("submit", (event) => {
          event.preventDefault();
          this._saveAssetCatalog(asset, catalogForm);
        });
      }
      let classificationForm = null;
      let loadClassificationCharacters = null;
      if (asset.classificationAvailable) {
        const visualClass = node("select", {
          attributes: { name: "visual_class", required: "" },
        }, [
          ["anime", "動漫／二次元"],
          ["realistic", "真人／寫實"],
          ["illustration", "插畫／風格化"],
        ].map(([value, text]) => node("option", {
          text, attributes: { value },
        })));
        const characters = node("select", {
          attributes: {
            name: "character_ids", multiple: "", required: "", size: "4",
            "aria-describedby": `asset-classification-help-${asset.assetId}`,
          },
        });
        const help = node("small", {
          id: `asset-classification-help-${asset.assetId}`,
          text: "正在載入同類型角色…",
        });
        const submit = node("button", {
          className: "provider-fabric__button", type: "submit",
          text: "確認並鎖定分類", attributes: { disabled: "" },
        });
        classificationForm = node("form", {
          className: "provider-fabric__asset-catalog provider-fabric__asset-classification",
        }, [
          node("strong", { text: "人工分類（不可逆）" }),
          node("p", { text: "只針對這個已驗證版本；不會從名稱或縮圖猜測，也不會送出生成工作。" }),
          node("label", {}, [node("span", { text: "視覺類型" }), visualClass]),
          node("label", {}, [node("span", { text: "畫面中的角色（1–4）" }), characters, help]),
          node("label", { className: "provider-fabric__check" }, [
            node("input", { attributes: { type: "checkbox", name: "confirmed", value: "yes", required: "" } }),
            node("span", { text: "我已核對這個版本、類型與角色；確認後不可改成另一類型。" }),
          ]),
          submit,
        ]);
        loadClassificationCharacters = () => this._loadAssetClassificationCharacters(
          visualClass.value, characters, submit, help,
        );
        visualClass.addEventListener("change", loadClassificationCharacters);
        characters.addEventListener("change", () => {
          const selected = [...characters.selectedOptions];
          if (selected.length <= 4) return;
          selected.slice(4).forEach((option) => { option.selected = false; });
          this._announce("一個素材最多可綁定 4 個角色。", true);
        });
        classificationForm.addEventListener("submit", (event) => {
          event.preventDefault();
          this._classifyAsset(asset, classificationForm);
        });
      }
      card.append(
        media,
        node("div", { className: "provider-fabric__asset-body" }, [
          node("div", { className: "provider-fabric__asset-badges" }, [
            node("span", { className: `provider-fabric__badge provider-fabric__badge--${provider.tone}`, text: provider.badge }),
            node("span", {
              className: "provider-fabric__scope-badge",
              text: asset.catalogScope === "castle" ? "Castle" : (imported ? "已匯入" : "外部唯讀"),
            }),
            node("span", {
              className: `provider-fabric__class${catalogOnly ? " provider-fabric__class--unknown" : ""}`,
              text: visualClassLabel(asset.visualClass),
            }),
          ]),
          node("h3", { text: asset.title }),
          node("p", {
            className: "provider-fabric__asset-meta",
            text: asset.characterIds.length
              ? `角色：${asset.characterIds.map((id) => {
                const character = this._state.providerCharacters.find((item) => item.id === id)
                  || this._characters.find((item) => item.id === id);
                return character ? character.label : id;
              }).join("、")}`
              : "尚未連結角色",
          }),
          node("div", { className: "provider-fabric__asset-actions" }, [action, generationButton, adopt]),
          adoption?.error ? node("p", { className: "provider-fabric__asset-error", text: adoption.error }) : null,
          generationDetail?.error
            ? node("p", { className: "provider-fabric__asset-error", text: generationDetail.error })
            : null,
          generationDetail?.detail?.available
            ? this._renderAssetGenerationDetail(generationDetail.detail)
            : null,
          catalogForm,
          classificationForm,
          handoffPanel,
        ]),
      );
      if (loadClassificationCharacters) queueMicrotask(loadClassificationCharacters);
      return card;
    }

    _renderAssetGenerationDetail(detail) {
      const promptLabels = {
        frame_intent: "鏡頭內容", observable_action: "動作／Prompt", camera: "鏡頭",
        audio_intent: "聲音", language: "語言", shot_id: "鏡號",
      };
      const body = node("div", { className: "provider-fabric__generation-detail" }, [
        node("strong", { text: "這份作品的生成資料" }),
        node("p", {
          text: `工作流：${capabilityLabel(detail.capability)} · ${providerMeta(detail.providerId).label}`,
        }),
      ]);
      if (detail.module) body.append(node("p", { text: `模組：${detail.module}` }));
      if (detail.characters.length) {
        body.append(node("p", { text: `角色：${detail.characters.map((item) => item.name).join("、")}` }));
      }
      for (const [key, value] of Object.entries(detail.prompt)) {
        body.append(node("div", { className: "provider-fabric__generation-prompt" }, [
          node("span", { text: promptLabels[key] || key }), node("p", { text: value }),
        ]));
      }
      const optionNodes = optionSummaryNodes(detail.options);
      if (optionNodes.length) body.append(node("dl", { className: "provider-fabric__generation-options" }, optionNodes));
      const usage = [];
      if (detail.usage.charged !== null) usage.push(`實際使用 ${detail.usage.charged} 點`);
      if (detail.usage.remaining !== null) usage.push(`餘額 ${detail.usage.remaining} 點`);
      if (usage.length) body.append(node("p", { className: "provider-fabric__generation-usage", text: usage.join(" · ") }));
      return body;
    }

    async _loadAssetGenerationDetail(asset) {
      if (!asset?.assetId || this._assetGenerationDetails.get(asset.assetId)?.loading) return;
      this._assetGenerationDetails.set(asset.assetId, { loading: true, error: "", detail: null });
      this.render();
      try {
        const raw = await requestJson(`/api/assets/${pathId(asset.assetId)}/generation-detail`);
        const detail = normalizeAssetGenerationDetail(raw, asset);
        if (!detail) throw new Error("Unsafe asset generation detail response");
        this._assetGenerationDetails.set(asset.assetId, {
          loading: false,
          error: detail.available ? "" : "這份舊素材沒有保留生成資料。",
          detail,
        });
      } catch (error) {
        this._assetGenerationDetails.set(asset.assetId, {
          loading: false, error: publicErrorMessage(error), detail: null,
        });
      }
      this.render();
    }

    async _loadAssetHandoff(asset) {
      if (!asset?.assetId || !asset.assetVersionId || this._assetHandoffs.get(asset.assetId)?.loading) return;
      this._assetHandoffs.set(asset.assetId, { loading: true, error: "", candidates: null });
      this.render();
      try {
        const raw = await requestJson(`/api/assets/${pathId(asset.assetId)}/handoff`, {
          method: "POST",
          body: JSON.stringify({
            schema_version: "castle.media-asset-handoff.v1",
            asset_version_id: asset.assetVersionId,
          }),
        });
        const candidates = normalizeAssetHandoff(raw, asset);
        if (!candidates) throw Object.assign(new Error("Unsafe media handoff response"), {
          publicMessage: "素材與可用模組的驗證結果不一致，已停止。",
        });
        this._assetHandoffs.set(asset.assetId, { loading: false, error: "", candidates });
      } catch (error) {
        this._assetHandoffs.set(asset.assetId, {
          loading: false, error: publicErrorMessage(error), candidates: null,
        });
      }
      this.render();
    }

    async _adoptLocalHistory(asset) {
      const locator = asset?.localSourceLocator;
      if (!asset?.assetId || !locator || this._localAdoptions.get(asset.assetId)?.loading) return;
      this._localAdoptions.set(asset.assetId, { loading: true, done: false, error: "" });
      this.render();
      try {
        await this._requestLocalHistoryAdoption(asset);
        this._localAdoptions.set(asset.assetId, { loading: false, done: true, error: "" });
        this._announce("作品已鎖定版本並加入統一資源；現在可供專案自動選材。", false);
        await this.refreshAssets();
      } catch (error) {
        this._localAdoptions.set(asset.assetId, {
          loading: false, done: false, error: publicErrorMessage(error),
        });
        this.render();
      }
    }

    async _requestLocalHistoryAdoption(asset) {
      const locator = asset?.localSourceLocator;
      if (!asset?.assetId || !locator) throw new Error("Local history locator is unavailable");
      const raw = await requestJson("/api/history/adopt", {
        method: "POST",
        body: JSON.stringify({
          schema_version: "castle.local-history-adoption.v1",
          filename: locator.filename,
          subfolder: locator.subfolder,
          type: locator.type,
          prompt_id: locator.promptId,
        }),
      });
      if (
        raw?.schema_version !== "castle.local-history-adoption.response.v1"
        || raw.ok !== true || !safeId(raw.asset?.projection_id)
        || !safeId(raw.asset?.asset_version_id)
        || raw.authority?.owner_scoped !== true
        || raw.authority?.history_membership_checked !== true
        || raw.authority?.file_integrity_checked !== true
        || raw.authority?.provider_transport_performed !== false
        || raw.authority?.credits_spent !== 0
        || containsSensitiveField(raw)
      ) throw new Error("Unsafe local history adoption response");
      return raw.asset;
    }

    async _adoptVisibleLocalHistory(assets) {
      if (this._state.localBulkAdopting) return;
      const candidates = safeArray(assets).filter(
        (asset) => asset?.catalogScope === "castle" && asset.localSourceLocator,
      ).slice(0, 100);
      if (!candidates.length) return;
      this._state.localBulkAdopting = true;
      let completed = 0;
      let failed = 0;
      for (const asset of candidates) {
        this._state.localBulkProgress = `正在驗證 ${completed + failed + 1} / ${candidates.length}`;
        this.render();
        try {
          await this._requestLocalHistoryAdoption(asset);
          completed += 1;
        } catch (_error) {
          failed += 1;
        }
      }
      this._state.localBulkAdopting = false;
      this._state.localBulkProgress = "";
      this._announce(
        failed
          ? `已加入 ${completed} 個；${failed} 個因權限、格式或檔案驗證未通過。`
          : `這一頁 ${completed} 個作品已加入統一資源。`,
        failed > 0,
      );
      await this.refreshAssets();
    }

    _renderPaidAction() {
      const section = node("section", {
        className: "provider-fabric__section provider-fabric__paid",
        attributes: { "aria-labelledby": "provider-fabric-paid" },
      });
      section.append(node("div", { className: "provider-fabric__section-head" }, [
        node("div", {}, [
          node("h2", {
            id: "provider-fabric-paid",
            text: this._projectQuickRun ? "試做進度" : "生成",
          }),
          node("p", {
            text: this._projectQuickRun
              ? "從專案帶入的角色、素材與鏡頭設定已保留。"
              : "單張或多張參考圖都從這裡生成；系統會自動選擇相符路線，當下價格只作紀錄。",
          }),
        ]),
      ]));
      if (
        !this._state.pendingIntent && !this._state.plan && !this._state.confirmedMessage
        && !this._state.confirmationUncertainMessage && !this._state.lastIntent
      ) {
        section.append(node("div", { className: "provider-fabric__empty provider-fabric__empty--compact" }, [
          node("strong", {
            text: this._projectQuickRun && this._state.planMessage
              ? "這次沒有開始製作"
              : (this._projectQuickRun ? "沒有收到試做內容" : "尚未選擇參考圖"),
          }),
          node("span", {
            text: this._projectQuickRun
              ? (this._state.planMessage || "請回到專案重新選擇鏡頭。")
              : "從媒體庫選擇一張或多張已驗證圖片後即可生成。",
          }),
        ]));
      }
      if (this._state.pendingIntent && !this._state.plan) {
        const intent = this._state.pendingIntent;
        const characterNames = safeArray(intent.characters).map((item) => {
          const id = safeId(item?.character_id);
          return safeText(item?.display_name)
            || this._characters.find((character) => character.id === id)?.label
            || this._state.providerCharacters.find((character) => character.id === id)?.label
            || "已選角色";
        });
        const planButton = node("button", {
          className: "provider-fabric__button",
          type: "button",
          text: this._state.planning
            ? "正在建立工作…"
            : (this._projectQuickRun ? "開始試做" : "生成"),
          attributes: { disabled: this._state.planning ? "" : null },
        });
        planButton.addEventListener("click", () => this._requestPlan({ autoConfirm: true }));
        section.append(node("div", { className: "provider-fabric__quote" }, [
          node("h3", { text: this._projectQuickRun ? "正在準備這個鏡頭" : "準備製作" }),
          this._projectQuickRun ? node("p", {
            text: characterNames.length
              ? `使用角色：${characterNames.join("、")}`
              : "角色與鏡頭設定已帶入。",
          }) : node("dl", { className: "provider-fabric__quote-meta" }, [
            node("div", {}, [node("dt", { text: "要做什麼" }), node("dd", {
              text: safeId(intent.action_id)
                ? handoffActionLabel(safeId(intent.action_id))
                : capabilityLabel(safeId(intent.capability)),
            })]),
            node("div", {}, [node("dt", { text: "角色" }), node("dd", {
              text: characterNames.length ? characterNames.join("、") : "未指定角色",
            })]),
            node("div", {}, [node("dt", { text: "角色類型" }), node("dd", {
              text: visualClassLabel(safeText(intent.visual_class)),
            })]),
            node("div", {}, [node("dt", { text: "使用素材" }), node("dd", {
              text: safeArray(intent.assets).map(intentAssetLabel).join("、") || "不需要素材",
            })]),
            ...optionSummaryNodes(intent.options),
          ]),
          node("p", { text: this._state.planMessage || "參考圖已準備好。" }),
          this._projectQuickRun ? null : planButton,
        ]));
      }
      if (this._state.plan) {
        section.append(this._projectQuickRun
          ? node("div", {
            className: "provider-fabric__notice",
            text: "路線已選好，正在建立工作…",
            attributes: { role: "status" },
          })
          : this._renderQuote());
      }
      if (this._state.confirmedMessage) {
        section.append(node("div", {
          className: "provider-fabric__notice provider-fabric__notice--success",
          text: this._state.confirmedMessage,
          attributes: { role: "status" },
        }));
      }
      if (this._state.confirmationUncertainMessage) {
        section.append(node("div", {
          className: `provider-fabric__notice provider-fabric__confirmation-uncertain${
            this._projectQuickRun ? "" : " provider-fabric__notice--error"
          }`,
          text: this._state.confirmationUncertainMessage,
          attributes: { role: this._projectQuickRun ? "status" : "alert" },
        }));
      }
      if (this._state.lastIntent) section.append(this._renderLastIntentStatus());
      return section;
    }

    _renderLastIntentStatus() {
      const intent = this._state.lastIntent;
      const copy = INTENT_STATUS_COPY[intent.state];
      const detail = intent.state === "completed" && intent.outputVerified
        ? "供應商已完成，輸出已經 Castle 驗證並納入媒體庫。"
        : copy.detail;
      const provider = providerMeta(intent.providerId);
      const refresh = node("button", {
        className: "provider-fabric__button provider-fabric__button--quiet",
        type: "button",
        text: this._state.lastIntentRefreshing ? "讀取中…" : "重新整理狀態",
        attributes: { disabled: this._state.lastIntentRefreshing ? "" : null },
      });
      refresh.addEventListener("click", () => this._refreshLastIntentStatus());
      const card = node("article", {
        className: "provider-fabric__intent-status",
        attributes: { "aria-labelledby": "provider-fabric-last-intent-title" },
      });
      const metadata = this._projectQuickRun ? null : node("dl", {
        className: "provider-fabric__quote-meta provider-fabric__intent-status-meta",
      }, [
        node("div", {}, [node("dt", { text: "工作識別碼" }), node("dd", { text: intent.intentId })]),
        node("div", {}, [node("dt", { text: "功能" }), node("dd", { text: intent.capability })]),
        node("div", {}, [node("dt", { text: "角色類型" }), node("dd", { text: visualClassLabel(intent.visualClass) })]),
        node("div", {}, [node("dt", { text: "授權點數" }), node("dd", { text: `${intent.creditsAuthorized} 點` })]),
        node("div", {}, [node("dt", { text: "已扣點數" }), node("dd", { text: intent.creditsCharged === null ? "尚未回報" : `${intent.creditsCharged} 點` })]),
        node("div", {}, [node("dt", { text: "剩餘點數" }), node("dd", { text: intent.remainingCredits === null ? "尚未回報" : `${intent.remainingCredits} 點` })]),
        node("div", {}, [node("dt", { text: "供應商傳輸" }), node("dd", { text: intent.transportMayHaveOccurred ? "可能已發生" : "目前記錄未顯示送出" })]),
        node("div", {}, [node("dt", { text: "供應商收據" }), node("dd", { text: intent.providerReceiptAvailable ? "已有持久紀錄" : "尚無持久紀錄" })]),
        node("div", {}, [node("dt", { text: "輸出驗證" }), node("dd", {
          text: intent.outputVerified ? "已驗證並納入媒體庫" : "尚未驗證或納入媒體庫",
        })]),
        node("div", {}, [node("dt", { text: "最後更新" }), node("dd", { text: intent.updatedAt || "尚未手動查詢" })]),
      ]);
      card.append(
        node("div", { className: "provider-fabric__intent-status-head" }, [
          node("div", {}, [
            node("span", { className: `provider-fabric__badge provider-fabric__badge--${provider.tone}`, text: provider.badge }),
            node("h3", {
              id: "provider-fabric-last-intent-title",
              text: this._projectQuickRun
                ? "這個鏡頭的製作進度"
                : this._state.confirmationUncertainPlanId
                ? "先前已確認工作（不屬於上述不確定請求）"
                : "最近一次已確認工作",
            }),
          ]),
          node("span", { className: `provider-fabric__status provider-fabric__status--${copy.tone}`, text: copy.label }),
        ]),
        node("p", { className: "provider-fabric__intent-status-copy", text: detail }),
      );
      if (metadata) card.append(metadata);
      if (this._state.lastIntentMessage) {
        card.append(node("div", {
          className: `provider-fabric__notice${this._state.lastIntentError ? " provider-fabric__notice--error" : ""}`,
          text: this._state.lastIntentMessage,
          attributes: { role: this._state.lastIntentError ? "alert" : "status" },
        }));
      }
      card.append(node("div", { className: "provider-fabric__intent-status-actions" }, [
        node("p", {
          text: this._projectQuickRun
            ? "這頁會自動更新進度；手動更新也不會建立另一份工作。"
            : "只讀取 Castle 的持久狀態，不會查詢供應商、不會花點，也不會自動輪詢。",
        }),
        refresh,
      ]));
      return card;
    }

    async _requestPlan({ autoConfirm = false } = {}) {
      if (autoConfirm) return this._submitGeneration();
      if (
        this._mediaOnly
        || this._state.confirmationUncertainPlanId
        || !this._state.pendingIntent
        || this._state.plan
        || this._planRequest
      ) return;
      const request = this._state.pendingIntent;
      const revision = this._intentRevision;
      const controller = new AbortController();
      let confirmAutomatically = false;
      this._planRequest = controller;
      this._state.planning = true;
      this._state.planMessage = this._projectQuickRun
        ? "Castle 正在選擇可用方式並建立工作…"
        : "正在取得報價；目前仍未送出生成。";
      this.render();
      try {
        const data = await requestJson(PLAN_ENDPOINT, {
          method: "POST",
          body: JSON.stringify(request),
          signal: controller.signal,
        });
        if (
          controller.signal.aborted
          || revision !== this._intentRevision
          || this._state.pendingIntent !== request
        ) return;
        if (data.confirmation_required !== true || data.provider_write_performed !== false || data.credits_spent !== 0) {
          throw Object.assign(new Error("Unsafe plan response"), {
            publicMessage: "後端沒有證明這次只做規劃，已停止流程。",
          });
        }
        if (!planResponseMatchesRequest(data, request)) {
          throw Object.assign(new Error("Plan binding mismatch"), {
            publicMessage: "後端報價與目前選擇的角色、素材或選項不一致，已停止流程。",
          });
        }
        const planId = safeId(data.plan_id);
        const token = typeof data.plan_token === "string" ? data.plan_token : "";
        const credits = data.quote && data.quote.credits;
        const selectedAccountId = safeId(data.selected_account_id);
        if (
          !planId
          || data.plan_id !== planId
          || !token
          || !Number.isSafeInteger(credits)
          || credits < 0
          || !selectedAccountId
          || data.selected_account_id !== selectedAccountId
        ) {
          throw Object.assign(new Error("Invalid plan response"), {
            publicMessage: "報價資料不完整，已停止流程。",
          });
        }
        this._state.plan = {
          planId,
          token,
          credits,
          selectedAccountId,
          providerId: safeId(data.decision && data.decision.selected_provider_id),
          variantId: safeId(data.decision && data.decision.selected_variant_id),
          profileKey: safeText(request.profile_key),
          actionId: safeId(request.action_id),
          expiresAt: Number.isInteger(data.expires_at) ? data.expires_at : 0,
          capability: safeText(data.intent && data.intent.capability),
          visualClass: safeText(data.intent && data.intent.visual_class),
          characters: safeArray(data.intent && data.intent.characters).map((item) => Object.freeze({
            id: safeId(item && item.character_id),
            name: safeText(item && item.display_name),
          })).filter((item) => item.id),
          assets: safeArray(data.intent && data.intent.assets).map((item) => Object.freeze({
            id: safeId(item && item.asset_id), kind: safeId(item && item.kind),
          })).filter((item) => item.id),
          characterIds: safeArray(data.intent && data.intent.characters).map(
            (item) => safeId(item && item.character_id)
          ).filter(Boolean),
          assetIds: safeArray(data.intent && data.intent.assets).map(
            (item) => safeId(item && item.asset_id)
          ).filter(Boolean),
          options: Object.freeze({ ...(data.options || {}) }),
          maxCredits: data.spend_guard.max_credits,
        };
        this._state.planMessage = this._projectQuickRun
          ? "可用方式已選好，正在建立工作…"
          : `已記錄當下價格 ${credits} 點；正在建立生成工作。`;
        confirmAutomatically = autoConfirm;
      } catch (error) {
        if (
          controller.signal.aborted
          || revision !== this._intentRevision
          || this._state.pendingIntent !== request
        ) return;
        this._discardPlan({ keepIntent: true });
        this._state.planMessage = publicErrorMessage(error);
      } finally {
        if (this._planRequest === controller) this._planRequest = null;
        if (revision === this._intentRevision) this._state.planning = false;
        this.render();
      }
      if (
        confirmAutomatically
        && revision === this._intentRevision
        && this._state.plan?.planId
      ) await this._confirmPlan(null, null, { automatic: true });
    }

    async _submitGeneration() {
      if (
        this._mediaOnly
        || !this._state.pendingIntent
        || this._state.plan
        || this._planRequest
      ) return;
      const request = this._state.pendingIntent;
      const requestId = this._state.generationRequestId || newGenerationRequestId();
      this._state.generationRequestId = requestId;
      const revision = this._intentRevision;
      const controller = new AbortController();
      this._planRequest = controller;
      this._state.planning = true;
      this._state.planMessage = "正在建立工作…";
      this.render();
      try {
        let data = null;
        let lastError = null;
        for (let attempt = 0; attempt < 2; attempt += 1) {
          try {
            data = await requestJson(GENERATION_ENDPOINT, {
              method: "POST",
              body: JSON.stringify({
                schema_version: SCHEMA_VERSION,
                request_id: requestId,
                plan: request,
              }),
              signal: controller.signal,
            });
            break;
          } catch (error) {
            lastError = error;
            const deterministicRejection = Number.isInteger(error?.status)
              && error.status >= 400 && error.status < 500;
            if (deterministicRejection || controller.signal.aborted || attempt > 0) throw error;
          }
        }
        if (!data) throw lastError || new Error("Generation response unavailable");
        if (
          controller.signal.aborted
          || revision !== this._intentRevision
          || this._state.pendingIntent !== request
        ) return;
        const decision = data.decision && typeof data.decision === "object" ? data.decision : {};
        const quote = data.quote && typeof data.quote === "object" ? data.quote : {};
        const providerId = safeId(decision.selected_provider_id);
        const credits = quote.credits;
        const selectedAccountId = safeId(data.outbox && data.outbox.account_id);
        const confirmation = Object.freeze({
          planId: safeId(data.plan_id),
          credits,
          selectedAccountId,
          providerId,
          capability: safeId(request.capability),
          visualClass: safeText(request.visual_class),
        });
        const confirmationRaw = {};
        for (const field of [
          "schema_version", "plan_id", "intent_id", "decision_id", "outbox",
          "client_request_id", "state", "credits_authorized", "credits_spent",
          "transport_dispatched", "next_action",
        ]) confirmationRaw[field] = data[field];
        const confirmed = normalizeConfirmationResponse(confirmationRaw, confirmation);
        if (
          !confirmed
          || data.request_id !== requestId
          || data.confirmation_required !== false
          || data.status_path !== `/api/intents/${confirmed.intentId}/status`
          || quote.provider_id !== providerId
          || quote.capability !== request.capability
          || !Number.isSafeInteger(credits)
          || credits < 0
          || (safeId(request.provider_id) && request.provider_id !== providerId)
        ) {
          throw Object.assign(new Error("Unsafe generation response"), {
            publicMessage: "工作回應不完整，請重新整理任務狀態。",
          });
        }
        this._state.pendingIntent = null;
        this._state.generationRequestId = "";
        this._state.confirmationUncertainMessage = "";
        this._state.confirmationUncertainPlanId = "";
        this._state.planMessage = "";
        this._state.confirmedMessage = this._projectQuickRun
          ? "已開始製作這個鏡頭。"
          : `已排入${providerMeta(providerId).label}工作。`;
        this._rememberConfirmedIntent({
          intentId: confirmed.intentId,
          providerId,
          capability: confirmation.capability,
          visualClass: confirmation.visualClass,
          creditsAuthorized: credits,
        });
        this.dispatchEvent(new CustomEvent("castle-provider-confirmed", {
          bubbles: true,
          composed: true,
          detail: Object.freeze({
            intentId: confirmed.intentId,
            state: "pending",
            creditsAuthorized: credits,
          }),
        }));
      } catch (error) {
        if (
          controller.signal.aborted
          || revision !== this._intentRevision
          || this._state.pendingIntent !== request
        ) return;
        this._state.confirmedMessage = "";
        const deterministicRejection = Number.isInteger(error?.status)
          && error.status >= 400 && error.status < 500;
        this._state.planMessage = deterministicRejection
          ? `這次沒有建立工作：${publicErrorMessage(error)}`
          : "暫時沒收到工作回應；可以直接再按一次，系統會沿用同一請求，不會重複建立或扣點。";
        this._announce(this._state.planMessage, deterministicRejection);
      } finally {
        if (this._planRequest === controller) this._planRequest = null;
        if (revision === this._intentRevision) this._state.planning = false;
        this.render();
      }
    }

    _renderQuote() {
      const plan = this._state.plan;
      const characterNames = plan.characters.map((character) => character.name
        || this._characters.find((item) => item.id === character.id)?.label
        || this._state.providerCharacters.find((item) => item.id === character.id)?.label
        || "已選角色");
      const confirm = node("button", {
        className: "provider-fabric__button",
        type: "button",
        text: "生成",
      });
      confirm.addEventListener("click", () => this._confirmPlan(
        confirm, null, { automatic: true },
      ));
      const provider = providerMeta(plan.providerId);
      const cancel = node("button", {
        className: "provider-fabric__button provider-fabric__button--quiet",
        type: "button",
        text: "取消這份報價",
      });
      cancel.addEventListener("click", () => {
        this._discardPlan();
        this._state.planMessage = "";
        this.render();
      });
      return node("div", { className: "provider-fabric__quote", attributes: { role: "group", "aria-labelledby": "provider-fabric-quote-title" } }, [
        node("div", { className: "provider-fabric__quote-head" }, [
          node("div", {}, [
            node("span", { className: `provider-fabric__badge provider-fabric__badge--${provider.tone}`, text: provider.badge }),
            node("h3", { id: "provider-fabric-quote-title", text: "當下價格紀錄" }),
          ]),
          node("strong", { className: "provider-fabric__credits", text: `${plan.credits} 點` }),
        ]),
        node("dl", { className: "provider-fabric__quote-meta" }, [
          node("div", {}, [node("dt", { text: "來源" }), node("dd", { text: provider.label })]),
          node("div", {}, [node("dt", { text: "要做什麼" }), node("dd", {
            text: plan.actionId ? handoffActionLabel(plan.actionId) : capabilityLabel(plan.capability),
          })]),
          node("div", {}, [node("dt", { text: "角色類型" }), node("dd", { text: visualClassLabel(plan.visualClass) })]),
          node("div", {}, [node("dt", { text: "角色" }), node("dd", { text: characterNames.length ? characterNames.join("、") : "未指定" })]),
          node("div", {}, [node("dt", { text: "素材" }), node("dd", {
            text: plan.assets.map((asset) => intentAssetLabel({
              asset_id: asset.id, kind: asset.kind,
            })).join("、") || "不需要素材",
          })]),
          ...optionSummaryNodes(plan.options),
          node("div", {}, [node("dt", { text: "報價有效至" }), node("dd", { text: plan.expiresAt ? new Date(plan.expiresAt * 1000).toLocaleTimeString("zh-Hant") : "無法確認" })]),
          node("div", {}, [node("dt", { text: "目前花費" }), node("dd", { text: "0 點" })]),
        ]),
        node("details", { className: "provider-fabric__technical-details" }, [
          node("summary", { text: "技術資訊" }),
          node("dl", { className: "provider-fabric__quote-meta" }, [
            node("div", {}, [node("dt", { text: "模組版本" }), node("dd", { text: plan.variantId || "已由後端鎖定" })]),
            node("div", {}, [node("dt", { text: "規格設定" }), node("dd", { text: plan.profileKey || "已由後端鎖定" })]),
          ]),
        ]),
        node("div", { className: "provider-fabric__form-actions" }, [
          cancel,
          confirm,
        ]),
      ]);
    }

    async _confirmPlan(button, consent, { automatic = false } = {}) {
      const plan = this._state.plan;
      if (this._mediaOnly) return;
      if (!plan || (!automatic && !consent?.checked) || this._confirming) return;
      const confirmation = Object.freeze({
        planId: plan.planId,
        credits: plan.credits,
        selectedAccountId: plan.selectedAccountId,
        providerId: plan.providerId,
        capability: plan.capability,
        visualClass: plan.visualClass,
      });
      let requestBody = JSON.stringify({
        schema_version: SCHEMA_VERSION,
        plan_token: plan.token,
        confirmed: true,
        accepted_credits: confirmation.credits,
      });
      this._confirming = true;
      if (button) button.disabled = true;
      if (consent) consent.disabled = true;
      this._discardPlan();
      try {
        const confirmationRequest = requestJson(`/api/intents/${confirmation.planId}/confirm`, {
          method: "POST",
          body: requestBody,
        });
        requestBody = "";
        const data = await confirmationRequest;
        const confirmed = normalizeConfirmationResponse(data, confirmation);
        if (!confirmed) {
          throw Object.assign(new Error("Unsafe confirmation response"), {
            publicMessage: "後端沒有證明工作仍未傳送，請到任務中心確認狀態。",
          });
        }
        const intentId = confirmed.intentId;
        this._state.confirmationUncertainMessage = "";
        this._state.confirmationUncertainPlanId = "";
        this._state.confirmedMessage = this._projectQuickRun
          ? "已開始製作這個鏡頭。"
          : "已建立待處理工作；本頁沒有直接呼叫供應商，也沒有自動重送。";
        this._rememberConfirmedIntent({
          intentId,
          providerId: confirmation.providerId,
          capability: confirmation.capability,
          visualClass: confirmation.visualClass,
          creditsAuthorized: confirmation.credits,
        });
        this.dispatchEvent(new CustomEvent("castle-provider-confirmed", {
          bubbles: true,
          composed: true,
          detail: Object.freeze({
            intentId,
            state: "pending",
            creditsAuthorized: confirmation.credits,
          }),
        }));
      } catch (error) {
        this._state.confirmedMessage = "";
        const deterministicRejection = Number.isInteger(error?.status)
          && error.status >= 400 && error.status < 500;
        if (deterministicRejection) {
          this._state.confirmationUncertainPlanId = "";
          this._state.confirmationUncertainMessage = "";
          this._state.planMessage = this._projectQuickRun
            ? "這次沒有建立工作。請回到專案再按一次試做。"
            : `這次沒有建立工作：${publicErrorMessage(error)}`;
          this._announce(this._state.planMessage, true);
        } else if (this._projectQuickRun) {
          this._state.confirmationUncertainPlanId = confirmation.planId;
          this._state.confirmedMessage = "工作已送出，Castle 正在核對進度…";
          this.render();
          const reconciled = await this._reconcileProjectConfirmation(confirmation);
          if (!reconciled) {
            this._state.confirmedMessage = "";
            this._state.confirmationUncertainMessage = "這次工作的狀態還在核對中。你不需要重新操作；稍後可在這裡更新進度。";
            this._announce(this._state.confirmationUncertainMessage, false);
          }
        } else {
          this._state.confirmationUncertainPlanId = confirmation.planId;
          this._state.confirmationUncertainMessage = "工作建立回應中斷，Castle 正在核對是否已建立；目前不需要再次操作。";
          this._announce(this._state.confirmationUncertainMessage, false);
        }
      } finally {
        requestBody = "";
        this._confirming = false;
        this.render();
      }
    }

    async _reconcileProjectConfirmation(confirmation) {
      const intentId = await deterministicIntentIdForPlan(confirmation.planId);
      if (!intentId || !this.isConnected) return false;
      const expected = Object.freeze({
        intentId,
        providerId: confirmation.providerId,
        capability: confirmation.capability,
        visualClass: confirmation.visualClass,
        creditsAuthorized: confirmation.credits,
      });
      for (const delay of [0, 150, 450, 900, 1800]) {
        if (delay) await new Promise((resolve) => window.setTimeout(resolve, delay));
        if (!this.isConnected || !this._projectQuickRun) return false;
        try {
          const data = await requestJson(`/api/intents/${intentId}/status`);
          const normalized = normalizeIntentStatus(data, expected);
          if (!normalized) continue;
          this._state.confirmationUncertainPlanId = "";
          this._state.confirmationUncertainMessage = "";
          this._state.confirmedMessage = "已找到這個鏡頭的工作，製作正在繼續。";
          this._state.lastIntent = normalized;
          this._state.lastIntentMessage = "已自動接回工作進度。";
          this._state.lastIntentError = false;
          this._scheduleProjectStatusRefresh();
          this.dispatchEvent(new CustomEvent("castle-provider-confirmed", {
            bubbles: true,
            composed: true,
            detail: Object.freeze({
              intentId,
              state: normalized.state,
              creditsAuthorized: confirmation.credits,
            }),
          }));
          return true;
        } catch (_error) {
          /* A just-created durable record can briefly be unavailable. */
        }
      }
      return false;
    }

    _rememberConfirmedIntent({ intentId, providerId, capability, visualClass, creditsAuthorized }) {
      this._cancelIntentStatusRequest();
      this._state.lastIntent = Object.freeze({
        intentId,
        providerId,
        capability,
        visualClass,
        state: "pending",
        terminal: false,
        retryAvailable: false,
        transportMayHaveOccurred: false,
        providerReceiptAvailable: false,
        outputVerified: false,
        creditsAuthorized,
        creditsCharged: null,
        remainingCredits: null,
        createdAt: "",
        updatedAt: "",
        statusChecked: false,
      });
      this._state.lastIntentMessage = this._projectQuickRun
        ? "工作已建立，Castle 會繼續處理。"
        : "確認已成功；目前只知道 Castle 已建立待處理工作。需要時可手動讀取最新持久狀態。";
      this._state.lastIntentError = false;
      this._scheduleProjectStatusRefresh({ delay: 0 });
    }

    async _refreshLastIntentStatus({ automatic = false } = {}) {
      const intent = this._state.lastIntent;
      if (!intent || this._intentStatusRequest) return;
      const controller = new AbortController();
      const revision = this._intentStatusRevision;
      const intentId = intent.intentId;
      this._intentStatusRequest = controller;
      this._state.lastIntentRefreshing = true;
      if (!automatic) {
        this._state.lastIntentMessage = this._projectQuickRun
          ? "正在更新這個鏡頭的進度…"
          : "正在讀取 Castle 的持久狀態；不會呼叫供應商。";
      }
      this._state.lastIntentError = false;
      if (!automatic) this.render();
      try {
        const data = await requestJson(`/api/intents/${intentId}/status`, {
          signal: controller.signal,
        });
        if (
          controller.signal.aborted
          || this._intentStatusRequest !== controller
          || revision !== this._intentStatusRevision
          || this._state.lastIntent !== intent
          || this._state.lastIntent.intentId !== intentId
        ) return;
        const normalized = normalizeIntentStatus(data, intent);
        if (!normalized) {
          throw Object.assign(new Error("Unsafe intent status response"), {
            publicMessage: "最新狀態資料不完整或與已確認工作不一致，已拒絕採用。",
          });
        }
        this._state.lastIntent = normalized;
        this._state.lastIntentMessage = this._projectQuickRun
          ? (automatic ? "進度已自動更新。" : "進度已更新。")
          : "已讀取 Castle 的持久狀態；這次查詢沒有呼叫供應商，也沒有花點。";
        this._state.lastIntentError = false;
      } catch (error) {
        if (
          controller.signal.aborted
          || this._intentStatusRequest !== controller
          || revision !== this._intentStatusRevision
          || this._state.lastIntent !== intent
        ) return;
        const detail = publicErrorMessage(error) || "無法確認最新狀態。";
        this._state.lastIntentMessage = this._projectQuickRun
          ? (automatic
            ? "暫時讀不到最新進度，Castle 會自動再試。"
            : "目前讀不到最新進度，原本的工作仍會繼續；稍後再按一次更新即可。")
          : `工作 ${intentId} 已確認成功；${detail} 原本的工作識別與最後可信狀態仍保留，請勿重新送出。`;
        this._state.lastIntentError = !automatic;
      } finally {
        if (this._intentStatusRequest === controller) {
          this._intentStatusRequest = null;
          this._state.lastIntentRefreshing = false;
          this.render();
          this._scheduleProjectStatusRefresh();
        }
      }
    }

    _scheduleProjectStatusRefresh({ delay = PROJECT_STATUS_REFRESH_MS } = {}) {
      this._stopProjectStatusRefresh();
      const intent = this._state.lastIntent;
      if (
        !this._projectQuickRun || !this.isConnected || !intent || intent.terminal
        || !Number.isSafeInteger(delay) || delay < 0
      ) return;
      this._projectStatusTimer = window.setTimeout(() => {
        this._projectStatusTimer = null;
        if (!this.isConnected || !this._projectQuickRun || this._state.lastIntent?.terminal) return;
        if (this._intentStatusRequest) {
          this._scheduleProjectStatusRefresh();
          return;
        }
        this._refreshLastIntentStatus({ automatic: true });
      }, delay);
    }

    _stopProjectStatusRefresh() {
      if (this._projectStatusTimer !== null) window.clearTimeout(this._projectStatusTimer);
      this._projectStatusTimer = null;
    }

    _discardPlan({ keepIntent = false } = {}) {
      this._intentRevision += 1;
      this._cancelPlanRequest();
      if (this._state.plan) this._state.plan.token = "";
      this._state.plan = null;
      if (!keepIntent) {
        this._state.pendingIntent = null;
        this._state.generationRequestId = "";
      }
    }

    _cancelPlanRequest() {
      if (this._planRequest) this._planRequest.abort();
      this._planRequest = null;
      this._state.planning = false;
    }

    _cancelIntentStatusRequest() {
      const wasRefreshing = Boolean(this._intentStatusRequest) || this._state.lastIntentRefreshing;
      this._intentStatusRevision += 1;
      if (this._intentStatusRequest) this._intentStatusRequest.abort();
      this._intentStatusRequest = null;
      this._state.lastIntentRefreshing = false;
      if (wasRefreshing && this._state.lastIntent) {
        this._state.lastIntentMessage = "狀態讀取已停止；已保留最後可信狀態。需要時可手動重新整理。";
        this._state.lastIntentError = false;
      }
    }

    _announce(message, isError) {
      const safeMessage = safeText(message, "操作沒有完成。" );
      if (this._statusRegion && this._statusRegion.isConnected) {
        this._statusRegion.textContent = safeMessage;
        this._statusRegion.classList.toggle("is-error", isError === true);
      }
    }
  }

  if (!customElements.get("castle-provider-fabric")) {
    customElements.define("castle-provider-fabric", CastleProviderFabric);
  }

  window.CastleProviderFabric = Object.freeze({
    mount(host, options = {}) {
      if (!(host instanceof Element)) throw new TypeError("A host element is required");
      const component = document.createElement("castle-provider-fabric");
      component.configure(options);
      host.replaceChildren(component);
      return component;
    },
  });
})();

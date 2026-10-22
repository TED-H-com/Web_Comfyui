"use strict";

const $ = (id) => document.getElementById(id);

let authMode = "login";
let authStatus = { lane: "local", user: null, accessEmail: "", needsSetup: false, configured: true, authenticated: false };
let currentUser = null;
let studios = [];
let features = [];
let currentStudio = null;
let currentFeatureId = null;
let studioRouteProblem = "";
let currentView = "characters"; // "characters" | "studio" | "projects" | "library" | "resources" | "dance-library" | "orchestrator" | "admin-users"
let currentRoute = { view: "characters" };
let characters = [];
let charactersLoaded = false;
let providerCharacters = [];
let providerCharactersLoaded = false;
let providerCharactersError = "";
let characterActions = [];
let selectedCharacterId = "";
let activeCharacterId = "";
let selectedCharacterActionId = "";
let editorOverlayCharacterId = "";
let historyItems = [];
const catalogueViewState = new Map();
let historyLoadError = "";
const OWNER_HISTORY_ENDPOINT = "/api/history?max_items=500&scope=own";
let localHistoryOpenRevision = 0;
let localHistoryBridgeListening = false;
let libraryPosts = [];
let libraryPostsLoaded = false;
let libraryPostsError = "";
let selectedHistoryKey = "";
let showHiddenCharacterLibrary = false;
let showHiddenGlobalLibrary = false;
let librarySearchQuery = "";
let libraryVisibleLimit = 24;
let librarySearchTimer = null;
let orchestratorMobilePane = "overview";
let lastHistoryFingerprint = "";
const ACTIVE_JOBS_KEY = "castle.activeJobs.v2";
const TASK_SEEN_KEY = "castle.taskSeen.v1";
const LAST_ACTION_KEY = "castle.lastCharacterAction.v1";
const AUDIO_EXPORT_PREF_KEY = "castle.audioExportFormat.v1";
const AUDIO_EXPORT_FORMAT_OPTIONS = [
  { value: "wav", label: "WAV", sub: "未壓縮・適合剪輯" },
  { value: "mp3", label: "MP3", sub: "檔案較小・通用" },
  { value: "mp4", label: "MP4", sub: "720p 黑底影片・直接上傳" },
];
const DANCE_LIBRARY_ENDPOINTS = Object.freeze({
  catalog: "/api/dance-library",
  import: "/api/dance-library/import",
  prepare: "/api/dance-library/prepare",
  manage: "/api/dance-library/manage",
  update: "/api/dance-library/update",
  rescan: "/api/dance-library/rescan",
});
const DANCE_LIBRARY_STATUSES = Object.freeze([
  { value: "pending", label: "待整理" },
  { value: "approved", label: "已核准" },
  { value: "hidden", label: "已隱藏" },
  { value: "problem", label: "有問題" },
]);
const DANCE_COMPATIBILITY_OPTIONS = Object.freeze([
  { value: "anime", label: "動漫角色" },
  { value: "realistic", label: "寫實角色" },
]);
const CHARACTER_DRAFT_SCHEMA = 1;
const WARDROBE_REFERENCE_MAX_FILE_BYTES = 15 * 1024 * 1024;
const ACTIVE_JOB_ERROR_TTL_MS = 48 * 60 * 60 * 1000;
const CHAINED_VOICE_TTL_MS = 2 * 60 * 60 * 1000;
const TRACKED_JOB_MISSING_GRACE_MS = 45 * 1000;
const historyDetailCache = new Map();
let queueJobs = [];
let queueSummary = { running: 0, pending: 0, total: 0, external: 0 };
let queueRecentErrors = [];
let queuePanelOpen = false;
let taskScope = "own";
let terminalJobs = [];
let queueTerminalJobs = [];
const jobEvents = new Map();
let queueEventSource = null;
let queueEventsWanted = false;
let queueReconnectTimer = null;
let queueSnapshotLoaded = false;
let historySnapshotLoaded = false;
let queueRefreshesInFlight = 0;
let historyRefreshesInFlight = 0;
let trackedPromptMissing = { promptId: "", since: 0 };
const historyRefreshTimers = new Set();
const foregroundRefreshes = new Set();
let orchestratorRuns = [];
let orchestratorSummary = { runs: {}, tasks: {}, active: [] };
let orchestratorResources = { resources: [], summary: {} };
let selectedOrchestratorRunId = "";
let orchestratorRunDetail = null;
let orchestratorCursor = 0;
let orchestratorArtifacts = [];
let orchestratorArtifactFilters = { q: "", tag: "", artifact_type: "" };
const fieldGetters = {};
const fileInputs = {};
// slot id -> filename already on the server (from 方式二：從網址下載)
const serverFiles = {};
const wardrobeCache = new Map();
const wardrobeLoadPromises = new Map();
const wardrobeRevisions = new Map();
const characterPostsCache = new Map();
const danceTemplateCache = new Map();
let activeDancePreviewVideo = null;
let dancePreviewObserver = null;
let danceLibraryReturnRoute = null;
let danceManagerSearchTimer = null;
let danceLibraryManageState = {
  items: [],
  loaded: false,
  loading: false,
  error: "",
  message: "",
  status: "pending",
  query: "",
  selected: new Set(),
};
let adminUsers = [];
let adminUsersMessage = "";
let adminUsersError = "";
let experimentalCreateOpen = false;
let draftFlushNow = null;
const draftRevisions = new Map();
let draftStorageWarningShown = false;
let pendingRenderSnapshot = null;
let pendingProviderIntent = null;
let pendingProviderAutoStart = false;
let pendingResourceFocus = "";
let cachedProjectPreview = null;
let composerBinding = null;
let submitInFlight = false;
let trackedPromptId = "";
let submissionSequence = 0;
let sessionEpoch = 0;
let generationFsm = { state: "disabled", reason: "", detail: "", pct: 0, resultPromptId: "", postResult: null, composerKey: "" };
const sheetStack = [];
let sheetAfterPop = null;
let bodyScrollY = 0;
let composerHasFocus = false;
let lastComposerFieldState = null;
let preferredAudioExportFormat = "wav";
let voiceBatchState = {
  step: "input",
  source: "paste",
  rawText: "",
  segments: [],
  skipped: [],
  error: "",
  message: "",
  history: [],
  historyLoaded: false,
  batch: null,
  busy: false,
  pollTimer: null,
  cacheTick: Date.now(),
  loadSeq: 0,
};

// ---- tiny DOM builder (no innerHTML with dynamic data) --------------------
function h(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "html") node.innerHTML = value; // only used with static strings
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (key === "dataset") Object.assign(node.dataset, value);
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined || child === false) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function safePublicErrorText(value, fallback = "", maxLength = 600) {
  let text = String(value || "").replace(/[\u0000-\u001f\u007f]+/g, " ").replace(/\s+/g, " ").trim();
  if (!text) return fallback;
  text = text
    .replace(/\b(?:aip|whsec)_[A-Za-z0-9_-]{8,}\b/gi, "[秘密已隱藏]")
    .replace(/\bBearer\s+\S+/gi, "Bearer [秘密已隱藏]")
    .replace(/\/(?:home|root|tmp|etc|var)\/[^\s,，;；)）\]]+/g, "[內部路徑已隱藏]")
    .replace(/[A-Za-z]:\\[^\s,，;；)）\]]+/g, "[內部路徑已隱藏]");
  return text.slice(0, Math.max(1, maxLength));
}

function technicalDetailsDisclosure(detail) {
  const text = safePublicErrorText(detail, "", 600);
  if (!text) return null;
  return h("details", { class: "error-technical-details" }, [
    h("summary", { text: "技術詳情" }),
    h("code", { text }),
  ]);
}

function showAlert(message, error = false, options = {}) {
  const el = $("alert");
  const publicMessage = error
    ? safePublicErrorText(message, "要求未完成，請稍後重試。", 360)
    : String(message || "");
  const technical = error ? technicalDetailsDisclosure(options.technicalDetail) : null;
  clear(el);
  if (publicMessage) el.appendChild(h("span", { class: "alert-copy", text: publicMessage }));
  if (technical) el.appendChild(technical);
  el.className = `alert${error ? " error" : ""}${publicMessage || technical ? "" : " hidden"}`;
  el.setAttribute("role", error ? "alert" : "status");
  el.setAttribute("aria-live", error ? "assertive" : "polite");
}

function readJsonStorage(key, fallback) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "");
    return value === null || value === undefined ? fallback : value;
  } catch (_) {
    return fallback;
  }
}

function writeJsonStorage(key, value) {
  localStorage.setItem(key, JSON.stringify(value));
}

function normalizeAudioExportFormat(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return AUDIO_EXPORT_FORMAT_OPTIONS.some((option) => option.value === normalized) ? normalized : "wav";
}

preferredAudioExportFormat = normalizeAudioExportFormat(readJsonStorage(AUDIO_EXPORT_PREF_KEY, "wav"));

function setPreferredAudioExportFormat(value) {
  preferredAudioExportFormat = normalizeAudioExportFormat(value);
  try {
    writeJsonStorage(AUDIO_EXPORT_PREF_KEY, preferredAudioExportFormat);
  } catch (_) {
    /* Private browsing/storage denial should not block choosing a download. */
  }
  document.querySelectorAll("[data-audio-format-choice]").forEach((button) => {
    button.classList.toggle("active", button.dataset.value === preferredAudioExportFormat);
    button.setAttribute("aria-pressed", button.dataset.value === preferredAudioExportFormat ? "true" : "false");
  });
  document.querySelectorAll("[data-audio-result-format]").forEach((link) => {
    link.classList.toggle("preferred", link.dataset.audioResultFormat === preferredAudioExportFormat);
  });
  document.querySelectorAll("[data-audio-item-key]").forEach((link) => {
    const item = historyItems.find((candidate) => historyKey(candidate) === link.dataset.audioItemKey);
    const download = item ? audioExportDownload(item, preferredAudioExportFormat) : null;
    if (!download) return;
    link.href = download.href;
    link.download = download.filename;
    link.title = `下載 ${download.format.toUpperCase()}`;
    link.setAttribute("aria-label", `下載${download.filename}`);
  });
}

function storageIdentity() {
  return encodeURIComponent(String((currentUser && currentUser.email) || "signed-out").trim().toLowerCase());
}

function normalizedSessionEmail(user = currentUser) {
  return String((user && user.email) || "").trim().toLowerCase();
}

function captureSessionContext() {
  return { epoch: sessionEpoch, email: normalizedSessionEmail() };
}

function sessionContextIsCurrent(context) {
  return Boolean(context) && context.epoch === sessionEpoch && context.email === normalizedSessionEmail();
}

function staleSessionError() {
  const error = new Error("登入身分已變更");
  error.name = "StaleSessionError";
  return error;
}

function assertSessionContext(context) {
  if (!sessionContextIsCurrent(context)) throw staleSessionError();
}

function isStaleSessionError(error) {
  return Boolean(error && error.name === "StaleSessionError");
}

function normalizeLocalHistorySourceLocator(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const expectedKeys = ["filename", "promptId", "subfolder", "type"];
  const observedKeys = Object.keys(value).sort();
  if (observedKeys.length !== expectedKeys.length || observedKeys.some((key, index) => key !== expectedKeys[index])) return null;
  if (
    typeof value.filename !== "string"
    || !value.filename
    || value.filename.length > 500
    || value.filename.includes("\u0000")
    || typeof value.subfolder !== "string"
    || value.subfolder.length > 500
    || value.subfolder.includes("\u0000")
    || typeof value.type !== "string"
    || !["input", "output", "temp"].includes(value.type)
    || typeof value.promptId !== "string"
    || (value.promptId.startsWith("local-")
      ? !/^local-[A-Za-z0-9_-]{6,80}$/.test(value.promptId)
      : !/^[A-Za-z0-9_.:-]{1,128}$/.test(value.promptId))
  ) return null;
  return Object.freeze({
    filename: value.filename,
    subfolder: value.subfolder,
    type: value.type,
    promptId: value.promptId,
  });
}

function exactOwnerHistoryItem(items, locator) {
  const matches = (Array.isArray(items) ? items : []).filter((item) => (
    item
    && typeof item === "object"
    && !Array.isArray(item)
    && item.filename === locator.filename
    && item.subfolder === locator.subfolder
    && item.type === locator.type
    && item.promptId === locator.promptId
  ));
  if (matches.length !== 1 || matches[0].hidden) return null;
  return matches[0];
}

async function openLocalHistoryContinuation(event) {
  const session = captureSessionContext();
  const revision = ++localHistoryOpenRevision;
  if (!currentUser || authStatus.authenticated !== true) return;
  const locator = normalizeLocalHistorySourceLocator(event && event.detail);
  if (!locator) return;
  try {
    // Always re-resolve through the authenticated owner-only endpoint. A
    // cached gallery row may have been renamed, hidden, deleted, or moved to a
    // different owner in another tab since this Resource Center card rendered.
    const data = await api(OWNER_HISTORY_ENDPOINT);
    assertSessionContext(session);
    if (revision !== localHistoryOpenRevision) return;
    const ownerItems = Array.isArray(data.images) ? data.images : [];
    const item = exactOwnerHistoryItem(ownerItems, locator);
    if (!item) {
      showAlert("這份作品已更新、隱藏或不屬於目前帳號，已停止開啟。", true);
      return;
    }
    openResultDetail(item);
  } catch (error) {
    if (isStaleSessionError(error) || !sessionContextIsCurrent(session)) return;
    showAlert(`作品確認失敗：${error.message}`, true);
  }
}

function setupLocalHistoryContinuationBridge() {
  if (localHistoryBridgeListening) return;
  localHistoryBridgeListening = true;
  window.addEventListener("castle-local-history-open", openLocalHistoryContinuation);
}

function userStorageKey(base) {
  return `${base}:${storageIdentity()}`;
}

function activeJobsTable() {
  if (!currentUser) return [];
  const value = readJsonStorage(userStorageKey(ACTIVE_JOBS_KEY), []);
  return Array.isArray(value) ? value.filter((job) => job && job.promptId) : [];
}

function saveActiveJobs(jobs) {
  if (!currentUser) return;
  writeJsonStorage(userStorageKey(ACTIVE_JOBS_KEY), jobs.slice(-80));
}

function rememberActiveJob(promptId, characterId, action, extra = {}) {
  if (!promptId || !characterId || !action) return;
  const jobs = activeJobsTable().filter((job) => job.promptId !== promptId);
  jobs.push({ promptId, characterId, action, ts: Date.now(), ...extra });
  saveActiveJobs(jobs);
}

function updateActiveJob(promptId, patch) {
  if (!promptId) return;
  const jobs = activeJobsTable();
  const index = jobs.findIndex((job) => job.promptId === promptId);
  if (index < 0) return;
  jobs[index] = { ...jobs[index], ...patch, ts: Date.now() };
  saveActiveJobs(jobs);
}

function replaceActiveJobPrompt(oldPromptId, newPromptId, patch = {}) {
  if (!oldPromptId || !newPromptId) return;
  const jobs = activeJobsTable();
  const index = jobs.findIndex((job) => job.promptId === oldPromptId);
  if (index < 0) return;
  jobs[index] = { ...jobs[index], ...patch, promptId: newPromptId, ts: Date.now() };
  saveActiveJobs(jobs);
  if (trackedPromptId === oldPromptId) {
    trackedPromptId = newPromptId;
    generationFsm.resultPromptId = newPromptId;
    setGenerationFsm("progress", {
      detail: characterJobLabel(jobs[index]),
      pct: Math.max(70, Number(generationFsm.pct || 0)),
      promptId: newPromptId,
    });
  }
}

function draftKey(characterId, actionId) {
  return `castle.draft.${storageIdentity()}.${characterId}.${actionId}`;
}

function draftCapabilityMatches(actionId, capability) {
  return Boolean(
    capability
    && String(capability.actionId || "") === String(actionId || "")
    && String(capability.featureId || "").trim()
    && String(capability.variantId || "").trim()
  );
}

function sanitizeDraftState(value, feature = null, capability = null) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const state = { ...value };
  if (!feature) return state;
  const serverBound = new Set(Array.isArray(capability && capability.serverBoundUploads)
    ? capability.serverBoundUploads
    : []);
  const allowedSlots = new Set((feature.uploads || [])
    .map((upload) => String(upload && upload.id || ""))
    .filter((slot) => slot && !serverBound.has(slot)));
  for (const field of ["serverFiles", "serverFileLabels", "libraryPicks", "userPhotoPicks"]) {
    const source = state[field];
    if (!source || typeof source !== "object" || Array.isArray(source)) {
      state[field] = {};
      continue;
    }
    state[field] = Object.fromEntries(Object.entries(source).filter(([slot]) => allowedSlots.has(slot)));
  }
  if (["h3-image-video", "h3-reference-video"].includes(feature.id)) {
    for (const field of ["description", "aspect", "motionProfile", "quality"]) {
      if (state[field] !== undefined && typeof state[field] !== "string") delete state[field];
    }
  }
  return state;
}

function warnDraftStorageUnavailable() {
  if (draftStorageWarningShown) return;
  draftStorageWarningShown = true;
  showToast("此瀏覽器目前無法自動保留草稿");
}

function removeDraftStorageKey(key, warn = false) {
  try {
    localStorage.removeItem(key);
    return true;
  } catch (_) {
    if (warn) warnDraftStorageUnavailable();
    return false;
  }
}

function loadDraftSnapshot(characterId, actionId, capability, feature = null) {
  const key = draftKey(characterId, actionId);
  let hasStoredDraft = false;
  try {
    hasStoredDraft = localStorage.getItem(key) !== null;
  } catch (_) {
    return { state: {}, restored: false, savedAt: 0 };
  }
  if (!hasStoredDraft) return { state: {}, restored: false, savedAt: 0 };
  const value = readJsonStorage(key, null);
  const valid = Boolean(
    draftCapabilityMatches(actionId, capability)
    && value
    && typeof value === "object"
    && !Array.isArray(value)
    && value.schema === CHARACTER_DRAFT_SCHEMA
    && value.actionId === actionId
    && value.featureId === capability.featureId
    && value.variantId === capability.variantId
    && value.state
    && typeof value.state === "object"
    && !Array.isArray(value.state)
  );
  if (!valid) {
    removeDraftStorageKey(key);
    return { state: {}, restored: false, savedAt: 0 };
  }
  return {
    state: sanitizeDraftState(value.state, feature, capability),
    restored: true,
    savedAt: Number(value.savedAt || 0),
  };
}

function loadDraft(characterId, actionId, capability, feature = null) {
  return loadDraftSnapshot(characterId, actionId, capability, feature).state;
}

function saveDraft(characterId, actionId, capability, value, feature = null) {
  if (!draftCapabilityMatches(actionId, capability)) return false;
  try {
    writeJsonStorage(draftKey(characterId, actionId), {
      schema: CHARACTER_DRAFT_SCHEMA,
      actionId,
      featureId: capability.featureId,
      variantId: capability.variantId,
      savedAt: Date.now(),
      state: sanitizeDraftState(value, feature, capability),
    });
    return true;
  } catch (_) {
    warnDraftStorageUnavailable();
    return false;
  }
}

function clearDraft(characterId, actionId) {
  return removeDraftStorageKey(draftKey(characterId, actionId), true);
}

function currentDraftRevision(characterId, actionId) {
  return Number(draftRevisions.get(draftKey(characterId, actionId)) || 0);
}

function bumpDraftRevision(characterId, actionId) {
  const revision = currentDraftRevision(characterId, actionId) + 1;
  draftRevisions.set(draftKey(characterId, actionId), revision);
  return revision;
}

function clearDraftAfterSubmit(characterId, actionId, state = null, submittedRevision = null) {
  if (state && typeof state.__clearSaveTimer === "function") state.__clearSaveTimer();
  const latestRevision = currentDraftRevision(characterId, actionId);
  if (state && submittedRevision !== null && latestRevision !== submittedRevision) {
    state.__draftCleared = false;
    const key = `${characterId}:${actionId}`;
    const latestState = composerBinding && composerBinding.key === key ? composerBinding.state : state;
    if (Number(latestState.__draftRevision || 0) === latestRevision && typeof latestState.__saveDraftNow === "function") {
      latestState.__draftCleared = false;
      latestState.__saveDraftNow();
    }
    return false;
  }
  if (state) {
    state.__draftCleared = true;
    state.__draftDirty = false;
  }
  if (state && draftFlushNow === state.__saveDraftNow) draftFlushNow = null;
  clearDraft(characterId, actionId);
  if (composerBinding && composerBinding.state === state && composerBinding.draftNotice) {
    composerBinding.draftNotice.remove();
    composerBinding.draftNotice = null;
  }
  return true;
}

function taskSeenTable() {
  if (!currentUser) return {};
  const value = readJsonStorage(userStorageKey(TASK_SEEN_KEY), {});
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function saveTaskSeen(value) {
  if (currentUser) writeJsonStorage(userStorageKey(TASK_SEEN_KEY), value || {});
}

function parseRoute() {
  const hash = window.location.hash || (window.location.pathname.replace(/\/$/, "") === "/admin" ? "#/admin" : "#/");
  let parts;
  try {
    parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  } catch (_) {
    return { view: "characters" };
  }
  if (!parts.length) return { view: "characters" };
  if (parts[0] === "studio") {
    if (parts.length <= 2) return { view: "studio", featureId: parts[1] || "" };
    return { view: "characters" };
  }
  if (
    parts[0] === "provider-characters"
    && parts.length === 3
    && /^[a-z0-9][a-z0-9._:-]{0,127}$/.test(parts[1])
    && parts[2] === "studio"
  ) {
    return { view: "provider-character-studio", characterId: parts[1] };
  }
  if (parts[0] === "manage") {
    const mediaKinds = ["image", "video", "audio", "text"];
    if (
      parts.length === 4
      && (parts[1] === "all" || /^[a-z0-9][a-z0-9._:-]{0,127}$/.test(parts[1]))
      && mediaKinds.includes(parts[2])
      && parts[3] === "provider"
    ) {
      return {
        view: "library",
        characterId: parts[1] === "all" ? "" : parts[1],
        kind: parts[2],
        source: "provider",
      };
    }
    if (
      parts.length === 6
      && /^[a-z0-9][a-z0-9._:-]{0,127}$/.test(parts[1])
      && ["all", ...mediaKinds].includes(parts[2])
      && parts[3] === "castle"
      && /^[a-z0-9][a-z0-9._:-]{0,127}$/.test(parts[4])
      && /^[a-z0-9][a-z0-9._:-]{0,127}$/.test(parts[5])
    ) {
      return {
        view: "library",
        characterId: parts[1],
        kind: parts[2],
        featureId: parts[4],
        variantId: parts[5],
      };
    }
    if (parts.length > 3 || (parts[2] && !mediaKinds.includes(parts[2]))) {
      return { view: "characters" };
    }
    const characterId = parts[1] && parts[1] !== "all" ? parts[1] : "";
    const kind = mediaKinds.includes(parts[2]) ? parts[2] : "all";
    return { view: "library", characterId, kind };
  }
  if (parts[0] === "dance-library") return { view: "dance-library" };
  if (parts[0] === "projects") return { view: "projects" };
  if (parts[0] === "workflows") return { view: "workflows" };
  if (window.WebComfyDocs && parts[0] === "workflow" && parts[1]) return { view: "workflows" };
  if (parts[0] === "resources") return { view: "resources" };
  if (parts[0] === "orchestrator") {
    if (parts[1] === "runs" && parts[2]) return { view: "orchestrator", pane: "detail", runId: parts[2] };
    if (parts[1] === "runs") return { view: "orchestrator", pane: "runs" };
    return { view: "orchestrator", pane: "overview" };
  }
  if (parts[0] === "admin") return { view: "admin" };
  if (parts[0] === "admin-users") return { view: "admin-users" };
  if (parts[0] === "c" && parts[1]) {
    if (parts[2] === "studio") {
      if (parts.length <= 4) return { view: "studio", characterId: parts[1], featureId: parts[3] || "" };
      return { view: "character-workspace", characterId: parts[1], actionId: parts.slice(2).join("/") };
    }
    if (parts[2] === "library") {
      if (parts.length === 3) return { view: "character-library", characterId: parts[1], media: "image" };
      if (parts.length === 4 && ["image", "video", "audio", "text"].includes(parts[3])) {
        return { view: "character-library", characterId: parts[1], media: parts[3] };
      }
      return { view: "character-workspace", characterId: parts[1], actionId: parts.slice(2).join("/") };
    }
    if (parts[2] === "wardrobe") {
      if (parts.length === 3) return { view: "character-wardrobe", characterId: parts[1] };
      return { view: "character-workspace", characterId: parts[1], actionId: parts.slice(2).join("/") };
    }
    if (parts[2] === "settings") {
      if (parts.length === 3) return { view: "character-settings", characterId: parts[1] };
      return { view: "character-workspace", characterId: parts[1], actionId: parts.slice(2).join("/") };
    }
    return {
      view: "character-workspace",
      characterId: parts[1],
      actionId: parts.length > 3 ? parts.slice(2).join("/") : (parts[2] || ""),
    };
  }
  return { view: "characters" };
}

function routePath(route) {
  if (!route || route.view === "characters") return "#/";
  if (route.view === "studio") {
    const featurePart = route.featureId ? `/${encodeURIComponent(route.featureId)}` : "";
    if (route.characterId) return `#/c/${encodeURIComponent(route.characterId)}/studio${featurePart}`;
    return `#/studio${featurePart}`;
  }
  if (route.view === "provider-character-studio") {
    const characterId = safeProviderCharacterId(route.characterId);
    return characterId ? `#/provider-characters/${encodeURIComponent(characterId)}/studio` : "#/";
  }
  if (route.view === "library") {
    if (route.source === "provider") {
      if (!["image", "video", "audio", "text"].includes(route.kind)) return "#/";
      const characterPart = route.characterId ? encodeURIComponent(route.characterId) : "all";
      return `#/manage/${characterPart}/${route.kind}/provider`;
    }
    if (route.featureId || route.variantId) {
      const characterId = String(route.characterId || "");
      const featureId = String(route.featureId || "");
      const variantId = String(route.variantId || "");
      const kind = ["all", "image", "video", "audio", "text"].includes(route.kind) ? route.kind : "";
      if (
        !kind
        || !/^[a-z0-9][a-z0-9._:-]{0,127}$/.test(characterId)
        || !/^[a-z0-9][a-z0-9._:-]{0,127}$/.test(featureId)
        || !/^[a-z0-9][a-z0-9._:-]{0,127}$/.test(variantId)
      ) return "#/";
      return `#/manage/${encodeURIComponent(characterId)}/${kind}/castle/${encodeURIComponent(featureId)}/${encodeURIComponent(variantId)}`;
    }
    const characterPart = route.characterId ? encodeURIComponent(route.characterId) : "all";
    const kindPart = ["image", "video", "audio", "text"].includes(route.kind) ? `/${route.kind}` : "";
    return route.characterId || kindPart ? `#/manage/${characterPart}${kindPart}` : "#/manage";
  }
  if (route.view === "dance-library") return "#/dance-library";
  if (route.view === "projects") return "#/projects";
  if (route.view === "workflows") return "#/workflows";
  if (route.view === "resources") return "#/resources";
  if (route.view === "orchestrator") {
    if (route.runId) return `#/orchestrator/runs/${encodeURIComponent(route.runId)}`;
    if (route.pane === "runs") return "#/orchestrator/runs";
    return "#/orchestrator";
  }
  if (route.view === "admin") return "#/admin";
  if (route.view === "admin-users") return "#/admin-users";
  if (route.view === "character-library") {
    const media = ["video", "audio", "text"].includes(route.media) ? `/${route.media}` : "";
    return `#/c/${encodeURIComponent(route.characterId)}/library${media}`;
  }
  if (route.view === "character-wardrobe") {
    return `#/c/${encodeURIComponent(route.characterId)}/wardrobe`;
  }
  if (route.view === "character-settings") {
    return `#/c/${encodeURIComponent(route.characterId)}/settings`;
  }
  if (route.view === "character-workspace") {
    return `#/c/${encodeURIComponent(route.characterId)}${route.actionId ? `/${encodeURIComponent(route.actionId)}` : ""}`;
  }
  return "#/";
}

function routeCharacterContextId(route = currentRoute) {
  if (!route || !["studio", "character-workspace", "character-library", "character-wardrobe", "character-settings"].includes(route.view)) return "";
  return String(route.characterId || "");
}

function studioRoute(featureId = "", characterId = routeCharacterContextId()) {
  return {
    view: "studio",
    ...(featureId ? { featureId } : {}),
    ...(characterId ? { characterId } : {}),
  };
}

function studioCharacterContextPending(route = currentRoute) {
  return Boolean(route && route.view === "studio" && route.characterId && !charactersLoaded);
}

function routeKeepsRenderState(fromRoute, toRoute) {
  if (!fromRoute || !toRoute) return false;
  if (routePath(fromRoute) === routePath(toRoute)) return true;
  if (fromRoute.view === "library" && toRoute.view === "library"
    && libraryRouteSource(fromRoute) === libraryRouteSource(toRoute)) return true;
  if (fromRoute.view === "provider-character-studio" && toRoute.view === "provider-character-studio") {
    return fromRoute.characterId === toRoute.characterId;
  }
  return ["character-library", "character-wardrobe", "character-settings"].includes(fromRoute.view)
    && fromRoute.view === toRoute.view
    && fromRoute.characterId === toRoute.characterId;
}

function captureRenderState() {
  const ws = $("workspace");
  const renderRoot = document.querySelector(".layout");
  const active = document.activeElement;
  const focusTarget = active && renderRoot && renderRoot.contains(active) && active.id && active.matches("input, textarea, select, button, [tabindex]") ? active : null;
  const remembered = focusTarget ? {
    focusId: focusTarget.id,
    selectionStart: typeof focusTarget.selectionStart === "number" ? focusTarget.selectionStart : null,
    selectionEnd: typeof focusTarget.selectionEnd === "number" ? focusTarget.selectionEnd : null,
  } : (lastComposerFieldState || {});
  return {
    workspaceScrollTop: ws ? ws.scrollTop : 0,
    windowScrollY: window.scrollY,
    focusId: remembered.focusId || "",
    selectionStart: remembered.selectionStart === undefined ? null : remembered.selectionStart,
    selectionEnd: remembered.selectionEnd === undefined ? null : remembered.selectionEnd,
  };
}

function restoreRenderState(snapshot) {
  if (!snapshot) return;
  requestAnimationFrame(() => {
    const ws = $("workspace");
    if (ws) ws.scrollTop = snapshot.workspaceScrollTop || 0;
    window.scrollTo({ top: snapshot.windowScrollY || 0, behavior: "auto" });
    const field = snapshot.focusId ? document.getElementById(snapshot.focusId) : null;
    if (!field || field.disabled) return;
    field.focus({ preventScroll: true });
    if (typeof field.setSelectionRange === "function" && snapshot.selectionStart !== null) {
      field.setSelectionRange(snapshot.selectionStart, snapshot.selectionEnd === null ? snapshot.selectionStart : snapshot.selectionEnd);
    }
  });
}

function focusCurrentRoute() {
  const ids = [];
  if (currentRoute.view === "library") {
    ids.push(currentLibrarySource() === "provider"
      ? `provider-fabric-tab-${currentLibraryKind()}`
      : `global-library-kind-${currentLibraryKind()}`);
  }
  else if (currentRoute.view === "character-library") ids.push(`character-media-tab-${currentRoute.media || "image"}`);
  else if (currentRoute.view === "character-wardrobe") ids.push("character-local-tab-wardrobe");
  else if (currentRoute.view === "character-settings") ids.push("character-local-tab-settings");
  else if (currentRoute.view === "character-workspace") ids.push("character-local-tab-create");
  else if (currentRoute.view === "provider-character-studio") ids.push("providerCharacterPrompt");
  else if (currentRoute.view === "studio") ids.push("studioToolTrigger", `studio-side-tool-${currentFeatureId || ""}`);
  else if (currentRoute.view === "projects") ids.push("project-preview-heading");
  else if (currentRoute.view === "orchestrator") ids.push(`orchestratorPane-${currentRoute.pane || "overview"}`);
  for (const id of ids) {
    const target = document.getElementById(id);
    if (target && target.getClientRects().length) {
      target.focus({ preventScroll: true });
      return;
    }
  }
  const heading = $("workspace")?.querySelector("h1");
  if (heading) {
    heading.setAttribute("tabindex", "-1");
    heading.focus({ preventScroll: true });
  }
}

function beginWorkspaceRender(className = "workspace-route-root") {
  const workspace = $("workspace");
  const next = h("div", { class: className });
  const current = workspace.firstElementChild;
  if (current) workspace.replaceChild(next, current);
  else workspace.appendChild(next);
  while (next.nextSibling) next.nextSibling.remove();
  return next;
}

function navigate(route) {
  if (composerBinding) retireBoundComposerDraft({ flush: true });
  else if (typeof draftFlushNow === "function") draftFlushNow();
  if (sheetStack.length) closeAllSheets();
  pendingRenderSnapshot = routeKeepsRenderState(currentRoute, route) ? captureRenderState() : null;
  const path = routePath(route);
  if (window.location.hash === path) {
    applyRoute();
  } else {
    // Commit the URL and matching workspace in the same click task.  Relying
    // on a later hashchange left a visible (and occasionally persistent)
    // split state where the address named the new action but the old composer
    // and selected rail item remained on screen.
    history.pushState(history.state, "", path);
    applyRoute();
  }
}

function applyRoute() {
  if (sheetStack.length) closeAllSheets();
  const previousRoute = currentRoute;
  const nextRoute = parseRoute();
  const keepRenderState = routeKeepsRenderState(previousRoute, nextRoute);
  currentRoute = nextRoute;
  currentView = ["studio", "projects", "library", "resources", "workflows", "dance-library", "orchestrator", "admin-users", "admin"].includes(currentRoute.view)
    ? currentRoute.view
    : "characters";
  const routeCharacterId = routeCharacterContextId(currentRoute);
  activeCharacterId = routeCharacterId;
  if (routeCharacterId) selectedCharacterId = routeCharacterId;
  if (currentRoute.view === "orchestrator") {
    orchestratorMobilePane = currentRoute.pane || "overview";
    if (currentRoute.runId) {
      selectedOrchestratorRunId = currentRoute.runId;
      if (!orchestratorRunDetail || orchestratorRunDetail.id !== currentRoute.runId) orchestratorRunDetail = null;
    }
  }
  const waitingForCharacterContext = studioCharacterContextPending(currentRoute);
  if (currentRoute.view === "studio" && !waitingForCharacterContext) {
    applyStudioRouteSelection();
  }
  if (currentRoute.actionId) selectedCharacterActionId = currentRoute.actionId;
  syncViewTabs();
  renderSidebar();
  if (!waitingForCharacterContext) renderWorkspace();
  if (activeCharacterId) refreshActiveCharacterCapabilities();
  if (currentRoute.view === "orchestrator" && currentRoute.runId && !orchestratorRunDetail) {
    loadOrchestratorRun(currentRoute.runId);
  }
  if (!document.hidden && currentUser && authStatus.authenticated) {
    if (currentRoute.view === "orchestrator" && previousRoute.view !== "orchestrator") {
      runForegroundRefresh("orchestrator", () => refreshOrchestrator({ silent: true }));
    }
    if (routeNeedsHistory(currentRoute) && !routeNeedsHistory(previousRoute)) {
      runForegroundRefresh("history", () => refreshHistory({ auto: true }));
    }
  }
  const renderSnapshot = keepRenderState ? pendingRenderSnapshot : null;
  restoreRenderState(renderSnapshot);
  pendingRenderSnapshot = null;
  if (!renderSnapshot) {
    requestAnimationFrame(() => {
      if (!keepRenderState) {
        const workspace = $("workspace");
        if (workspace) workspace.scrollTop = 0;
        window.scrollTo({ top: 0, behavior: "auto" });
      }
      focusCurrentRoute();
    });
  }
}

// ---- api ------------------------------------------------------------------
function apiErrorFromResponse(data, status) {
  const rawError = data && data.error;
  const rawMessage = rawError && typeof rawError === "object"
    ? (rawError.message || rawError.code)
    : rawError;
  const error = new Error(safePublicErrorText(rawMessage, `HTTP ${status}`, 360));
  error.name = "CastleApiError";
  error.errorCode = safePublicErrorText(
    (data && (data.error_code || data.errorCode)) || (rawError && rawError.code),
    "",
    80,
  );
  error.technicalDetail = safePublicErrorText(
    data && (data.technical_detail || data.technicalDetail),
    "",
    600,
  );
  const submissionState = String(data && (data.submission_state || data.submissionState) || "");
  error.submissionState = ["not_submitted", "submitted", "unknown"].includes(submissionState)
    ? submissionState
    : "";
  error.retryable = Boolean(data && data.retryable);
  return error;
}

function generationErrorPresentation(error, requestSent = false) {
  const summary = safePublicErrorText(
    error && error.message,
    "生成要求未完成，請稍後重試。",
    360,
  );
  const declaredState = String(error && error.submissionState || "");
  const submissionState = ["not_submitted", "submitted", "unknown"].includes(declaredState)
    ? declaredState
    : (requestSent ? "unknown" : "not_submitted");
  const certainty = {
    not_submitted: "未送出",
    submitted: "已送出，執行失敗",
    unknown: "可能已送出，正在確認",
  }[submissionState];
  return {
    summary,
    detail: `${certainty} · ${summary}`,
    submissionState,
    technicalDetail: safePublicErrorText(error && error.technicalDetail, "", 600),
  };
}

function runtimeGenerationErrorPresentation(message, type = "") {
  const combined = `${String(type || "")} ${String(message || "")}`.toLowerCase();
  if (combined.includes("memory_recovery_failed")) {
    return { code: "memory_recovery_failed", message: "系統無法騰出 H3 所需記憶體，這次尚未開始生成；不需要重新上傳素材。" };
  }
  if (["out of memory", "cuda oom", "memory_headroom_low"].some((token) => combined.includes(token))) {
    return { code: "generation_memory_exhausted", message: "生成時記憶體不足，系統已停止這次工作並保留輸入；稍後可直接重試。" };
  }
  if (["timed out", "timeout"].some((token) => combined.includes(token))) {
    return { code: "generation_timeout", message: "生成服務等待過久而停止，沒有確認到完整輸出。" };
  }
  if (["no such file", "file not found", "cannot find input", "input file"].some((token) => combined.includes(token))) {
    return { code: "generation_input_missing", message: "找不到這次工作使用的輸入素材；請重新選取素材後再試。" };
  }
  if (["model not found", "checkpoint", "lora"].some((token) => combined.includes(token))) {
    return { code: "generation_model_unavailable", message: "這個工作流需要的模型目前不可用；不是 Prompt 寫錯。" };
  }
  if (["node not found", "class_type", "cannot import"].some((token) => combined.includes(token))) {
    return { code: "generation_node_unavailable", message: "這個工作流需要的節點目前不可用；不是 Prompt 寫錯。" };
  }
  if (["shape mismatch", "size mismatch", "invalid shape", "schema"].some((token) => combined.includes(token))) {
    return { code: "generation_workflow_incompatible", message: "工作流與目前模型或節點版本不相容；不是 Prompt 寫錯。" };
  }
  return { code: "generation_execution_failed", message: "生成已開始但沒有成功完成；系統已保留任務資料供檢查。" };
}

async function api(path, options = {}) {
  const session = captureSessionContext();
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  assertSessionContext(session);
  let data = {};
  try {
    data = await res.json();
  } catch (_) {
    /* non-JSON */
  }
  assertSessionContext(session);
  if (res.status === 401) {
    resetSessionUi();
    showAuth("login");
  }
  if (!res.ok || data.ok === false) throw apiErrorFromResponse(data, res.status);
  return data;
}

// ---- auth -----------------------------------------------------------------
function isAdmin() {
  return currentUser && currentUser.role === "admin";
}

function isOperator() {
  return Boolean(currentUser && currentUser.operator === true);
}

function setServiceStatus(ok, text) {
  const pill = $("servicePill");
  if (!pill) return;
  if (window.WebComfyDocs) { pill.textContent = "互動說明"; pill.className = "service-pill"; return; }
  pill.textContent = text || (ok ? "服務正常" : "服務連線異常");
  pill.className = `service-pill ${ok ? "ok" : "error"}`;
}

function renderAdminChrome() {
  document.querySelectorAll(".admin-only").forEach((node) => node.classList.toggle("hidden", !isAdmin()));
  document.querySelectorAll(".project-authorized").forEach((node) => (
    node.classList.toggle("hidden", !(isAdmin() || isOperator()))
  ));
  if (!isAdmin()) {
    queuePanelOpen = false;
    renderQueuePanel();
  }
}

function renderIdentityChip() {
  const chip = $("identityChip");
  if (!chip) return;
  clear(chip);
  if (!currentUser) {
    chip.classList.add("hidden");
    return;
  }
  chip.classList.remove("hidden");
  chip.appendChild(h("span", { class: "identity-name", text: currentUser.displayName || currentUser.email || "使用者" }));
  if (currentUser.role === "admin") chip.appendChild(h("span", { class: "identity-role", text: "管理員" }));
  chip.appendChild(h("span", { class: "identity-chevron", text: "▾", "aria-hidden": "true" }));
  chip.setAttribute("aria-label", `帳號選單：${currentUser.displayName || currentUser.email || "使用者"}`);
}

function showAuth(mode) {
  authMode = mode;
  const lane = authStatus.lane || "local";
  const isAccess = lane === "access";
  const setupPassword = mode === "setup-password";
  const localSetup = mode === "setup";
  $("authGate").classList.remove("hidden");
  $("authPassword").value = "";
  $("authPasswordConfirm").value = "";
  $("authPassword").removeAttribute("aria-invalid");
  $("authPasswordConfirm").removeAttribute("aria-invalid");
  $("authMessage").textContent = "";
  $("authConfirmWrap").classList.toggle("hidden", !setupPassword);
  $("authPasswordConfirm").required = setupPassword;
  $("authIdentity").classList.toggle("hidden", !isAccess);
  if (isAccess) $("authIdentity").textContent = `以 ${authStatus.accessEmail || "Cloudflare Access 使用者"} 登入`;
  if (setupPassword) {
    $("authTitle").textContent = "設定你的登入密碼";
    $("authText").textContent = "信箱驗證已通過，第一次進入請設定 Castle 平台密碼。";
    $("authSubmit").textContent = "設定並進入";
    $("authPassword").autocomplete = "new-password";
  } else if (localSetup) {
    $("authTitle").textContent = "設定本機管理密碼";
    $("authText").textContent = "第一次啟用主控台，請先建立本機管理密碼。";
    $("authSubmit").textContent = "設定並進入";
    $("authPassword").autocomplete = "new-password";
  } else {
    $("authTitle").textContent = isAccess ? "輸入平台密碼" : "本機管理登入";
    $("authText").textContent = isAccess ? "Cloudflare Access 已確認你的信箱，請輸入 Castle 平台密碼。" : "這是 local-workstation 的 ComfyUI 管理入口。";
    $("authSubmit").textContent = "登入";
    $("authPassword").autocomplete = "current-password";
  }
  $("authPassword").focus();
}

function hideAuth() {
  $("authGate").classList.add("hidden");
  $("authMessage").textContent = "";
}

function redirectToLoginReturn() {
  const returnPath = globalThis.CastleLoginReturn?.reviewReturnPathFromLocation(window.location) || "";
  if (!returnPath) return false;
  window.location.replace(returnPath);
  return true;
}

async function checkAuth() {
  const data = await api("/api/auth/status");
  const previousIdentity = normalizedSessionEmail();
  authStatus = data;
  const nextUser = data.user || null;
  const nextIdentity = normalizedSessionEmail(nextUser);
  currentUser = nextUser;
  if (previousIdentity !== nextIdentity) resetSessionUi({ keepUser: true });
  localStorage.removeItem(ACTIVE_JOBS_KEY);
  renderIdentityChip();
  renderAdminChrome();
  if (!data.configured && data.lane === "local") return showAuth("setup"), false;
  if (data.lane === "access" && data.needsSetup && !data.authenticated) return showAuth("setup-password"), false;
  if (!data.authenticated) return showAuth("login"), false;
  hideAuth();
  return true;
}

async function submitAuth(event) {
  event.preventDefault();
  let session = captureSessionContext();
  const submittedMode = authMode;
  const password = $("authPassword").value;
  $("authPassword").removeAttribute("aria-invalid");
  $("authPasswordConfirm").removeAttribute("aria-invalid");
  if (password.length < 8) {
    $("authMessage").textContent = "密碼至少 8 個字。";
    $("authPassword").setAttribute("aria-invalid", "true");
    $("authPassword").focus();
    return;
  }
  if (authMode === "setup-password" && password !== $("authPasswordConfirm").value) {
    $("authMessage").textContent = "兩次輸入的密碼不一致。";
    $("authPasswordConfirm").setAttribute("aria-invalid", "true");
    $("authPasswordConfirm").focus();
    return;
  }
  const submitLabel = $("authSubmit").textContent;
  $("authSubmit").disabled = true;
  $("authSubmit").textContent = authMode === "login" ? "登入中…" : "設定中…";
  try {
    const path = authMode === "setup"
      ? "/api/auth/setup"
      : (authMode === "setup-password" ? "/api/auth/setup-password" : "/api/auth/login");
    await api(path, {
      method: "POST",
      body: JSON.stringify({ password }),
    });
    assertSessionContext(session);
    const authenticated = await checkAuth();
    if (!authenticated) return;
    session = captureSessionContext();
    hideAuth();
    if (redirectToLoginReturn()) return;
    await startAppData();
    assertSessionContext(session);
  } catch (error) {
    if (isStaleSessionError(error)) return;
    if (error.message === "needs-setup") {
      authStatus.needsSetup = true;
      showAuth("setup-password");
      return;
    }
    $("authMessage").textContent = error.message;
  } finally {
    $("authSubmit").disabled = false;
    if (authMode === submittedMode) $("authSubmit").textContent = submitLabel;
  }
}

async function logout() {
  queueEventsWanted = false;
  if (queueEventSource) {
    queueEventSource.close();
    queueEventSource = null;
  }
  retireBoundComposerDraft({ flush: true });
  resetSessionUi();
  try {
    await api("/api/auth/logout", { method: "POST" });
  } catch (_) {
    /* ignore */
  }
  location.reload();
}

function resetSessionUi(options = {}) {
  sessionEpoch += 1;
  localHistoryOpenRevision += 1;
  pendingProviderIntent = null;
  pendingProviderAutoStart = false;
  if (cachedProjectPreview) cachedProjectPreview.remove();
  cachedProjectPreview = null;
  retireBoundComposerDraft({ flush: false });
  if (!options.keepUser) {
    currentUser = null;
    authStatus = { ...authStatus, user: null, accessEmail: "", authenticated: false };
  }
  queueEventsWanted = false;
  if (queueReconnectTimer) {
    clearTimeout(queueReconnectTimer);
    queueReconnectTimer = null;
  }
  if (queueEventSource) {
    queueEventSource.close();
    queueEventSource = null;
  }
  if (Array.isArray(window.castleTimers)) {
    for (const timer of window.castleTimers) clearInterval(timer);
  }
  window.castleTimers = null;
  foregroundRefreshes.clear();
  for (const timer of historyRefreshTimers) clearTimeout(timer);
  historyRefreshTimers.clear();
  closeAllSheets();
  document.querySelectorAll(".character-editor-overlay, .history-lightbox, .app-toast").forEach((node) => node.remove());
  characters = [];
  charactersLoaded = false;
  providerCharacters = [];
  providerCharactersLoaded = false;
  providerCharactersError = "";
  characterActions = [];
  studios = [];
  features = [];
  currentStudio = null;
  currentFeatureId = null;
  studioRouteProblem = "";
  selectedCharacterId = "";
  activeCharacterId = "";
  selectedCharacterActionId = "";
  editorOverlayCharacterId = "";
  experimentalCreateOpen = false;
  selectedHistoryKey = "";
  lastHistoryFingerprint = "";
  showHiddenCharacterLibrary = false;
  showHiddenGlobalLibrary = false;
  librarySearchQuery = "";
  libraryVisibleLimit = 24;
  if (librarySearchTimer) clearTimeout(librarySearchTimer);
  librarySearchTimer = null;
  historyDetailCache.clear();
  wardrobeCache.clear();
  wardrobeLoadPromises.clear();
  wardrobeRevisions.clear();
  characterPostsCache.clear();
  catalogueViewState.clear();
  draftRevisions.clear();
  danceTemplateCache.clear();
  stopActiveDancePreview();
  if (dancePreviewObserver) dancePreviewObserver.disconnect();
  dancePreviewObserver = null;
  danceLibraryReturnRoute = null;
  if (danceManagerSearchTimer) clearTimeout(danceManagerSearchTimer);
  danceManagerSearchTimer = null;
  danceLibraryManageState = {
    items: [], loaded: false, loading: false, error: "", message: "",
    status: "pending", query: "", selected: new Set(),
  };
  adminUsers = [];
  adminUsersMessage = "";
  adminUsersError = "";
  orchestratorRuns = [];
  orchestratorSummary = { runs: {}, tasks: {}, active: [] };
  orchestratorResources = { resources: [], summary: {} };
  selectedOrchestratorRunId = "";
  orchestratorRunDetail = null;
  orchestratorCursor = 0;
  orchestratorArtifacts = [];
  orchestratorArtifactFilters = { q: "", tag: "", artifact_type: "" };
  orchestratorMobilePane = "overview";
  if (voiceBatchState.pollTimer) clearTimeout(voiceBatchState.pollTimer);
  voiceBatchState = {
    step: "input", source: "paste", rawText: "", segments: [], skipped: [], error: "", message: "",
    history: [], historyLoaded: false, batch: null, busy: false, pollTimer: null, cacheTick: Date.now(), loadSeq: 0,
  };
  for (const key of Object.keys(serverFiles)) delete serverFiles[key];
  for (const key of Object.keys(fileInputs)) delete fileInputs[key];
  for (const key of Object.keys(fieldGetters)) delete fieldGetters[key];
  const sidebar = $("sidebarGroups");
  if (sidebar) clear(sidebar);
  queueJobs = [];
  queueSummary = { running: 0, pending: 0, total: 0, external: 0 };
  queueRecentErrors = [];
  queueSnapshotLoaded = false;
  historySnapshotLoaded = false;
  queueRefreshesInFlight = 0;
  historyRefreshesInFlight = 0;
  trackedPromptMissing = { promptId: "", since: 0 };
  taskScope = "own";
  terminalJobs = [];
  queueTerminalJobs = [];
  historyItems = [];
  historyLoadError = "";
  libraryPosts = [];
  libraryPostsLoaded = false;
  libraryPostsError = "";
  jobEvents.clear();
  trackedPromptId = "";
  submitInFlight = false;
  composerBinding = null;
  draftFlushNow = null;
  pendingRenderSnapshot = null;
  lastComposerFieldState = null;
  generationFsm = { state: "disabled", reason: "", detail: "", pct: 0, resultPromptId: "", postResult: null, composerKey: "" };
  const workspace = $("workspace");
  if (workspace) {
    const loading = beginWorkspaceRender();
    loading.appendChild(h("section", { class: "panel empty-state-panel" }, [h("div", { class: "empty", text: "正在載入你的工作區…" })]));
  }
  renderIdentityChip();
  renderAdminChrome();
  renderTaskChip();
  renderStickyGenerate();
}

// ---- one accessible sheet controller -------------------------------------
function lockBodyScroll() {
  if (document.body.classList.contains("sheet-open")) return;
  bodyScrollY = window.scrollY;
  document.body.style.top = `-${bodyScrollY}px`;
  document.body.classList.add("sheet-open");
  const app = document.querySelector(".app");
  if (app && "inert" in app) app.inert = true;
}

function unlockBodyScroll() {
  if (!document.body.classList.contains("sheet-open")) return;
  document.body.classList.remove("sheet-open");
  document.body.style.top = "";
  const app = document.querySelector(".app");
  if (app && "inert" in app) app.inert = false;
  window.scrollTo(0, bodyScrollY);
}

function sheetFocusable(dialog) {
  return Array.from(dialog.querySelectorAll('button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'))
    .filter((element) => !element.closest(".hidden") && element.getClientRects().length > 0);
}

function closeTopSheetFromPop() {
  const entry = sheetStack.pop();
  if (!entry) return;
  entry.root.remove();
  if (entry.opener) entry.opener.setAttribute("aria-expanded", "false");
  if (!sheetStack.length) unlockBodyScroll();
  if (entry.opener && entry.opener.isConnected) entry.opener.focus({ preventScroll: true });
  scheduleVisualViewportFocusSync();
}

function closeAllSheets() {
  while (sheetStack.length) {
    const entry = sheetStack.pop();
    entry.root.remove();
    if (entry.opener) entry.opener.setAttribute("aria-expanded", "false");
  }
  clear($("sheetLayer"));
  unlockBodyScroll();
  sheetAfterPop = null;
  if (history.state && history.state.castleSheet) {
    const nextState = { ...history.state };
    delete nextState.castleSheet;
    history.replaceState(nextState, "", window.location.href);
  }
  scheduleVisualViewportFocusSync();
}

function dismissTopSheet(afterClose = null) {
  if (!sheetStack.length) {
    if (afterClose) afterClose();
    return;
  }
  sheetAfterPop = afterClose;
  history.back();
}

function openSheet({ id, title, opener, content, className = "", onOpen = null, onKeyDown = null }) {
  if (sheetStack.length) dismissTopSheet(() => openSheet({ id, title, opener, content, className, onOpen, onKeyDown }));
  else {
    const titleId = `sheet-title-${id}-${Date.now()}`;
    const heading = h("h2", { id: titleId, tabindex: "-1", text: title });
    const dialog = h("section", {
      class: `app-sheet ${className}`.trim(),
      role: "dialog",
      "aria-modal": "true",
      "aria-labelledby": titleId,
    }, [
      h("header", { class: "sheet-head" }, [
        heading,
        h("button", { class: "sheet-close", type: "button", text: "關閉", "aria-label": `關閉${title}`, onclick: () => dismissTopSheet() }),
      ]),
      h("div", { class: "sheet-body" }, [content]),
    ]);
    const root = h("div", { class: "sheet-root" }, [h("button", {
      class: "sheet-scrim",
      type: "button",
      "aria-label": `關閉${title}`,
      onclick: () => dismissTopSheet(),
    }), dialog]);
    root.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        dismissTopSheet();
        return;
      }
      if (typeof onKeyDown === "function") onKeyDown(event);
      if (event.defaultPrevented) return;
      if (event.key !== "Tab") return;
      const focusable = sheetFocusable(dialog);
      if (!focusable.length) {
        event.preventDefault();
        heading.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && (document.activeElement === first || document.activeElement === heading)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });
    lockBodyScroll();
    if (opener) opener.setAttribute("aria-expanded", "true");
    $("sheetLayer").appendChild(root);
    sheetStack.push({ id, root, dialog, opener: opener || document.activeElement });
    history.pushState({ ...(history.state || {}), castleSheet: id }, "", window.location.href);
    requestAnimationFrame(() => {
      heading.focus({ preventScroll: true });
      if (typeof onOpen === "function") onOpen({ root, dialog });
    });
  }
}

function sheetAction(callback) {
  dismissTopSheet(callback);
}

function shellMenuButton(label, callback, extraClass = "") {
  return h("button", { class: `shell-menu-row ${extraClass}`.trim(), type: "button", text: label, onclick: () => sheetAction(callback) });
}

function refreshAllData() {
  loadRegistry();
  loadCharacters();
  refreshQueue();
  if (isAdmin()) refreshOrchestrator();
  refreshHistory();
}

function renderAccountMenuContent({ managementOnly = false } = {}) {
  const wrap = h("div", { class: "shell-menu-list" });
  if (!managementOnly) {
    wrap.appendChild(h("div", { class: "shell-identity-card" }, [
      h("strong", { text: (currentUser && (currentUser.displayName || currentUser.email)) || "使用者" }),
      h("span", { text: (currentUser && currentUser.email) || "" }),
      h("span", { text: isAdmin() ? "管理員" : "使用者" }),
    ]));
    wrap.appendChild(h("div", { class: "shell-service-row" }, [
      h("span", { text: "服務狀態" }),
      h("strong", { text: $("servicePill").textContent || "未知" }),
    ]));
    wrap.appendChild(shellMenuButton("重新整理", refreshAllData));
    wrap.appendChild(shellMenuButton("角色創作資源中心", () => navigate({ view: "resources" })));
  }
  if (isAdmin() || isOperator()) {
    wrap.appendChild(h("hr", { class: "shell-menu-divider" }));
    wrap.appendChild(shellMenuButton("專案規劃（不執行）", () => navigate({ view: "projects" })));
  }
  if (isOperator()) {
    wrap.appendChild(shellMenuButton("舞蹈模板庫", () => {
      danceLibraryReturnRoute = { ...currentRoute };
      navigate({ view: "dance-library" });
    }));
  }
  if (isAdmin()) {
    wrap.appendChild(shellMenuButton("管理外部角色與素材", () => {
      pendingResourceFocus = "clipmode";
      navigate({ view: "resources" });
    }));
    wrap.appendChild(shellMenuButton("工作流監控", () => navigate({ view: "orchestrator" })));
    wrap.appendChild(h("a", { class: "shell-menu-row", href: "/admin", text: "管理中心" }));
    wrap.appendChild(shellMenuButton("使用者管理", () => navigate({ view: "admin-users" })));
  }
  if (!managementOnly) {
    wrap.appendChild(h("hr", { class: "shell-menu-divider" }));
    wrap.appendChild(shellMenuButton("登出", logout, "danger"));
  }
  return wrap;
}

function openAccountMenu(opener, phone = false) {
  openSheet({ id: "account", title: phone ? "更多" : "帳號", opener, content: renderAccountMenuContent(), className: phone ? "phone-sheet" : "identity-sheet" });
}

function openManagementMenu(opener) {
  if (!isAdmin()) return;
  openSheet({ id: "management", title: "管理", opener, content: renderAccountMenuContent({ managementOnly: true }), className: "identity-sheet" });
}

// ---- registry -------------------------------------------------------------
async function loadRegistry() {
  const session = captureSessionContext();
  if (typeof draftFlushNow === "function") draftFlushNow();
  try {
    const data = await api("/api/features");
    assertSessionContext(session);
    studios = data.studios || [];
    features = data.features || [];
    const routedFeatureId = currentRoute.view === "studio" ? currentRoute.featureId : "";
    const waitingForCharacterContext = studioCharacterContextPending(currentRoute);
    if (!waitingForCharacterContext) {
      if (currentRoute.view === "studio") applyStudioRouteSelection();
      else reconcileStudioFeature(routedFeatureId || currentFeatureId || "", false);
    }
    renderSidebar();
    if ((!currentRoute.view.startsWith("character-") && !waitingForCharacterContext) || charactersLoaded) renderWorkspace();
    showAlert("");
    setServiceStatus(true, "服務正常");
  } catch (error) {
    if (isStaleSessionError(error)) return;
    setServiceStatus(false, "服務連線異常");
  }
}

function studioFeatures(studioId) {
  const available = availableStudioFeatures(studioId);
  const groups = new Map();
  for (const feature of available) {
    const key = feature.toolGroup || feature.id;
    if (!groups.has(key) || feature.id === currentFeatureId) groups.set(key, feature);
  }
  return [...groups.values()];
}

function studioToolTitle(feature) {
  return (feature.toolGroup && features.find((item) => item.id === feature.toolGroup)?.title) || feature.title;
}

function studioToolVersions(feature) {
  return feature.toolGroup
    ? availableStudioFeatures(feature.studio).filter((item) => item.toolGroup === feature.toolGroup)
    : [feature];
}

function studioToolSubtitle(feature) {
  const versions = studioToolVersions(feature);
  return versions.length > 1 ? versions.map((item) => item.versionLabel).join(" / ") : feature.subtitle;
}

function currentFeature() {
  return availableStudioFeatures().find((feature) => feature.id === currentFeatureId) || null;
}

function currentCharacter() {
  return characters.find((c) => c.id === selectedCharacterId) || characters[0] || null;
}

function activeCharacter() {
  return characters.find((c) => c.id === activeCharacterId) || null;
}

function characterFixedPrompt(character) {
  const style = (character && character.style) || {};
  return String(style.fixedPrompt || "").trim();
}

function characterNegativePrompt(character) {
  const style = (character && character.style) || {};
  return String(style.negativePrompt || "").trim();
}

function mergePrompt(prefix, value) {
  return [prefix, value].map((item) => String(item || "").trim()).filter(Boolean).join(", ");
}

function aiSisterPersona(character) {
  if (!character) return "";
  const voice = character.voice || {};
  if (character.kind === "ai_sister" && voice.persona) return String(voice.persona);
  if (String(character.id || "").startsWith("ai-sister-")) return String(character.id).slice("ai-sister-".length);
  return "";
}

function applyCharacterDefaults(feature, settings) {
  const character = activeCharacter();
  if (!character) return settings;
  const next = {
    ...settings,
    _characterId: character.id,
    _characterName: character.name,
  };
  const fixed = characterFixedPrompt(character);
  const negative = characterNegativePrompt(character);
  if (fixed && Object.prototype.hasOwnProperty.call(next, "positive")) {
    next.positive = mergePrompt(fixed, next.positive);
  }
  if (negative && Object.prototype.hasOwnProperty.call(next, "negative")) {
    next.negative = mergePrompt(next.negative, negative);
  }

  const voice = character.voice || {};
  if ((feature.id === "voice-speak" || feature.id === "voxcpm-tts") && voice.persona && next.voiceSource !== "library") {
    next.persona = String(voice.persona);
  }
  if (feature.id === "post-generator") {
    const sister = aiSisterPersona(character);
    if (sister) next.sister = sister;
  }
  return next;
}

function featureReadyForContext(feature) {
  if (!feature) return false;
  if (feature.id === "voice-speak" && !activeCharacter() && typeof feature.genericReady === "boolean") {
    return feature.genericReady === true;
  }
  return feature.ready === true;
}

function featureMissingNodesForContext(feature) {
  if (feature && feature.id === "voice-speak" && !activeCharacter() && Array.isArray(feature.genericMissingNodes)) {
    return feature.genericMissingNodes;
  }
  return feature && Array.isArray(feature.missingNodes) ? feature.missingNodes : [];
}

function interactiveReady(feature) {
  if (feature && feature.operatorOnly && !isOperator()) return false;
  return (feature.runnable && featureReadyForContext(feature)) || (feature.kind === "text" && featureReadyForContext(feature));
}

function featureUnavailableReason(feature) {
  if (!feature) return "尚未設定對應模組";
  if (feature.operatorOnly && !isOperator()) return "這項功能只開放給指定操作人";
  if (feature.id === "voice-speak" && !activeCharacter() && feature.genericRuntimeReason) {
    return String(feature.genericRuntimeReason);
  }
  if (feature.runtimeReason) return String(feature.runtimeReason);
  const missingModels = Array.isArray(feature.missingModels) ? feature.missingModels : [];
  const missingNodes = featureMissingNodesForContext(feature);
  if (missingModels.length && missingNodes.length) return "必要模型與元件尚未安裝完整";
  if (missingModels.length) return "必要模型尚未安裝完整";
  if (missingNodes.length) return "必要元件尚未安裝完整";
  return "工具目前尚未就緒";
}

function characterCapabilities(character) {
  return character && Array.isArray(character.capabilities) ? character.capabilities : [];
}

// A capability must pass both the server-projected class and this consumer
// entry policy. This prevents a malformed future snapshot from opening a
// realistic-only tool for anime (or vice versa). Illustration intentionally
// has no entries until an illustration-specific module is qualified.
const CHARACTER_FEATURE_VISUAL_POLICY = Object.freeze({
  "anime-scene-card": Object.freeze({ actions: Object.freeze(["photoshoot"]), visualClasses: Object.freeze(["anime"]) }),
  img2video: Object.freeze({ actions: Object.freeze(["image-video"]), visualClasses: Object.freeze(["anime"]) }),
  dance: Object.freeze({ actions: Object.freeze(["dance"]), visualClasses: Object.freeze(["anime"]) }),
  "post-generator": Object.freeze({ actions: Object.freeze(["post"]), visualClasses: Object.freeze(["anime"]) }),
  "voice-speak": Object.freeze({ actions: Object.freeze(["voice-speak"]), visualClasses: Object.freeze(["anime", "realistic"]) }),
  lipsync: Object.freeze({ actions: Object.freeze(["lipsync"]), visualClasses: Object.freeze(["realistic"]) }),
  "h3-image-video": Object.freeze({ actions: Object.freeze(["h3-image-video"]), visualClasses: Object.freeze(["anime", "realistic"]) }),
  "h3-reference-video": Object.freeze({ actions: Object.freeze(["h3-reference-video"]), visualClasses: Object.freeze(["anime", "realistic"]) }),
});

function capabilityMatchesFeatureVisualPolicy(capability, visualClass) {
  const featureId = String(capability && capability.featureId || "").trim();
  const actionId = String(capability && capability.actionId || "").trim();
  const policy = CHARACTER_FEATURE_VISUAL_POLICY[featureId];
  return Boolean(
    policy
    && policy.actions.includes(actionId)
    && policy.visualClasses.includes(visualClass)
  );
}

function eligibleCharacterCapabilities(character) {
  const exactVisualClass = characterVisualClass(character);
  if (exactVisualClass === "unknown") return [];
  return characterCapabilities(character).filter((capability) => (
    capability
    && String(capability.actionId || "").trim()
    && String(capability.featureId || "").trim()
    && String(capability.variantId || "").trim()
    && String(capability.visualClass || "").trim().toLowerCase() === exactVisualClass
    && capabilityMatchesFeatureVisualPolicy(capability, exactVisualClass)
  ));
}

function enabledCharacterCapabilities(character) {
  return eligibleCharacterCapabilities(character).filter((capability) => (
    capability.enabled === true && capability.runtimeReady === true
  ));
}

function characterCapabilityUnavailableReasons(character) {
  return [...new Set(eligibleCharacterCapabilities(character)
    .filter((capability) => capability.enabled === true && capability.runtimeReady === false)
    .map((capability) => String(capability.runtimeReason || capability.disabledReason || "").trim())
    .filter(Boolean))];
}

function characterNoActionMessage(character) {
  const reasons = characterCapabilityUnavailableReasons(character);
  if (reasons.length) return reasons.join("；");
  if (eligibleCharacterCapabilities(character).some((capability) => capability.enabled !== true)) {
    return "這個角色的可用功能目前都在角色設定中關閉。";
  }
  return "這個角色目前沒有已驗證的創作功能。";
}

function enabledActionCapability(character, actionId, featureId = "") {
  const wantedAction = String(actionId || "");
  const wantedFeature = String(featureId || "");
  const matches = enabledCharacterCapabilities(character).filter((capability) => (
    String(capability.actionId || "") === wantedAction
    && (!wantedFeature || String(capability.featureId || "") === wantedFeature)
  ));
  return matches.length === 1 ? matches[0] : null;
}

function enabledFeatureCapability(character, featureId) {
  const wantedFeature = String(featureId || "");
  const matches = enabledCharacterCapabilities(character).filter((capability) => (
    String(capability.featureId || "") === wantedFeature
  ));
  return matches.length === 1 ? matches[0] : null;
}

function capabilityStillEnabled(characterId, actionId, variantId = "", featureId = "") {
  const character = characters.find((item) => item.id === characterId) || null;
  if (!character) return null;
  const capability = enabledActionCapability(character, actionId, featureId);
  if (!capability) return null;
  if (variantId && String(capability.variantId || "") !== String(variantId)) return null;
  return capability;
}

function h3ProviderIntent(character, capability, state = {}) {
  const visualClass = characterVisualClass(character);
  const profileKey = String(capability && capability.providerProfileKey || "").trim();
  const assetId = String(capability && capability.providerAssetId || "").trim();
  const actionId = String(capability && capability.actionId || "").trim();
  const featureId = String(capability && capability.featureId || "").trim();
  if (
    !character || !["anime", "realistic"].includes(visualClass)
    || !["h3-image-video", "h3-reference-video"].includes(actionId)
    || featureId !== actionId
    || !/^local_comfy:[a-z0-9][a-z0-9._:-]{0,127}$/.test(profileKey)
    || assetId !== `castle-character:${character.id}:avatar`
  ) return null;
  const choose = (key, current) => {
    const allowed = allowedOptionValues(capability, key) || new Set();
    const selected = typeof current === "string" ? current : "";
    return allowed.has(selected) ? selected : (allowed.values().next().value || "");
  };
  const aspect = choose("aspect", state.aspect);
  const motionProfile = choose("motionProfile", state.motionProfile || state.motionPreset);
  const quality = choose("quality", state.quality);
  if (!aspect || !motionProfile || !quality) return null;
  return {
    schema_version: 1,
    capability: "image_to_video",
    visual_class: visualClass,
    characters: [{
      character_id: character.id,
      visual_class: visualClass,
      display_name: String(character.name || character.id),
      external_refs: [],
    }],
    assets: [{
      asset_id: assetId,
      kind: "image",
      uri: `castle://character-assets/${character.id}/avatar`,
      visual_class: visualClass,
      character_ids: [character.id],
      mime_type: null,
      byte_size: null,
      sha256: null,
      duration_seconds: null,
    }],
    options: { aspect, motion_profile: motionProfile, quality },
    preferred_providers: ["local_comfy"],
    provider_id: "local_comfy",
    max_credits: 0,
    action_id: actionId,
    feature_id: featureId,
    profile_key: profileKey,
  };
}

function openH3ProviderPlan(character, capability, state) {
  const intent = h3ProviderIntent(character, capability, state);
  if (!intent) {
    showAlert("這個 H3 模組尚未完成資源規劃契約。", true);
    return;
  }
  pendingProviderIntent = intent;
  pendingProviderAutoStart = false;
  navigate({ view: "resources" });
}

function allowedOptionValues(capability, optionId) {
  const spec = capability && capability.allowedOptions;
  if (!spec) return null;
  let raw;
  if (Array.isArray(spec)) {
    const entry = spec.find((item) => item && typeof item === "object" && String(item.id || item.field || item.key || "") === optionId);
    if (entry) raw = entry.values ?? entry.options ?? entry.allowed;
    else if (optionId === "mode" && spec.every((item) => ["string", "number", "boolean"].includes(typeof item))) raw = spec;
  } else if (typeof spec === "object") {
    raw = spec[optionId];
  }
  if (raw && typeof raw === "object" && !Array.isArray(raw)) raw = raw.values ?? raw.options ?? raw.allowed;
  if (raw === undefined || raw === null) return null;
  const values = Array.isArray(raw) ? raw : [raw];
  return new Set(values.map((value) => String(value)));
}

function filterCapabilityOptions(capability, optionId, options) {
  const list = Array.isArray(options) ? options : [];
  const allowed = allowedOptionValues(capability, optionId);
  if (!allowed) return list;
  return list.filter((option) => {
    const value = option && typeof option === "object"
      ? (option.value !== undefined ? option.value : (option.width && option.height ? `${option.width}x${option.height}` : option.id))
      : option;
    return allowed.has(String(value));
  });
}

function filterCapabilityOptionsByIds(capability, optionIds, options) {
  const ids = Array.isArray(optionIds) ? optionIds : [optionIds];
  const constrainedId = ids.find((id) => allowedOptionValues(capability, id) !== null);
  return constrainedId ? filterCapabilityOptions(capability, constrainedId, options) : (Array.isArray(options) ? options : []);
}

function capabilityServerBindsUpload(capability, uploadId) {
  return Boolean(
    capability
    && Array.isArray(capability.serverBoundUploads)
    && capability.serverBoundUploads.includes(uploadId)
  );
}

function normalizeCapabilityChoice(capability, optionIds, options, current, fallback = "") {
  const ids = Array.isArray(optionIds) ? optionIds : [optionIds];
  const constrained = ids.some((id) => allowedOptionValues(capability, id) !== null);
  const filtered = filterCapabilityOptionsByIds(capability, optionIds, options);
  if (!filtered.length) return { options: filtered, value: constrained ? "" : fallback };
  const values = new Set(filtered.map((option) => String(option && typeof option === "object" ? option.value : option)));
  const first = filtered[0] && typeof filtered[0] === "object" ? filtered[0].value : filtered[0];
  return { options: filtered, value: values.has(String(current)) ? current : first };
}

function capabilityField(feature, capability, field) {
  if (!field) return null;
  if (!["select", "aspect"].includes(field.type) && field.display !== "cards") return field;
  const visualClass = String(capability && capability.visualClass || "").trim().toLowerCase();
  const classOptions = field.optionsByVisualClass && typeof field.optionsByVisualClass === "object"
    ? field.optionsByVisualClass[visualClass]
    : null;
  const sourceOptions = Array.isArray(classOptions) ? classOptions : (field.options || []);
  let options = filterCapabilityOptions(capability, field.id, sourceOptions);
  const character = activeCharacter();
  if (feature && feature.id === "dance" && field.id === "mode" && character && characterVisualClass(character) === "anime") {
    options = options.filter((option) => String(option && typeof option === "object" ? option.value : option) === "animate");
  }
  if (!options.length && allowedOptionValues(capability, field.id)) return null;
  if (options === field.options && !Array.isArray(classOptions)) return field;
  const values = new Set(options.map((option) => (
    field.type === "aspect" ? `${option.width}x${option.height}` : String(option.value)
  )));
  const fallback = options[0]
    ? (field.type === "aspect" ? `${options[0].width}x${options[0].height}` : options[0].value)
    : field.default;
  return {
    ...field,
    options,
    default: values.has(String(field.default)) ? field.default : fallback,
  };
}

function studioBaseFeatures(studioId = "") {
  const contextId = routeCharacterContextId();
  const character = contextId ? characters.find((item) => item.id === contextId) || null : null;
  if (contextId && !character) return [];
  return features.filter((feature) => (
    (!studioId || feature.studio === studioId)
    && !feature.uiHidden
    && (!feature.characterOnly || Boolean(character))
    && (!feature.operatorOnly || isOperator())
  ));
}

function availableStudioFeatures(studioId = "") {
  const base = studioBaseFeatures(studioId);
  const contextId = routeCharacterContextId();
  const character = contextId ? characters.find((item) => item.id === contextId) || null : null;
  if (!character) return base;
  const allowed = new Set(enabledCharacterCapabilities(character).map((capability) => String(capability.featureId || "")));
  return base.filter((feature) => allowed.has(feature.id));
}

function compatibleStudioFeature(preferredId = "") {
  const available = availableStudioFeatures();
  return available.find((feature) => feature.id === preferredId)
    || available.find((feature) => interactiveReady(feature))
    || available[0]
    || null;
}

function reconcileStudioFeature(preferredId = currentFeatureId, replaceRoute = false) {
  const selected = compatibleStudioFeature(preferredId || "");
  currentFeatureId = selected ? selected.id : null;
  currentStudio = selected ? selected.studio : (studios[0] && studios[0].id);
  if (replaceRoute && currentRoute.view === "studio") {
    const canonical = studioRoute(currentFeatureId || "", currentRoute.characterId || "");
    if (routePath(currentRoute) !== routePath(canonical)) {
      currentRoute = canonical;
      history.replaceState(history.state, "", routePath(currentRoute));
    }
  }
  return selected;
}

function applyStudioRouteSelection() {
  studioRouteProblem = "";
  const requestedId = String(currentRoute.featureId || "");
  if (requestedId) {
    const exact = availableStudioFeatures().find((feature) => feature.id === requestedId) || null;
    if (!exact) {
      const known = features.find((feature) => feature.id === requestedId) || null;
      currentFeatureId = null;
      currentStudio = known ? known.studio : (studios[0] && studios[0].id);
      studioRouteProblem = known
        ? "這個工具不適用目前角色或入口。沒有替你改開其他功能。"
        : "這個連結指定的創作工具不存在。沒有替你改開其他功能。";
      return null;
    }
  }
  return reconcileStudioFeature(requestedId || currentFeatureId || "", true);
}

function safeProviderCharacterId(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return /^[a-z0-9][a-z0-9._:-]{0,127}$/.test(normalized) ? normalized : "";
}

function safeProviderProfileUrl(value, profileAssetId) {
  const assetId = safeProviderCharacterId(profileAssetId);
  const candidate = String(value || "").trim();
  if (!assetId || !candidate.startsWith("/") || candidate.startsWith("//")) return "";
  try {
    const parsed = new URL(candidate, window.location.origin);
    const match = /^\/api\/assets\/([^/]+)\/preview$/.exec(parsed.pathname);
    if (parsed.origin !== window.location.origin || parsed.search || !match) return "";
    return safeProviderCharacterId(decodeURIComponent(match[1])) === assetId
      ? parsed.pathname
      : "";
  } catch (_) {
    return "";
  }
}

const PROVIDER_PHOTOSHOOT_ASPECTS = Object.freeze([
  Object.freeze({ value: "768x1024", label: "直式" }),
  Object.freeze({ value: "1024x1024", label: "方形" }),
  Object.freeze({ value: "1024x768", label: "橫式" }),
]);
const PROVIDER_PHOTOSHOOT_SAFETY_NEGATIVE = "child, minor, underage";

function normalizeProviderGenerationOptions(value) {
  if (!Array.isArray(value)) return null;
  const entries = new Map();
  for (const raw of value) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
    const id = String(raw.id || "").trim();
    if (!id || entries.has(id)) return null;
    entries.set(id, raw);
  }
  if ([...entries.keys()].some((id) => !["aspect_ratio", "prompt", "negative_prompt", "seed"].includes(id))) return null;
  const aspect = entries.get("aspect_ratio");
  const prompt = entries.get("prompt");
  const negativePrompt = entries.get("negative_prompt");
  const seed = entries.get("seed");
  if (!aspect || !prompt || !negativePrompt || !seed) return null;
  const knownAspects = new Set(PROVIDER_PHOTOSHOOT_ASPECTS.map((option) => option.value));
  const aspects = [...new Set((Array.isArray(aspect.values) ? aspect.values : [])
    .map((item) => String(item || "").trim())
    .filter((item) => knownAspects.has(item)))];
  const defaultAspect = String(aspect.default || "").trim();
  const promptMaxLength = Number(prompt.max_length);
  const negativePromptMaxLength = Number(negativePrompt.max_length);
  const seedMin = Number(seed.min);
  const seedMax = Number(seed.max);
  const seedDefault = Number(seed.default);
  if (
    !aspects.length || !aspects.includes(defaultAspect)
    || !Number.isInteger(promptMaxLength) || promptMaxLength < 1 || promptMaxLength > 4000
    || !Number.isInteger(negativePromptMaxLength) || negativePromptMaxLength < 1 || negativePromptMaxLength > 4000
    || !Number.isSafeInteger(seedMin) || !Number.isSafeInteger(seedMax) || !Number.isSafeInteger(seedDefault)
    || seedMin < -1 || seedMax > 2147483647 || seedMin > seedMax
    || seedDefault < seedMin || seedDefault > seedMax
  ) return null;
  return { aspects, defaultAspect, promptMaxLength, negativePromptMaxLength, seedMin, seedMax, seedDefault };
}

function normalizeProviderGenerationEntrypoint(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  if (
    value.kind !== "provider_character_studio"
    || value.action_id !== "photoshoot"
    || value.feature_id !== "clipmode-character-photoshoot"
    || value.variant_id !== "photoshoot.sdxl-character-lora.v1"
  ) return null;
  const allowedOptions = normalizeProviderGenerationOptions(value.allowed_options);
  if (!allowedOptions) return null;
  return {
    kind: value.kind,
    actionId: value.action_id,
    featureId: value.feature_id,
    variantId: value.variant_id,
    allowedOptions,
  };
}

function normalizeProviderCharacterCard(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const id = safeProviderCharacterId(raw.character_id);
  const rawName = String(raw.display_name || "").trim();
  const visualClass = String(raw.visual_class || "").trim().toLowerCase();
  const name = /^clipmode\s+[a-z0-9]{4,}$/i.test(rawName)
    ? ({
      anime: "待命名動漫角色",
      illustration: "待命名動漫角色",
      realistic: "待命名真人角色",
      unknown: "待命名角色",
    }[visualClass] || "待命名角色")
    : rawName;
  const profileAssetId = safeProviderCharacterId(raw.profile_asset_id);
  if (!id || !name || name.length > 160 || !["anime", "realistic", "illustration", "unknown"].includes(visualClass)) return null;
  const sourceProviderIds = [...new Set((Array.isArray(raw.source_provider_ids) ? raw.source_provider_ids : [])
    .map(safeProviderCharacterId)
    .filter(Boolean))];
  const generationEntrypoint = raw.generation_enabled === true
    ? normalizeProviderGenerationEntrypoint(raw.generation_entrypoint)
    : null;
  return {
    id,
    name,
    visualClass,
    profileAssetId,
    avatarUrl: safeProviderProfileUrl(raw.profile_url, profileAssetId),
    sourceProviderIds,
    catalogOnly: raw.catalog_only === true,
    generationEnabled: raw.generation_enabled === true && Boolean(generationEntrypoint),
    generationEntrypoint,
  };
}

async function fetchProviderCharacterCards() {
  const byId = new Map();
  let cursor = "";
  for (let page = 0; page < 8; page += 1) {
    const endpoint = cursor
      ? `/api/provider-characters?cursor=${encodeURIComponent(cursor)}`
      : "/api/provider-characters";
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 6000);
    let data;
    try {
      data = await api(endpoint, { signal: controller.signal });
    } catch (error) {
      if (error && error.name === "AbortError") {
        throw new Error("角色資料讀取逾時；本機角色仍可正常使用。");
      }
      throw error;
    } finally {
      window.clearTimeout(timeout);
    }
    if (data.provider_transport_performed !== false || data.credits_spent !== 0) {
      throw new Error("外部角色清單未證明為零扣點唯讀操作");
    }
    for (const raw of Array.isArray(data.characters) ? data.characters : []) {
      const character = normalizeProviderCharacterCard(raw);
      if (character && !byId.has(character.id)) byId.set(character.id, character);
    }
    const next = typeof data.next_cursor === "string" ? data.next_cursor : "";
    if (!next) break;
    if (next.length > 8192 || /[\u0000-\u001f\u007f]/.test(next) || next === cursor) {
      throw new Error("外部角色清單分頁資料不正確");
    }
    cursor = next;
    if (page === 7) throw new Error("外部角色清單頁數超出上限");
  }
  return [...byId.values()];
}

async function loadCharacters() {
  const session = captureSessionContext();
  const renderAvailableCharacters = () => {
    if (!sessionContextIsCurrent(session)) return;
    activeCharacterId = routeCharacterContextId(currentRoute);
    if (currentView === "studio") applyStudioRouteSelection();
    renderSidebar();
    if (["characters", "studio", "resources"].includes(currentView)) renderWorkspace();
  };
  const legacyTask = api("/api/characters").then((data) => {
    assertSessionContext(session);
    characters = Array.isArray(data.characters) ? data.characters : [];
    charactersLoaded = true;
    characterActions = Array.isArray(data.actions) ? data.actions : [];
    if (!selectedCharacterId || !characters.some((c) => c.id === selectedCharacterId)) {
      selectedCharacterId = (characters[0] || {}).id || "";
    }
    renderAvailableCharacters();
  }).catch((error) => {
    if (isStaleSessionError(error)) return;
    charactersLoaded = true;
    renderAvailableCharacters();
    showAlert(`Castle 角色讀取失敗：${error.message}`, true);
  });
  const providerTask = fetchProviderCharacterCards().then((value) => {
    assertSessionContext(session);
    providerCharacters = value;
    providerCharactersError = "";
    providerCharactersLoaded = true;
    renderAvailableCharacters();
  }).catch((error) => {
    if (isStaleSessionError(error)) return;
    providerCharacters = [];
    providerCharactersError = safePublicErrorText(
      error && error.message,
      "外部角色資料暫時讀取失敗。",
    );
    providerCharactersLoaded = true;
    renderAvailableCharacters();
  });
  await Promise.all([legacyTask, providerTask]);
}

async function refreshActiveCharacterCapabilities() {
  const characterId = activeCharacterId;
  if (document.hidden || !characterId || !currentUser || !authStatus.authenticated) return;
  const index = characters.findIndex((character) => character.id === characterId);
  if (index < 0) return;
  try {
    const data = await api(`/api/characters/${encodeURIComponent(characterId)}/capabilities`);
    if (activeCharacterId !== characterId || !Array.isArray(data.capabilities)) return;
    const previous = JSON.stringify(characters[index].capabilities || []);
    const next = JSON.stringify(data.capabilities);
    if (previous === next) return;
    characters[index] = { ...characters[index], capabilities: data.capabilities };
    if (currentView === "studio") applyStudioRouteSelection();
    renderSidebar();
    if (
      currentView === "studio"
      || (["character-workspace", "character-library", "character-wardrobe", "character-settings"].includes(currentRoute.view) && currentRoute.characterId === characterId)
    ) renderWorkspace();
  } catch (_) {
    /* The last server snapshot remains usable; generate still rechecks runtime. */
  }
}

// ---- sidebar (grouped tools) ----------------------------------------------
function statusDot(feature) {
  if (window.WebComfyDocs) return h("span", { class: "tool-dot tool" });
  let cls = "muted";
  if (feature.runnable) cls = interactiveReady(feature) ? "ok" : "missing";
  else if (feature.kind === "storage" || feature.kind === "text") cls = "tool";
  return h("span", { class: `tool-dot ${cls}` });
}

function renderSidebar() {
  const wrap = $("sidebarGroups");
  clear(wrap);
  for (const studio of studios) {
    const list = studioFeatures(studio.id);
    if (!list.length) continue;
    const tools = h("div", { class: "side-tools" });
    for (const feature of list) {
      tools.appendChild(
        h("button", {
          id: `studio-side-tool-${feature.id}`,
          class: `side-tool${feature.id === currentFeatureId && currentView === "studio" ? " active" : ""}`,
          type: "button",
          onclick: () => selectFeature(feature.id),
        }, [
          h("span", { class: "side-tool-ico", text: feature.icon || "•" }),
          h("span", { class: "side-tool-name", text: studioToolTitle(feature) }),
          statusDot(feature),
        ])
      );
    }
    wrap.appendChild(h("div", { class: "side-group" }, [
      h("div", { class: "side-group-head" }, [
        h("span", { class: "side-group-ico", text: studio.icon || "" }),
        h("span", { text: studio.title }),
      ]),
      tools,
    ]));
  }
}

function selectFeature(id) {
  const feature = availableStudioFeatures().find((item) => item.id === id) || null;
  if (!feature) {
    showAlert(activeCharacter() ? "這個工具不適用目前角色。" : "這個工具目前不可用。", true);
    return;
  }
  currentFeatureId = feature.id;
  currentStudio = feature.studio;
  navigate(studioRoute(feature.id));
  window.WebComfyDocs?.openFeature(feature.id, document.getElementById(`studio-side-tool-${feature.id}`));
}

// ---- view switching -------------------------------------------------------
function syncViewTabs() {
  document.body.dataset.view = currentView;
  document.querySelectorAll(".view-tab, .phone-tab[data-view]").forEach((tab) => {
    const active = tab.dataset.view === currentView;
    tab.classList.toggle("active", active);
    if (active) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  });
  renderAdminChrome();
}

function setView(view) {
  if (sheetStack.length) closeAllSheets();
  if (view === "studio") {
    const keepGenericFeature = currentRoute.view === "studio" && !currentRoute.characterId;
    navigate(studioRoute(keepGenericFeature ? (currentFeatureId || "") : "", ""));
    return;
  }
  navigate({ view });
}

function renderWorkspace() {
  document.body.dataset.view = currentView;
  const characterComposer = ["character-workspace", "provider-character-studio"].includes(currentRoute.view);
  document.body.classList.toggle("has-character-composer", characterComposer);
  if (!characterComposer) unbindGenerateComposer();
  if (currentRoute.view === "character-workspace") renderCharacterWorkspaceRoute();
  else if (currentRoute.view === "provider-character-studio") renderProviderCharacterStudioRoute();
  else if (currentRoute.view === "character-library") renderCharacterLibraryRoute();
  else if (currentRoute.view === "character-wardrobe") renderCharacterWardrobeRoute();
  else if (currentRoute.view === "character-settings") renderCharacterSettingsRoute();
  else if (currentView === "characters") renderCharactersView();
  else if (currentView === "library") renderLibraryView();
  else if (currentView === "projects") renderProjectPreviewView();
  else if (currentView === "resources") renderProviderFabricView();
  else if (currentView === "workflows") {
    const ws = beginWorkspaceRender();
    window.WorkflowLibrary.mount(ws, window.WebComfyDocs ? window.WebComfyDocs.libraryOptions(openSheet) : {openSheet, canLoad: isOperator()});
  }
  else if (currentView === "dance-library") {
    if (isOperator()) renderDanceLibraryManager();
    else renderOperatorRequiredView();
  }
  else if (currentView === "orchestrator") {
    if (isAdmin()) renderOrchestratorView();
    else renderAdminRequiredView();
  }
  else if (currentView === "admin") {
    if (isAdmin()) renderAdminHome();
    else renderAdminRequiredView();
  }
  else if (currentView === "admin-users") {
    if (isAdmin()) renderAdminUsersView();
    else renderAdminRequiredView();
  }
  else renderStage();
}

function renderProjectPreviewView() {
  if (!(isAdmin() || isOperator())) {
    const ws = beginWorkspaceRender();
    ws.appendChild(h("section", { class: "panel empty-state-panel" }, [
      h("div", { class: "empty", text: "需要管理員或指定操作員權限" }),
    ]));
    return;
  }
  const ws = beginWorkspaceRender("workspace-route-root project-preview-route");
  const host = h("div", { class: "project-preview-host" });
  ws.appendChild(host);
  if (!window.CastleProjectPreview || typeof window.CastleProjectPreview.mount !== "function") {
    host.appendChild(h("section", { class: "panel empty-state-panel" }, [
      h("div", { class: "empty", text: "專案規劃工具載入失敗，請重新整理頁面。" }),
    ]));
    return;
  }
  const options = {
    authorized: true,
    canConfigureProviders: isAdmin(),
    preferenceScope: storageIdentity(),
    characters: characters.map((character) => ({
      id: character.id,
      label: character.name || character.id,
      visualClass: characterVisualClass(character),
      avatarUrl: characterProfilePicUrl(character),
      profileAssetId: `castle-character:${character.id}:avatar`,
    })),
  };
  let component = cachedProjectPreview;
  if (component) {
    component.configure(options);
    host.replaceChildren(component);
  } else {
    component = window.CastleProjectPreview.mount(host, options);
    cachedProjectPreview = component;
    component.addEventListener("castle-project-provider-plan", (event) => {
      const intent = event && event.detail;
      if (!intent || typeof intent !== "object" || Array.isArray(intent)) {
        showAlert("專案規劃資料不完整，已停止。", true);
        return;
      }
      pendingProviderIntent = intent;
      pendingProviderAutoStart = true;
      navigate({ view: "resources" });
    });
  }
}

function providerFabricCharacterVisualClass(character) {
  return characterVisualClass(character);
}

function renderProviderFabricView() {
  const ws = beginWorkspaceRender("workspace-route-root provider-fabric-route");
  const host = h("div", { class: "provider-fabric-host" });
  ws.appendChild(host);
  if (!window.CastleProviderFabric || typeof window.CastleProviderFabric.mount !== "function") {
    host.appendChild(h("section", { class: "panel empty-state-panel" }, [
      h("div", { class: "empty", text: "資源中心載入失敗，請重新整理頁面。" }),
    ]));
    return;
  }
  const requestedIntent = pendingProviderIntent;
  const autoStartPendingIntent = pendingProviderAutoStart === true;
  pendingProviderIntent = null;
  pendingProviderAutoStart = false;
  const component = window.CastleProviderFabric.mount(host, {
    isAdmin: isAdmin(),
    isOperator: isOperator(),
    characters: characters.map((character) => ({
      id: character.id,
      label: character.name || character.id,
      visualClass: providerFabricCharacterVisualClass(character),
      avatarUrl: characterProfilePicUrl(character),
    })),
    pendingIntent: requestedIntent,
    autoStartPendingIntent,
  });
  component.addEventListener("castle-provider-catalog-changed", () => {
    loadCharacters();
  }, { once: true });
  const requestedFocus = pendingResourceFocus;
  pendingResourceFocus = "";
  if (requestedFocus && component && typeof component.focusSection === "function") {
    component.focusSection(requestedFocus);
  }
}

function renderAdminRequiredView() {
  const ws = beginWorkspaceRender();
  ws.appendChild(h("section", { class: "panel empty-state-panel" }, [
    h("div", { class: "empty", text: "需要管理員權限" }),
  ]));
}

function renderOperatorRequiredView() {
  const ws = beginWorkspaceRender();
  ws.appendChild(h("section", { class: "panel empty-state-panel" }, [
    h("div", { class: "empty", text: "需要模板庫操作員權限" }),
  ]));
}

// ---- character platform ----------------------------------------------------
function actionFeature(action, character) {
  const capability = enabledActionCapability(character, action && action.id);
  if (!capability) return null;
  return featureById(capability.featureId);
}

async function createCharacter() {
  const session = captureSessionContext();
  const name = window.prompt("角色名稱", "");
  if (name === null) return;
  if (!name.trim()) {
    showAlert("角色名稱不能空白。", true);
    return;
  }
  try {
    const data = await api("/api/characters", {
      method: "POST",
      body: JSON.stringify({
        name: name.trim(),
        kind: "custom",
        profile: "",
        personality: "",
        origin: "castle-local",
        style: { base: "realistic", substyle: "", fixedPrompt: name.trim(), negativePrompt: "lowres, bad anatomy, watermark, text" },
      }),
    });
    assertSessionContext(session);
    selectedCharacterId = data.character.id;
    await loadCharacters();
    assertSessionContext(session);
    showAlert("角色已建立。");
  } catch (error) {
    if (isStaleSessionError(error)) return;
    showAlert(`建立角色失敗：${error.message}`, true);
  }
}

async function createExperimentalCharacter(form) {
  const session = captureSessionContext();
  const data = new FormData(form);
  const name = String(data.get("name") || "").trim();
  const input = form.querySelector('input[name="photos"]');
  const files = Array.from((input && input.files) || []);
  if (!name) {
    showAlert("請輸入角色名稱。", true);
    return;
  }
  if (!files.length) {
    showAlert("請至少選一張照片。", true);
    return;
  }
  if (files.length > 6) {
    showAlert("最多一次上傳 6 張照片。", true);
    return;
  }
  for (const file of files) {
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type) || file.size > 15 * 1024 * 1024) {
      showAlert(file.size > 15 * 1024 * 1024 ? "檔案太大" : "不支援的圖片格式", true);
      return;
    }
  }
  try {
    const photos = [];
    for (const file of files) {
      const payload = await fileToData(file);
      assertSessionContext(session);
      photos.push({ name: file.name, data: payload.dataUrl });
    }
    const result = await api("/api/characters/experimental", {
      method: "POST",
      body: JSON.stringify({ name, photos }),
    });
    assertSessionContext(session);
    experimentalCreateOpen = false;
    selectedCharacterId = result.character.id;
    await loadCharacters();
    assertSessionContext(session);
    navigate({ view: "character-workspace", characterId: result.character.id });
    showAlert("實驗角色已建立。");
  } catch (error) {
    if (isStaleSessionError(error)) return;
    showAlert(error.message, true);
  }
}

function renderExperimentalCreateForm() {
  const form = h("form", {
    class: "experimental-create-form",
    onsubmit: (event) => {
      event.preventDefault();
      createExperimentalCharacter(event.target);
    },
  }, [
    labeledField("名稱", h("input", { name: "name", maxlength: "120", required: "" })),
    labeledField("照片", h("input", { name: "photos", type: "file", accept: "image/jpeg,image/png,image/webp", multiple: "", required: "" })),
    h("div", { class: "experimental-create-actions" }, [
      h("button", { class: "primary", type: "submit", text: "建立" }),
      h("button", { class: "ghost", type: "button", text: "取消", onclick: () => { experimentalCreateOpen = false; renderCharactersView(); } }),
    ]),
  ]);
  return form;
}

async function saveCharacter(character, form) {
  const session = captureSessionContext();
  const data = new FormData(form);
  const eligibleActions = eligibleCharacterActions(character);
  const actionIds = [];
  for (const action of eligibleActions) {
    if (data.get(`action:${action.id}`)) actionIds.push(action.id);
  }
  const voiceIds = String(data.get("voiceLibraryIds") || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  const officialAssetOverrides = {
    notes: String(data.get("assetNotes") || ""),
  };
  let body;
  if (character.official === true) {
    body = {
      voice: {
        voiceLibraryIds: voiceIds,
      },
      assets: officialAssetOverrides,
      actions: actionIds,
    };
  } else {
    body = {
      name: String(data.get("name") || "").trim(),
      kind: String(data.get("kind") || "custom").trim(),
      group: String(data.get("group") || character.group || "custom").trim(),
      tagline: String(data.get("tagline") || ""),
      profile: String(data.get("profile") || ""),
      personality: String(data.get("personality") || ""),
      origin: String(data.get("origin") || ""),
      color: String(data.get("color") || ""),
      style: {
        base: String(data.get("styleBase") || ""),
        substyle: String(data.get("styleSubstyle") || ""),
        fixedPrompt: String(data.get("fixedPrompt") || ""),
        negativePrompt: String(data.get("negativePrompt") || ""),
      },
      languageModel: {
        provider: String(data.get("lmProvider") || ""),
        model: String(data.get("lmModel") || ""),
        apiOwner: String(data.get("lmApiOwner") || ""),
      },
      voice: {
        ...(character.voice || {}),
        engine: String(data.get("voiceEngine") || ""),
        persona: String(data.get("voicePersona") || ""),
        trainingSource: String(data.get("voiceTrainingSource") || ""),
        voiceLibraryIds: voiceIds,
      },
      assets: officialAssetOverrides,
      actions: actionIds,
    };
    if (isAdmin()) body.visualClass = String(data.get("visualClass") || "unknown").trim();
  }
  try {
    const result = await api(`/api/characters/${encodeURIComponent(character.id)}`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    assertSessionContext(session);
    selectedCharacterId = result.character.id;
    await loadCharacters();
    assertSessionContext(session);
    showAlert("角色已更新。");
  } catch (error) {
    if (isStaleSessionError(error)) return;
    showAlert(`角色更新失敗：${error.message}`, true);
  }
}

async function deleteSelectedCharacter(character) {
  const session = captureSessionContext();
  if (!window.confirm(`刪除角色「${character.name}」？`)) return;
  try {
    await api(`/api/characters/${encodeURIComponent(character.id)}/delete`, { method: "POST", body: "{}" });
    assertSessionContext(session);
    selectedCharacterId = "";
    if (activeCharacterId === character.id) activeCharacterId = "";
    await loadCharacters();
    assertSessionContext(session);
    showAlert("角色已刪除。");
  } catch (error) {
    if (isStaleSessionError(error)) return;
    showAlert(`刪除角色失敗：${error.message}`, true);
  }
}

const CHARACTER_TYPES = [
  ["anime", "動漫角色", "動漫風格的 AI 姊妹與朋友"],
  ["realistic", "寫實角色", "真人與寫實角色"],
  ["flux", "精緻寫真角色", "Flux 精緻寫真"],
  ["experimental", "未知角色（實驗場）", "你的私人實驗角色"],
];
const CHARACTER_VISUAL_GROUPS = [
  ["anime", "動漫角色", "動漫與插畫風格角色"],
  ["realistic", "真人角色", "真人與寫實風格角色"],
  ["unknown", "待補資料", "缺少角色素材或尚待確認分類"],
];
function characterType(character) {
  return String(character && character.type || "").trim() || "experimental";
}

function characterVisualClass(character) {
  const value = String(character && character.visualClass || "").trim().toLowerCase();
  return ["anime", "realistic", "illustration", "unknown"].includes(value) ? value : "unknown";
}

function characterVisualClassLabel(character) {
  return {
    anime: "動漫",
    realistic: "真人／寫實",
    illustration: "插畫",
    unknown: "待補資料",
  }[characterVisualClass(character)];
}

function characterWallVisualGroup(character) {
  const visualClass = characterVisualClass(character);
  return visualClass === "illustration" ? "anime" : visualClass;
}

function normalizeEmail(value) {
  return String(value || "").trim().toLowerCase();
}

function canEditCharacter(character) {
  if (isAdmin()) return true;
  return characterType(character) === "experimental" && normalizeEmail(character.ownerEmail) && normalizeEmail(character.ownerEmail) === normalizeEmail(currentUser && currentUser.email);
}

function characterInitials(character) {
  return String(character.name || "?").trim().slice(0, 2).toUpperCase() || "?";
}

function characterColor(character) {
  const color = String(character.color || "").trim();
  return /^#[0-9a-fA-F]{3,8}$/.test(color) ? color : "#2f8fe0";
}

function characterAssetUrl(character, kind) {
  return ((character.assetUrls || {})[kind]) || "";
}

function characterProfilePicUrl(character) {
  return characterAssetUrl(character, "avatar");
}

function profilePicFallbackNode(character, className) {
  return h("div", {
    class: `${className} fallback`,
    text: characterInitials(character),
    style: `--character-color:${characterColor(character)}`,
  });
}

function profilePicNode(character, className = "character-avatar") {
  const url = characterProfilePicUrl(character);
  if (!url) return profilePicFallbackNode(character, className);
  return h("img", {
    class: className,
    src: url,
    alt: "",
    loading: "lazy",
    decoding: "async",
    onerror: (event) => event.currentTarget.replaceWith(profilePicFallbackNode(character, className)),
  });
}

const ACTION_VERBS = {
  photoshoot: "場景寫真",
  "group-photo": "合照",
  "multi-angle-selfie": "多角度自拍",
  "image-video": "生成影片",
  "h3-image-video": "H3 首／尾格有聲影片",
  "h3-reference-video": "H3 角色參考影片",
  dance: "跳舞",
  "voice-speak": "說話",
  lipsync: "對嘴",
  post: "貼文",
};

const P2_ACTION_GROUPS = [
  ["photo", "照片", ["photoshoot", "group-photo", "multi-angle-selfie"]],
  ["video", "影片", ["image-video", "h3-image-video", "h3-reference-video", "dance", "lipsync"]],
  ["voice", "聲音", ["voice-speak"]],
  ["text", "文字", ["post"]],
];

const P2_ACTION_ICONS = {
  photoshoot: "▧",
  "group-photo": "◉",
  "multi-angle-selfie": "◫",
  "image-video": "▶",
  "h3-image-video": "▸",
  "h3-reference-video": "◈",
  dance: "♪",
  lipsync: "◌",
  "voice-speak": "◖",
  post: "✎",
};

const PHOTO_ACTION_SECTIONS = [
  ["photoshoot", "寫真"],
  ["group-photo", "合照"],
  ["multi-angle-selfie", "自拍"],
];

const VIDEO_ACTION_LABELS = {
  "image-video": "生成影片",
  "h3-image-video": "H3 首／尾格有聲影片",
  "h3-reference-video": "H3 角色參考影片",
  dance: "跳舞",
  lipsync: "對嘴",
};

const ACTION_PLACEHOLDERS = {
  photoshoot: "例如：午後窗邊、黑色西裝、自然微笑、柔和底片感",
  "group-photo": "例如：像老朋友一起喝咖啡，溫暖自然",
  "image-video": "例如：慢慢轉頭看鏡頭，輕微微笑，鏡頭緩慢推近",
  dance: "例如：全身入鏡，動作自然，舞台光線乾淨",
  post: "例如：今天想分享生成工作流的小心得",
};

const ANIME_SCENE_OPTIONS = [
  { value: "tech", label: "科技實驗室", sub: "藍紫燈光與未來感空間" },
  { value: "culture", label: "明亮藝廊", sub: "暖光、木地板與留白展牆" },
  { value: "relationship", label: "溫馨咖啡廳", sub: "綠植與午後暖光" },
  { value: "environment", label: "森林綠地", sub: "陽光、草地與自然景深" },
  { value: "travel", label: "海邊露台", sub: "藍天、海景與度假感" },
  { value: "workplace", label: "明亮工作室", sub: "落地窗與清爽辦公空間" },
];

function actionVerb(actionOrId) {
  const id = typeof actionOrId === "string" ? actionOrId : actionOrId && actionOrId.id;
  return ACTION_VERBS[id] || (actionOrId && actionOrId.label) || id || "生成";
}

function featureById(id) {
  return features.find((feature) => feature.id === id) || null;
}

function fieldById(feature, id) {
  return (feature && feature.fields || []).find((field) => field.id === id) || null;
}

function fieldDefault(feature, id, fallback = "") {
  const field = fieldById(feature, id);
  return field && field.default !== undefined ? field.default : fallback;
}

function trustedItemCharacterId(item) {
  if (!item || item.provenanceTrusted !== true || item.characterAttribution !== "server") return "";
  return String((item.character && item.character.id) || "");
}

// Library organization is independent of the stricter generation receipt.
function libraryItemCharacterIds(item) {
  if (item && item.libraryAttribution === "catalog" && Array.isArray(item.libraryCharacterIds)) return item.libraryCharacterIds;
  const trusted = trustedItemCharacterId(item);
  return trusted ? [trusted] : [];
}

function providerLibraryCharacter(character) {
  return {
    ...providerStudioPresentationCharacter(character),
    providerCharacter: true,
  };
}

function libraryCharacters() {
  const byId = new Map(characters.map((character) => [character.id, character]));
  const importedCharacters = typeof providerCharacters === "undefined" ? [] : providerCharacters;
  for (const character of importedCharacters) {
    if (!byId.has(character.id)) byId.set(character.id, providerLibraryCharacter(character));
  }
  return [...byId.values()];
}

function libraryCharacterById(characterId) {
  return libraryCharacters().find((character) => character.id === characterId) || null;
}

function isProviderLibraryCharacter(character) {
  return Boolean(character && character.providerCharacter === true);
}

function libraryItemCharacterBucket(item) {
  const characterId = libraryItemCharacterIds(item)[0];
  if (!characterId) return "_unassigned";
  return libraryCharacterById(characterId) ? characterId : "_removed";
}

function worksForCharacter(characterId) {
  return historyItems
    .filter((item) => libraryItemCharacterIds(item).includes(characterId))
    .sort((a, b) => Number(b.modified || 0) - Number(a.modified || 0));
}

function historyItemVariantId(item) {
  if (!item || typeof item !== "object") return "";
  const provenance = item.provenance && typeof item.provenance === "object" ? item.provenance : {};
  const action = item.action && typeof item.action === "object" ? item.action : {};
  for (const value of [item.variantId, provenance.variantId, action.variantId]) {
    const variantId = String(value || "").trim();
    if (variantId) return variantId;
  }
  return "";
}

function providerEntrypointWorksForCharacter(characterId, entrypoint) {
  if (!entrypoint) return [];
  return worksForCharacter(characterId).filter((item) => (
    historyItemFeatureId(item) === entrypoint.featureId
    && historyItemVariantId(item) === entrypoint.variantId
  ));
}

function actionLabelFromItem(item) {
  if (item && item.action && item.action.label) return item.action.label;
  return (item && (item.feature || item.workflow)) || "作品";
}

function galleryDisplayTitle(character, item) {
  const name = (item.character && item.character.name) || (character && character.name) || "";
  return [name, actionLabelFromItem(item), formatTimestamp(item.modified || 0)].filter(Boolean).join("・");
}

function characterWorksCount(characterId) {
  return worksForCharacter(characterId).filter((item) => !item.hidden).length;
}

function activeJobsForCharacter(characterId) {
  const annotations = new Map(activeJobsTable().map((job) => [job.promptId, job]));
  const rows = queueJobs
    .filter((job) => job.characterId === characterId)
    .map((job) => ({ ...(annotations.get(job.promptId) || {}), ...job, promptId: job.promptId }));
  const visibleIds = new Set(rows.map((job) => job.promptId));
  const serverRows = [...queueJobs, ...terminalJobs, ...queueTerminalJobs, ...queueRecentErrors];
  const serverById = new Map(serverRows.filter((job) => job && job.promptId).map((job) => [job.promptId, job]));
  for (const annotation of annotations.values()) {
    if (annotation.characterId !== characterId || visibleIds.has(annotation.promptId)) continue;
    const chainStage = annotation.chainedJob && annotation.chainedJob.stage;
    const carriesLocalState = Boolean(annotation.chainedJob) || annotation.state === "error";
    const serverJob = serverById.get(annotation.promptId);
    const chainInFlight = chainStage === "voice" || chainStage === "continuing";
    if (!carriesLocalState || (!serverJob && !chainInFlight)) continue;
    rows.push({ ...annotation, ...(serverJob || {}), promptId: annotation.promptId });
    visibleIds.add(annotation.promptId);
  }
  return rows;
}

function characterJobLabel(job) {
  if (job.chainedJob) {
    if (job.chainedJob.stage === "voice") return "語音生成中...";
    if (job.chainedJob.stage === "continuing") return "準備對嘴...";
    if (job.chainedJob.stage === "lipsync") return "對嘴中...";
    if (job.chainedJob.stage === "error") return "生成失敗";
    if (job.chainedJob.stage === "cancelled") return "已取消";
  }
  if (job.state === "error") return "生成失敗";
  if (job.state === "cancelled") return "已取消";
  return actionVerb(job.action || job.actionId) || job.actionLabel || "生成";
}

function reconcileActiveJobs() {
  const queueIds = new Set(queueJobs.map((job) => job.promptId).filter(Boolean));
  const cancelledIds = new Set([...terminalJobs, ...queueTerminalJobs]
    .filter((job) => job && job.state === "cancelled")
    .map((job) => job.promptId)
    .filter(Boolean));
  const historyIds = new Set(historyItems.map((item) => item.promptId).filter(Boolean));
  const errorCutoff = Date.now() - ACTIVE_JOB_ERROR_TTL_MS;
  const chainedCutoff = Date.now() - CHAINED_VOICE_TTL_MS;
  const next = [];
  for (const job of activeJobsTable()) {
    if (queueIds.has(job.promptId)) {
      next.push(job);
      continue;
    }
    const chainedStage = job.chainedJob && job.chainedJob.stage;
    if (cancelledIds.has(job.promptId)) {
      if (job.state === "cancelled" && (!job.chainedJob || chainedStage === "cancelled")) next.push(job);
      else next.push({
        ...job,
        state: "cancelled",
        error: "",
        chainedJob: job.chainedJob ? { ...job.chainedJob, stage: "cancelled" } : job.chainedJob,
        ts: Date.now(),
      });
      continue;
    }
    if (chainedStage === "voice" || chainedStage === "continuing") {
      if (Number(job.ts || 0) > chainedCutoff) {
        next.push(job);
      } else {
        next.push({
          ...job,
          state: "error",
          error: "語音結果遺失，請重試",
          chainedJob: { ...job.chainedJob, stage: "error" },
          ts: Date.now(),
        });
      }
      continue;
    }
    if (job.state === "error") {
      if (Number(job.ts || 0) > errorCutoff) next.push(job);
      continue;
    }
    if (historyIds.has(job.promptId)) continue;
    const queuedError = queueErrorForPrompt(job.promptId);
    if (queuedError) {
      next.push({
        ...job,
        state: "error",
        error: queuedError.message || queuedError.type || "生成失敗",
        ts: Date.now(),
      });
      continue;
    }
    if (Number(job.ts || 0) > errorCutoff) next.push(job);
  }
  saveActiveJobs(next);
}

function newestPhotoWork(characterId) {
  return worksForCharacter(characterId).find((item) => !item.hidden && item.kind !== "video" && item.kind !== "audio") || null;
}

function nextWardrobeRevision(characterId) {
  const revision = Number(wardrobeRevisions.get(characterId) || 0) + 1;
  wardrobeRevisions.set(characterId, revision);
  return revision;
}

async function settleWardrobeRequest(characterId, revision, request, outfits) {
  if (wardrobeRevisions.get(characterId) === revision) {
    wardrobeCache.set(characterId, outfits);
    return outfits;
  }
  const newer = wardrobeLoadPromises.get(characterId);
  if (newer && newer.promise !== request) return newer.promise;
  return wardrobeCache.get(characterId) || [];
}

async function loadWardrobe(characterId, force = false) {
  if (!characterId) return [];
  const callerSession = captureSessionContext();
  const pending = wardrobeLoadPromises.get(characterId);
  if (force && pending && pending.kind === "mutation") {
    let mutationError = null;
    try {
      await pending.promise;
    } catch (error) {
      mutationError = error;
    }
    try {
      assertSessionContext(callerSession);
    } catch (error) {
      if (isStaleSessionError(mutationError)) throw mutationError;
      throw error;
    }
    if (isStaleSessionError(mutationError)) throw mutationError;
    /* A forced read still runs after an ordinary same-session mutation failure. */
    return loadWardrobe(characterId, true);
  }
  if (!force && pending) return pending.promise;
  if (!force && wardrobeCache.has(characterId)) return wardrobeCache.get(characterId);
  const revision = nextWardrobeRevision(characterId);
  let request;
  request = (async () => {
    const data = await api(`/api/characters/${encodeURIComponent(characterId)}/wardrobe`);
    assertSessionContext(callerSession);
    const outfits = Array.isArray(data.outfits) ? data.outfits : [];
    return settleWardrobeRequest(characterId, revision, request, outfits);
  })();
  const record = { kind: "load", revision, promise: request };
  wardrobeLoadPromises.set(characterId, record);
  try {
    return await request;
  } finally {
    if (wardrobeLoadPromises.get(characterId) === record) wardrobeLoadPromises.delete(characterId);
  }
}

async function mutateWardrobe(characterId, operation) {
  const session = captureSessionContext();
  const revision = nextWardrobeRevision(characterId);
  wardrobeCache.delete(characterId);
  let request;
  request = (async () => {
    const result = await operation();
    assertSessionContext(session);
    const outfits = Array.isArray(result.outfits) ? result.outfits : [];
    return settleWardrobeRequest(characterId, revision, request, outfits);
  })();
  const record = { kind: "mutation", revision, promise: request };
  wardrobeLoadPromises.set(characterId, record);
  try {
    return await request;
  } finally {
    if (wardrobeLoadPromises.get(characterId) === record) wardrobeLoadPromises.delete(characterId);
  }
}

function normalizeDanceTemplate(raw) {
  raw = raw && typeof raw === "object" ? raw : {};
  const tags = Array.isArray(raw.tags)
    ? raw.tags.map((tag) => String(tag || "").trim()).filter(Boolean)
    : String(raw.tags || "").split(",").map((tag) => tag.trim()).filter(Boolean);
  const compatible = Array.isArray(raw.compatibleCharacterTypes)
    ? raw.compatibleCharacterTypes.map((value) => String(value || "").trim()).filter(Boolean)
    : [];
  const duration = Number(raw.duration || 0);
  return {
    ...raw,
    id: String(raw.id || ""),
    title: String(raw.title || raw.label || "未命名舞蹈").trim() || "未命名舞蹈",
    category: String(raw.category || "uncategorized").trim() || "uncategorized",
    tags,
    duration: Number.isFinite(duration) && duration > 0 ? duration : 0,
    storyboardUrl: String(raw.storyboardUrl || raw.thumbUrl || ""),
    previewUrl: String(raw.previewUrl || raw.url || ""),
    runtimeReady: raw.runtimeReady === true,
    featured: raw.featured === true,
    favorite: raw.favorite === true,
    rank: Number.isFinite(Number(raw.rank)) ? Number(raw.rank) : 0,
    status: String(raw.status || "pending"),
    compatibleCharacterTypes: compatible,
    problem: String(raw.problem || raw.error || ""),
  };
}

function danceLibraryCharacterType(character, capability) {
  if (!character || !capability) return "";
  const type = characterVisualClass(character);
  const variantId = String(capability.variantId || "");
  if (type === "anime" && variantId.startsWith("dance.anime.")) return "anime";
  if (type === "realistic" && variantId.startsWith("dance.realistic.")) return "realistic";
  return "";
}

async function loadDanceTemplates(characterTypeValue, force = false) {
  const characterTypeKey = String(characterTypeValue || "").trim();
  if (!["anime", "realistic"].includes(characterTypeKey)) return [];
  const session = captureSessionContext();
  if (!force && danceTemplateCache.has(characterTypeKey)) {
    return danceTemplateCache.get(characterTypeKey).templates;
  }
  const params = new URLSearchParams({ character_type: characterTypeKey });
  const data = await api(`${DANCE_LIBRARY_ENDPOINTS.catalog}?${params.toString()}`);
  assertSessionContext(session);
  const templates = (Array.isArray(data.templates) ? data.templates : [])
    .map(normalizeDanceTemplate)
    .filter((template) => (
      template.id
      && template.compatibleCharacterTypes.includes(characterTypeKey)
    ));
  danceTemplateCache.set(characterTypeKey, {
    revision: String(data.revision || ""),
    categories: Array.isArray(data.categories) ? data.categories : [],
    templates,
  });
  return templates;
}

async function importSelectedDanceTemplate(templateId, slot = "video") {
  const id = String(templateId || "").trim();
  if (!id) throw new Error("請先選擇舞蹈模板");
  const data = await api(DANCE_LIBRARY_ENDPOINTS.import, {
    method: "POST",
    body: JSON.stringify({ id, slot }),
  });
  const file = data && data.file;
  const serverFile = typeof file === "string"
    ? file
    : String((file && (file.serverFile || file.filename)) || data.serverFile || data.filename || "");
  if (!serverFile) throw new Error("模板匯入回應缺少影片檔案");
  return {
    serverFile,
    label: String((file && (file.title || file.name)) || data.title || data.name || "舞蹈模板"),
  };
}

function danceDurationLabel(seconds) {
  const total = Math.max(0, Math.round(Number(seconds || 0)));
  if (!total) return "";
  const minutes = Math.floor(total / 60);
  const remain = total % 60;
  return `${minutes}:${String(remain).padStart(2, "0")}`;
}

function danceCategoryLabel(category) {
  const labels = {
    all: "全部",
    featured: "精選",
    favorite: "收藏",
    dance: "舞蹈",
    workout: "健身",
    gesture: "手勢",
    movement: "走位",
    uncategorized: "未分類",
  };
  return labels[String(category || "")] || String(category || "未分類");
}

function danceAutoplayAllowed() {
  const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const saveData = navigator.connection && navigator.connection.saveData === true;
  return !reduced && !saveData;
}

function stopActiveDancePreview(reset = false) {
  const video = activeDancePreviewVideo;
  activeDancePreviewVideo = null;
  if (!video) return;
  video.pause();
  video.classList.remove("is-playing");
  const frame = video.closest(".dance-template-media");
  if (frame) frame.classList.remove("is-playing");
  if (reset) {
    try {
      video.currentTime = 0;
    } catch (_) {
      /* Metadata may not be available yet. */
    }
  }
}

function loadDancePreview(video) {
  if (!video || video.src || !video.dataset.previewSrc) return;
  video.src = video.dataset.previewSrc;
  video.load();
}

function playDancePreview(video) {
  if (!video || !danceAutoplayAllowed()) return;
  if (activeDancePreviewVideo && activeDancePreviewVideo !== video) stopActiveDancePreview(true);
  loadDancePreview(video);
  activeDancePreviewVideo = video;
  const frame = video.closest(".dance-template-media");
  if (frame) frame.classList.add("is-playing");
  video.classList.add("is-playing");
  video.play().catch(() => {
    if (activeDancePreviewVideo === video) activeDancePreviewVideo = null;
    video.classList.remove("is-playing");
    if (frame) frame.classList.remove("is-playing");
  });
}

function resetDancePreviewObserver() {
  stopActiveDancePreview(true);
  if (dancePreviewObserver) dancePreviewObserver.disconnect();
  dancePreviewObserver = null;
}

function observeDancePreview(video) {
  if (!video || !video.dataset.previewSrc || typeof IntersectionObserver === "undefined") return;
  if (!dancePreviewObserver) {
    const visibility = new Map();
    dancePreviewObserver = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.target.isConnected) {
          visibility.delete(entry.target);
          dancePreviewObserver.unobserve(entry.target);
          continue;
        }
        visibility.set(entry.target, entry.isIntersecting ? entry.intersectionRatio : 0);
        if (entry.isIntersecting && danceAutoplayAllowed()) loadDancePreview(entry.target);
        else if (activeDancePreviewVideo === entry.target) stopActiveDancePreview(true);
      }
      const touchLayout = window.matchMedia && window.matchMedia("(hover: none)").matches;
      if (!touchLayout || !danceAutoplayAllowed()) return;
      let best = null;
      let bestRatio = 0.72;
      for (const [candidate, ratio] of visibility.entries()) {
        if (candidate.isConnected && ratio > bestRatio) {
          best = candidate;
          bestRatio = ratio;
        }
      }
      if (best && best !== activeDancePreviewVideo) playDancePreview(best);
      else if (!best && activeDancePreviewVideo) stopActiveDancePreview(true);
    }, { root: null, rootMargin: "180px 0px", threshold: [0, 0.35, 0.72, 0.9] });
  }
  dancePreviewObserver.observe(video);
}

function renderDanceTemplateMedia(template) {
  const mediaReady = template.runtimeReady === true;
  const storyboardUrl = mediaReady ? template.storyboardUrl : "";
  const previewUrl = mediaReady ? template.previewUrl : "";
  const fallback = h("span", {
    class: `dance-template-storyboard-fallback${storyboardUrl ? " hidden" : ""}`,
    text: mediaReady ? "暫無故事板" : "舞蹈預覽準備中",
  });
  const storyboard = storyboardUrl ? h("img", {
    src: storyboardUrl,
    alt: "",
    loading: "lazy",
    decoding: "async",
    onerror: (event) => {
      event.currentTarget.remove();
      fallback.classList.remove("hidden");
    },
  }) : null;
  const video = h("video", {
    class: "dance-template-preview",
    muted: "",
    loop: "",
    playsinline: "",
    preload: "none",
    tabindex: "-1",
    "aria-hidden": "true",
    dataset: { previewSrc: previewUrl },
  });
  video.muted = true;
  const frame = h("span", { class: "dance-template-media" }, [
    storyboard,
    fallback,
    video,
    h("span", { class: "dance-template-play-mark", text: "▶", "aria-hidden": "true" }),
  ]);
  video.addEventListener("ended", () => frame.classList.remove("is-playing"));
  requestAnimationFrame(() => {
    if (video.isConnected) observeDancePreview(video);
  });
  return { frame, video };
}

function openDanceTemplatePreview(template, opener) {
  stopActiveDancePreview(true);
  const video = h("video", {
    class: "dance-template-full-video",
    src: template.previewUrl || "",
    poster: template.storyboardUrl || null,
    controls: "",
    muted: "",
    loop: "",
    playsinline: "",
    preload: "metadata",
  });
  video.muted = true;
  openSheet({
    id: `dance-template-preview-${template.id}`,
    title: template.title,
    opener,
    className: "dance-template-preview-sheet",
    content: h("div", { class: "dance-template-full-preview" }, [
      video,
      h("div", { class: "dance-template-full-meta" }, [
        h("strong", { text: template.title }),
        h("span", {
          text: [danceCategoryLabel(template.category), danceDurationLabel(template.duration), ...(template.tags || []).slice(0, 3)]
            .filter(Boolean).join(" · "),
        }),
      ]),
    ]),
    onOpen: () => {
      if (danceAutoplayAllowed()) video.play().catch(() => {});
    },
  });
}

function chainLabelForAction(actionId) {
  if (actionId === "video-loop") return "影片循環";
  return actionVerb(actionId);
}

function prefilledFileLabel(value, fallback = "已選取素材") {
  if (!value) return "";
  const name = String(value).split("/").pop();
  return name || fallback;
}

function fileDraftPatch(slot, serverFile, label) {
  return {
    serverFiles: { [slot]: serverFile },
    serverFileLabels: { [slot]: label || prefilledFileLabel(serverFile) },
  };
}

function mergeDraft(characterId, actionId, capability, patch) {
  if (!draftCapabilityMatches(actionId, capability)) return false;
  const feature = featureById(capability.featureId);
  if (!feature) return false;
  const draft = loadDraft(characterId, actionId, capability, feature);
  const saved = saveDraft(characterId, actionId, capability, {
    ...draft,
    ...patch,
    serverFiles: { ...(draft.serverFiles || {}), ...(patch.serverFiles || {}) },
    serverFileLabels: { ...(draft.serverFileLabels || {}), ...(patch.serverFileLabels || {}) },
  }, feature);
  if (!saved) return false;
  bumpDraftRevision(characterId, actionId);
  return true;
}

function retireCurrentComposerBeforePrefill(characterId, actionId) {
  const key = `${characterId}:${actionId}`;
  if (!composerBinding || composerBinding.key !== key) return;
  const state = composerBinding.state;
  if (state && typeof state.__clearSaveTimer === "function") state.__clearSaveTimer();
  if (typeof draftFlushNow === "function") draftFlushNow();
  if (state) state.__draftCleared = true;
  draftFlushNow = null;
}

function itemImportBody(item, slot, nextActionId = "") {
  const body = {
    filename: item.filename || "",
    subfolder: item.subfolder || "",
    type: item.type || "output",
    promptId: item.promptId || "",
  };
  if (nextActionId) return { ...body, nextActionId };
  return {
    ...body,
    slot,
    expectedKind: item.kind || "",
  };
}

async function importItemToServerFile(item, slot, nextActionId = "") {
  const session = captureSessionContext();
  const data = await api("/api/history/import-input", {
    method: "POST",
    body: JSON.stringify(itemImportBody(item, slot, nextActionId)),
  });
  assertSessionContext(session);
  return {
    serverFile: data.filename,
    label: data.name || item.filename || data.filename,
    nextAction: data.nextAction || null,
  };
}

async function prefillActionFromItem(characterId, item, actionId, slot, capability, extraDraft = {}, stillCurrent = null, nextActionId = "") {
  const variantId = capability && capability.variantId;
  const featureId = capability && capability.featureId;
  let currentCapability = capabilityStillEnabled(characterId, actionId, variantId, featureId);
  if (!currentCapability) {
    throw new Error("這個角色目前不能使用這個動作");
  }
  if (capabilityServerBindsUpload(currentCapability, slot)) {
    throw new Error("這項角色功能會使用已驗證的角色造型，不能帶入任意圖片");
  }
  const session = captureSessionContext();
  const sourceHash = window.location.hash;
  const imported = await importItemToServerFile(item, slot, nextActionId);
  assertSessionContext(session);
  if (window.location.hash !== sourceHash || (stillCurrent && !stillCurrent())) return false;
  const confirmed = imported.nextAction;
  if (!confirmed || confirmed.id !== nextActionId || confirmed.scope !== "character") {
    throw new Error("伺服器沒有確認這個接續動作");
  }
  if (confirmed.characterId !== characterId || confirmed.actionId !== actionId) {
    throw new Error("角色接續目標已變更，請重新選擇");
  }
  slot = confirmed.inputSlot;
  currentCapability = capabilityStillEnabled(
    confirmed.characterId,
    confirmed.actionId,
    confirmed.variantId,
    confirmed.featureId,
  );
  if (!currentCapability) {
    throw new Error("角色功能已變更，素材沒有寫入草稿");
  }
  if (capabilityServerBindsUpload(currentCapability, slot)) {
    throw new Error("角色功能已變更，這份素材不能帶入草稿");
  }
  retireCurrentComposerBeforePrefill(characterId, actionId);
  const merged = mergeDraft(characterId, actionId, currentCapability, {
    ...fileDraftPatch(slot, imported.serverFile, imported.label),
    ...(confirmed.draftDefaults || extraDraft || {}),
  });
  if (!merged) throw new Error("角色功能已變更，素材沒有寫入草稿");
  navigate({ view: "character-workspace", characterId: confirmed.characterId, actionId: confirmed.actionId });
  return true;
}

async function openStudioVideoLoopFromItem(item, nextActionId, stillCurrent = null) {
  const session = captureSessionContext();
  const sourceHash = window.location.hash;
  const imported = await importItemToServerFile(item, "video", nextActionId);
  assertSessionContext(session);
  if (window.location.hash !== sourceHash || (stillCurrent && !stillCurrent())) return false;
  const confirmed = imported.nextAction;
  if (
    !confirmed
    || confirmed.id !== nextActionId
    || confirmed.scope !== "generic"
    || confirmed.featureId !== "video-loop"
    || confirmed.inputSlot !== "video"
  ) {
    throw new Error("伺服器沒有確認影片循環入口");
  }
  activeCharacterId = "";
  navigate({ view: "studio", featureId: "video-loop" });
  setTimeout(() => {
    if (currentRoute.view !== "studio" || currentFeatureId !== "video-loop") return;
    serverFiles.video = imported.serverFile;
    const input = fileInputs.video;
    const slot = input && input.closest(".upload-slot, .dropzone");
    const preview = slot && slot.querySelector(".drop-preview");
    if (input) input.value = "";
    if (preview) {
      clear(preview);
      preview.appendChild(h("span", { class: "file-name", text: `🎞️ ${imported.label}` }));
    }
    showAlert("已用一般工具開啟影片循環；輸出不會歸屬角色，也不會保留原音訊。");
  }, 0);
  return true;
}

async function loadResultContinuations(item, host, onDone = null, display = {}) {
  if (!item || !host) return;
  const session = captureSessionContext();
  const loadId = `${Date.now()}-${Math.random()}`;
  host.dataset.continuationLoadId = loadId;
  try {
    const params = new URLSearchParams({
      filename: item.filename || "",
      subfolder: item.subfolder || "",
      type: item.type || "output",
      promptId: item.promptId || "",
    });
    const data = await api(`/api/history/continuations?${params.toString()}`);
    assertSessionContext(session);
    if (!host.isConnected || host.dataset.continuationLoadId !== loadId) return;
    clear(host);
    const actions = data.schema === "castle.result-chain.v1" && Array.isArray(data.actions)
      ? data.actions
      : [];
    const menu = renderChainMenu(actions, item, onDone, display);
    host.appendChild(menu || h("div", {
      class: "result-detail-chain-empty",
      text: "此作品目前沒有可接續的生成動作。",
    }));
  } catch (error) {
    if (!host.isConnected || host.dataset.continuationLoadId !== loadId) return;
    clear(host);
    host.appendChild(h("div", {
      class: "result-detail-chain-empty",
      text: `暫時無法確認接續功能：${error.message}`,
    }));
  }
}

function renderChainMenu(actions, item, onDone, display = {}) {
  const options = Array.isArray(actions)
    ? actions.filter((option) => option && option.id && option.inputSlot && option.expectedKind)
    : [];
  if (!options.length) return null;
  const status = h("div", { class: "chain-status" });
  let operationSeq = 0;
  const buttons = options.map((option) => h("button", {
    class: display.primary ? "primary small" : "ghost small",
    type: "button",
    text: option.hint ? `${option.label}・${option.hint}` : option.label,
    onclick: async () => {
      const seq = ++operationSeq;
      for (const button of buttons) button.disabled = true;
      status.textContent = "準備素材中…";
      try {
        const stillCurrent = () => status.isConnected && operationSeq === seq;
        let completed = false;
        if (option.scope === "generic") {
          completed = await openStudioVideoLoopFromItem(item, option.id, stillCurrent);
        } else if (option.scope === "character") {
          const character = characters.find((entry) => entry.id === option.characterId);
          if (!character) throw new Error("找不到這個角色，請重新整理");
          const currentCapability = capabilityStillEnabled(
            option.characterId,
            option.actionId,
            option.variantId,
            option.featureId,
          );
          if (!currentCapability) throw new Error("這個角色目前不能使用這個動作");
          completed = await prefillActionFromItem(
            option.characterId,
            item,
            option.actionId,
            option.inputSlot,
            currentCapability,
            option.draftDefaults || {},
            stillCurrent,
            option.id,
          );
        } else {
          throw new Error("不支援的接續入口");
        }
        if (!completed) return;
        if (onDone) onDone();
      } catch (error) {
        if (!status.isConnected || operationSeq !== seq) return;
        status.textContent = `無法帶入素材：${error.message}`;
        status.className = "chain-status err";
      } finally {
        if (status.isConnected && operationSeq === seq) {
          for (const button of buttons) button.disabled = false;
        }
      }
    },
  }));
  return h("div", { class: "chain-menu" }, [
    h("span", { class: "chain-menu-label", text: "用它來生成" }),
    ...buttons,
    status,
  ]);
}

function resultCharacter(item) {
  if (item && item.character && item.character.id) {
    return characters.find((character) => character.id === item.character.id) || item.character;
  }
  const characterId = item && item.characterId;
  const explicit = characters.find((character) => character.id === characterId);
  if (explicit) return explicit;
  return null;
}

function resultMetaLine(item, character) {
  const inferredName = (item.character && item.character.name) || (character && character.name) || "";
  const characterName = item.kind === "text" || libraryItemCharacterIds(item).length
    ? (inferredName || "未標示角色")
    : (inferredName ? `${inferredName}（未驗證）` : "未標示角色");
  const action = item.kind === "text" ? "貼文" : actionLabelFromItem(item);
  const timestamp = Number(item.modified || item.ts || 0);
  return [characterName, action, formatTimestamp(timestamp)].filter(Boolean).join(" · ");
}

function renderResultPreview(item) {
  if (item.kind === "text" || (!item.url && item.text !== undefined)) {
    return h("div", { class: "result-detail-text", text: item.text || "" });
  }
  return renderDetailMedia(item);
}

function audioExportDownload(item, outputFormat) {
  const format = normalizeAudioExportFormat(outputFormat);
  const urls = item && item.audioExportUrls && typeof item.audioExportUrls === "object"
    ? item.audioExportUrls
    : {};
  const href = String(urls[format] || "");
  if (!href) return null;
  const original = String(item.filename || "castle-voice");
  const stem = original.replace(/\.[^.]+$/, "") || "castle-voice";
  return { format, href, filename: `${stem}.${format}` };
}

async function downloadAudioExport(event, item, outputFormat, control) {
  event.preventDefault();
  event.stopPropagation();
  const download = audioExportDownload(item, outputFormat);
  if (!download || !control || control.dataset.audioExportBusy === "1") return;
  const session = captureSessionContext();
  const originalText = control.textContent;
  control.dataset.audioExportBusy = "1";
  control.classList.add("is-loading");
  control.setAttribute("aria-busy", "true");
  control.setAttribute("aria-disabled", "true");
  control.textContent = `${download.format.toUpperCase()} 轉檔中…`;
  try {
    const prepareUrl = `${download.href}${download.href.includes("?") ? "&" : "?"}prepare=1`;
    const response = await fetch(prepareUrl, { headers: { Accept: "application/json" } });
    assertSessionContext(session);
    let payload = {};
    try {
      payload = await response.json();
    } catch (_) {
      /* The status below still provides a useful fallback message. */
    }
    assertSessionContext(session);
    if (response.status === 401) {
      resetSessionUi();
      showAuth("login");
    }
    if (!response.ok || payload.ok === false) throw new Error(payload.error || `HTTP ${response.status}`);
    const anchor = h("a", {
      href: download.href,
      download: download.filename,
      style: "display:none",
    });
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    showAlert(`已開始下載 ${download.format.toUpperCase()}。`);
  } catch (error) {
    showAlert(`下載失敗：${error.message}`, true);
  } finally {
    delete control.dataset.audioExportBusy;
    control.classList.remove("is-loading");
    control.removeAttribute("aria-busy");
    control.removeAttribute("aria-disabled");
    control.textContent = originalText;
  }
}

function renderAudioExportActions(item, options = {}) {
  const downloads = AUDIO_EXPORT_FORMAT_OPTIONS
    .map((option) => ({ option, download: audioExportDownload(item, option.value) }))
    .filter((entry) => entry.download);
  if (!downloads.length) {
    return h("a", { class: "ghost small", href: item.downloadUrl || item.url, download: item.filename || "作品", text: "下載原始檔" });
  }
  return h("div", { class: `audio-export-actions${options.compact ? " compact" : ""}`, role: "group", "aria-label": "下載語音格式" }, [
    h("span", { class: "audio-export-label", text: options.compact ? "下載這一段" : "下載語音" }),
    h("div", { class: "audio-export-buttons" }, downloads.map(({ option, download }) => h("a", {
      class: `ghost small${download.format === preferredAudioExportFormat ? " preferred" : ""}`,
      href: download.href,
      download: download.filename,
      title: option.sub,
      text: option.label,
      dataset: { audioResultFormat: download.format },
      onclick: (event) => downloadAudioExport(event, item, download.format, event.currentTarget),
    }))),
    options.compact ? null : h("span", {
      class: "audio-export-note",
      text: "WAV 是未壓縮音檔；MP3 檔案較小；MP4 是 720p 黑底影片＋聲音，可直接交給只收影片的平台。",
    }),
  ]);
}

function resultDownloadLink(item) {
  if (item.kind === "text" || (!item.url && item.text !== undefined)) {
    const filename = `${String(item.topic || "貼文").replace(/[\\/:*?"<>|]+/g, "-") || "貼文"}.txt`;
    return h("a", {
      class: "ghost small",
      href: `data:text/plain;charset=utf-8,${encodeURIComponent(item.text || "")}`,
      download: filename,
      text: "下載",
    });
  }
  if (item.kind === "audio") return renderAudioExportActions(item);
  return h("a", { class: "ghost small", href: item.downloadUrl || item.url, download: item.filename || "作品", text: "下載" });
}

function refreshCurrentResultCollection() {
  catalogueViewState.forEach(state => { state.loadedAt = 0; state.data = null; });
  if (["character-workspace", "provider-character-studio"].includes(currentRoute.view)) refreshHistoryDrivenSurfaces();
  else if (currentRoute.view === "character-library") renderCharacterLibraryRoute();
  else if (currentView === "library") paintHistory();
}

function refreshHistoryDrivenSurfaces() {
  document.querySelectorAll("[data-character-works]").forEach((badge) => {
    badge.textContent = `${characterWorksCount(badge.dataset.characterWorks)} 媒體`;
  });
  if (currentRoute.view === "character-workspace") {
    const character = routeCharacter();
    const currentFeed = document.querySelector(".character-results-zone > .character-feed");
    if (character && currentFeed) currentFeed.replaceWith(renderCharacterFeed(character));
  } else if (currentRoute.view === "provider-character-studio") {
    const source = providerStudioCharacter();
    const currentFeed = document.querySelector(".character-results-zone > .character-feed");
    if (source && source.generationEntrypoint && currentFeed) {
      currentFeed.replaceWith(renderProviderCharacterFeed(
        providerStudioPresentationCharacter(source),
        source.generationEntrypoint,
      ));
    }
  } else if (currentRoute.view === "character-library") {
    const character = routeCharacter();
    const currentPanel = $("character-media-panel");
    if (!character || !currentPanel) return;
    const works = worksForCharacter(character.id).filter((item) => showHiddenCharacterLibrary || !item.hidden);
    const activeMedia = ["image", "video", "audio", "text"].includes(currentRoute.media) ? currentRoute.media : "image";
    currentPanel.replaceWith(renderCharacterMediaPanel(character, activeMedia, {
      photos: works.filter((item) => item.kind === "image"),
      videos: works.filter((item) => item.kind === "video"),
      sounds: works.filter((item) => item.kind === "audio"),
    }));
  }
}

function renderResultDetailSurface(item, options = {}) {
  const attributedCharacter = resultCharacter(item);
  const character = attributedCharacter || options.character || null;
  const isHistoryItem = Boolean(item && item.filename && (item.url || item.downloadUrl));
  const meta = h("div", { class: "result-detail-meta", text: resultMetaLine(item, character) });
  const chainHost = h("div", { class: "result-detail-chain" }, [
    h("div", {
      class: "result-detail-chain-empty",
      text: isHistoryItem ? "正在確認可接續的功能…" : "此作品目前沒有可接續的生成動作。",
    }),
  ]);
  const actions = h("div", { class: "result-detail-actions" }, [
    resultDownloadLink(item),
    h("button", {
      class: "ghost small",
      type: "button",
      text: "改名",
      disabled: !isHistoryItem,
      "aria-disabled": isHistoryItem ? "false" : "true",
      onclick: async () => {
        if (await renameItem(item)) dismissTopSheet(refreshCurrentResultCollection);
      },
    }),
    h("button", {
      class: "ghost small danger-text",
      type: "button",
      text: item.hidden ? "顯示" : "隱藏",
      disabled: !isHistoryItem,
      "aria-disabled": isHistoryItem ? "false" : "true",
      onclick: async () => {
        if (!item.hidden && !window.confirm("確定隱藏此作品？")) return;
        if (await toggleHidden(item, { confirmed: true })) dismissTopSheet(refreshCurrentResultCollection);
      },
    }),
  ]);
  const nodesBox = isHistoryItem ? h("div", { class: "detail-nodes" }) : null;
  const generationSummary = isHistoryItem ? h("section", { class: "generation-summary" }) : null;
  const extras = isHistoryItem ? h("details", { class: "result-detail-more" }, [
    h("summary", { text: "更多作品操作與技術節點" }),
    h("div", { class: "result-detail-extra-actions" }, [
      h("a", { class: "ghost small", href: item.url, target: "_blank", rel: "noreferrer", text: "開啟原始檔" }),
      h("button", { class: "ghost small", type: "button", text: "加入工作流庫", onclick: () => adoptHistoryArtifact(item) }),
      item.kind === "audio" ? h("button", { class: "ghost small", type: "button", text: "加入聲音庫", onclick: () => importVoiceItem(item) }) : null,
      h("button", {
        class: "ghost small danger-text",
        type: "button",
        text: "刪除",
        onclick: async () => {
          if (await deleteItem(item)) dismissTopSheet(refreshCurrentResultCollection);
        },
      }),
    ]),
    nodesBox,
  ]) : null;
  const surface = h("div", { class: "result-detail-surface" }, [
    h("div", { class: "result-detail-preview" }, [renderResultPreview(item)]),
    meta,
    generationSummary,
    chainHost,
    actions,
    extras,
  ]);
  if (isHistoryItem) {
    loadResultContinuations(item, chainHost, () => dismissTopSheet(), { primary: true });
  }
  if (nodesBox) loadHistoryDetail(item, nodesBox, null, generationSummary);
  return surface;
}

function openResultDetail(item, opener = null, options = {}) {
  if (!item) return;
  openSheet({
    id: "result-detail",
    title: item.topic || item.filename || "作品詳細",
    opener: opener || document.activeElement,
    className: "result-detail-sheet",
    content: renderResultDetailSurface(item, options),
  });
}

function providerCharacterProfilePicNode(character) {
  if (!character.avatarUrl) return profilePicFallbackNode(character, "wall-card-profile-pic");
  return h("img", {
    class: "wall-card-profile-pic",
    src: character.avatarUrl,
    alt: "",
    loading: "lazy",
    decoding: "async",
    onerror: (event) => event.currentTarget.replaceWith(profilePicFallbackNode(character, "wall-card-profile-pic")),
  });
}

function openProviderCharacterMaterials(character) {
  navigate({
    view: "library",
    characterId: character.id,
    kind: "image",
    source: "provider",
  });
}

function openProviderCharacterCastleOutputs(character, entrypoint) {
  if (!character || !entrypoint) return;
  navigate({
    view: "library",
    characterId: character.id,
    kind: "image",
    featureId: entrypoint.featureId,
    variantId: entrypoint.variantId,
  });
}

function openProviderCharacterStudio(character) {
  if (!character || characterVisualClass(character) === "unknown") return;
  navigate({ view: "provider-character-studio", characterId: character.id });
}

function renderProviderCharacterWallCard(character) {
  const classified = characterVisualClass(character) !== "unknown";
  const generationReady = character.generationEnabled === true && Boolean(character.generationEntrypoint);
  const materialReady = classified && Boolean(character.profileAssetId);
  const status = generationReady ? "本機可用" : (materialReady ? "素材已就緒" : "需補素材");
  const tagline = generationReady
    ? `${characterVisualClassLabel(character)}角色 · 本機生成已就緒`
    : (materialReady
      ? `${characterVisualClassLabel(character)}角色 · 角色素材已就緒`
      : (classified ? `${characterVisualClassLabel(character)}角色 · 缺少角色素材` : "待補資料 · 缺少角色素材"));
  return h("article", {
    class: "wall-card provider-character-card",
    dataset: { providerCharacterId: character.id },
    style: `--character-color:${characterColor(character)}`,
  }, [
    h("div", { class: "wall-card-media" }, [
      providerCharacterProfilePicNode(character),
      h("span", { class: "works-badge", text: status }),
    ]),
    h("div", { class: "wall-card-body" }, [
      h("div", { class: "wall-card-name", text: character.name }),
      h("div", {
        class: "wall-card-tagline",
        text: tagline,
      }),
      h("div", { class: "provider-character-card-actions" }, [
        generationReady ? h("button", {
          class: "primary small provider-character-create",
          type: "button",
          text: "開始創作",
          onclick: () => openProviderCharacterStudio(character),
        }) : h("button", {
          class: "primary small provider-character-materials",
          type: "button",
          text: materialReady ? "前往角色素材" : "補角色素材",
          onclick: () => openProviderCharacterMaterials(character),
        }),
        generationReady ? h("button", {
          class: "ghost small provider-character-materials",
          type: "button",
          text: "查看素材",
          onclick: () => openProviderCharacterMaterials(character),
        }) : null,
      ]),
    ]),
  ]);
}

function renderCharacterCardWall() {
  const wrap = h("div", { class: "card-wall" });
  if (providerCharactersError) {
    wrap.appendChild(h("div", { class: "panel-note provider-character-error", text: providerCharactersError }));
  }
  const legacyIds = new Set(characters.map((character) => character.id));
  const providerOnlyCharacters = providerCharacters.filter((character) => !legacyIds.has(character.id));
  for (const [visualClass, label, subtitle] of CHARACTER_VISUAL_GROUPS) {
    const legacyList = characters.filter((character) => characterWallVisualGroup(character) === visualClass);
    const providerList = providerOnlyCharacters
      .filter((character) => characterWallVisualGroup(character) === visualClass)
      .sort((left, right) => left.name.localeCompare(right.name, "zh-Hant"));
    const children = [
      ...legacyList.map(renderCharacterWallCard),
      ...providerList.map(renderProviderCharacterWallCard),
    ];
    if (visualClass === "unknown") {
      children.push(h("button", { class: "wall-card new-character-card", type: "button", onclick: () => { experimentalCreateOpen = true; renderCharactersView(); } }, [
        h("div", { class: "new-card-plus", text: "+" }),
        h("div", { class: "wall-card-name", text: "建立實驗角色" }),
        h("div", { class: "wall-card-tagline", text: "上傳照片建立私人角色" }),
      ]));
    }
    wrap.appendChild(h("section", { class: "wall-section", dataset: { visualClass } }, [
      h("div", { class: "wall-section-head" }, [
        h("div", {}, [
          h("h2", { text: label }),
          h("p", { text: subtitle }),
        ]),
        h("span", { text: `${legacyList.length + providerList.length} 位` }),
      ]),
      experimentalCreateOpen && visualClass === "unknown" ? renderExperimentalCreateForm() : null,
      h("div", { class: "wall-grid" }, children),
    ]));
  }
  return wrap;
}

function renderCharacterWallCard(character) {
  return h("button", {
    class: "wall-card",
    type: "button",
    dataset: { characterId: character.id },
    style: `--character-color:${characterColor(character)}`,
    onclick: () => navigate({ view: "character-workspace", characterId: character.id }),
  }, [
    h("div", { class: "wall-card-media" }, [
      profilePicNode(character, "wall-card-profile-pic"),
      h("span", { class: "works-badge", dataset: { characterWorks: character.id }, text: `${characterWorksCount(character.id)} 媒體` }),
    ]),
    h("div", { class: "wall-card-body" }, [
      h("div", { class: "wall-card-name", text: character.name }),
      isAdmin() && characterType(character) === "experimental" && character.ownerEmail ? h("div", { class: "wall-card-owner", text: character.ownerEmail }) : null,
      h("div", { class: "wall-card-tagline", text: character.tagline || character.profile || character.origin || "未設定一句話定位" }),
      h("div", { class: "wall-card-enter", text: "進入" }),
    ]),
  ]);
}

function routeCharacter() {
  return characters.find((character) => character.id === currentRoute.characterId) || null;
}

function providerStudioCharacter() {
  if (currentRoute.view !== "provider-character-studio") return null;
  const characterId = safeProviderCharacterId(currentRoute.characterId);
  return providerCharacters.find((character) => character.id === characterId) || null;
}

function providerStudioPresentationCharacter(character) {
  return {
    id: character.id,
    name: character.name,
    visualClass: character.visualClass,
    assetUrls: { avatar: character.avatarUrl || "" },
    color: "#6f55e8",
  };
}

function providerGenerationContractMatches(character, expectedEntrypoint) {
  if (!character || character.generationEnabled !== true || !character.generationEntrypoint || !expectedEntrypoint) return false;
  const current = character.generationEntrypoint;
  return (
    current.kind === expectedEntrypoint.kind
    && current.actionId === expectedEntrypoint.actionId
    && current.featureId === expectedEntrypoint.featureId
    && current.variantId === expectedEntrypoint.variantId
    && JSON.stringify(current.allowedOptions) === JSON.stringify(expectedEntrypoint.allowedOptions)
  );
}

function currentProviderGenerationContract(characterId, expectedEntrypoint) {
  const current = providerCharacters.find((item) => item.id === characterId) || null;
  return providerGenerationContractMatches(current, expectedEntrypoint) ? current.generationEntrypoint : null;
}

function renderCurrentComposerRoute() {
  if (currentRoute.view === "provider-character-studio") renderProviderCharacterStudioRoute();
  else renderCharacterWorkspaceRoute();
}

function providerPhotoshootReason(character, entrypoint, state) {
  if (!navigator.onLine) return "目前離線，請重新連線後再生成";
  if (!currentProviderGenerationContract(character.id, entrypoint)) return "這個角色的生成設定已更新，請回角色頁重新開啟";
  const options = entrypoint.allowedOptions;
  const prompt = String(state.prompt || "").trim();
  const negativePrompt = String(state.negativePrompt || "").trim();
  if (!prompt) return "請先描述想拍的畫面";
  if (prompt.length > options.promptMaxLength) return `畫面描述不可超過 ${options.promptMaxLength} 字`;
  if (negativePrompt.length > options.negativePromptMaxLength) return `自訂負面提示詞不可超過 ${options.negativePromptMaxLength} 字`;
  if (!options.aspects.includes(String(state.aspectRatio || ""))) return "請重新選擇畫面比例";
  if (!Number.isSafeInteger(Number(state.seed)) || Number(state.seed) < options.seedMin || Number(state.seed) > options.seedMax) {
    return "Seed 超出可用範圍";
  }
  return "";
}

function renderProviderPhotoshootForm(character, entrypoint) {
  if (composerBinding) retireBoundComposerDraft({ flush: true });
  resetGetters();
  const action = { id: entrypoint.actionId, label: "角色寫真" };
  const feature = {
    id: entrypoint.featureId,
    kind: "image",
    ready: true,
    runnable: true,
    uploads: [],
    fields: [],
  };
  const capability = {
    actionId: entrypoint.actionId,
    featureId: entrypoint.featureId,
    variantId: entrypoint.variantId,
  };
  const draftSnapshot = loadDraftSnapshot(character.id, action.id, capability, feature);
  const options = entrypoint.allowedOptions;
  const rawDraft = draftSnapshot.state;
  const draftAspect = String(rawDraft.aspectRatio || "");
  const draftSeed = typeof rawDraft.seed === "number" ? rawDraft.seed : Number.NaN;
  const state = {
    prompt: typeof rawDraft.prompt === "string" ? rawDraft.prompt.slice(0, options.promptMaxLength) : "",
    negativePrompt: typeof rawDraft.negativePrompt === "string"
      ? rawDraft.negativePrompt.slice(0, options.negativePromptMaxLength)
      : "",
    aspectRatio: options.aspects.includes(draftAspect) ? draftAspect : options.defaultAspect,
    seed: Number.isSafeInteger(draftSeed) && draftSeed >= options.seedMin && draftSeed <= options.seedMax
      ? draftSeed
      : options.seedDefault,
    __draftCleared: false,
    __draftDirty: false,
    __draftRetired: false,
  };
  let saveTimer = null;
  let form = null;
  const clearSaveTimer = () => {
    clearTimeout(saveTimer);
    saveTimer = null;
  };
  const saveDraftNow = () => {
    if (!state.__draftDirty || state.__draftCleared || state.__draftRetired) return;
    if (saveDraft(character.id, action.id, capability, {
      prompt: state.prompt,
      negativePrompt: state.negativePrompt,
      aspectRatio: state.aspectRatio,
      seed: state.seed,
    }, feature)) state.__draftDirty = false;
  };
  state.__clearSaveTimer = clearSaveTimer;
  state.__saveDraftNow = saveDraftNow;
  state.__draftRevision = currentDraftRevision(character.id, action.id);
  const update = (patch) => {
    if (state.__draftRetired) return;
    Object.assign(state, patch);
    state.__draftRevision = bumpDraftRevision(character.id, action.id);
    state.__draftCleared = false;
    state.__draftDirty = true;
    if (composerBinding && composerBinding.state === state) draftFlushNow = saveDraftNow;
    clearSaveTimer();
    saveTimer = setTimeout(saveDraftNow, 250);
    updateGenerateReadiness();
  };
  const aspectOptions = PROVIDER_PHOTOSHOOT_ASPECTS
    .filter((option) => options.aspects.includes(option.value));
  const promptField = textareaMini(
    "想拍什麼",
    state.prompt,
    ACTION_PLACEHOLDERS.photoshoot,
    (value) => update({ prompt: value }),
    true,
  );
  const promptInput = promptField.querySelector("textarea");
  promptInput.id = "providerCharacterPrompt";
  promptInput.maxLength = options.promptMaxLength;
  const negativeField = textareaMini(
    "自訂負面提示詞（選填）",
    state.negativePrompt,
    "例如：模糊、變形、多餘手指、浮水印、文字",
    (value) => update({ negativePrompt: value }),
  );
  negativeField.querySelector("textarea").maxLength = options.negativePromptMaxLength;
  negativeField.appendChild(renderHint(`系統固定加入：${PROVIDER_PHOTOSHOOT_SAFETY_NEGATIVE}`));
  const seedInput = h("input", {
    type: "number",
    min: String(options.seedMin),
    max: String(options.seedMax),
    step: "1",
    value: String(state.seed),
    inputmode: "numeric",
    oninput: (event) => update({
      seed: event.currentTarget.value === "" ? Number.NaN : Number(event.currentTarget.value),
    }),
  });
  form = h("form", {
    class: "gen-form minimal-form provider-character-form",
    onsubmit: (event) => submitProviderCharacterPhotoshoot(event, character, action, feature, capability, entrypoint, state),
  }, [
    h("div", { class: "field role-bound-summary" }, [
      h("span", { class: "field-label", text: "角色模型" }),
      h("div", { class: "bound-value", text: `${character.name} 的專屬角色模型` }),
      renderHint("模型已由角色綁定；不需要選模型，也不會套用到其他角色。"),
    ]),
    promptField,
    negativeField,
    radioCards("畫面比例", aspectOptions, state.aspectRatio, (value) => update({ aspectRatio: value })),
    h("details", { class: "advanced mini-advanced" }, [
      h("summary", { text: "進階" }),
      h("div", { class: "advanced-body" }, [
        h("label", { class: "field mini-field" }, [
          h("span", { class: "field-label", text: "Seed（-1 為隨機）" }),
          seedInput,
        ]),
      ]),
    ]),
  ]);
  const restoredDraftNotice = draftSnapshot.restored ? h("div", {
    class: "draft-restored-notice",
    role: "status",
  }, [
    h("div", { class: "draft-restored-copy" }, [
      h("strong", { text: "已恢復保留的內容" }),
      h("span", { text: "上次尚未送出的畫面描述與選項已保留。" }),
    ]),
    h("button", {
      class: "ghost draft-clear-button",
      type: "button",
      text: "從空白開始",
      onclick: () => startComposerFromBlank(character, action, state),
    }),
  ]) : null;
  const panel = h("section", { class: "panel action-panel" }, [
    renderSectionHeader("角色寫真", "描述畫面即可生成；角色模型由伺服器精確綁定。", [], { compact: true }),
    restoredDraftNotice,
    form,
  ]);
  bindGenerateComposer(
    character,
    action,
    feature,
    capability,
    state,
    form,
    saveDraftNow,
    restoredDraftNotice,
    { reasonForSubmit: () => providerPhotoshootReason(character, entrypoint, state) },
  );
  return panel;
}

async function submitProviderCharacterPhotoshoot(event, character, action, feature, capability, entrypoint, state) {
  event.preventDefault();
  if (submitInFlight) return;
  const currentContract = currentProviderGenerationContract(character.id, entrypoint);
  const reason = providerPhotoshootReason(character, entrypoint, state);
  if (!currentContract || reason) {
    setGenerationFsm("disabled", { reason: reason || "這個角色目前不能生成", detail: reason || "這個角色目前不能生成" }, { force: true });
    return;
  }
  const session = captureSessionContext();
  const submitKey = composerKey(character, action);
  const submitId = ++submissionSequence;
  const draftRevision = currentDraftRevision(character.id, action.id);
  if (generationFsm.state === "error") trackedPromptId = "";
  submitInFlight = true;
  let requestSent = false;
  setGenerationFsm("submitting", {
    reason: "", detail: "未送出", pct: 0, resultPromptId: "", postResult: null, composerKey: submitKey,
  }, { force: true });
  showAlert("");
  try {
    requestSent = true;
    setGenerationFsm("submitting", { detail: "可能已送出，正在確認", composerKey: submitKey }, { force: true });
    const result = await api("/api/generate", {
      method: "POST",
      body: JSON.stringify({
        featureId: currentContract.featureId,
        characterId: character.id,
        action: currentContract.actionId,
        variantId: currentContract.variantId,
        settings: {
          prompt: String(state.prompt || "").trim(),
          // The server owns and appends the non-overridable safety negative.
          // Send only the operator's text so the public max_length contract is
          // exact and the fixed terms cannot be duplicated in the workflow.
          negative_prompt: String(state.negativePrompt || "").trim(),
          aspect_ratio: state.aspectRatio,
          seed: Number(state.seed),
        },
      }),
    });
    assertSessionContext(session);
    const promptId = result.prompt_id || result.promptId || "";
    if (!promptId) throw new Error("伺服器未回傳任務編號");
    rememberActiveJob(promptId, character.id, action.id, {
      featureId: currentContract.featureId,
      variantId: currentContract.variantId,
      providerCharacter: true,
    });
    clearDraftAfterSubmit(character.id, action.id, state, draftRevision);
    if (composerBinding && composerBinding.key === submitKey && submissionSequence === submitId) {
      trackedPromptId = promptId;
      setGenerationFsm("queued", {
        promptId, resultPromptId: promptId, detail: "已加入佇列 · 等待中", pct: 0, composerKey: submitKey,
      }, { force: true });
    }
    showAlert(`已加入佇列：${promptId}`);
    await refreshQueue();
    assertSessionContext(session);
    scheduleHistoryRefresh();
  } catch (error) {
    if (isStaleSessionError(error)) return;
    const presented = generationErrorPresentation(error, requestSent);
    showAlert(presented.detail, true, { technicalDetail: presented.technicalDetail });
    if (composerBinding && composerBinding.key === submitKey && submissionSequence === submitId) {
      setGenerationFsm("error", {
        detail: presented.detail,
        reason: presented.summary,
        technicalDetail: presented.technicalDetail,
        submissionState: presented.submissionState,
        composerKey: submitKey,
      }, { force: true });
    }
  } finally {
    if (sessionContextIsCurrent(session) && submissionSequence === submitId) submitInFlight = false;
  }
}

function renderProviderCharacterFeed(character, entrypoint) {
  const items = providerEntrypointWorksForCharacter(character.id, entrypoint).filter((item) => !item.hidden);
  const jobs = activeJobsForCharacter(character.id).filter((job) => (
    String(job.featureId || "") === entrypoint.featureId
    && String(job.variantId || "") === entrypoint.variantId
  ));
  const previewLimit = window.matchMedia("(max-width: 767px)").matches ? 2 : 6;
  return h("section", { class: "panel character-feed" }, [
    renderSectionHeader("最近作品", "只顯示這個角色寫真工作流的任務與作品。", [
      h("button", { class: "ghost small", type: "button", text: "更新", onclick: async () => refreshHistory() }),
    ], { compact: true }),
    jobs.length ? h("div", { class: "running-chip-row" }, jobs.map((job) => {
      const progress = jobProgress(job);
      return h("div", { class: `running-chip ${job.state || "pending"}` }, [
        h("span", { text: characterJobLabel(job) }),
        h("b", { text: progress.label }),
      ]);
    })) : null,
    h("div", { class: "character-feed-preview-list" }, items.length
      ? items.slice(0, previewLimit).map((item, index) => renderCharacterFeedPreview(item, index))
      : [h("div", { class: "empty", text: "還沒有作品，先生成第一張寫真。" })]),
    h("div", { class: "provider-studio-library-actions" }, [
      h("button", {
        class: "primary provider-studio-castle-outputs",
        type: "button",
        text: items.length ? `此工作流全部生成作品（${items.length}）` : "此工作流全部生成作品",
        onclick: () => openProviderCharacterCastleOutputs(character, entrypoint),
      }),
      h("button", {
        class: "ghost provider-studio-imported-assets",
        type: "button",
        text: "角色素材",
        onclick: () => openProviderCharacterMaterials(character),
      }),
    ]),
  ]);
}

function renderProviderCharacterStudioRoute() {
  const ws = beginWorkspaceRender("provider-character-studio-route");
  if (!providerCharactersLoaded) {
    ws.appendChild(h("section", { class: "panel" }, [h("div", { class: "empty", text: "正在讀取角色生成設定…" })]));
    return;
  }
  const source = providerStudioCharacter();
  if (!source) {
    if (composerBinding) unbindGenerateComposer();
    ws.appendChild(h("section", { class: "panel" }, [
      h("div", { class: "empty", text: providerCharactersError || "找不到這個角色。" }),
      h("button", { class: "ghost", type: "button", text: "回角色卡片牆", onclick: () => navigate({ view: "characters" }) }),
    ]));
    return;
  }
  const character = providerStudioPresentationCharacter(source);
  if (!source.generationEnabled || !source.generationEntrypoint) {
    if (composerBinding) unbindGenerateComposer();
    ws.appendChild(h("div", { class: "workspace-header-block" }, [
      h("section", { class: "workspace-hero", style: `--character-color:${characterColor(character)}` }, [
        h("button", { class: "back-link", type: "button", text: "← 角色", onclick: () => navigate({ view: "characters" }) }),
        profilePicNode(character, "workspace-avatar"),
        h("div", { class: "workspace-title" }, [h("h1", { text: character.name }), h("p", { text: "角色創作工作室" })]),
      ]),
      h("section", { class: "panel" }, [
        h("div", { class: "empty", text: "這個角色目前尚未開放生成。" }),
        h("button", { class: "ghost", type: "button", text: "查看角色素材", onclick: () => openProviderCharacterMaterials(source) }),
      ]),
    ]));
    return;
  }
  ws.appendChild(h("div", { class: "workspace-header-block" }, [
    h("section", { class: "workspace-hero provider-studio-hero", style: `--character-color:${characterColor(character)}` }, [
      h("button", { class: "back-link", type: "button", text: "← 角色", "aria-label": "回角色卡片牆", onclick: () => navigate({ view: "characters" }) }),
      profilePicNode(character, "workspace-avatar"),
      h("div", { class: "workspace-title" }, [
        h("h1", { text: character.name }),
        h("p", { text: "角色創作工作室" }),
      ]),
      h("button", { class: "ghost provider-studio-materials", type: "button", text: "角色素材", onclick: () => openProviderCharacterMaterials(source) }),
    ]),
    h("div", { class: "character-creation-layout provider-character-creation-layout" }, [
      h("div", { class: "character-composer-zone" }, [renderProviderPhotoshootForm(character, source.generationEntrypoint)]),
      h("div", { class: "character-results-zone" }, [renderProviderCharacterFeed(character, source.generationEntrypoint)]),
    ]),
  ]));
}

function lastCharacterActions() {
  const value = readJsonStorage(userStorageKey(LAST_ACTION_KEY), {});
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function defaultActionForCharacter(character) {
  const actions = enabledCharacterActions(character);
  if (!actions.length) return "";
  const last = lastCharacterActions();
  const remembered = last && last[character.id];
  if (remembered && actions.some((action) => action.id === remembered)) return remembered;
  return actions[0].id;
}

function selectWorkspaceAction(character, actionId) {
  const last = lastCharacterActions();
  last[character.id] = actionId;
  writeJsonStorage(userStorageKey(LAST_ACTION_KEY), last);
  navigate({ view: "character-workspace", characterId: character.id, actionId });
}

function switchWorkspaceCharacter(target) {
  if (!target) return;
  if (currentRoute.view === "character-library") {
    navigate({ view: "character-library", characterId: target.id, media: currentRoute.media || "image" });
    return;
  }
  if (currentRoute.view === "character-wardrobe") {
    navigate({ view: "character-wardrobe", characterId: target.id });
    return;
  }
  if (currentRoute.view === "character-settings") {
    navigate({ view: "character-settings", characterId: target.id });
    return;
  }
  const enabled = enabledCharacterActions(target);
  const currentActionId = currentRoute.actionId || selectedCharacterActionId;
  const keep = currentActionId && enabled.some((action) => action.id === currentActionId);
  const actionId = currentActionId || defaultActionForCharacter(target);
  if (keep || !currentActionId) {
    const last = lastCharacterActions();
    last[target.id] = actionId;
    writeJsonStorage(userStorageKey(LAST_ACTION_KEY), last);
  }
  navigate({ view: "character-workspace", characterId: target.id, actionId });
}

function characterTypeLabel(character) {
  const type = characterType(character);
  const found = CHARACTER_TYPES.find(([id]) => id === type);
  return found ? found[1] : "自訂角色";
}

function openCharacterSwitcher(character, opener) {
  const compact = window.matchMedia("(max-width: 767px)").matches;
  const search = h("input", {
    class: "character-switch-search",
    type: "search",
    placeholder: "搜尋角色",
    "aria-label": "搜尋角色",
    autocomplete: "off",
  });
  const empty = h("div", { class: "empty compact hidden", text: "找不到符合的角色。" });
  const list = h("div", { class: "character-switch-list" });
  const rows = characters.map((item) => {
    const current = item.id === character.id;
    const row = h("button", {
      class: `character-switch-row${current ? " current" : ""}`,
      type: "button",
      "aria-current": current ? "true" : null,
      "aria-label": `${item.name || item.id}，${characterVisualClassLabel(item)}${current ? "，目前角色" : ""}`,
      onclick: () => {
        if (current) {
          dismissTopSheet();
          return;
        }
        sheetAction(() => switchWorkspaceCharacter(item));
      },
      dataset: { search: `${item.name || ""} ${characterVisualClassLabel(item)} ${item.id || ""}`.toLocaleLowerCase("zh-Hant") },
    }, [
      profilePicNode(item, "character-switch-row-avatar"),
      h("span", { class: "character-switch-row-copy" }, [
        h("strong", { text: item.name || item.id }),
        h("span", { text: characterVisualClassLabel(item) }),
      ]),
      current ? h("span", { class: "character-switch-current", text: "目前" }) : null,
    ]);
    list.appendChild(row);
    return row;
  });
  search.addEventListener("input", () => {
    const query = search.value.trim().toLocaleLowerCase("zh-Hant");
    let visible = 0;
    for (const row of rows) {
      const matches = !query || row.dataset.search.includes(query);
      row.classList.toggle("hidden", !matches);
      if (matches) visible += 1;
    }
    empty.classList.toggle("hidden", visible > 0);
  });
  const actions = compact ? h("div", { class: "character-switcher-actions" }, [
    canEditCharacter(character) ? h("button", {
      class: "ghost",
      type: "button",
      text: "角色設定",
      onclick: () => sheetAction(() => navigate({ view: "character-settings", characterId: character.id })),
    }) : null,
    h("button", {
      class: "ghost",
      type: "button",
      text: "回角色卡片牆",
      onclick: () => sheetAction(() => navigate({ view: "characters" })),
    }),
  ]) : null;
  openSheet({
    id: "character-switcher",
    title: "角色選單",
    opener,
    className: "character-switcher-sheet",
    content: h("div", { class: "character-switcher" }, [search, list, empty, actions]),
    onOpen: () => {
      if (!compact) requestAnimationFrame(() => search.focus({ preventScroll: true }));
    },
  });
}

function renderTabBar({ label, tabs, className = "" }) {
  const buttons = tabs.map((tab) => h("button", {
    id: tab.id,
    class: `shared-tab${tab.active ? " active" : ""}`,
    type: "button",
    role: "tab",
    "aria-selected": tab.active ? "true" : "false",
    "aria-current": tab.active ? "page" : null,
    "aria-controls": tab.panelId || null,
    tabindex: tab.active ? "0" : "-1",
    text: tab.label,
    onclick: tab.onclick,
  }));
  const bar = h("div", { class: `shared-tab-bar ${className}`.trim(), role: "tablist", "aria-label": label }, buttons);
  bar.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const current = Math.max(0, buttons.indexOf(document.activeElement));
    const index = event.key === "Home" ? 0
      : (event.key === "End" ? buttons.length - 1
        : (current + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) % buttons.length);
    buttons.forEach((button, buttonIndex) => button.setAttribute("tabindex", buttonIndex === index ? "0" : "-1"));
    buttons[index].focus();
  });
  return bar;
}

function renderCharacterLocalTabs(character) {
  const active = characterRouteSubtab(currentRoute);
  return renderTabBar({
    label: "角色工作區",
    className: "character-local-tabs",
    tabs: [
      {
        id: "character-local-tab-create",
        panelId: "character-route-panel",
        label: "創作",
        active: active === "create",
        onclick: () => navigate({ view: "character-workspace", characterId: character.id, actionId: defaultActionForCharacter(character) }),
      },
      {
        id: "character-local-tab-library",
        panelId: "character-route-panel",
        label: "內容",
        active: active === "content",
        onclick: () => navigate({
          view: "character-library",
          characterId: character.id,
          media: currentRoute.view === "character-library" ? currentRoute.media : "image",
        }),
      },
      {
        id: "character-local-tab-wardrobe",
        panelId: "character-route-panel",
        label: "衣櫃",
        active: active === "wardrobe",
        onclick: () => navigate({ view: "character-wardrobe", characterId: character.id }),
      },
      {
        id: "character-local-tab-settings",
        panelId: "character-route-panel",
        label: "設定",
        active: active === "settings",
        onclick: () => navigate({ view: "character-settings", characterId: character.id }),
      },
    ],
  });
}

function characterRouteSubtab(route = currentRoute) {
  return {
    "character-workspace": "create",
    "character-library": "content",
    "character-wardrobe": "wardrobe",
    "character-settings": "settings",
  }[route && route.view] || "create";
}

function workspaceHeaderSignature(character) {
  return JSON.stringify([
    character.id,
    character.name || "",
    character.tagline || "",
    character.profile || "",
    character.origin || "",
    characterColor(character),
    characterProfilePicUrl(character),
    canEditCharacter(character),
  ]);
}

function updateCharacterLocalTabs(frame) {
  const activeSubtab = characterRouteSubtab(currentRoute);
  for (const [id, active] of [
    ["character-local-tab-create", activeSubtab === "create"],
    ["character-local-tab-library", activeSubtab === "content"],
    ["character-local-tab-wardrobe", activeSubtab === "wardrobe"],
    ["character-local-tab-settings", activeSubtab === "settings"],
  ]) {
    const tab = frame.querySelector(`#${id}`);
    if (!tab) continue;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
    if (active) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
    tab.setAttribute("tabindex", active ? "0" : "-1");
  }
}

function ensureCharacterRouteFrame(character) {
  const workspace = $("workspace");
  let frame = workspace.firstElementChild;
  if (!frame || !frame.classList.contains("character-route-frame")) {
    frame = h("div", { class: "character-route-frame character-workspace-v2", dataset: { characterId: character.id } }, [
      renderWorkspaceHeader(character),
      h("div", { class: "character-route-content" }),
    ]);
    const current = workspace.firstElementChild;
    if (current) workspace.replaceChild(frame, current);
    else workspace.appendChild(frame);
    while (frame.nextSibling) frame.nextSibling.remove();
  } else {
    frame.dataset.characterId = character.id;
    const currentHeader = frame.querySelector(":scope > .workspace-header-block");
    const signature = workspaceHeaderSignature(character);
    if (!currentHeader || currentHeader.dataset.signature !== signature) {
      const nextHeader = renderWorkspaceHeader(character);
      if (currentHeader) frame.replaceChild(nextHeader, currentHeader);
      else frame.insertBefore(nextHeader, frame.firstChild);
    }
  }
  updateCharacterLocalTabs(frame);
  return frame.querySelector(".character-route-content");
}

function replaceCharacterRouteContent(host, next) {
  const frame = host.parentElement;
  frame.replaceChild(next, host);
}

function bindCharacterRoutePanel(panel, tabId) {
  panel.id = "character-route-panel";
  panel.setAttribute("role", "tabpanel");
  panel.setAttribute("tabindex", "0");
  panel.setAttribute("aria-labelledby", tabId);
  return panel;
}

function renderCharacterWorkspaceRoute() {
  const character = routeCharacter();
  if (!character) {
    if (composerBinding) unbindGenerateComposer();
    const ws = beginWorkspaceRender();
    ws.appendChild(h("section", { class: "panel" }, [
      h("div", { class: "empty", text: "找不到這個角色。" }),
      h("button", { class: "ghost", type: "button", text: "回角色卡片牆", onclick: () => navigate({ view: "characters" }) }),
    ]));
    return;
  }
  const actions = enabledCharacterActions(character);
  const requestedActionId = String(currentRoute.actionId || "");
  const requestedAction = requestedActionId ? actions.find((item) => item.id === requestedActionId) || null : null;
  activeCharacterId = character.id;
  selectedCharacterId = character.id;
  if (requestedActionId && !requestedAction) {
    if (composerBinding) unbindGenerateComposer();
    selectedCharacterActionId = "";
    const catalogAction = characterActions.find((item) => item.id === requestedActionId) || null;
    const host = ensureCharacterRouteFrame(character);
    const next = h("div", { class: "character-route-content" }, [
      h("section", { class: "panel" }, [
        h("div", {
          class: "empty",
          text: `「${catalogAction ? catalogAction.label : requestedActionId}」目前不適用 ${character.name}，沒有替你改開其他功能。`,
        }),
        actions.length ? h("div", { class: "empty-actions" }, actions.map((item) => h("button", {
          class: "ghost",
          type: "button",
          text: `改用「${item.label}」`,
          onclick: () => navigate({ view: "character-workspace", characterId: character.id, actionId: item.id }),
        }))) : null,
      ]),
    ]);
    bindCharacterRoutePanel(next, "character-local-tab-create");
    replaceCharacterRouteContent(host, next);
    return;
  }
  const actionId = requestedAction ? requestedAction.id : defaultActionForCharacter(character);
  if (!requestedActionId && actionId) {
    selectedCharacterActionId = actionId;
    currentRoute = { view: "character-workspace", characterId: character.id, actionId };
    history.replaceState(history.state, "", routePath(currentRoute));
  }
  const action = requestedAction || actions.find((item) => item.id === actionId) || null;
  if (!action && composerBinding) unbindGenerateComposer();
  selectedCharacterActionId = action ? action.id : "";
  const host = ensureCharacterRouteFrame(character);
  const next = h("div", { class: "character-route-content" }, [
    action ? h("div", { class: "character-creation-layout" }, [
      renderActionPicker(character, action.id),
      h("div", { class: "character-composer-zone" }, [renderMinimalActionPanel(character, action)]),
      h("div", { class: "character-results-zone" }, [renderCharacterFeed(character)]),
    ]) : h("div", { class: "empty compact", text: characterNoActionMessage(character) }),
  ]);
  bindCharacterRoutePanel(next, "character-local-tab-create");
  replaceCharacterRouteContent(host, next);
}

function renderWorkspaceHeader(character) {
  const canEdit = canEditCharacter(character);
  return h("div", { class: "workspace-header-block", dataset: { signature: workspaceHeaderSignature(character) } }, [
    h("section", { class: "workspace-hero", style: `--character-color:${characterColor(character)}` }, [
      h("button", { class: "back-link", type: "button", text: "← 角色", "aria-label": "回角色卡片牆", onclick: () => navigate({ view: "characters" }) }),
      profilePicNode(character, "workspace-avatar"),
      h("div", { class: "workspace-title" }, [
        h("h1", { text: character.name }),
        h("p", { text: character.tagline || character.profile || character.origin || "" }),
      ]),
      h("button", {
        id: "characterSwitcherTrigger",
        class: "character-switcher-trigger",
        type: "button",
        text: "角色選單 ▾",
        "aria-haspopup": "dialog",
        "aria-expanded": "false",
        onclick: (event) => openCharacterSwitcher(character, event.currentTarget),
      }),
      canEdit ? h("button", {
        class: "settings-btn",
        type: "button",
        text: "角色設定",
        onclick: () => navigate({ view: "character-settings", characterId: character.id }),
      }) : null,
    ]),
    renderCharacterLocalTabs(character),
  ]);
}

function characterActionAvailability(character, actionId) {
  const action = enabledCharacterActions(character).find((item) => item.id === actionId) || null;
  const feature = action ? actionFeature(action, character) : null;
  const enabled = Boolean(action && feature && interactiveReady(feature));
  return { action, enabled, reason: enabled ? "" : featureUnavailableReason(feature) };
}

function renderActionTile({ actionId, label, icon, active, enabled, reason = "", variant = "grid", onSelect }) {
  const unavailable = reason || "尚未就緒";
  return h("button", {
    id: `character-action-${variant}-${actionId}`,
    class: `shared-action-tile ${variant}${active ? " active" : ""}${enabled ? "" : " disabled"}`,
    type: "button",
    disabled: !enabled,
    "aria-disabled": enabled ? "false" : "true",
    "aria-current": active ? "true" : null,
    "aria-label": enabled ? label : `${label}，${unavailable}`,
    title: enabled ? null : unavailable,
    onclick: enabled ? onSelect : null,
  }, [
    h("span", { class: "action-tile-icon", text: icon || "•", "aria-hidden": "true" }),
    h("span", { class: "action-tile-label", text: label }),
    enabled ? null : h("span", { class: "action-tile-status", text: unavailable }),
  ]);
}

function renderActionGroups(character, selectedActionId, variant) {
  const enabledIds = new Set(enabledCharacterActions(character).map((action) => action.id));
  return P2_ACTION_GROUPS.map(([, groupLabel, actionIds]) => {
    const visibleActionIds = actionIds.filter((actionId) => enabledIds.has(actionId));
    if (!visibleActionIds.length) return null;
    return h("section", { class: "action-picker-group" }, [
      h("h3", { text: groupLabel }),
      h("div", { class: "action-picker-grid" }, visibleActionIds.map((actionId) => {
      const availability = characterActionAvailability(character, actionId);
      return renderActionTile({
        actionId,
        label: actionVerb(availability.action || actionId),
        icon: P2_ACTION_ICONS[actionId],
        active: actionId === selectedActionId,
        enabled: availability.enabled,
        reason: availability.reason,
        variant,
        onSelect: () => {
          if (actionId === selectedActionId) {
            dismissTopSheet();
            return;
          }
          sheetAction(() => selectWorkspaceAction(character, actionId));
        },
      });
      })),
    ]);
  }).filter(Boolean);
}

function openActionPicker(character, selectedActionId, opener) {
  openSheet({
    id: "character-action-picker",
    title: "選擇動作",
    opener,
    className: "action-picker-sheet",
    content: h("div", { class: "action-picker-sheet-groups" }, renderActionGroups(character, selectedActionId, "grid")),
  });
}

function renderActionPicker(character, selectedActionId) {
  const selected = characterActions.find((action) => action.id === selectedActionId);
  return h("aside", { class: "character-action-picker", "aria-label": "選擇動作" }, [
    h("button", {
      id: "characterActionPickerTrigger",
      class: "action-picker-trigger",
      type: "button",
      text: `${actionVerb(selected || selectedActionId)} ▾`,
      "aria-haspopup": "dialog",
      "aria-expanded": "false",
      onclick: (event) => openActionPicker(character, selectedActionId, event.currentTarget),
    }),
    h("div", { class: "desktop-action-rail" }, renderActionGroups(character, selectedActionId, "rail")),
  ]);
}

function renderWorkspaceOverlay() {
  const old = document.querySelector(".character-editor-overlay");
  if (old) old.remove();
  if (!editorOverlayCharacterId) return;
  const character = characters.find((item) => item.id === editorOverlayCharacterId);
  if (!character) return;
  if (!canEditCharacter(character)) {
    editorOverlayCharacterId = "";
    showAlert("沒有權限", true);
    return;
  }
  const overlay = h("div", { class: "character-editor-overlay" }, [
    h("div", { class: "editor-modal" }, [
      h("div", { class: "editor-modal-head" }, [
        h("div", {}, [
          h("h2", { text: "角色設定" }),
          h("p", { text: character.name }),
        ]),
        h("button", { class: "ghost small", type: "button", text: "關閉", onclick: () => { editorOverlayCharacterId = ""; renderWorkspaceOverlay(); } }),
      ]),
      renderCharacterEditor(character),
    ]),
  ]);
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) {
      editorOverlayCharacterId = "";
      renderWorkspaceOverlay();
    }
  });
  document.body.appendChild(overlay);
}

function radioCards(label, options, current, onChange) {
  const buttons = [];
  const group = h("div", { class: "mini-card-row" });
  for (const opt of options) {
    const btn = h("button", {
      type: "button",
      class: `mini-card${String(opt.value) === String(current) ? " active" : ""}`,
      onclick: () => {
        current = opt.value;
        buttons.forEach((item) => item.classList.toggle("active", item.dataset.value === String(current)));
        onChange(current);
      },
      dataset: { value: String(opt.value) },
    }, [
      h("span", { text: opt.label }),
      opt.sub ? h("small", { text: opt.sub }) : null,
    ]);
    buttons.push(btn);
    group.appendChild(btn);
  }
  return h("div", { class: "field mini-field" }, [h("span", { class: "field-label", text: label }), group]);
}

function renderAudioFormatPreference() {
  const group = h("div", { class: "mini-card-row audio-format-choice", role: "group", "aria-label": "預設語音下載格式" });
  for (const option of AUDIO_EXPORT_FORMAT_OPTIONS) {
    const active = option.value === preferredAudioExportFormat;
    group.appendChild(h("button", {
      type: "button",
      class: `mini-card${active ? " active" : ""}`,
      dataset: { audioFormatChoice: "1", value: option.value },
      "aria-pressed": active ? "true" : "false",
      onclick: () => setPreferredAudioExportFormat(option.value),
    }, [
      h("span", { text: option.label }),
      h("small", { text: option.sub }),
    ]));
  }
  return h("div", { class: "field mini-field audio-format-field" }, [
    h("span", { class: "field-label", text: "預設下載格式" }),
    group,
    renderHint("之後按下載時會優先使用這個格式；作品詳細裡仍可隨時改選 WAV、MP3 或 MP4。"),
  ]);
}

function renderQualitySelector(current, onChange, capability = null) {
  const choice = normalizeCapabilityChoice(capability, "quality", qualityOptions(), current, "standard");
  return radioCards("品質", choice.options, choice.value, onChange);
}

function renderComposerUploadSlot(feature, uploadId, label, state, onChange) {
  return uploadSlot(feature, uploadId, label, state, onChange);
}

function renderSectionHeader(title, sub = "", actions = [], options = {}) {
  return h("div", { class: `panel-head-row${options.compact ? " compact" : ""}` }, [
    h("div", {}, [
      h(options.level || "h2", { class: options.titleClass || "panel-h2", text: title }),
      sub ? h("p", { class: "panel-sub", text: sub }) : null,
    ]),
    actions.length ? h("div", { class: options.actionsClass || "section-header-actions" }, actions) : null,
  ]);
}

function textareaMini(label, value, placeholder, onChange, required = false) {
  const ta = h("textarea", { rows: "3", placeholder: placeholder || "" });
  ta.value = value || "";
  ta.addEventListener("input", () => onChange(ta.value));
  return h("label", { class: `field mini-field${required && !ta.value.trim() ? " required-empty" : ""}` }, [
    h("span", { class: "field-label", text: required ? `${label} *` : label }),
    ta,
  ]);
}

function inputMini(label, value, placeholder, onChange, required = false) {
  const input = h("input", { type: "text", placeholder: placeholder || "" });
  input.value = value || "";
  input.addEventListener("input", () => onChange(input.value));
  return h("label", { class: `field mini-field${required && !input.value.trim() ? " required-empty" : ""}` }, [
    h("span", { class: "field-label", text: required ? `${label} *` : label }),
    input,
  ]);
}

function selectMini(label, options, value, onChange) {
  const sel = buildSelect(options, value);
  sel.addEventListener("change", () => onChange(sel.value));
  return h("label", { class: "field mini-field" }, [h("span", { class: "field-label", text: label }), sel]);
}

function aspectOptions(feature, capability = null) {
  const field = fieldById(feature, "aspect");
  const raw = field && Array.isArray(field.options) ? field.options : [];
  const opts = filterCapabilityOptions(capability, "aspect", raw);
  return opts.map((opt) => {
    const ratio = opt.width > opt.height ? "landscape" : (opt.width === opt.height ? "square" : "portrait");
    const label = ratio === "portrait" ? "直式" : (ratio === "landscape" ? "橫式" : "方形");
    return { value: `${opt.width}x${opt.height}`, label };
  }).filter((opt, index, list) => list.findIndex((item) => item.label === opt.label) === index);
}

function qualityOptions() {
  return [
    { value: "fast", label: "快速" },
    { value: "standard", label: "標準" },
    { value: "fine", label: "精緻" },
  ];
}

function platformOptions() {
  return [
    { value: "threads", label: "Threads" },
    { value: "instagram", label: "Instagram" },
    { value: "x", label: "X" },
    { value: "facebook", label: "Facebook" },
    { value: "xiaohongshu", label: "小紅書" },
    { value: "linkedin", label: "LinkedIn" },
  ];
}

function setAspectSettings(settings, value) {
  const [width, height] = String(value || "").split("x").map(Number);
  if (width && height) {
    settings.width = width;
    settings.height = height;
  }
}

function renderWardrobePicker(character, state, update, options = {}) {
  const wrap = h("div", { class: "field mini-field wardrobe-field hidden" });
  const title = h("span", { class: "field-label", text: options.label || "服裝" });
  const list = h("div", { class: "wardrobe-row" });
  wrap.appendChild(h("div", { class: "wardrobe-picker-head" }, [
    title,
    h("button", {
      class: "ghost small wardrobe-manage-link",
      type: "button",
      text: "查看角色造型",
      onclick: () => navigate({ view: "character-wardrobe", characterId: character.id }),
    }),
  ]));
  wrap.appendChild(list);
  const repaintActive = () => {
    list.querySelectorAll(".wardrobe-card").forEach((node) => {
      node.classList.toggle("active", node.dataset.outfit === String(state.outfit || ""));
    });
  };
  async function load() {
    clear(list);
    try {
      const allowed = allowedOptionValues(options.capability, "wardrobeTheme");
      const outfits = (await loadWardrobe(character.id))
        .filter((outfit) => !outfit.custom && outfit.approved === true)
        .filter((outfit) => !allowed || allowed.has(String(outfit.theme || outfit.label || "")));
      if (!outfits.length) {
        if (options.required) {
          wrap.classList.remove("hidden");
          list.appendChild(h("div", { class: "empty compact", text: "這個角色目前沒有已驗證的造型" }));
        }
        return;
      }
      if (options.required && !outfits.some((outfit) => String(outfit.theme || outfit.label || "") === String(state.outfit || ""))) {
        const first = outfits[0];
        update({
          outfit: first.theme || first.label || "",
          outfitLabel: first.label || first.theme || "",
          outfitUrl: first.outfitUrl || "",
        }, { persist: false });
      }
      wrap.classList.remove("hidden");
      if (!options.required) list.appendChild(h("button", {
        type: "button",
        dataset: { outfit: "" },
        class: `wardrobe-card${!state.outfit ? " active" : ""}`,
        text: "不指定",
        onclick: () => { update({ outfit: "", outfitLabel: "", outfitUrl: "" }); repaintActive(); },
      }));
      for (const outfit of outfits) {
        const value = outfit.theme || outfit.label || "";
        list.appendChild(h("button", {
          type: "button",
          dataset: { outfit: value },
          class: `wardrobe-card${state.outfit === value ? " active" : ""}`,
          onclick: () => {
            update({ outfit: value, outfitLabel: outfit.label || value, outfitUrl: outfit.outfitUrl || "" });
            repaintActive();
          },
        }, [
          outfit.thumbUrl || outfit.outfitUrl ? h("img", { src: outfit.thumbUrl || outfit.outfitUrl, alt: "", loading: "lazy" }) : null,
          h("span", { text: outfit.label || value }),
        ]));
      }
    } catch (_) {
      wrap.classList.remove("hidden");
      list.appendChild(h("div", { class: "empty compact wardrobe-picker-error" }, [
        h("span", { text: "造型讀取失敗，沒有替你選擇其他造型。" }),
        h("button", { class: "ghost small", type: "button", text: "重試", onclick: load }),
      ]));
    }
  }
  setTimeout(load, 0);
  return wrap;
}

function renderAnimeScenePicker(character, capability, state, update) {
  const wrap = h("div", { class: "field mini-field scene-field" });
  const list = h("div", { class: "scene-row" });
  const options = filterCapabilityOptions(capability, "sceneTheme", ANIME_SCENE_OPTIONS);
  const repaintActive = () => {
    list.querySelectorAll(".scene-card").forEach((node) => {
      node.classList.toggle("active", node.dataset.scene === String(state.sceneTheme || ""));
    });
  };
  wrap.appendChild(h("span", { class: "field-label", text: "動漫場景" }));
  if (!options.length) {
    wrap.appendChild(h("div", { class: "empty compact", text: "目前沒有已驗證的動漫場景" }));
    return wrap;
  }
  for (const option of options) {
    const value = String(option.value || "");
    const params = new URLSearchParams({ kind: "scene", theme: value, thumb: "1" });
    list.appendChild(h("button", {
      type: "button",
      dataset: { scene: value },
      class: `scene-card${String(state.sceneTheme || "") === value ? " active" : ""}`,
      onclick: () => {
        update({ sceneTheme: value });
        repaintActive();
      },
    }, [
      h("img", { src: `/api/characters/${encodeURIComponent(character.id)}/asset?${params}`, alt: "", loading: "lazy" }),
      h("strong", { text: option.label || value }),
      option.sub ? h("small", { text: option.sub }) : null,
    ]));
  }
  wrap.appendChild(list);
  return wrap;
}

function moveDanceTemplateGridFocus(event, grid) {
  const current = event.target.closest(".dance-template-select");
  if (!current || !grid.contains(current)) return;
  if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
  const buttons = Array.from(grid.querySelectorAll(".dance-template-select:not([disabled])"));
  const index = buttons.indexOf(current);
  if (index < 0) return;
  const columns = Math.max(1, String(getComputedStyle(grid).gridTemplateColumns || "").split(" ").filter(Boolean).length);
  let next = index;
  if (event.key === "ArrowLeft") next = Math.max(0, index - 1);
  else if (event.key === "ArrowRight") next = Math.min(buttons.length - 1, index + 1);
  else if (event.key === "ArrowUp") next = Math.max(0, index - columns);
  else if (event.key === "ArrowDown") next = Math.min(buttons.length - 1, index + columns);
  else if (event.key === "Home") next = 0;
  else if (event.key === "End") next = buttons.length - 1;
  if (next === index) return;
  event.preventDefault();
  buttons[next].focus();
}

function renderDanceTemplatePicker(feature, state, update, options = {}) {
  resetDancePreviewObserver();
  const character = options.character || activeCharacter();
  const capability = options.capability || (character ? enabledFeatureCapability(character, feature.id) : null);
  const characterTypeKey = danceLibraryCharacterType(character, capability);
  const wrap = h("div", {
    class: "field mini-field dance-template-field",
    dataset: { danceTemplatePicker: "1", selectedId: String(state.danceTemplateId || "") },
  });
  const body = h("div", { class: "dance-template-body" });
  const controls = h("div", { class: "dance-template-controls hidden" });
  const categories = h("div", { class: "dance-template-filter-row", role: "group", "aria-label": "舞蹈分類" });
  const quickFilters = h("div", { class: "dance-template-filter-row", role: "group", "aria-label": "模板篩選" });
  const resultCount = h("div", { class: "dance-template-result-count", role: "status", "aria-live": "polite" });
  const templateGrid = h("div", { class: "dance-template-grid", role: "list" });
  const templateStatus = h("div", { class: "url-status", role: "status", "aria-live": "polite" });
  const selectedSummary = h("div", { class: "dance-template-selected hidden", "aria-live": "polite" });
  const search = h("input", {
    type: "search",
    class: "dance-template-search",
    placeholder: "搜尋名稱、分類或標籤",
    "aria-label": "搜尋舞蹈模板",
  });
  let templates = [];
  let activeCategory = "all";
  let quickFilter = "all";
  let query = "";
  let searchTimer = null;

  state.__danceTemplateValidated = false;
  fieldGetters.danceTemplateId = () => state.__danceTemplateValidated ? String(state.danceTemplateId || "") : "";

  const videoUpload = (feature.uploads || []).find((item) => item.id === "video") || null;
  const ownVideoBody = h("div", { class: "dance-own-video-body" });
  const ownVideo = h("details", {
    class: "dance-own-video",
    open: serverFiles.video || (state.serverFiles && state.serverFiles.video) ? "" : null,
  }, [
    h("summary", { text: "使用自己的影片" }),
    h("p", { text: "可上傳檔案、貼網址、從 Drive 或未整理的 crawl 素材挑選。" }),
    ownVideoBody,
  ]);
  let ownVideoLoaded = false;
  function ensureOwnVideoLoaded() {
    if (ownVideoLoaded) return;
    ownVideoLoaded = true;
    ownVideoBody.appendChild(renderComposerUploadSlot(
      feature,
      "video",
      "上傳自己的動作影片",
      state,
      () => {
        state.__danceTemplateValidated = false;
        if (state.danceTemplateId) update({ danceTemplateId: "" });
        wrap.dataset.selectedId = "";
        repaintActive();
        renderSelectedSummary();
        templateStatus.textContent = "已改用自己的影片";
        templateStatus.className = "url-status ok";
      },
    ));
  }
  ownVideo.addEventListener("toggle", () => {
    if (ownVideo.open) ensureOwnVideoLoaded();
  });
  if (ownVideo.open) ensureOwnVideoLoaded();

  function clearOwnVideo() {
    delete serverFiles.video;
    if (state.serverFiles) delete state.serverFiles.video;
    if (state.serverFileLabels) delete state.serverFileLabels.video;
    const input = fileInputs.video;
    if (input) {
      input.value = "";
      if (videoUpload) clearDropzoneSelection(videoUpload, input);
    }
  }

  function selectedTemplate() {
    return templates.find((template) => template.id === String(state.danceTemplateId || "")) || null;
  }

  function repaintActive() {
    const selectedId = String(state.danceTemplateId || "");
    wrap.dataset.selectedId = selectedId;
    templateGrid.querySelectorAll(".dance-template-card").forEach((card) => {
      const active = card.dataset.templateId === selectedId;
      card.classList.toggle("active", active);
      const button = card.querySelector(".dance-template-select");
      if (button) button.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }

  function renderSelectedSummary() {
    clear(selectedSummary);
    const selected = selectedTemplate();
    selectedSummary.classList.toggle("hidden", !selected);
    if (!selected) return;
    const media = selected.runtimeReady && selected.storyboardUrl
      ? h("img", { src: selected.storyboardUrl, alt: "", loading: "lazy" })
      : h("span", { class: "dance-template-selected-fallback", text: "♪" });
    const preview = h("button", {
      type: "button",
      class: "ghost small",
      text: "完整預覽",
      disabled: !selected.runtimeReady || !selected.previewUrl,
      onclick: (event) => openDanceTemplatePreview(selected, event.currentTarget),
    });
    selectedSummary.appendChild(media);
    selectedSummary.appendChild(h("span", { class: "dance-template-selected-copy" }, [
      h("small", { text: "已選模板" }),
      h("strong", { text: selected.title }),
      h("span", {
        text: [danceCategoryLabel(selected.category), danceDurationLabel(selected.duration)].filter(Boolean).join(" · "),
      }),
    ]));
    selectedSummary.appendChild(h("span", { class: "dance-template-selected-actions" }, [
      preview,
      h("button", {
        type: "button",
        class: "ghost small",
        text: "清除",
        onclick: () => {
          state.__danceTemplateValidated = false;
          update({ danceTemplateId: "" });
          repaintActive();
          renderSelectedSummary();
          templateStatus.textContent = "已清除模板選擇";
          templateStatus.className = "url-status";
        },
      }),
    ]));
  }

  function chooseTemplate(template, video) {
    clearOwnVideo();
    state.__danceTemplateValidated = true;
    if (update({ danceTemplateId: template.id }) === false) {
      state.__danceTemplateValidated = false;
      return;
    }
    repaintActive();
    renderSelectedSummary();
    templateStatus.textContent = `已選擇「${template.title}」；送出生成時才會準備原始影片。`;
    templateStatus.className = "url-status ok";
    if (video) playDancePreview(video);
  }

  function templateMatches(template) {
    if (activeCategory !== "all" && template.category !== activeCategory) return false;
    if (quickFilter === "featured" && !template.featured) return false;
    if (quickFilter === "favorite" && !template.favorite) return false;
    if (!query) return true;
    const haystack = [template.title, template.category, ...(template.tags || [])].join(" ").toLocaleLowerCase("zh-Hant");
    return haystack.includes(query);
  }

  function renderFilterButtons() {
    clear(quickFilters);
    const quickOptions = [
      { value: "all", label: "全部" },
      { value: "featured", label: "精選", count: templates.filter((item) => item.featured).length },
      { value: "favorite", label: "收藏", count: templates.filter((item) => item.favorite).length },
    ];
    for (const option of quickOptions) {
      if (option.value !== "all" && !option.count) continue;
      const active = quickFilter === option.value;
      quickFilters.appendChild(h("button", {
        type: "button",
        class: `dance-filter-chip${active ? " active" : ""}`,
        "aria-pressed": active ? "true" : "false",
        text: `${option.label}${option.count === undefined ? "" : ` ${option.count}`}`,
        onclick: () => {
          quickFilter = option.value;
          renderFilterButtons();
          renderGrid();
        },
      }));
    }
    clear(categories);
    const values = [...new Set(templates.map((template) => template.category).filter(Boolean))]
      .sort((a, b) => danceCategoryLabel(a).localeCompare(danceCategoryLabel(b), "zh-Hant"));
    for (const value of ["all", ...values]) {
      const count = value === "all" ? templates.length : templates.filter((item) => item.category === value).length;
      const active = activeCategory === value;
      categories.appendChild(h("button", {
        type: "button",
        class: `dance-filter-chip${active ? " active" : ""}`,
        "aria-pressed": active ? "true" : "false",
        text: `${danceCategoryLabel(value)} ${count}`,
        onclick: () => {
          activeCategory = value;
          renderFilterButtons();
          renderGrid();
        },
      }));
    }
  }

  function renderGrid() {
    resetDancePreviewObserver();
    clear(templateGrid);
    const shown = templates.filter(templateMatches).sort((a, b) => (
      Number(b.featured) - Number(a.featured)
      || Number(b.favorite) - Number(a.favorite)
      || Number(a.rank || 0) - Number(b.rank || 0)
      || a.title.localeCompare(b.title, "zh-Hant")
    ));
    resultCount.textContent = `顯示 ${shown.length} / ${templates.length} 個核准模板`;
    if (!shown.length) {
      templateGrid.appendChild(h("div", { class: "empty compact", text: "沒有符合目前條件的模板。" }));
      return;
    }
    for (const template of shown) {
      const { frame, video } = renderDanceTemplateMedia(template);
      const duration = danceDurationLabel(template.duration);
      const selected = template.id === String(state.danceTemplateId || "");
      const select = h("button", {
        type: "button",
        class: "dance-template-select",
        "aria-pressed": selected ? "true" : "false",
        "aria-label": `選擇${template.title}${duration ? `，長度 ${duration}` : ""}，${danceCategoryLabel(template.category)}`,
        onclick: () => chooseTemplate(template, video),
      }, [
        frame,
        h("span", { class: "dance-template-card-copy" }, [
          h("strong", { text: template.title }),
          h("span", { class: "dance-template-card-meta" }, [
            h("span", { text: danceCategoryLabel(template.category) }),
            duration ? h("span", { text: duration }) : null,
          ]),
          (template.tags || []).length
            ? h("span", { class: "dance-template-card-tags", text: template.tags.slice(0, 2).join(" · ") })
            : null,
        ]),
      ]);
      const card = h("article", {
        class: `dance-template-card${selected ? " active" : ""}`,
        role: "listitem",
        dataset: { templateId: template.id },
        onmouseenter: () => playDancePreview(video),
        onmouseleave: () => {
          if (activeDancePreviewVideo === video) stopActiveDancePreview(true);
        },
        onfocusin: () => playDancePreview(video),
        onfocusout: () => setTimeout(() => {
          if (!card.contains(document.activeElement) && activeDancePreviewVideo === video) stopActiveDancePreview(true);
        }, 0),
      }, [
        select,
        h("span", { class: "dance-template-card-badges", "aria-hidden": "true" }, [
          template.featured ? h("span", { text: "精選" }) : null,
          template.favorite ? h("span", { text: "♥" }) : null,
          !template.runtimeReady ? h("span", { class: "preparing", text: "準備中" }) : null,
        ]),
        h("button", {
          type: "button",
          class: "dance-template-expand",
          "aria-label": `完整預覽${template.title}`,
          text: "⛶",
          disabled: !template.runtimeReady || !template.previewUrl,
          onclick: (event) => {
            event.stopPropagation();
            openDanceTemplatePreview(template, event.currentTarget);
          },
        }),
        h("span", { class: "dance-template-check", text: "✓", "aria-hidden": "true" }),
      ]);
      templateGrid.appendChild(card);
    }
    repaintActive();
  }

  search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      query = search.value.trim().toLocaleLowerCase("zh-Hant");
      renderGrid();
    }, 120);
  });
  templateGrid.addEventListener("keydown", (event) => moveDanceTemplateGridFocus(event, templateGrid));

  const manageButton = isOperator() ? h("button", {
    type: "button",
    class: "ghost small",
    text: "管理模板庫",
    onclick: () => {
      danceLibraryReturnRoute = { ...currentRoute };
      navigate({ view: "dance-library" });
    },
  }) : null;
  wrap.appendChild(h("div", { class: "dance-template-heading" }, [
    h("span", { class: "field-label", text: "舞蹈模板" }),
    manageButton,
  ]));
  body.appendChild(h("div", { class: "dance-template-loading-grid", "aria-hidden": "true" },
    Array.from({ length: 8 }, () => h("span", { class: "dance-template-skeleton" }))));
  body.appendChild(controls);
  controls.appendChild(search);
  controls.appendChild(quickFilters);
  controls.appendChild(categories);
  controls.appendChild(resultCount);
  controls.appendChild(selectedSummary);
  controls.appendChild(templateGrid);
  controls.appendChild(templateStatus);
  wrap.appendChild(body);
  wrap.appendChild(ownVideo);

  if (!characterTypeKey) {
    clear(body);
    body.appendChild(h("div", {
      class: "empty compact",
      text: "這個角色目前沒有相符且已核准的舞蹈模板；你仍可使用自己的影片。",
    }));
    return wrap;
  }

  const templateSession = captureSessionContext();
  loadDanceTemplates(characterTypeKey).then((items) => {
    if (!sessionContextIsCurrent(templateSession) || !wrap.isConnected) return;
    templates = items;
    const loading = body.querySelector(".dance-template-loading-grid");
    if (loading) loading.remove();
    controls.classList.remove("hidden");
    renderFilterButtons();
    if (state.danceTemplateId && !selectedTemplate()) {
      state.__danceTemplateValidated = false;
      update({ danceTemplateId: "" });
      templateStatus.textContent = "原先選擇的模板已不再適用，請重新選擇。";
      templateStatus.className = "url-status err";
    } else if (state.danceTemplateId) {
      state.__danceTemplateValidated = true;
      update({}, { persist: false });
    }
    renderSelectedSummary();
    renderGrid();
    if (!templates.length) {
      templateStatus.textContent = "目前沒有明確標示為此角色類型、且已核准的模板。";
      templateStatus.className = "url-status";
    }
  }).catch((error) => {
    if (!sessionContextIsCurrent(templateSession) || !wrap.isConnected) return;
    clear(body);
    body.appendChild(h("div", { class: "empty compact" }, [
      h("span", { text: `模板庫暫時無法讀取：${error.message}` }),
      h("button", {
        type: "button",
        class: "ghost small",
        text: "重試",
        onclick: () => {
          danceTemplateCache.delete(characterTypeKey);
          const replacement = renderDanceTemplatePicker(feature, state, update, options);
          if (wrap.isConnected) wrap.replaceWith(replacement);
        },
      }),
    ]));
  });

  return wrap;
}

function danceManageStatusMatches(item, status) {
  if (status === "problem") return item.status === "problem" || Boolean(item.problem);
  return item.status === status && !item.problem;
}

async function loadDanceLibraryManage(force = false) {
  if (danceLibraryManageState.loading) return danceLibraryManageState.items;
  if (danceLibraryManageState.loaded && !force) return danceLibraryManageState.items;
  danceLibraryManageState.loading = true;
  danceLibraryManageState.error = "";
  try {
    const data = await api(DANCE_LIBRARY_ENDPOINTS.manage);
    const items = (Array.isArray(data.templates) ? data.templates : (Array.isArray(data.items) ? data.items : []))
      .map(normalizeDanceTemplate)
      .filter((item) => item.id);
    const knownIds = new Set(items.map((item) => item.id));
    danceLibraryManageState.items = items;
    danceLibraryManageState.selected = new Set(
      [...danceLibraryManageState.selected].filter((id) => knownIds.has(id)),
    );
    danceLibraryManageState.loaded = true;
    return items;
  } catch (error) {
    danceLibraryManageState.error = error.message;
    danceLibraryManageState.loaded = false;
    throw error;
  } finally {
    danceLibraryManageState.loading = false;
  }
}

async function updateDanceLibrary(payload, successMessage) {
  danceLibraryManageState.error = "";
  danceLibraryManageState.message = "";
  await api(DANCE_LIBRARY_ENDPOINTS.update, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  danceTemplateCache.clear();
  danceLibraryManageState.loaded = false;
  danceLibraryManageState.message = successMessage || "模板庫已更新。";
  await loadDanceLibraryManage(true);
}

async function rescanDanceLibrary() {
  danceLibraryManageState.error = "";
  danceLibraryManageState.message = "正在重新掃描模板來源…";
  await api(DANCE_LIBRARY_ENDPOINTS.rescan, { method: "POST", body: "{}" });
  danceTemplateCache.clear();
  danceLibraryManageState.loaded = false;
  danceLibraryManageState.message = "重新掃描完成。";
  await loadDanceLibraryManage(true);
}

async function prepareDanceLibraryItem(id) {
  await api(DANCE_LIBRARY_ENDPOINTS.prepare, {
    method: "POST",
    body: JSON.stringify({ id, kinds: ["storyboard", "preview", "runtime"] }),
  });
  danceTemplateCache.clear();
  danceLibraryManageState.loaded = false;
  danceLibraryManageState.message = "已送出預覽與執行檔準備。";
  await loadDanceLibraryManage(true);
}

function danceManagerStatusSelect(value, ariaLabel) {
  return h("select", { class: "dance-manager-select", "aria-label": ariaLabel }, DANCE_LIBRARY_STATUSES.map((status) =>
    h("option", {
      value: status.value,
      text: status.label,
      selected: status.value === value,
    })));
}

function renderDanceManagerRow(item, categories) {
  const titleInput = h("input", {
    type: "text",
    value: item.title,
    maxlength: "120",
    "aria-label": `${item.title}的顯示名稱`,
  });
  const categoryInput = h("input", {
    type: "text",
    value: item.category === "uncategorized" ? "" : item.category,
    list: "dance-manager-categories",
    maxlength: "32",
    placeholder: "例如 dance",
    "aria-label": `${item.title}的分類`,
  });
  const tagsInput = h("input", {
    type: "text",
    value: (item.tags || []).join(", "),
    maxlength: "240",
    placeholder: "全身, 快節奏",
    "aria-label": `${item.title}的標籤`,
  });
  const statusSelect = danceManagerStatusSelect(item.status, `${item.title}的狀態`);
  const rankInput = h("input", {
    type: "number",
    value: String(item.rank || 0),
    min: "0",
    max: "9999",
    step: "1",
    "aria-label": `${item.title}的排序`,
  });
  const featured = h("input", {
    type: "checkbox",
    checked: item.featured,
    "aria-label": `將${item.title}設為精選`,
  });
  featured.checked = item.featured;
  const favorite = h("input", {
    type: "checkbox",
    checked: item.favorite,
    "aria-label": `將${item.title}加入收藏`,
  });
  favorite.checked = item.favorite;
  const compatibilityChecks = DANCE_COMPATIBILITY_OPTIONS.map((option) => {
    const input = h("input", {
      type: "checkbox",
      value: option.value,
      checked: item.compatibleCharacterTypes.includes(option.value),
    });
    input.checked = item.compatibleCharacterTypes.includes(option.value);
    return { option, input };
  });
  const selected = danceLibraryManageState.selected.has(item.id);
  const select = h("input", {
    type: "checkbox",
    checked: selected,
    "aria-label": `批次選取${item.title}`,
    onchange: (event) => {
      if (event.target.checked) danceLibraryManageState.selected.add(item.id);
      else danceLibraryManageState.selected.delete(item.id);
      renderDanceLibraryManager();
    },
  });
  select.checked = selected;
  const save = h("button", { class: "primary small", type: "button", text: "儲存" });
  save.addEventListener("click", async () => {
    save.disabled = true;
    save.textContent = "儲存中…";
    const compatibleCharacterTypes = compatibilityChecks
      .filter(({ input }) => input.checked)
      .map(({ option }) => option.value);
    try {
      await updateDanceLibrary({
        id: item.id,
        title: titleInput.value.trim(),
        category: categoryInput.value.trim() || "uncategorized",
        tags: tagsInput.value.split(",").map((tag) => tag.trim()).filter(Boolean),
        compatibleCharacterTypes,
        status: statusSelect.value,
        featured: featured.checked,
        favorite: favorite.checked,
        rank: Math.max(0, Number(rankInput.value || 0)),
      }, `已儲存「${titleInput.value.trim() || item.title}」。`);
      if (currentRoute.view === "dance-library") renderDanceLibraryManager();
    } catch (error) {
      danceLibraryManageState.error = error.message;
      if (save.isConnected) {
        save.disabled = false;
        save.textContent = "儲存";
      }
      if (currentRoute.view === "dance-library") renderDanceLibraryManager();
    }
  });

  return h("article", { class: `dance-manager-card${selected ? " selected" : ""}` }, [
    h("div", { class: "dance-manager-card-select" }, [select]),
    h("div", { class: "dance-manager-media" }, [
      item.runtimeReady && item.storyboardUrl
        ? h("img", { src: item.storyboardUrl, alt: "", loading: "lazy", decoding: "async" })
        : h("span", { text: "預覽準備中" }),
      item.runtimeReady && item.previewUrl ? h("button", {
        type: "button",
        class: "dance-manager-preview",
        text: "▶ 預覽",
        onclick: (event) => openDanceTemplatePreview(item, event.currentTarget),
      }) : null,
    ]),
    h("div", { class: "dance-manager-fields" }, [
      h("label", {}, [h("span", { text: "顯示名稱" }), titleInput]),
      h("label", {}, [h("span", { text: "分類" }), categoryInput]),
      h("label", { class: "wide" }, [h("span", { text: "標籤（逗號分隔）" }), tagsInput]),
      h("label", {}, [h("span", { text: "狀態" }), statusSelect]),
      h("label", {}, [h("span", { text: "排序" }), rankInput]),
      h("fieldset", { class: "dance-manager-compatibility wide" }, [
        h("legend", { text: "可用角色類型" }),
        ...compatibilityChecks.map(({ option, input }) => h("label", {}, [input, h("span", { text: option.label })])),
      ]),
      h("label", { class: "dance-manager-featured" }, [featured, h("span", { text: "精選模板" })]),
      h("label", { class: "dance-manager-featured" }, [favorite, h("span", { text: "加入收藏" })]),
      h("div", { class: "dance-manager-runtime" }, [
        h("span", { class: item.runtimeReady ? "ready" : "preparing", text: item.runtimeReady ? "執行檔已準備" : "執行檔準備中" }),
        item.problem ? h("span", { class: "problem", text: item.problem }) : null,
        categories.includes(item.category) ? null : h("span", { text: "新分類" }),
      ]),
      Array.isArray(item.variants) && item.variants.length
        ? h("details", { class: "dance-manager-variants wide" }, [
            h("summary", { text: `原始檔版本（${item.variants.length}）` }),
            h("div", {}, item.variants.map((variant) => h("div", { class: "dance-manager-variant" }, [
              h("strong", { text: String(variant.filename || variant.name || "未命名檔案") }),
              h("span", {
                text: [
                  variant.codec,
                  variant.width && variant.height ? `${variant.width}×${variant.height}` : "",
                  variant.size ? formatBytes(Number(variant.size)) : "",
                ].filter(Boolean).join(" · "),
              }),
            ]))),
          ])
        : null,
    ]),
    h("div", { class: "dance-manager-card-actions" }, [
      h("button", {
        class: "ghost small",
        type: "button",
        text: "準備預覽／執行檔",
        onclick: async (event) => {
          const button = event.currentTarget;
          button.disabled = true;
          button.textContent = "準備中…";
          try {
            await prepareDanceLibraryItem(item.id);
            if (currentRoute.view === "dance-library") renderDanceLibraryManager();
          } catch (error) {
            danceLibraryManageState.error = error.message;
            if (currentRoute.view === "dance-library") renderDanceLibraryManager();
          }
        },
      }),
      save,
    ]),
  ]);
}

function renderDanceLibraryManager() {
  resetDancePreviewObserver();
  const ws = beginWorkspaceRender("workspace-route-root dance-library-manager-route");
  const state = danceLibraryManageState;
  const goBack = () => navigate(danceLibraryReturnRoute || { view: "characters" });
  const rescan = h("button", { class: "ghost", type: "button", text: "重新掃描" });
  rescan.addEventListener("click", async () => {
    rescan.disabled = true;
    rescan.textContent = "掃描中…";
    try {
      await rescanDanceLibrary();
      if (currentRoute.view === "dance-library") renderDanceLibraryManager();
    } catch (error) {
      state.error = error.message;
      if (rescan.isConnected) {
        rescan.disabled = false;
        rescan.textContent = "重新掃描";
      }
      if (currentRoute.view === "dance-library") renderDanceLibraryManager();
    }
  });
  ws.appendChild(h("section", { class: "panel dance-manager-panel" }, [
    h("div", { class: "panel-head-row dance-manager-head" }, [
      h("div", {}, [
        h("h1", { class: "panel-h1", text: "舞蹈模板庫" }),
        h("p", {
          class: "panel-sub",
          text: "整理顯示名稱、分類、相容角色與核准狀態；一般使用者只會看到明確相容且已核准的模板。",
        }),
      ]),
      h("div", { class: "dance-manager-head-actions" }, [
        h("button", { class: "ghost", type: "button", text: "返回", onclick: goBack }),
        rescan,
      ]),
    ]),
  ]));
  const panel = ws.querySelector(".dance-manager-panel");
  if (state.message) panel.appendChild(h("div", { class: "panel-note", text: state.message }));
  if (state.error) panel.appendChild(h("div", { class: "alert error", text: state.error }));

  if (!state.loaded) {
    if (state.error) {
      panel.appendChild(h("div", { class: "empty" }, [
        h("span", { text: `模板庫讀取失敗：${state.error}` }),
        h("button", {
          type: "button",
          class: "ghost small",
          text: "重試",
          onclick: () => {
            state.error = "";
            renderDanceLibraryManager();
          },
        }),
      ]));
    } else {
      panel.appendChild(h("div", { class: "empty", text: state.loading ? "讀取模板庫中…" : "準備讀取模板庫…" }));
    }
    if (!state.loading && !state.error) {
      loadDanceLibraryManage().then(() => {
        if (currentRoute.view === "dance-library") renderDanceLibraryManager();
      }).catch(() => {
        if (currentRoute.view === "dance-library") renderDanceLibraryManager();
      });
    }
    return;
  }

  const query = state.query.trim().toLocaleLowerCase("zh-Hant");
  const statusItems = state.items.filter((item) => danceManageStatusMatches(item, state.status));
  const shown = statusItems.filter((item) => {
    if (!query) return true;
    const variantNames = (Array.isArray(item.variants) ? item.variants : [])
      .flatMap((variant) => [variant.name, variant.filename, variant.path])
      .filter(Boolean);
    return [item.title, item.category, ...(item.tags || []), item.problem, ...variantNames]
      .filter(Boolean).join(" ").toLocaleLowerCase("zh-Hant").includes(query);
  });
  const categories = [...new Set(state.items.map((item) => item.category).filter(Boolean))].sort();
  panel.appendChild(h("datalist", { id: "dance-manager-categories" }, categories.map((category) =>
    h("option", { value: category }))));

  const tabs = h("div", { class: "dance-manager-tabs", role: "tablist", "aria-label": "模板狀態" });
  for (const status of DANCE_LIBRARY_STATUSES) {
    const count = state.items.filter((item) => danceManageStatusMatches(item, status.value)).length;
    const active = state.status === status.value;
    tabs.appendChild(h("button", {
      type: "button",
      role: "tab",
      class: `dance-manager-tab${active ? " active" : ""}`,
      "aria-selected": active ? "true" : "false",
      text: `${status.label} ${count}`,
      onclick: () => {
        state.status = status.value;
        renderDanceLibraryManager();
      },
    }));
  }
  const search = h("input", {
    id: "danceManagerSearch",
    type: "search",
    value: state.query,
    placeholder: "搜尋名稱、分類、標籤或問題",
    "aria-label": "搜尋模板庫",
    oninput: (event) => {
      state.query = event.target.value;
      clearTimeout(danceManagerSearchTimer);
      danceManagerSearchTimer = setTimeout(() => {
        danceManagerSearchTimer = null;
        if (currentRoute.view !== "dance-library") return;
        renderDanceLibraryManager();
        requestAnimationFrame(() => {
          const next = $("danceManagerSearch");
          if (!next) return;
          next.focus({ preventScroll: true });
          if (typeof next.setSelectionRange === "function") next.setSelectionRange(next.value.length, next.value.length);
        });
      }, 120);
    },
  });
  panel.appendChild(h("div", { class: "dance-manager-toolbar" }, [tabs, search]));

  const visibleIds = shown.map((item) => item.id);
  const allVisibleSelected = visibleIds.length > 0 && visibleIds.every((id) => state.selected.has(id));
  const selectAll = h("input", {
    type: "checkbox",
    checked: allVisibleSelected,
    "aria-label": "選取目前顯示的全部模板",
    onchange: (event) => {
      for (const id of visibleIds) {
        if (event.target.checked) state.selected.add(id);
        else state.selected.delete(id);
      }
      renderDanceLibraryManager();
    },
  });
  selectAll.checked = allVisibleSelected;
  const batchStatus = danceManagerStatusSelect("", "批次設定狀態");
  batchStatus.insertBefore(h("option", { value: "", text: "狀態不變", selected: "true" }), batchStatus.firstChild);
  batchStatus.value = "";
  const batchCategory = h("input", {
    type: "text",
    list: "dance-manager-categories",
    placeholder: "分類不變",
    maxlength: "32",
    "aria-label": "批次設定分類",
  });
  const applyBatch = h("button", {
    type: "button",
    class: "primary small",
    text: `套用到 ${state.selected.size} 個`,
    disabled: state.selected.size ? null : "",
  });
  applyBatch.addEventListener("click", async () => {
    const ids = [...state.selected];
    if (!ids.length) return;
    const patch = {};
    if (batchStatus.value) patch.status = batchStatus.value;
    if (batchCategory.value.trim()) patch.category = batchCategory.value.trim();
    if (!Object.keys(patch).length) {
      state.error = "請先選擇要套用的狀態或分類。";
      renderDanceLibraryManager();
      return;
    }
    applyBatch.disabled = true;
    applyBatch.textContent = "套用中…";
    try {
      await updateDanceLibrary({ ids, ...patch }, `已批次更新 ${ids.length} 個模板。`);
      state.selected.clear();
      if (currentRoute.view === "dance-library") renderDanceLibraryManager();
    } catch (error) {
      state.error = error.message;
      if (applyBatch.isConnected) {
        applyBatch.disabled = false;
        applyBatch.textContent = `套用到 ${ids.length} 個`;
      }
      if (currentRoute.view === "dance-library") renderDanceLibraryManager();
    }
  });
  panel.appendChild(h("div", { class: "dance-manager-bulk" }, [
    h("label", { class: "dance-manager-select-all" }, [selectAll, h("span", { text: `選取目前 ${shown.length} 個` })]),
    batchStatus,
    batchCategory,
    applyBatch,
  ]));
  panel.appendChild(h("div", {
    class: "dance-manager-result-count",
    role: "status",
    "aria-live": "polite",
    text: `顯示 ${shown.length} 個 · 已選 ${state.selected.size} 個`,
  }));
  const list = h("div", { class: "dance-manager-list" });
  if (!shown.length) list.appendChild(h("div", { class: "empty", text: "沒有符合目前條件的模板。" }));
  else for (const item of shown) list.appendChild(renderDanceManagerRow(item, categories));
  panel.appendChild(list);
}

function renderAdvancedFields(feature, capability = null) {
  const body = h("div", { class: "advanced-body" });
  for (const rawField of feature.fields || []) {
    const field = capabilityField(feature, capability, rawField);
    if (!field) continue;
    if (feature.id === "dance" && field.id === "mode" && (field.options || []).length === 1) {
      const onlyMode = field.options[0].value;
      fieldGetters[field.id] = () => onlyMode;
      continue;
    }
    body.appendChild(renderField(field));
  }
  return h("details", { class: "advanced mini-advanced" }, [
    h("summary", { text: "進階" }),
    body,
  ]);
}

function uploadSlot(feature, uploadId, labelOverride = "", state = null, onChange = null) {
  const up = (feature.uploads || []).find((item) => item.id === uploadId);
  if (!up) return null;
  const labels = state && state.serverFileLabels ? state.serverFileLabels : {};
  const onFileChange = (slot, resolved = null) => {
    if (state) {
      if (resolved && resolved.serverFile) {
        state.serverFiles = { ...(state.serverFiles || {}), [slot]: resolved.serverFile };
        state.serverFileLabels = { ...(state.serverFileLabels || {}), [slot]: resolved.label || prefilledFileLabel(resolved.serverFile) };
      } else {
        if (state.serverFiles) delete state.serverFiles[slot];
        if (state.serverFileLabels) delete state.serverFileLabels[slot];
      }
    }
    if (typeof onChange === "function") onChange(slot);
  };
  return renderDropzone({ ...up, label: labelOverride || up.label, required: up.required, serverFileLabel: labels[uploadId] || "", onFileChange });
}

function replaceablePhotoSlot(character, feature, slot, state, update, options = {}) {
  const defaultUrl = options.defaultUrl || state.defaultFiles[slot] || "";
  if (!defaultUrl) return renderComposerUploadSlot(feature, slot, options.uploadLabel || "", state, () => update({}));
  const defaultLabel = options.defaultLabel || "使用預設照片";
  let expanded = false;
  const body = h("div", { class: "replaceable-photo-body hidden" });
  const current = h("span", { class: "replaceable-photo-current" });
  const changeBtn = h("button", {
    type: "button",
    class: "ghost small",
    text: "更換",
    onclick: () => {
      expanded = !expanded;
      render();
    },
  });
  const wrap = h("div", { class: "replaceable-photo-slot" }, [
    h("div", { class: "replaceable-photo-summary" }, [current, changeBtn]),
    body,
  ]);

  function clearUploadState() {
    delete serverFiles[slot];
    if (state.serverFiles) delete state.serverFiles[slot];
    if (state.serverFileLabels) delete state.serverFileLabels[slot];
    const input = fileInputs[slot];
    if (input) input.value = "";
  }

  function libraryPhotos() {
    const photos = [];
    const avatar = characterProfilePicUrl(character);
    if (avatar) photos.push({ url: avatar, label: "角色頭像" });
    if (characterType(character) === "experimental") {
      const media = character.assets && Array.isArray(character.assets.characterMedia) ? character.assets.characterMedia : [];
      for (const item of media) {
        if (item.url && item.filename) photos.push({ url: item.url, label: item.label || "實驗照片", userPhoto: item.filename });
      }
    }
    for (const work of worksForCharacter(character.id).filter((item) => !item.hidden && item.kind !== "video" && item.kind !== "audio").slice(0, 24)) {
      if (work.url) photos.push({ url: work.url, label: work.filename || "素材庫照片" });
    }
    return photos;
  }

  function applyDefault() {
    clearUploadState();
    state.defaultFiles[slot] = defaultUrl;
    if (!state.libraryPicks || typeof state.libraryPicks !== "object") state.libraryPicks = {};
    delete state.libraryPicks[slot];
    if (!state.userPhotoPicks || typeof state.userPhotoPicks !== "object") state.userPhotoPicks = {};
    delete state.userPhotoPicks[slot];
    expanded = false;
    update({
      libraryPicks: { ...state.libraryPicks },
      userPhotoPicks: { ...state.userPhotoPicks },
      serverFiles: { ...(state.serverFiles || {}) },
      serverFileLabels: { ...(state.serverFileLabels || {}) },
    });
    render();
  }

  function applyLibrary(photo) {
    if (!photo || !photo.url) return;
    clearUploadState();
    state.defaultFiles[slot] = photo.url;
    if (!state.libraryPicks || typeof state.libraryPicks !== "object") state.libraryPicks = {};
    if (!state.userPhotoPicks || typeof state.userPhotoPicks !== "object") state.userPhotoPicks = {};
    state.libraryPicks[slot] = photo.url;
    if (photo.userPhoto) state.userPhotoPicks[slot] = photo.userPhoto;
    else delete state.userPhotoPicks[slot];
    expanded = false;
    update({
      libraryPicks: { ...state.libraryPicks },
      userPhotoPicks: { ...state.userPhotoPicks },
      serverFiles: { ...(state.serverFiles || {}) },
      serverFileLabels: { ...(state.serverFileLabels || {}) },
    });
    render();
  }

  function uploadChanged() {
    if (!state.libraryPicks || typeof state.libraryPicks !== "object") state.libraryPicks = {};
    delete state.libraryPicks[slot];
    if (state.userPhotoPicks && typeof state.userPhotoPicks === "object") delete state.userPhotoPicks[slot];
    delete state.defaultFiles[slot];
    update({ libraryPicks: { ...state.libraryPicks }, userPhotoPicks: { ...(state.userPhotoPicks || {}) } });
    current.textContent = currentLabel();
  }

  function currentLabel() {
    const input = fileInputs[slot];
    const file = input && input.files && input.files[0];
    if (file) return `已選上傳照片：${file.name}`;
    if (serverFiles[slot]) return `已選上傳照片：${(state.serverFileLabels || {})[slot] || prefilledFileLabel(serverFiles[slot])}`;
    if (state.libraryPicks && state.libraryPicks[slot]) return "已選素材庫照片";
    return defaultLabel;
  }

  function render() {
    current.textContent = currentLabel();
    body.classList.toggle("hidden", !expanded);
    clear(body);
    if (!expanded) return;
    const grid = h("div", { class: "replaceable-photo-grid" });
    const photos = libraryPhotos();
    for (const photo of photos) {
      grid.appendChild(h("button", {
        type: "button",
        class: `replaceable-photo-tile${state.defaultFiles[slot] === photo.url ? " active" : ""}`,
        "aria-label": `選擇${photo.label}`,
        onclick: () => applyLibrary(photo),
      }, [
        h("img", { src: photo.url, alt: "", loading: "lazy" }),
        h("span", { text: photo.label }),
      ]));
    }
    body.appendChild(h("div", { class: "replaceable-photo-choice" }, [
      h("div", { class: "method-tag", text: "上傳照片" }),
      renderComposerUploadSlot(feature, slot, options.uploadLabel || "", state, uploadChanged),
    ]));
    body.appendChild(h("div", { class: "replaceable-photo-choice" }, [
      h("div", { class: "method-tag", text: "從素材庫挑" }),
      photos.length ? grid : h("div", { class: "empty compact", text: "素材庫目前沒有可用照片" }),
    ]));
    body.appendChild(h("button", { type: "button", class: "ghost small", text: "用回預設", onclick: applyDefault }));
  }

  render();
  return wrap;
}

function buildMinimalSettings(actionId, feature, state, advanced) {
  const settings = { ...advanced };
  const boundAnimeSceneCard = actionId === "photoshoot"
    && (state.serverBoundUploads || []).includes("image")
    && (state.serverBoundUploads || []).includes("scene");
  if (["photoshoot", "group-photo", "multi-angle-selfie", "image-video", "dance"].includes(actionId) && state.quality) {
    settings.quality = state.quality;
  }
  if (actionId === "image-video") {
    if (state.motionScale) settings.motionScale = state.motionScale;
    if (state.duration) settings.duration = state.duration;
  }
  if (["h3-image-video", "h3-reference-video"].includes(actionId)) {
    settings.description = state.description || "";
    settings.aspect = state.aspect || "";
    settings.motionProfile = state.motionProfile || "";
    settings.quality = "quality";
  }
  if (actionId === "post" && state.postLength) settings.postLength = state.postLength;
  if (fieldById(feature, "aspect") && state.aspect) setAspectSettings(settings, state.aspect);
  if (state.outfit || state.outfitLabel) {
    const outfitText = state.outfitLabel || state.outfit;
    if (!boundAnimeSceneCard) {
      settings.positive = [outfitText ? `outfit: ${outfitText}` : "", settings.positive || ""].filter(Boolean).join(", ");
    }
    settings.wardrobe = {
      theme: state.outfit || "",
      label: state.outfitLabel || state.outfit || "",
      source: state.outfitUrl ? "character-wardrobe" : "text",
    };
    settings.wardrobeTheme = state.outfit || "";
  }
  if (actionId === "photoshoot" && boundAnimeSceneCard) {
    delete settings.positive;
    delete settings.negative;
    settings.sceneTheme = state.sceneTheme || "";
    settings.posePreset = "original";
    settings.quality = "standard";
  } else if (actionId === "photoshoot") {
    settings.positive = [settings.positive, state.prompt].filter(Boolean).join(", ");
  }
  if (actionId === "group-photo") settings.positive = state.vibe || settings.positive || "";
  if (actionId === "multi-angle-selfie") settings.positive = [state.expression ? `${state.expression} expression` : "", settings.positive || ""].filter(Boolean).join(", ");
  if (actionId === "image-video") {
    settings.positive = state.motion || settings.positive || "";
    settings.motionPreset = state.motionPreset || "idle";
  }
  if (actionId === "dance") {
    settings.mode = state.mode || "animate";
    settings.positive = state.description || settings.positive || "";
  }
  if (actionId === "voice-speak") {
    settings.voiceSource = state.voiceSource || "persona";
    settings.text = state.text || "";
    settings.persona = state.persona || settings.persona || "";
    settings.voiceId = state.voiceId || settings.voiceId || "";
  }
  if (actionId === "lipsync") {
    settings.voiceId = state.voiceId || settings.voiceId || "";
    settings.mode = state.mode || settings.mode || "sequential";
  }
  if (actionId === "post") {
    settings.mode = "advanced";
    settings.topic = state.topic || "";
    settings.platform = state.platform || "threads";
    settings.length = state.postLength || "medium";
    settings.tone = settings.tone || "warm";
  }
  return settings;
}

function collectAdvancedSettings(feature) {
  if (!document.querySelector(".minimal-form details.advanced[open]")) return {};
  const settings = {};
  for (const field of feature.fields || []) {
    const get = fieldGetters[field.id];
    if (!get) continue;
    const value = get();
    if (field.type === "aspect") setAspectSettings(settings, value);
    else settings[field.id] = value;
  }
  return settings;
}

async function collectMinimalFiles(feature, state) {
  const session = captureSessionContext();
  const files = {};
  for (const up of feature.uploads || []) {
    if ((state.serverBoundUploads || []).includes(up.id)) continue;
    if (feature.id === "lipsync" && up.id === "voice" && state.voiceSource !== "upload") continue;
    if (feature.id === "dance" && up.id === "video" && state.danceTemplateId && state.__danceTemplateValidated === true) {
      const imported = await importSelectedDanceTemplate(state.danceTemplateId, up.id);
      assertSessionContext(session);
      if (state.__draftRetired) throw new Error("已離開原本編輯畫面，這次沒有送出");
      files[up.id] = { serverFile: imported.serverFile };
      continue;
    }
    const hasUserPhoto = Boolean(state.userPhotoPicks && state.userPhotoPicks[up.id]);
    if (!slotHasFile(up.id, state) && !hasUserPhoto) {
      if (up.required) throw new Error(`請提供「${up.label}」`);
      continue;
    }
    const resolved = await ensureSlotServerFile(feature, state, up.id);
    assertSessionContext(session);
    files[up.id] = { serverFile: resolved.serverFile };
  }
  return files;
}

async function importUserPhoto(characterId, filename, slot) {
  const data = await api(`/api/characters/${encodeURIComponent(characterId)}/user-photo-import/${encodeURIComponent(filename)}`, {
    method: "POST",
    body: JSON.stringify({ slot }),
  });
  return { serverFile: data.filename, label: data.name || filename || data.filename };
}

async function uploadInputFile(file, slot) {
  const session = captureSessionContext();
  const params = new URLSearchParams({ raw: "1", slot });
  const res = await fetch(`/api/files/upload-input?${params}`, {
    method: "POST",
    headers: {
      "Content-Type": file.type || "application/octet-stream",
      "X-Castle-Filename": encodeURIComponent(file.name || `${slot}.bin`),
    },
    body: file,
  });
  assertSessionContext(session);
  let data = {};
  try {
    data = await res.json();
  } catch (_) {
    /* non-JSON */
  }
  assertSessionContext(session);
  if (res.status === 401) {
    resetSessionUi();
    showAuth("login");
  }
  if (!res.ok || data.ok === false) throw new Error(data.error || `HTTP ${res.status}`);
  if (!data.filename) throw new Error("上傳回應缺少檔名，請重試");
  return { serverFile: data.filename, label: data.name || file.name || data.filename };
}

function assertCurrentSlotInput(slot, boundInput) {
  if (!boundInput || fileInputs[slot] !== boundInput || !boundInput.isConnected) {
    throw new Error("編輯畫面已更新，這次沒有送出");
  }
}

function rememberSlotServerFile(state, slot, resolved, boundInput) {
  assertCurrentSlotInput(slot, boundInput);
  if (state && state.__draftRetired) throw new Error("已離開原本編輯畫面，這次沒有送出");
  serverFiles[slot] = resolved.serverFile;
  if (state) {
    state.serverFiles = { ...(state.serverFiles || {}), [slot]: resolved.serverFile };
    state.serverFileLabels = { ...(state.serverFileLabels || {}), [slot]: resolved.label || prefilledFileLabel(resolved.serverFile) };
    if (typeof state.__saveDraftNow === "function" && !state.__draftRetired) {
      state.__draftDirty = true;
      state.__saveDraftNow();
    }
  }
  boundInput.value = "";
  return resolved;
}

async function ensureSlotServerFile(feature, state, slot) {
  const session = captureSessionContext();
  const input = fileInputs[slot] || null;
  assertCurrentSlotInput(slot, input);
  const remembered = serverFiles[slot] || (state.serverFiles && state.serverFiles[slot]);
  if (remembered) {
    return rememberSlotServerFile(state, slot, {
      serverFile: remembered,
      label: (state.serverFileLabels || {})[slot] || prefilledFileLabel(remembered),
    }, input);
  }
  const file = input && input.files && input.files[0];
  if (file) {
    const uploaded = await uploadInputFile(file, slot);
    assertSessionContext(session);
    return rememberSlotServerFile(state, slot, uploaded, input);
  }
  if (state.userPhotoPicks && state.userPhotoPicks[slot]) {
    const imported = await importUserPhoto(activeCharacterId || selectedCharacterId, state.userPhotoPicks[slot], slot);
    assertSessionContext(session);
    return rememberSlotServerFile(state, slot, imported, input);
  }
  if (state.defaultFiles && state.defaultFiles[slot]) {
    const defaultFile = await fileFromDefaultUrl(state.defaultFiles[slot], `${slot}.png`);
    assertSessionContext(session);
    assertCurrentSlotInput(slot, input);
    const uploaded = await uploadInputFile(defaultFile, slot);
    assertSessionContext(session);
    return rememberSlotServerFile(state, slot, uploaded, input);
  }
  const up = (feature.uploads || []).find((item) => item.id === slot);
  throw new Error(`請提供「${(up && up.label) || slot}」`);
}

async function fileFromDefaultUrl(url, name) {
  const session = captureSessionContext();
  const res = await fetch(url);
  assertSessionContext(session);
  if (!res.ok) throw new Error("預設素材讀取失敗");
  const blob = await res.blob();
  assertSessionContext(session);
  return new File([blob], name, { type: blob.type || "application/octet-stream" });
}

function validUploadIds(feature) {
  return new Set((feature && feature.uploads || []).map((up) => up.id));
}

function slotHasFile(slot, state) {
  const input = fileInputs[slot];
  return Boolean(
    (input && input.files && input.files[0])
    || serverFiles[slot]
    || (state.defaultFiles && state.defaultFiles[slot])
    || (slot === "video" && state.danceTemplateId && state.__danceTemplateValidated === true)
  );
}

function requiredUploadSlots(actionId, state, feature) {
  const serverBound = new Set(state.serverBoundUploads || []);
  const uploads = (feature && feature.uploads || []).filter((up) => up.required && !serverBound.has(up.id));
  if (actionId !== "lipsync") return uploads;
  return uploads.filter((up) => up.id !== "voice" || state.voiceSource === "upload");
}

function missingRequiredUpload(actionId, state, feature) {
  return requiredUploadSlots(actionId, state, feature).find((up) => !slotHasFile(up.id, state)) || null;
}

function clearLipsyncVoiceFile(state) {
  delete serverFiles.voice;
  const input = fileInputs.voice;
  if (input) input.value = "";
  if (state.serverFiles) delete state.serverFiles.voice;
  if (state.serverFileLabels) delete state.serverFileLabels.voice;
}

function normalizeLipsyncVoiceState(state, source) {
  state.voiceSource = source || "library";
  clearLipsyncVoiceFile(state);
  if (state.voiceSource !== "tts") state.ttsText = "";
  if (state.voiceSource !== "library") state.voiceId = "";
}

function updateRequiredDropzones(form, actionId, state, feature) {
  if (!form) return;
  for (const up of feature.uploads || []) {
    const node = form.querySelector(`[data-upload-id="${up.id}"]`);
    if (!node) continue;
    const required = requiredUploadSlots(actionId, state, feature).some((item) => item.id === up.id);
    const empty = required && !slotHasFile(up.id, state);
    node.classList.toggle("required-empty", empty);
    let hint = node.querySelector(".required-hint");
    if (empty && !hint) {
      hint = h("span", { class: "required-hint", text: `請先提供「${up.label}」` });
      node.appendChild(hint);
    } else if (!empty && hint) {
      hint.remove();
    }
  }
}

function reasonForAction(actionId, state, feature) {
  if (!navigator.onLine) return "目前離線，請重新連線後再生成";
  if (!feature || !interactiveReady(feature)) return featureUnavailableReason(feature);
  const needsWardrobe = (
    (actionId === "photoshoot" && (state.serverBoundUploads || []).includes("image"))
    || (actionId === "image-video" && (state.serverBoundUploads || []).includes("image"))
    || (actionId === "dance" && (state.serverBoundUploads || []).includes("reference"))
  );
  if (needsWardrobe && !String(state.outfit || "").trim()) {
    return "請先選擇角色造型";
  }
  if (actionId === "photoshoot" && (state.serverBoundUploads || []).includes("scene") && !String(state.sceneTheme || "").trim()) {
    return "請先選擇動漫場景";
  }
  if (actionId === "image-video" && !String(state.motionPreset || "").trim()) return "目前沒有可用的動作選項";
  if (["h3-image-video", "h3-reference-video"].includes(actionId)) {
    if (!String(state.description || "").trim()) return "請描述角色要做什麼、鏡頭或聲音";
    if (!String(state.aspect || "").trim() || !String(state.motionProfile || "").trim()) {
      return "目前沒有符合這種角色類型的 H3 選項";
    }
  }
  if (actionId === "dance" && !String(state.mode || "").trim()) return "目前沒有可用的舞蹈模式";
  const missingUpload = missingRequiredUpload(actionId, state, feature);
  if (missingUpload) return `請先提供「${missingUpload.label}」`;
  if (actionId === "voice-speak" && !String(state.text || "").trim()) return "請輸入想說的話";
  if (actionId === "post" && !String(state.topic || "").trim()) return "請輸入主題";
  if (actionId === "lipsync") {
    if (!String(state.mode || "").trim()) return "目前沒有可用的對嘴影片模式";
    if (!String(state.voiceSource || "").trim()) return "目前沒有可用的對嘴聲音來源";
    if (state.voiceSource === "tts" && !String(state.ttsText || "").trim()) return "請輸入要讓角色說的話";
    if (state.voiceSource === "library" && !String(state.voiceId || "").trim()) return "請先選擇「素材庫音檔」";
  }
  if (actionId === "post" && (!String(state.platform || "").trim() || !String(state.postLength || "").trim())) {
    return "目前沒有可用的貼文平台或長度選項";
  }
  return "";
}

const GENERATION_STATE_RANK = { disabled: 0, ready: 0, submitting: 1, queued: 2, progress: 3, done: 4, error: 4 };

function composerKey(character, action) {
  return `${character.id}:${action.id}`;
}

function setGenerationFsm(nextState, patch = {}, options = {}) {
  const promptId = patch.promptId || "";
  const targetComposerKey = patch.composerKey || generationFsm.composerKey || "";
  if (composerBinding && targetComposerKey && targetComposerKey !== composerBinding.key) return false;
  if (promptId && trackedPromptId && promptId !== trackedPromptId) return false;
  const currentRank = GENERATION_STATE_RANK[generationFsm.state] || 0;
  const nextRank = GENERATION_STATE_RANK[nextState] || 0;
  if (!options.force && nextRank < currentRank) return false;
  if (!options.force && (generationFsm.state === "done" || generationFsm.state === "error") && nextState !== generationFsm.state) return false;
  const announceDone = nextState === "done" && generationFsm.state !== "done";
  generationFsm = { ...generationFsm, ...patch, composerKey: targetComposerKey, state: nextState };
  renderStickyGenerate();
  if (announceDone) showToast("完成");
  return true;
}

function showToast(message) {
  document.querySelectorAll(".app-toast").forEach((node) => node.remove());
  const toast = h("div", { class: "app-toast", role: "status", text: message });
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 2800);
}

function prepareComposerFields(form, character, action) {
  form.querySelectorAll("input, textarea, select").forEach((field, index) => {
    if (!field.id) field.id = `composer-${character.id}-${action.id}-${index}`;
  });
}

function updateGenerateReadiness(binding = composerBinding) {
  if (!binding || binding !== composerBinding || submitInFlight || trackedPromptId || ["done", "error"].includes(generationFsm.state)) return;
  if (generationFsm.composerKey && generationFsm.composerKey !== binding.key) return;
  const reason = typeof binding.reasonForSubmit === "function"
    ? binding.reasonForSubmit()
    : reasonForAction(binding.action.id, binding.state, binding.feature);
  setGenerationFsm(reason ? "disabled" : "ready", { reason, detail: reason, pct: 0, composerKey: binding.key }, { force: true });
}

function bindGenerateComposer(character, action, feature, capability, state, form, saveDraftNow, draftNotice = null, options = {}) {
  const key = composerKey(character, action);
  const keyChanged = generationFsm.composerKey !== key;
  if (keyChanged) {
    trackedPromptId = "";
    generationFsm = { state: "ready", reason: "", detail: "", pct: 0, resultPromptId: "", postResult: null, composerKey: key };
  }
  prepareComposerFields(form, character, action);
  composerBinding = {
    key, character, action, feature, capability, state, form, draftNotice,
    reasonForSubmit: typeof options.reasonForSubmit === "function" ? options.reasonForSubmit : null,
  };
  draftFlushNow = saveDraftNow;
  document.body.classList.add("has-character-composer");
  if (keyChanged) {
    const reason = composerBinding.reasonForSubmit
      ? composerBinding.reasonForSubmit()
      : reasonForAction(action.id, state, feature);
    setGenerationFsm(reason ? "disabled" : "ready", { reason, detail: reason, pct: 0, composerKey: key }, { force: true });
  } else if (!trackedPromptId && !submitInFlight) updateGenerateReadiness(composerBinding);
  else renderStickyGenerate();
}

function retireBoundComposerDraft({ flush = true } = {}) {
  const state = composerBinding && composerBinding.state;
  if (state && typeof state.__clearSaveTimer === "function") state.__clearSaveTimer();
  if (flush && typeof draftFlushNow === "function") draftFlushNow();
  if (state) {
    state.__draftCleared = true;
    state.__draftRetired = true;
  }
  draftFlushNow = null;
}

function unbindGenerateComposer() {
  retireBoundComposerDraft({ flush: true });
  composerBinding = null;
  trackedPromptId = "";
  generationFsm = { state: "disabled", reason: "", detail: "", pct: 0, resultPromptId: "", postResult: null, composerKey: "" };
  document.body.classList.remove("has-character-composer");
  renderStickyGenerate();
}

function requestCurrentGeneration() {
  if (!composerBinding || submitInFlight || generationFsm.composerKey !== composerBinding.key) return;
  composerBinding.form.requestSubmit();
}

function viewTrackedResult() {
  if (!composerBinding || generationFsm.composerKey !== composerBinding.key) return;
  if (generationFsm.postResult) {
    const character = composerBinding.character;
    openResultDetail({
      ...generationFsm.postResult,
      kind: "text",
      topic: composerBinding.state.topic || "貼文",
      ts: Date.now() / 1000,
      character: { id: character.id, name: character.name },
    }, document.activeElement, { character });
    return;
  }
  const promptId = generationFsm.resultPromptId || trackedPromptId;
  const item = historyItems.find((entry) => entry.promptId === promptId);
  if (item) openHistoryItem(item);
  else {
    showAlert("結果仍在同步，請稍後從「任務」查看。", true);
    openTaskTray($("statusPill"));
  }
}

function resetComposerForAnother() {
  if (!composerBinding) return;
  const { character, action, capability, feature } = composerBinding;
  trackedPromptId = "";
  retireBoundComposerDraft({ flush: true });
  const hasNewerDraft = loadDraftSnapshot(character.id, action.id, capability, feature).restored;
  if (!hasNewerDraft) clearDraft(character.id, action.id);
  generationFsm = { state: "ready", reason: "", detail: "", pct: 0, resultPromptId: "", postResult: null, composerKey: composerBinding.key };
  renderCurrentComposerRoute();
  if (hasNewerDraft) showToast("已保留你剛修改的內容");
}

function startComposerFromBlank(character, action, state) {
  if (submitInFlight) {
    showAlert("正在送出，完成後再清除內容。", true);
    return;
  }
  if (!window.confirm("清除這個動作保留的文字與選項，並從空白開始？這無法復原。")) return;
  if (!clearDraft(character.id, action.id)) {
    showAlert("無法清除保留內容，請確認瀏覽器允許網站儲存資料。", true);
    return;
  }
  if (composerBinding && composerBinding.state === state) retireBoundComposerDraft({ flush: false });
  else if (state && typeof state.__clearSaveTimer === "function") state.__clearSaveTimer();
  if (state) {
    state.__draftCleared = true;
    state.__draftDirty = false;
    state.__draftRetired = true;
  }
  bumpDraftRevision(character.id, action.id);
  trackedPromptId = "";
  generationFsm = {
    state: "ready",
    reason: "",
    detail: "",
    pct: 0,
    resultPromptId: "",
    postResult: null,
    composerKey: composerKey(character, action),
  };
  renderCurrentComposerRoute();
  showAlert("");
  showToast("已清除保留內容");
}

function closeGenerationError() {
  trackedPromptId = "";
  generationFsm = { state: "ready", reason: "", detail: "", pct: 0, resultPromptId: "", postResult: null, composerKey: composerBinding ? composerBinding.key : "" };
  updateGenerateReadiness();
}

function renderStickyGenerate() {
  const host = $("stickyGenerateHost");
  if (!host) return;
  clear(host);
  const visible = Boolean(
    composerBinding
    && ["character-workspace", "provider-character-studio"].includes(currentRoute.view)
    && generationFsm.composerKey === composerBinding.key
  );
  host.classList.toggle("hidden", !visible);
  if (!visible) return;
  const state = generationFsm.state;
  const waitingForOtherSubmit = submitInFlight && ["ready", "disabled"].includes(state);
  const displayState = waitingForOtherSubmit ? "submitting" : state;
  const actions = h("div", { class: "generation-actions" });
  let detail = generationFsm.detail || generationFsm.reason || "";
  if (waitingForOtherSubmit) {
    detail = "上一項正在送出，完成後即可繼續。";
    actions.appendChild(h("button", { class: "primary generation-primary submitting", type: "button", text: "上一項送出中…", disabled: true }));
  } else if (state === "disabled" || state === "ready") {
    actions.appendChild(h("button", { class: "primary generation-primary", type: "button", text: "生成", disabled: state === "disabled", onclick: requestCurrentGeneration }));
    if (
      state === "ready"
      && composerBinding
      && h3ProviderIntent(
        composerBinding.character,
        composerBinding.capability,
        composerBinding.state,
      )
    ) {
      actions.appendChild(h("button", {
        class: "ghost generation-secondary",
        type: "button",
        text: "查看資源規劃",
        onclick: () => openH3ProviderPlan(
          composerBinding.character,
          composerBinding.capability,
          composerBinding.state,
        ),
      }));
    }
  } else if (state === "submitting") {
    actions.appendChild(h("button", { class: "primary generation-primary submitting", type: "button", text: "送出中…", disabled: true }));
  } else if (state === "queued" || state === "progress") {
    actions.appendChild(h("button", { class: "primary generation-primary", type: "button", text: "生成中…", disabled: true }));
  } else if (state === "done") {
    actions.appendChild(h("button", { class: "primary generation-primary", type: "button", text: "查看結果", onclick: viewTrackedResult }));
    actions.appendChild(h("button", { class: "ghost generation-secondary", type: "button", text: "再做一個", onclick: resetComposerForAnother }));
    detail = detail || "完成";
  } else if (state === "error") {
    actions.appendChild(h("button", { class: "primary generation-primary", type: "button", text: "重試", onclick: requestCurrentGeneration }));
    actions.appendChild(h("button", { class: "ghost generation-secondary", type: "button", text: "關閉", onclick: closeGenerationError }));
  }
  const status = h("div", { class: `generation-status ${displayState}`, role: state === "error" ? "alert" : "status" }, [
    detail ? h("span", { class: "generation-detail", text: detail }) : null,
  ]);
  if (state === "error") {
    const technical = technicalDetailsDisclosure(generationFsm.technicalDetail);
    if (technical) status.appendChild(technical);
  }
  if (state === "queued" || state === "progress") {
    const pct = state === "progress" ? Math.max(0, Math.min(100, Number(generationFsm.pct || 0))) : 0;
    status.appendChild(h("div", {
      class: `generation-progress${state === "queued" ? " indeterminate" : ""}`,
      role: "progressbar",
      "aria-label": state === "queued" ? "等待中" : `執行中 ${pct}%`,
      "aria-valuemin": "0",
      "aria-valuemax": "100",
      "aria-valuenow": state === "progress" ? String(pct) : null,
    }, [h("span", { style: state === "progress" ? `width:${pct}%` : "" })]));
  }
  host.appendChild(h("div", { class: `sticky-generate-bar ${displayState}` }, [status, actions]));
}

function renderUnavailableComposer(message) {
  if (composerBinding) unbindGenerateComposer();
  return h("section", { class: "panel" }, [
    h("div", { class: "empty compact", text: message }),
  ]);
}

function renderMinimalActionPanel(character, action) {
  resetGetters();
  const capability = enabledActionCapability(character, action && action.id);
  const feature = actionFeature(action, character);
  if (!capability || !feature) return renderUnavailableComposer("這個角色不能使用這個動作。");
  if (composerBinding) retireBoundComposerDraft({ flush: true });
  const draftSnapshot = loadDraftSnapshot(character.id, action.id, capability, feature);
  const draft = draftSnapshot.state;
  const state = {
    quality: "standard",
    aspect: fieldDefault(feature, "aspect", "1088x1920"),
    motionPreset: "idle",
    motionProfile: "",
    description: "",
    motionScale: "natural",
    duration: "3s",
    sceneTheme: "tech",
    posePreset: "original",
    postLength: "medium",
    platform: "threads",
    voiceSource: "persona",
    mode: "sequential",
    defaultFiles: {},
    libraryPicks: {},
    userPhotoPicks: {},
    serverFiles: {},
    serverFileLabels: {},
    ...draft,
  };
  state.__draftCleared = false;
  state.__draftDirty = false;
  state.__draftRetired = false;
  // This is a server-owned capability contract. Never restore it from a
  // browser draft, where an older or edited value could expose the wrong slot.
  state.serverBoundUploads = Array.isArray(capability.serverBoundUploads)
    ? [...capability.serverBoundUploads]
    : [];
  const expressionOptions = [
    { value: "自然", label: "自然" }, { value: "開心", label: "開心" }, { value: "耍酷", label: "耍酷" },
    { value: "害羞", label: "害羞" }, { value: "驚訝", label: "驚訝" },
  ];
  const motionScaleOptions = [
    { value: "subtle", label: "輕微" }, { value: "natural", label: "自然" }, { value: "bold", label: "誇張" },
  ];
  const motionPresetOptions = [
    { value: "idle", label: "自然待機", sub: "輕微呼吸、眨眼，髮絲與衣服微動" },
    { value: "smile", label: "眨眼微笑", sub: "自然眨眼後輕輕微笑" },
  ];
  const durationOptions = [
    { value: "3s", label: "約 3 秒" },
  ];
  const rawDanceModeOptions = [
    { value: "animate", label: "動畫模式(AI 生成背景,比例跟參考圖)" },
  ];
  const danceModeOptions = characterVisualClass(character) === "anime"
    ? rawDanceModeOptions.filter((option) => option.value === "animate")
    : rawDanceModeOptions;
  const rawVoiceSourceOptions = [
    { value: "tts", label: "讓角色說這段話", sub: "先生成語音，再自動對嘴" },
    { value: "library", label: "素材庫音檔" },
    { value: "upload", label: "上傳" },
  ];
  const voiceSourceOptions = enabledActionCapability(character, "voice-speak")
    ? rawVoiceSourceOptions
    : rawVoiceSourceOptions.filter((option) => option.value !== "tts");
  const lipsyncModeOptions = ((fieldById(feature, "mode") || {}).options || []).map((option) => ({
    value: String(option.value),
    label: option.label || String(option.value),
    ...(option.sub ? { sub: option.sub } : {}),
  }));
  const postLengthOptions = [
    { value: "short", label: "短" }, { value: "medium", label: "中" }, { value: "long", label: "長" },
  ];
  const h3AspectOptions = [...(allowedOptionValues(capability, "aspect") || [])].map((value) => ({
    value, label: { portrait: "直式", landscape: "橫式", square: "方形" }[value] || value,
  }));
  const h3MotionOptions = [...(allowedOptionValues(capability, "motionProfile") || [])].map((value) => ({
    value, label: {
      gentle: "輕柔自然", expressive: "表情與動作明顯",
      natural: "真人自然動作", presenter: "真人講解／主持",
    }[value] || value,
  }));
  const qualityChoice = normalizeCapabilityChoice(capability, "quality", qualityOptions(), state.quality, "standard");
  const aspectChoice = normalizeCapabilityChoice(capability, "aspect", aspectOptions(feature, capability), state.aspect, "");
  const expressionChoice = normalizeCapabilityChoice(capability, "expression", expressionOptions, state.expression || "自然", "自然");
  const motionPresetChoice = normalizeCapabilityChoice(capability, "motionPreset", motionPresetOptions, state.motionPreset, "idle");
  const motionScaleChoice = normalizeCapabilityChoice(capability, "motionScale", motionScaleOptions, state.motionScale, "natural");
  const durationChoice = normalizeCapabilityChoice(capability, "duration", durationOptions, state.duration, "3s");
  const sceneThemeChoice = normalizeCapabilityChoice(capability, "sceneTheme", ANIME_SCENE_OPTIONS, state.sceneTheme, "tech");
  const danceModeChoice = normalizeCapabilityChoice(capability, "mode", danceModeOptions, state.mode || "animate", "animate");
  const lipsyncModeChoice = normalizeCapabilityChoice(capability, "mode", lipsyncModeOptions, state.mode || "sequential", "sequential");
  const voiceSourceChoice = normalizeCapabilityChoice(capability, "voiceSource", voiceSourceOptions, state.voiceSource || "library", "library");
  const platformChoice = normalizeCapabilityChoice(capability, "platform", platformOptions(), state.platform, "threads");
  const postLengthChoice = normalizeCapabilityChoice(capability, ["postLength", "length"], postLengthOptions, state.postLength, "medium");
  const h3AspectChoice = normalizeCapabilityChoice(capability, "aspect", h3AspectOptions, state.aspect, "");
  const h3MotionChoice = normalizeCapabilityChoice(capability, "motionProfile", h3MotionOptions, state.motionProfile, "");
  state.quality = qualityChoice.value;
  state.aspect = aspectChoice.value;
  state.expression = expressionChoice.value;
  state.motionPreset = motionPresetChoice.value;
  state.motionScale = motionScaleChoice.value;
  state.duration = durationChoice.value;
  state.sceneTheme = sceneThemeChoice.value;
  if (["h3-image-video", "h3-reference-video"].includes(action.id)) {
    state.aspect = h3AspectChoice.value;
    state.motionProfile = h3MotionChoice.value;
    state.quality = "quality";
  }
  if (action.id === "dance") state.mode = danceModeChoice.value;
  if (action.id === "lipsync") state.mode = lipsyncModeChoice.value;
  if (action.id === "lipsync") state.voiceSource = voiceSourceChoice.value;
  if (action.id === "post") {
    state.platform = platformChoice.value;
    state.postLength = postLengthChoice.value;
  }
  if (capability.variantId === "photoshoot.anime.scene-card.v1") {
    state.prompt = "";
    state.posePreset = "original";
  }
  if (!state.libraryPicks || typeof state.libraryPicks !== "object") state.libraryPicks = {};
  if (!state.userPhotoPicks || typeof state.userPhotoPicks !== "object") state.userPhotoPicks = {};
  if (action.id === "dance" && !danceModeChoice.options.length) {
    return renderUnavailableComposer("這個角色目前沒有可用的舞蹈模式。");
  }
  if (action.id === "image-video" && !motionPresetChoice.options.length) {
    return renderUnavailableComposer("這個角色目前沒有與影片模組相符的動作選項。");
  }
  if (action.id === "lipsync" && !lipsyncModeChoice.options.length) {
    return renderUnavailableComposer("這個角色目前沒有與對嘴模組相符的影片模式。");
  }
  if (action.id === "lipsync" && !voiceSourceChoice.options.length) {
    return renderUnavailableComposer("這個角色目前沒有可用的對嘴聲音來源。");
  }
  if (action.id === "post" && (!platformChoice.options.length || !postLengthChoice.options.length)) {
    return renderUnavailableComposer("這個角色目前沒有與貼文模組相符的平台或長度選項。");
  }
  if (["h3-image-video", "h3-reference-video"].includes(action.id)
      && (!h3AspectChoice.options.length || !h3MotionChoice.options.length)) {
    return renderUnavailableComposer("這個角色目前沒有與 H3 類型相符的畫面或動作選項。");
  }
  if (state.serverFiles && typeof state.serverFiles === "object") {
    const validSlots = validUploadIds(feature);
    for (const [slot, value] of Object.entries(state.serverFiles)) {
      if (validSlots.has(slot)) serverFiles[slot] = value;
    }
  }
  if (action.id === "lipsync" && (state.voiceSource || "library") !== "upload") clearLipsyncVoiceFile(state);
  if (action.id === "group-photo" && !serverFiles.image1 && characterProfilePicUrl(character)) state.defaultFiles.image1 = characterProfilePicUrl(character);
  if (
    action.id === "dance"
    && !serverFiles.reference
    && characterProfilePicUrl(character)
    && !capabilityServerBindsUpload(capability, "reference")
  ) {
    state.defaultFiles.reference = characterProfilePicUrl(character);
  }
  if (action.id === "multi-angle-selfie" && characterProfilePicUrl(character)) state.defaultFiles.face = characterProfilePicUrl(character);
  const newest = newestPhotoWork(character.id);
  if (action.id === "image-video" && newest && !serverFiles.image && !capabilityServerBindsUpload(capability, "image")) {
    state.defaultFiles.image = newest.url;
  }
  for (const [slot, url] of Object.entries(state.libraryPicks)) {
    if (typeof url === "string" && url) state.defaultFiles[slot] = url;
  }
  let saveTimer = null;
  let formWrap = null;
  let form = null;
  let lipsyncSourcePanels = {};
  const clearSaveTimer = () => {
    clearTimeout(saveTimer);
    saveTimer = null;
  };
  state.__clearSaveTimer = clearSaveTimer;
  const refreshSubmitState = () => {
    if (!formWrap) return;
    if (form) updateRequiredDropzones(form, action.id, state, feature);
    if (composerBinding && composerBinding.state === state) updateGenerateReadiness(composerBinding);
  };
  const refreshLipsyncSourcePanels = () => {
    for (const [source, node] of Object.entries(lipsyncSourcePanels)) {
      node.classList.toggle("hidden", source !== state.voiceSource);
    }
  };
  const draftStateForSave = () => {
    const {
      defaultFiles,
      serverBoundUploads,
      __clearSaveTimer,
      __saveDraftNow,
      __draftCleared,
      __draftDirty,
      __draftRetired,
      __draftRevision,
      __danceTemplateValidated,
      ...draftState
    } = state;
    return draftState;
  };
  const saveDraftNow = () => {
    if (state.__draftDirty && !state.__draftCleared && !state.__draftRetired) {
      if (saveDraft(character.id, action.id, capability, draftStateForSave(), feature)) state.__draftDirty = false;
    }
  };
  state.__saveDraftNow = saveDraftNow;
  state.__draftRevision = currentDraftRevision(character.id, action.id);
  const update = (patch, { persist = true } = {}) => {
    if (state.__draftRetired) return false;
    Object.assign(state, patch);
    if (persist) {
      state.__draftRevision = bumpDraftRevision(character.id, action.id);
      state.__draftCleared = false;
      state.__draftDirty = true;
      if (composerBinding && composerBinding.state === state) draftFlushNow = saveDraftNow;
      clearSaveTimer();
      saveTimer = setTimeout(saveDraftNow, 250);
    }
    refreshLipsyncSourcePanels();
    refreshSubmitState();
    return true;
  };

  form = h("form", { class: "gen-form minimal-form", onsubmit: (event) => submitMinimalAction(event, character, action, feature, capability, state) });
  if (action.id === "photoshoot") {
    if (capability.variantId === "photoshoot.anime.scene-card.v1") {
      form.appendChild(renderWardrobePicker(character, state, update, {
        capability,
        required: true,
        label: "角色造型",
      }));
      form.appendChild(renderAnimeScenePicker(character, capability, state, update));
      form.appendChild(renderHint("角色與服裝會保持原圖，不會被重畫成真人；只把透明角色圖放進同畫風的核准場景。"));
    } else {
      form.appendChild(textareaMini("想拍什麼", state.prompt, ACTION_PLACEHOLDERS.photoshoot, (value) => update({ prompt: value })));
      form.appendChild(renderWardrobePicker(character, state, update));
      form.appendChild(radioCards("比例", aspectChoice.options, state.aspect, (value) => update({ aspect: value })));
      form.appendChild(renderQualitySelector(state.quality, (value) => update({ quality: value }), capability));
    }
  } else if (action.id === "group-photo") {
    form.appendChild(replaceablePhotoSlot(character, feature, "image1", state, update, {
      defaultUrl: characterProfilePicUrl(character),
      defaultLabel: "角色照片：使用角色頭像",
      uploadLabel: "角色照片",
    }));
    form.appendChild(renderComposerUploadSlot(feature, "image2", "對方照片", state, () => update({})));
    form.appendChild(textareaMini("想要的感覺", state.vibe, ACTION_PLACEHOLDERS["group-photo"], (value) => update({ vibe: value })));
    form.appendChild(renderQualitySelector(state.quality, (value) => update({ quality: value }), capability));
  } else if (action.id === "multi-angle-selfie") {
    form.appendChild(radioCards("表情", expressionChoice.options, state.expression, (value) => update({ expression: value })));
    form.appendChild(renderWardrobePicker(character, state, update));
    form.appendChild(replaceablePhotoSlot(character, feature, "face", state, update, {
      defaultUrl: characterProfilePicUrl(character),
      defaultLabel: "臉部參考：使用角色頭像",
    }));
    form.appendChild(renderQualitySelector(state.quality, (value) => update({ quality: value }), capability));
  } else if (action.id === "image-video") {
    if (capabilityServerBindsUpload(capability, "image")) {
      form.appendChild(renderWardrobePicker(character, state, update, {
        capability,
        required: true,
        label: "角色造型",
      }));
      form.appendChild(renderHint("短片約 3 秒，會直接使用這個角色已驗證的造型，不會把角色轉成真人。"));
      form.appendChild(radioCards("動作", motionPresetChoice.options, state.motionPreset, (value) => update({ motionPreset: value })));
    } else {
      form.appendChild(replaceablePhotoSlot(character, feature, "image", state, update, {
        defaultUrl: newest ? newest.url : "",
        defaultLabel: newest ? `起始照片：使用最新作品 ${newest.filename}` : "起始照片",
        uploadLabel: "起始照片",
      }));
      form.appendChild(inputMini("動作描述", state.motion, ACTION_PLACEHOLDERS["image-video"], (value) => update({ motion: value })));
    }
    if (motionScaleChoice.options.length > 1) {
      form.appendChild(radioCards("動作幅度", motionScaleChoice.options, state.motionScale, (value) => update({ motionScale: value })));
    }
    if (durationChoice.options.length > 1) {
      form.appendChild(radioCards("長度", durationChoice.options, state.duration, (value) => update({ duration: value })));
    }
    if (qualityChoice.options.length > 1) {
      form.appendChild(renderQualitySelector(state.quality, (value) => update({ quality: value }), capability));
    }
  } else if (action.id === "dance") {
    if (danceModeChoice.options.length > 1) {
      form.appendChild(selectMini("模式", danceModeChoice.options, state.mode, (value) => update({ mode: value })));
    }
    if (capabilityServerBindsUpload(capability, "reference")) {
      form.appendChild(renderWardrobePicker(character, state, update, {
        capability,
        required: true,
        label: "角色造型",
      }));
      form.appendChild(renderHint("會使用核准的完整服裝角色圖，固定生成前約 5 秒；不能任意替換角色圖或自由修改安全 prompt。"));
    } else {
      form.appendChild(replaceablePhotoSlot(character, feature, "reference", state, update, {
        defaultUrl: characterProfilePicUrl(character),
        defaultLabel: "角色照片：使用角色頭像",
        uploadLabel: "角色照片",
      }));
    }
    form.appendChild(renderDanceTemplatePicker(feature, state, update, { character, capability }));
    if (!capabilityServerBindsUpload(capability, "reference")) {
      form.appendChild(textareaMini("動作描述", state.description, ACTION_PLACEHOLDERS.dance, (value) => update({ description: value })));
      form.appendChild(renderQualitySelector(state.quality, (value) => update({ quality: value }), capability));
    }
  } else if (["h3-image-video", "h3-reference-video"].includes(action.id)) {
    form.appendChild(h("div", { class: "field role-bound-summary" }, [
      h("span", { class: "field-label", text: "角色參考" }),
      h("div", { class: "bound-value", text: `${character.name} 的 Profile 圖` }),
      renderHint(`使用${characterVisualClass(character) === "anime" ? "動漫" : "真人"}專用 H3 模組；不會切換到另一種角色模型。`),
    ]));
    if (action.id === "h3-image-video") {
      form.appendChild(renderComposerUploadSlot(
        feature,
        "last_frame",
        "尾格圖片（選填）",
        state,
        () => update({}),
      ));
      form.appendChild(renderHint(`${character.name} 的 Profile 圖固定作為首格；提供尾格時，H3 會生成首格到尾格的有聲過場。`));
    }
    form.appendChild(textareaMini(
      "畫面、動作與聲音",
      state.description,
      "依時間順序描述角色動作、鏡頭、環境音或要說的話",
      (value) => update({ description: value }),
      true,
    ));
    form.appendChild(radioCards("畫面方向", h3AspectChoice.options, state.aspect, (value) => update({ aspect: value })));
    form.appendChild(radioCards("動作類型", h3MotionChoice.options, state.motionProfile, (value) => update({ motionProfile: value })));
    form.appendChild(renderHint(
      action.id === "h3-reference-video"
        ? "Ref2VA 會以角色 Profile 圖作為身分參考，適合角色參照與有聲影片。"
        : "FL2VA 會以角色 Profile 圖作為首格；尾格可留空，也可指定首尾格有聲影片。",
    ));
  } else if (action.id === "voice-speak") {
    state.voiceSource = "persona";
    state.voiceId = "";
    form.appendChild(textareaMini("想說的話", state.text, "輸入要讓角色說出的內容", (value) => update({ text: value }), true));
    form.appendChild(h("div", { class: "field role-bound-summary" }, [
      h("span", { class: "field-label", text: "聲音" }),
      h("div", { class: "bound-value", text: `${character.name} 的角色聲音` }),
      renderHint("聲音已跟角色綁定，不需要再選，也不會誤用其他角色的聲音。"),
    ]));
    form.appendChild(renderAudioFormatPreference());
  } else if (action.id === "lipsync") {
    form.appendChild(renderComposerUploadSlot(feature, "video", "影片", state, () => update({})));
    if (lipsyncModeChoice.options.length > 1) {
      form.appendChild(radioCards("影片如何配合語音", lipsyncModeChoice.options, state.mode, (value) => update({ mode: value })));
    }
    form.appendChild(radioCards("聲音來源", voiceSourceChoice.options, state.voiceSource, (value) => {
      normalizeLipsyncVoiceState(state, value);
      update({});
      clearSaveTimer();
      saveDraftNow();
    }));
    lipsyncSourcePanels.tts = h("div", { class: "source-panel" }, [
      textareaMini("想說的話", state.ttsText, "輸入要讓角色對嘴說出的內容", (value) => update({ ttsText: value }), true),
    ]);
    const sel = h("select");
    sel.appendChild(h("option", { value: "", text: "請選擇聲音庫音檔" }));
    loadCharacterVoiceOptions(sel, character, state.voiceId || "", "此角色尚未綁定聲音（可在角色設定綁定）").then((matched) => {
      if (matched === null) return;
      if (state.voiceId && !matched) {
        update({ voiceId: "" });
        sel.value = "";
      }
    });
    sel.addEventListener("change", () => update({ voiceId: sel.value }));
    lipsyncSourcePanels.library = h("div", { class: "source-panel" }, [
      labeledField("素材庫音檔", sel),
    ]);
    lipsyncSourcePanels.upload = h("div", { class: "source-panel" }, [
      renderComposerUploadSlot(feature, "voice", "上傳音檔", state, () => update({})),
    ]);
    refreshLipsyncSourcePanels();
    form.appendChild(lipsyncSourcePanels.tts);
    form.appendChild(lipsyncSourcePanels.library);
    form.appendChild(lipsyncSourcePanels.upload);
    if (qualityChoice.options.length > 1) {
      form.appendChild(renderQualitySelector(state.quality, (value) => update({ quality: value }), capability));
    }
  } else if (action.id === "post") {
    form.appendChild(inputMini("主題", state.topic, ACTION_PLACEHOLDERS.post, (value) => update({ topic: value }), true));
    form.appendChild(radioCards("平台", platformChoice.options, state.platform, (value) => update({ platform: value })));
    form.appendChild(radioCards("長度", postLengthChoice.options, state.postLength, (value) => update({ postLength: value })));
  }
  if (!capability.hideAdvanced) form.appendChild(renderAdvancedFields(feature, capability));
  const extras = action.id === "post" ? [h("div", { class: "post-result hidden", id: "postResult" })] : [];
  const hasLocalFileSlots = (feature.uploads || []).some((upload) => (
    !capabilityServerBindsUpload(capability, String(upload && upload.id || ""))
  ));
  const restoredDraftNotice = draftSnapshot.restored ? h("div", {
    class: "draft-restored-notice",
    role: "status",
  }, [
    h("div", { class: "draft-restored-copy" }, [
      h("strong", { text: "已恢復保留的內容" }),
      h("span", {
        text: hasLocalFileSlots
          ? "文字與選項已保留；本機檔案無法保留，需要重新選擇。"
          : "這可能是上次未送出的草稿，或你剛從作品／造型帶入的素材。",
      }),
    ]),
    h("button", {
      class: "ghost draft-clear-button",
      type: "button",
      text: "從空白開始",
      "aria-label": `清除 ${actionVerb(action)} 的保留內容並從空白開始`,
      onclick: () => startComposerFromBlank(character, action, state),
    }),
  ]) : null;
  formWrap = h("section", { class: "panel action-panel" }, [
    renderSectionHeader(
      actionVerb(action),
      capability.variantId === "photoshoot.anime.scene-card.v1"
        ? "選好造型與場景就能完成；角色像素不會被生成模型改寫。"
        : "只填必要內容；技術參數收在進階。",
      [],
      { compact: true },
    ),
    restoredDraftNotice,
    form,
    ...extras,
  ]);
  refreshLipsyncSourcePanels();
  refreshSubmitState();
  bindGenerateComposer(character, action, feature, capability, state, form, saveDraftNow, restoredDraftNotice);
  if (action.id === "post" && generationFsm.composerKey === composerKey(character, action) && generationFsm.postResult) {
    renderPostResult(formWrap.querySelector("#postResult"), generationFsm.postResult);
  }
  return formWrap;
}

async function submitMinimalAction(event, character, action, feature, renderedCapability, state) {
  event.preventDefault();
  if (submitInFlight) return;
  const renderedContractValid = (
    draftCapabilityMatches(action.id, renderedCapability)
    && renderedCapability.featureId === feature.id
  );
  const capability = renderedContractValid ? capabilityStillEnabled(
    character.id,
    action.id,
    renderedCapability.variantId,
    renderedCapability.featureId,
  ) : null;
  if (!capability) {
    if (state && typeof state.__clearSaveTimer === "function") state.__clearSaveTimer();
    if (state) state.__draftCleared = true;
    if (state && draftFlushNow === state.__saveDraftNow) draftFlushNow = null;
    clearDraft(character.id, action.id);
    showAlert("功能已更新，舊草稿沒有沿用；請重新確認。", true);
    setGenerationFsm("disabled", {
      reason: "功能已更新，請重新確認",
      detail: "未送出 · 功能已更新",
      composerKey: composerKey(character, action),
    }, { force: true });
    renderCharacterWorkspaceRoute();
    return;
  }
  const session = captureSessionContext();
  const submitKey = composerKey(character, action);
  const submitId = ++submissionSequence;
  const draftRevision = currentDraftRevision(character.id, action.id);
  const reason = reasonForAction(action.id, state, feature);
  if (reason) {
    setGenerationFsm("disabled", { reason, detail: reason }, { force: true });
    return;
  }
  if (generationFsm.state === "error") trackedPromptId = "";
  submitInFlight = true;
  let requestSent = false;
  setGenerationFsm("submitting", { reason: "", detail: "未送出", pct: 0, resultPromptId: "", postResult: null, composerKey: submitKey }, { force: true });
  showAlert("");
  try {
    if (action.id === "lipsync" && state.voiceSource === "tts") {
      const promptId = await submitLipsyncTtsChain(character, action, feature, capability, state, draftRevision, () => {
        requestSent = true;
        if (composerBinding && composerBinding.key === submitKey) setGenerationFsm("submitting", { detail: "可能已送出，正在確認", composerKey: submitKey }, { force: true });
      });
      assertSessionContext(session);
      if (composerBinding && composerBinding.key === submitKey && submissionSequence === submitId) {
        trackedPromptId = promptId;
        setGenerationFsm("queued", { promptId, resultPromptId: promptId, detail: "已加入佇列 · 等待中", pct: 0, composerKey: submitKey }, { force: true });
      }
      return;
    }
    const advanced = collectAdvancedSettings(feature);
    const settings = applyCharacterDefaults(feature, buildMinimalSettings(action.id, feature, state, advanced));
    if (action.id === "post") {
      requestSent = true;
      setGenerationFsm("submitting", { detail: "可能已送出，正在確認", composerKey: submitKey }, { force: true });
      const result = await api("/api/post", {
        method: "POST",
        body: JSON.stringify({ featureId: feature.id, characterId: character.id, action: action.id, variantId: capability.variantId, settings }),
      });
      assertSessionContext(session);
      clearDraftAfterSubmit(character.id, action.id, state, draftRevision);
      characterPostsCache.delete(character.id);
      libraryPostsLoaded = false;
      libraryPostsError = "";
      if (composerBinding && composerBinding.key === submitKey) {
        renderPostResult($("postResult"), result);
        trackedPromptId = "";
        setGenerationFsm("done", { detail: "完成", resultPromptId: "", postResult: result, composerKey: submitKey }, { force: true });
      }
      showAlert("貼文已生成。");
      return;
    }
    const files = await collectMinimalFiles(feature, state);
    assertSessionContext(session);
    if (!capabilityStillEnabled(character.id, action.id, capability.variantId, feature.id)) {
      throw new Error("這個角色的動作設定已變更，請重新選擇");
    }
    requestSent = true;
    setGenerationFsm("submitting", { detail: "可能已送出，正在確認", composerKey: submitKey }, { force: true });
    const result = await api("/api/generate", {
      method: "POST",
      body: JSON.stringify({ featureId: feature.id, characterId: character.id, action: action.id, variantId: capability.variantId, files, settings }),
    });
    assertSessionContext(session);
    const promptId = result.prompt_id || result.promptId || "";
    if (!promptId) throw new Error("伺服器未回傳任務編號");
    rememberActiveJob(promptId, character.id, action.id);
    clearDraftAfterSubmit(character.id, action.id, state, draftRevision);
    if (composerBinding && composerBinding.key === submitKey && submissionSequence === submitId) {
      trackedPromptId = promptId;
      setGenerationFsm("queued", { promptId, resultPromptId: promptId, detail: "已加入佇列 · 等待中", pct: 0, composerKey: submitKey }, { force: true });
    }
    showAlert(`已加入佇列：${promptId || "OK"}`);
    await refreshQueue();
    assertSessionContext(session);
    scheduleHistoryRefresh();
  } catch (error) {
    if (isStaleSessionError(error)) return;
    const presented = generationErrorPresentation(error, requestSent);
    showAlert(presented.detail, true, { technicalDetail: presented.technicalDetail });
    if (composerBinding && composerBinding.key === submitKey && submissionSequence === submitId) {
      setGenerationFsm("error", {
        detail: presented.detail,
        reason: presented.summary,
        technicalDetail: presented.technicalDetail,
        submissionState: presented.submissionState,
        composerKey: submitKey,
      }, { force: true });
    }
  } finally {
    if (sessionContextIsCurrent(session) && submissionSequence === submitId) {
      submitInFlight = false;
      if (composerBinding && composerBinding.key !== submitKey && !trackedPromptId) {
        generationFsm = { state: "ready", reason: "", detail: "", pct: 0, resultPromptId: "", postResult: null, composerKey: composerBinding.key };
        updateGenerateReadiness();
      }
    }
  }
}

async function submitLipsyncTtsChain(character, action, lipsyncFeature, lipsyncCapability, state, draftRevision, onRequest = null) {
  const session = captureSessionContext();
  const text = String(state.ttsText || "").trim();
  if (!text) throw new Error("請輸入要讓角色說的話");
  const video = await ensureSlotServerFile(lipsyncFeature, state, "video");
  assertSessionContext(session);
  if (!capabilityStillEnabled(character.id, action.id, lipsyncCapability.variantId, lipsyncFeature.id)) {
    throw new Error("這個角色的對嘴設定已變更，請重新選擇");
  }
  const currentCharacter = characters.find((item) => item.id === character.id) || character;
  const voiceCapability = enabledActionCapability(currentCharacter, "voice-speak");
  const voiceFeature = voiceCapability ? featureById(voiceCapability.featureId) : null;
  if (!voiceCapability || !voiceFeature || !interactiveReady(voiceFeature)) throw new Error("這個角色沒有可用的聲音生成模組");
  const voiceSettings = applyCharacterDefaults(voiceFeature, {
    voiceSource: "persona",
    text,
    quality: state.quality || "standard",
  });
  if (onRequest) onRequest();
  const voiceResult = await api("/api/generate", {
    method: "POST",
    body: JSON.stringify({ featureId: voiceFeature.id, characterId: character.id, action: "voice-speak", variantId: voiceCapability.variantId, files: {}, settings: voiceSettings }),
  });
  assertSessionContext(session);
  const promptId = voiceResult.prompt_id || voiceResult.promptId || "";
  if (!promptId) throw new Error("伺服器未回傳任務編號");
  rememberActiveJob(promptId, character.id, action.id, {
    chainedJob: {
      stage: "voice",
      nextAction: "lipsync",
      lipsyncFeatureId: lipsyncFeature.id,
      lipsyncVariantId: lipsyncCapability.variantId,
      text,
      videoServerFile: video.serverFile,
      videoLabel: video.label,
      quality: state.quality || "standard",
    },
  });
  clearDraftAfterSubmit(character.id, action.id, state, draftRevision);
  showAlert(`已加入佇列：語音生成中 ${promptId || ""}`.trim());
  await refreshQueue();
  assertSessionContext(session);
  scheduleHistoryRefresh();
  return promptId;
}

function queueErrorForPrompt(promptId) {
  return queueRecentErrors.find((error) => error.promptId === promptId) || null;
}

function terminalJobForPrompt(promptId) {
  return [...terminalJobs, ...queueTerminalJobs].find((job) => job.promptId === promptId) || null;
}

function historyItemForPrompt(promptId, kind = "") {
  return historyItems.find((item) => item.promptId === promptId && (!kind || item.kind === kind)) || null;
}

async function continueChainedJob(job) {
  const session = captureSessionContext();
  if (!job || !job.chainedJob || job.chainedJob.stage !== "voice") return false;
  const current = activeJobsTable().find((item) => item.promptId === job.promptId);
  if (!current || !current.chainedJob || current.chainedJob.stage !== "voice") return false;
  job = current;
  const queueIds = new Set(queueJobs.map((item) => item.promptId).filter(Boolean));
  if (queueIds.has(job.promptId)) return false;
  const terminal = terminalJobForPrompt(job.promptId);
  if (terminal && terminal.state === "cancelled") {
    updateActiveJob(job.promptId, {
      state: "cancelled",
      error: "",
      chainedJob: { ...job.chainedJob, stage: "cancelled" },
    });
    return true;
  }
  const queuedError = queueErrorForPrompt(job.promptId);
  const event = jobEvent(job.promptId);
  if (queuedError || event.error) {
    updateActiveJob(job.promptId, {
      state: "error",
      error: (queuedError && (queuedError.message || queuedError.type)) || event.error || "語音生成失敗",
      chainedJob: { ...job.chainedJob, stage: "error" },
    });
    return true;
  }
  const audio = historyItemForPrompt(job.promptId, "audio");
  if (!audio) return false;
  const character = characters.find((item) => item.id === job.characterId);
  const lipsyncCapability = character ? capabilityStillEnabled(
    character.id,
    "lipsync",
    job.chainedJob.lipsyncVariantId || "",
    job.chainedJob.lipsyncFeatureId || "",
  ) : null;
  const lipsyncFeature = lipsyncCapability ? featureById(lipsyncCapability.featureId) : null;
  if (!character || !lipsyncCapability || !lipsyncFeature) {
    updateActiveJob(job.promptId, { state: "error", error: "找不到角色或對嘴工具", chainedJob: { ...job.chainedJob, stage: "error" } });
    return true;
  }
  updateActiveJob(job.promptId, {
    state: "pending",
    error: "",
    chainedJob: { ...job.chainedJob, stage: "continuing" },
  });
  job = { ...job, chainedJob: { ...job.chainedJob, stage: "continuing" } };
  const previousActiveCharacterId = activeCharacterId;
  let settings = {};
  try {
    const imported = await importItemToServerFile(audio, "voice");
    assertSessionContext(session);
    const currentCapability = capabilityStillEnabled(character.id, "lipsync", lipsyncCapability.variantId, lipsyncFeature.id);
    if (!currentCapability) throw new Error("這個角色的對嘴設定已變更，請重新操作");
    activeCharacterId = character.id;
    settings = applyCharacterDefaults(lipsyncFeature, {
      quality: job.chainedJob.quality || "standard",
      mode: "sequential",
    });
    const result = await api("/api/generate", {
      method: "POST",
      body: JSON.stringify({
        featureId: lipsyncFeature.id,
        characterId: character.id,
        action: "lipsync",
        variantId: currentCapability.variantId,
        files: {
          video: { serverFile: job.chainedJob.videoServerFile },
          voice: { serverFile: imported.serverFile },
        },
        settings,
      }),
    });
    assertSessionContext(session);
    const nextPromptId = result.prompt_id || result.promptId || "";
    replaceActiveJobPrompt(job.promptId, nextPromptId, {
      action: "lipsync",
      state: "pending",
      chainedJob: {
        ...job.chainedJob,
        stage: "lipsync",
        voicePromptId: job.promptId,
        audioServerFile: imported.serverFile,
        audioLabel: imported.label,
      },
    });
    showAlert(`語音完成，已自動送出對嘴：${nextPromptId || "OK"}`);
    await refreshQueue();
    assertSessionContext(session);
    return true;
  } catch (error) {
    if (isStaleSessionError(error)) throw error;
    updateActiveJob(job.promptId, {
      state: "pending",
      error: "",
      chainedJob: { ...job.chainedJob, stage: "voice" },
    });
    throw error;
  } finally {
    if (sessionContextIsCurrent(session)) activeCharacterId = previousActiveCharacterId;
  }
}

async function reconcileChainedJobs() {
  const session = captureSessionContext();
  const jobs = activeJobsTable().filter((job) => job.chainedJob && job.chainedJob.stage === "voice");
  for (const job of jobs) {
    try {
      await continueChainedJob(job);
      assertSessionContext(session);
    } catch (error) {
      if (isStaleSessionError(error)) return;
      updateActiveJob(job.promptId, { state: "pending", error: "", chainedJob: { ...job.chainedJob, stage: "voice" } });
    }
  }
}

function renderCharacterFeed(character) {
  const items = worksForCharacter(character.id).filter((item) => !item.hidden);
  const jobs = activeJobsForCharacter(character.id);
  const previewLimit = window.matchMedia("(max-width: 767px)").matches ? 2 : 6;
  const latestMedia = items[0] && ["image", "video", "audio"].includes(items[0].kind) ? items[0].kind : "image";
  return h("section", { class: "panel character-feed" }, [
    renderSectionHeader("最近作品", "生成中的任務與最近完成的作品。", [
      h("button", {
        class: "ghost small",
        type: "button",
        text: "更新",
        onclick: async () => refreshHistory(),
      }),
    ], { compact: true }),
    jobs.length ? h("div", { class: "running-chip-row" }, jobs.map((job) => {
      const progress = jobProgress(job);
      return h("div", { class: `running-chip ${job.state || "pending"}` }, [
        h("span", { text: characterJobLabel(job) }),
        h("b", { text: progress.label }),
      ]);
    })) : null,
    h("div", { class: "character-feed-preview-list" }, items.length
      ? items.slice(0, previewLimit).map((item, index) => renderCharacterFeedPreview(item, index))
      : [h("div", { class: "empty", text: "還沒有作品，先生成第一件作品。" })]),
    items.length ? h("button", {
      class: "ghost character-feed-all",
      type: "button",
      text: `全部媒體作品（${items.length}）`,
      onclick: () => navigate({ view: "character-library", characterId: character.id, media: latestMedia }),
    }) : null,
  ]);
}

function renderCharacterFeedPreview(item, index) {
  let media;
  if (itemRenderKind(item) === "video") {
    media = h("video", { src: item.url, preload: "metadata", muted: "", playsinline: "", tabindex: "-1", "aria-hidden": "true" });
  } else if (item.kind === "audio") {
    media = h("span", { class: "feed-preview-audio", text: "♫", "aria-hidden": "true" });
  } else {
    media = h("img", { src: item.url, alt: "", loading: "lazy" });
  }
  const action = actionLabelFromItem(item);
  const time = formatTimestamp(Number(item.modified || 0));
  return h("button", {
    class: `character-feed-preview ${item.kind || "image"}${index >= 2 ? " phone-secondary" : ""}`,
    type: "button",
    "aria-label": `查看${action}，${time}`,
    onclick: (event) => openResultDetail(item, event.currentTarget),
  }, [
    h("span", { class: "feed-preview-media" }, [media]),
    h("span", { class: "feed-preview-copy" }, [
      h("strong", { text: action }),
      h("span", { text: time }),
    ]),
    h("span", { class: "feed-preview-arrow", text: "›", "aria-hidden": "true" }),
  ]);
}

function characterFeedThumb(item) {
  return thumbNode(item, {
    previewOnly: true,
    onOpen: (selected, node) => openResultDetail(selected, node),
  });
}

async function deleteWardrobeReference(character, outfit) {
  const filename = String((outfit && outfit.filename) || "");
  if (!filename || !outfit.custom) throw new Error("這不是可刪除的參考圖片");
  await mutateWardrobe(
    character.id,
    () => api(
      `/api/characters/${encodeURIComponent(character.id)}/wardrobe/${encodeURIComponent(filename)}/delete`,
      { method: "POST" },
    ),
  );
}

function openWardrobeLightbox(character, outfit, opener = null) {
  const poses = Array.isArray(outfit.poses) ? outfit.poses : [];
  const photoshootCapability = enabledActionCapability(character, "photoshoot");
  const photoshootThemes = allowedOptionValues(photoshootCapability, "wardrobeTheme");
  const photoshootAllowed = Boolean(
    photoshootCapability
    && !outfit.custom
    && outfit.approved === true
    && photoshootThemes
    && photoshootThemes.has(String(outfit.theme || ""))
  );
  const side = h("div", { class: "wardrobe-lightbox-side" });
  if (outfit.custom) side.appendChild(h("div", { class: "panel-note", text: "這是參考收藏，目前不會出現在生成選項。" }));
  if (!outfit.custom && outfit.approved !== true) {
    side.appendChild(h("div", { class: "panel-note", text: "這套造型仍在待審，不會出現在生成選項。" }));
  }
  const actions = h("div", { class: "wardrobe-lightbox-actions" });
  if (photoshootAllowed) actions.appendChild(h("button", {
    class: "primary",
    type: "button",
    text: "選這套拍寫真",
    onclick: () => {
      const currentCapability = capabilityStillEnabled(
        character.id,
        "photoshoot",
        photoshootCapability.variantId,
        photoshootCapability.featureId,
      );
      const currentThemes = allowedOptionValues(currentCapability, "wardrobeTheme");
      if (!currentCapability || !currentThemes || !currentThemes.has(String(outfit.theme || ""))) {
        showAlert("造型選項已更新，請重新選擇。", true);
        return;
      }
      if (!outfit.approved || outfit.custom) {
        showAlert("這個角色目前不能使用寫真功能。", true);
        return;
      }
      const merged = mergeDraft(character.id, "photoshoot", currentCapability, {
        outfit: outfit.theme || "",
        outfitLabel: outfit.label || outfit.theme || "",
        outfitUrl: outfit.outfitUrl || "",
      });
      if (!merged) {
        showAlert("寫真功能已更新，請重新整理後再選擇。", true);
        return;
      }
      sheetAction(() => navigate({ view: "character-workspace", characterId: character.id, actionId: "photoshoot" }));
    },
  }));
  if (outfit.custom && canEditCharacter(character)) actions.appendChild(h("button", {
    class: "ghost danger-text",
    type: "button",
    text: "從參考收藏刪除",
    onclick: async () => {
      if (!window.confirm("刪除這張參考圖片？此動作無法復原。")) return;
      try {
        await deleteWardrobeReference(character, outfit);
        showAlert("已從參考收藏刪除。");
        dismissTopSheet(() => {
          if (currentRoute.view === "character-wardrobe" && currentRoute.characterId === character.id) {
            renderCharacterWardrobeRoute();
          }
        });
      } catch (error) {
        showAlert(`刪除失敗：${error.message}`, true);
      }
    },
  }));
  if (actions.childElementCount) side.appendChild(actions);
  if (poses.length) side.appendChild(h("section", { class: "wardrobe-lightbox-poses" }, [
    h("h3", { class: "wardrobe-lightbox-pose-title", text: "表情姿勢變化" }),
    h("div", { class: "wardrobe-lightbox-pose-grid" }, poses.map((pose) => h("figure", { class: "wardrobe-lightbox-pose" }, [
      h("img", { src: pose.url || pose.thumbUrl, alt: "", loading: "lazy" }),
      h("figcaption", { text: pose.pose === "supported" ? "支持" : (pose.pose === "challenged" ? "質疑" : "勝利") }),
    ]))),
  ]));
  const hasSideContent = side.childElementCount > 0;
  const media = h("div", { class: `wardrobe-lightbox-grid${hasSideContent ? "" : " single"}` }, [
    h("figure", { class: "wardrobe-lightbox-main" }, [
      h("img", { src: outfit.outfitUrl || outfit.thumbUrl, alt: "", loading: "lazy" }),
      h("figcaption", { text: outfit.label || outfit.theme || "服裝" }),
    ]),
    hasSideContent ? side : null,
  ]);
  openSheet({
    id: `wardrobe-${character.id}-${outfit.theme || "custom"}`,
    title: outfit.label || outfit.theme || "服裝",
    opener,
    className: "wardrobe-detail-sheet",
    content: h("div", { class: "wardrobe-detail" }, [
      h("p", { class: "result-detail-meta", text: `${character.name} · 角色造型庫` }),
      media,
    ]),
  });
}

function renderWardrobeLibrary(character) {
  const session = captureSessionContext();
  const canPhotoshoot = Boolean(enabledActionCapability(character, "photoshoot"));
  const body = h("div", { class: "library-section-body" }, [h("div", { class: "empty compact", text: "讀取服裝中..." })]);
  loadWardrobe(character.id).then((outfits) => {
    if (!sessionContextIsCurrent(session)) return;
    clear(body);
    if (!outfits.length) {
      body.appendChild(h("div", { class: "empty compact", text: "目前沒有可用的服裝素材。" }));
    } else {
      const groups = [
        ["可用造型", outfits.filter((outfit) => !outfit.custom && outfit.approved === true), "已核准，可在支援的角色功能中選用。"],
        ["待審造型", outfits.filter((outfit) => !outfit.custom && outfit.approved !== true), "尚未核准，只供管理者檢查，不會出現在生成選項。"],
        ["參考收藏", outfits.filter((outfit) => outfit.custom), "僅供查看，目前不會出現在生成選項。"],
      ];
      for (const [title, entries, description] of groups) {
        if (!entries.length) continue;
        body.appendChild(h("section", { class: "wardrobe-library-group" }, [
          h("div", { class: "wardrobe-library-group-head" }, [
            h("h3", { text: title }),
            h("p", { text: description }),
          ]),
          h("div", { class: "wardrobe-library-grid" }, entries.map((outfit) => h("button", {
            class: `wardrobe-library-card${outfit.custom ? " reference" : ""}`,
            type: "button",
            onclick: (event) => openWardrobeLightbox(character, outfit, event.currentTarget),
          }, [
            h("img", { src: outfit.thumbUrl || outfit.outfitUrl, alt: "", loading: "lazy" }),
            h("span", { text: outfit.label || outfit.theme || "服裝" }),
            outfit.custom ? h("small", { text: "參考，不可生成" }) : null,
            !outfit.custom && outfit.approved !== true ? h("small", { text: "待審，不可生成" }) : null,
          ]))),
        ]));
      }
    }
  }).catch((error) => {
    if (isStaleSessionError(error)) return;
    clear(body);
    body.appendChild(h("div", { class: "empty compact", text: `服裝讀取失敗：${error.message}` }));
  });
  return h("section", { class: "library-subsection", dataset: { libraryTarget: "wardrobe" } }, [
    h("div", { class: "panel-head-row compact" }, [
      h("div", {}, [
        h("h2", { class: "panel-h2", text: "角色造型庫" }),
        h("p", { class: "panel-sub", text: canPhotoshoot ? "集中查看角色造型；只有核准造型會出現在支援的生成選項。" : "集中查看角色造型；目前沒有可使用造型庫的生成功能。" }),
      ]),
    ]),
    canEditCharacter(character) ? renderWardrobeReferenceUpload(character) : null,
    body,
  ]);
}

function renderWardrobeReferenceUpload(character) {
  const input = h("input", { type: "file", accept: "image/jpeg,image/png,image/webp", class: "hidden" });
  const filename = h("span", { class: "wardrobe-reference-filename", text: "尚未選擇圖片" });
  const submit = h("button", { class: "primary small", type: "button", text: "儲存為參考", disabled: "" });
  input.addEventListener("change", () => {
    const file = input.files && input.files[0];
    filename.textContent = file ? `${file.name} · ${formatBytes(file.size)}` : "尚未選擇圖片";
    submit.disabled = !file;
  });
  submit.addEventListener("click", async () => {
    const file = input.files && input.files[0];
    if (!file) {
      showAlert("請先選擇一張參考圖片。", true);
      return;
    }
    if (file.size > WARDROBE_REFERENCE_MAX_FILE_BYTES) {
      showAlert("參考圖片不可超過 15 MB。", true);
      return;
    }
    submit.disabled = true;
    submit.textContent = "儲存中…";
    try {
      const payload = await fileToData(file);
      await mutateWardrobe(character.id, () => api(`/api/characters/${encodeURIComponent(character.id)}/wardrobe`, {
        method: "POST",
        body: JSON.stringify({ file: payload }),
      }));
      showAlert("已加入參考收藏；它不會自動成為生成選項。");
      if (currentRoute.view === "character-wardrobe" && currentRoute.characterId === character.id) {
        renderCharacterWardrobeRoute();
      }
    } catch (error) {
      showAlert(`儲存失敗：${error.message}`, true);
      submit.disabled = false;
      submit.textContent = "儲存為參考";
    }
  });
  return h("details", { class: "wardrobe-reference-upload" }, [
    h("summary", { text: "加入參考圖片" }),
    h("p", { text: "只會加入參考收藏，不會出現在寫真、影片或跳舞的生成選項。" }),
    h("div", { class: "wardrobe-reference-upload-controls" }, [
      input,
      h("button", { class: "ghost small", type: "button", text: "選擇圖片", onclick: () => input.click() }),
      filename,
      submit,
    ]),
  ]);
}

function renderGeneratedLibrarySection(character, title, items, emptyText) {
  return h("section", { class: "library-subsection" }, [
    h("div", { class: "panel-head-row compact" }, [
      h("div", {}, [
        h("h2", { class: "panel-h2", text: title }),
        h("p", { class: "panel-sub", text: "依日期由新到舊排列。" }),
      ]),
    ]),
    h("div", { class: "gallery library character-library-gallery" }, items.length
      ? items.map((item) => characterFeedThumb(item))
      : [h("div", { class: "empty compact", text: emptyText })]),
  ]);
}

function itemActionId(item) {
  return item && item.action && item.action.id ? String(item.action.id) : "";
}

function itemRenderKind(item) {
  const declared = String((item && item.renderKind) || "").toLowerCase();
  if (["image", "video", "audio"].includes(declared)) return declared;
  // Compatibility for cached/pre-P9 gallery payloads. Animated WebP belongs
  // to the video filter but must still be rendered by an <img> element.
  if (String((item && item.filename) || "").toLowerCase().endsWith(".webp")) return "image";
  return String((item && item.kind) || "image").toLowerCase();
}

function renderLibraryTopSection(title, children, sub = "") {
  return h("section", { class: "panel library-section" }, [
    renderSectionHeader(title, sub, [], { compact: true }),
    h("div", { class: "library-section-stack" }, children),
  ]);
}

function characterLibraryThumb(item, label = "") {
  const node = characterFeedThumb(item);
  if (label) node.appendChild(h("span", { class: "library-item-label", text: label }));
  return node;
}

function renderPhotoLibrary(character, photos) {
  const children = [];
  if (!photos.length) {
    children.push(h("div", { class: "empty compact", text: "目前沒有照片作品，先從寫真、合照或自拍開始。" }));
    return renderLibraryTopSection("圖片", children, "生成照片集中在這裡；服裝請到角色衣櫃管理。");
  }
  for (const [actionId, title] of PHOTO_ACTION_SECTIONS) {
    const items = photos.filter((item) => itemActionId(item) === actionId);
    if (!items.length) continue;
    children.push(renderGeneratedLibrarySection(character, title, items, ""));
  }
  const known = new Set(PHOTO_ACTION_SECTIONS.map(([actionId]) => actionId));
  const other = photos.filter((item) => !known.has(itemActionId(item)));
  if (other.length) children.push(renderGeneratedLibrarySection(character, "其他照片", other, ""));
  return renderLibraryTopSection("圖片", children, "生成照片集中在這裡；服裝請到角色衣櫃管理。");
}

function renderVideoLibrary(character, videos) {
  return h("section", { class: "library-subsection" }, [
    h("div", { class: "gallery library character-library-gallery" }, videos.length
      ? videos.map((item) => characterLibraryThumb(item, VIDEO_ACTION_LABELS[itemActionId(item)] || ""))
      : [h("div", { class: "empty compact", text: "目前沒有影片作品，生成影片後會集中在這裡。" })]),
  ]);
}

function postMetaLine(post) {
  const platform = post.platform || "平台未設定";
  const topic = post.topic || "未命名主題";
  const date = post.ts ? formatTimestamp(Number(post.ts)) : "";
  return [platform, topic, date].filter(Boolean).join(" ・ ");
}

function openPostResultDetail(post) {
  const character = characters.find((item) => item.id === post.characterId) || routeCharacter();
  openResultDetail({ ...post, kind: "text", character: character ? { id: character.id, name: character.name } : null }, null, { character });
}

async function loadCharacterPosts(characterId, force = false) {
  const session = captureSessionContext();
  if (!force && characterPostsCache.has(characterId)) return characterPostsCache.get(characterId);
  const data = await api(`/api/characters/${encodeURIComponent(characterId)}/posts`);
  assertSessionContext(session);
  const posts = Array.isArray(data.posts) ? data.posts : [];
  characterPostsCache.set(characterId, posts);
  return posts;
}

async function loadLibraryPosts(force = false) {
  const session = captureSessionContext();
  if (!force && libraryPostsLoaded) return libraryPosts;
  try {
    const data = await api("/api/posts");
    assertSessionContext(session);
    libraryPosts = Array.isArray(data.posts) ? data.posts : [];
    libraryPostsLoaded = true;
    libraryPostsError = "";
    return libraryPosts;
  } catch (error) {
    if (isStaleSessionError(error)) throw error;
    libraryPosts = [];
    libraryPostsLoaded = true;
    libraryPostsError = error.message || "文字內容讀取失敗";
    throw error;
  }
}

async function deleteCharacterPost(character, postId) {
  await api(`/api/characters/${encodeURIComponent(character.id)}/posts/${encodeURIComponent(postId)}/delete`, { method: "POST" });
  await loadCharacterPosts(character.id, true);
  libraryPosts = libraryPosts.filter((post) => post.id !== postId);
  showAlert("貼文已刪除。");
  refreshCurrentResultCollection();
}

function renderPostCard(character, post, options = {}) {
  const meta = [options.showCharacter && character ? character.name : "", postMetaLine(post)].filter(Boolean).join(" ・ ");
  const canDelete = post.canDelete === true;
  return h("article", { class: "post-library-card" }, [
    h("button", { class: "post-library-preview", type: "button", text: post.text || "", onclick: () => openPostResultDetail(post) }),
    h("div", { class: "post-library-meta", text: meta }),
    h("div", { class: "post-library-actions" }, [
      h("button", { class: "ghost small", type: "button", text: "複製", onclick: () => copyText(post.text || "") }),
      canDelete && character ? h("button", {
        class: "ghost small danger-text",
        type: "button",
        text: "刪除",
        onclick: async () => {
          if (!window.confirm("刪除這篇貼文？")) return;
          try {
            await deleteCharacterPost(character, post.id);
          } catch (error) {
            showAlert(`刪除失敗：${error.message}`, true);
          }
        },
      }) : null,
    ]),
  ]);
}

function renderTextLibrary(character) {
  const session = captureSessionContext();
  const body = h("div", { class: "post-library-list" }, [h("div", { class: "empty compact", text: "讀取貼文中..." })]);
  loadCharacterPosts(character.id).then((posts) => {
    if (!sessionContextIsCurrent(session)) return;
    clear(body);
    if (!posts.length) {
      body.appendChild(h("div", { class: "empty compact", text: "還沒有貼文，從「貼文」動作開始。" }));
      return;
    }
    for (const post of posts) body.appendChild(renderPostCard(character, post));
  }).catch((error) => {
    if (isStaleSessionError(error)) return;
    clear(body);
    body.appendChild(h("div", { class: "empty compact", text: `貼文讀取失敗：${error.message}` }));
  });
  return h("section", { class: "library-subsection" }, [body]);
}

const CHARACTER_MEDIA_TABS = [
  ["image", "圖片"],
  ["video", "影片"],
  ["audio", "聲音"],
  ["text", "文字"],
];

const CATALOGUE_FIELDS = { source: "來源", genre: "題材", scene: "場景", pose: "姿勢", level: "構圖層級", outfit: "服裝／造型", purpose: "用途", review: "審核狀態", collection: "系列" };
const CATALOGUE_LABELS = { castle: "Castle 作品", scene: "場景產線", crawl: "影片採集", pictures: "圖片採集", approved: "已核准", staged: "待審", rejected: "已退件", retired: "已封存" };

function renderCatalogueExplorer(options = {}) {
  const session = captureSessionContext();
  const key = JSON.stringify([options.characterId || "", options.kind || "", Boolean(options.global)]);
  const state = catalogueViewState.get(key) || { filters: {}, q: "", order: "newest", character: options.characterId || "", kind: options.kind || "", data: null, offset: 0 };
  catalogueViewState.set(key, state);
  const wrap = h("section", { class: "panel catalogue-explorer" });
  const toolbar = h("div", { class: "catalogue-toolbar" });
  const search = h("input", { type: "search", value: state.q, placeholder: "搜尋名稱、標籤或場景", "aria-label": "搜尋素材" });
  const sort = h("select", { "aria-label": "排序" }, [["newest", "最新在前"], ["oldest", "最舊在前"], ["title", "名稱排序"]].map(([value, label]) => h("option", { value, text: label, selected: state.order === value ? "" : null })));
  const status = h("div", { class: "catalogue-status", role: "status", "aria-live": "polite" });
  const chips = h("div", { class: "catalogue-chips" });
  const filters = h("div", { class: "catalogue-filters" });
  const sidebar = h("details", { class: "catalogue-sidebar", open: window.matchMedia("(max-width: 767px)").matches ? null : "" }, [h("summary", { text: "分類篩選" }), filters]);
  const grid = h("div", { class: "catalogue-grid", id: options.global ? "libraryGallery" : null });
  const next = h("button", { class: "ghost small", type: "button", text: "下一頁" });
  const prev = h("button", { class: "ghost small", type: "button", text: "上一頁" });
  const pageLabel = h("span");
  const pager = h("div", { class: "catalogue-pagination" }, [prev, pageLabel, next]);
  let sequence = 0;
  const relevant = () => sessionContextIsCurrent(session) && wrap.isConnected;
  function labelControl(label, control) { return h("label", { class: "catalogue-field" }, [h("span", { text: label }), control]); }
  function selectControl(label, values, selected, change) {
    const control = h("select", { "aria-label": label }, values.map(([value, text]) => h("option", { value, text, selected: value === selected ? "" : null })));
    control.addEventListener("change", () => { change(control.value); state.offset = 0; load(); });
    return labelControl(label, control);
  }
  function paintFilters(data) {
    clear(filters);
    if (options.global) {
      filters.appendChild(selectControl("角色", [["", "全部角色"], ["_unassigned", "未分類角色"], ...libraryCharacters().map(c => [c.id, c.name])], state.character, value => { state.character = value; }));
      filters.appendChild(selectControl("素材類型", [["", "全部類型"], ["image", "圖片"], ["video", "影片"], ["audio", "聲音"]], state.kind, value => { state.kind = value; }));
    }
    for (const [field, label] of Object.entries(CATALOGUE_FIELDS)) {
      const values = (data.facets && data.facets[field]) || [];
      if (!values.length && !state.filters[field]) continue;
      const choices = [["", `全部${label}`], ...values.map(row => [row.value, `${CATALOGUE_LABELS[row.value] || row.value} (${row.count})`])];
      if (state.filters[field] && !choices.some(row => row[0] === state.filters[field])) choices.push([state.filters[field], state.filters[field]]);
      filters.appendChild(selectControl(label, choices, state.filters[field] || "", value => { state.filters[field] = value; }));
    }
    clear(chips);
    for (const [field, value] of Object.entries(state.filters)) {
      if (!value) continue;
      chips.appendChild(h("button", { type: "button", class: "ghost small", text: `${CATALOGUE_FIELDS[field]}：${CATALOGUE_LABELS[value] || value} ×`, "aria-label": `移除${CATALOGUE_FIELDS[field]}篩選`, onclick: () => { delete state.filters[field]; state.offset = 0; load(); } }));
    }
    if (state.q || Object.values(state.filters).some(Boolean)) chips.appendChild(h("button", { type: "button", class: "ghost small", text: "清除條件", onclick: () => { state.q = ""; search.value = ""; state.filters = {}; state.offset = 0; load(); } }));
  }
  function paint(data) {
    clear(grid);
    paintFilters(data);
    const items = data.items || [];
    if (!items.length) grid.appendChild(h("div", { class: "empty", text: data.index && data.index.updated ? "沒有符合條件的素材。試著移除部分篩選。" : "素材索引建立中，稍後按更新即可查看。" }));
    items.forEach((item, index) => {
      const classification = item.classification || {};
      const media = item.kind === "audio" ? h("span", { class: "catalogue-audio", text: "聲音" }) : h("img", { src: item.thumbUrl || item.url, alt: "", loading: "lazy", decoding: "async", width: "480", height: "600" });
      const card = h("button", { type: "button", class: "catalogue-card", "aria-label": `查看 ${item.title || item.filename}`, onclick: event => (itemRenderKind(item) === "image" ? openCatalogueLightbox : openCatalogueItem)(item, event.currentTarget, { items, index, onSelect: options.onSelect, onChanged: () => load() }) }, [
        h("span", { class: "catalogue-cover" }, [media, item.kind === "video" ? h("span", { class: "catalogue-media-label", text: "影片" }) : null]),
        h("span", { class: "catalogue-card-copy" }, [
          h("strong", { text: item.title || item.filename }),
          h("span", { text: [item.character && item.character.name, classification.scene, classification.pose].filter(Boolean).join(" · ") || "尚未分類" }),
          h("small", { text: [CATALOGUE_LABELS[classification.source] || classification.source, CATALOGUE_LABELS[classification.review] || classification.review, item.duplicateCount > 1 ? `${item.duplicateCount} 份相同檔案` : ""].filter(Boolean).join(" · ") }),
        ]),
      ]);
      grid.appendChild(card);
    });
    status.textContent = `${data.total.toLocaleString()} 件素材${data.total ? ` · ${state.offset + 1}–${state.offset + items.length}` : ""}`;
    pageLabel.textContent = `第 ${Math.floor(state.offset / 48) + 1} 頁`;
    prev.disabled = state.offset === 0;
    next.disabled = !data.hasMore;
    pager.hidden = data.total <= 48;
  }
  async function load() {
    const turn = ++sequence;
    status.textContent = state.data ? "更新中…" : "載入素材…";
    grid.setAttribute("aria-busy", "true");
    const params = new URLSearchParams({ character: state.character, kind: state.kind, q: state.q, order: state.order, offset: String(state.offset), limit: "48", ...state.filters });
    if (options.global ? showHiddenGlobalLibrary : showHiddenCharacterLibrary) params.set("hidden", "1");
    try {
      const data = await api(`/api/library?${params}`);
      if (!relevant() || sequence !== turn) return;
      state.data = data;
      state.loadedAt = Date.now();
      paint(data);
    } catch (error) {
      if (!relevant() || sequence !== turn || isStaleSessionError(error)) return;
      status.textContent = "素材載入失敗，請按更新重試。";
      if (!state.data) { clear(grid); grid.appendChild(h("div", { class: "empty", text: "目前無法讀取素材庫。" })); }
    } finally {
      if (sequence === turn) grid.removeAttribute("aria-busy");
    }
  }
  let debounce;
  search.addEventListener("input", () => { state.q = search.value.trim(); state.offset = 0; clearTimeout(debounce); debounce = setTimeout(load, 250); });
  sort.addEventListener("change", () => { state.order = sort.value; state.offset = 0; load(); });
  next.onclick = () => { state.offset += 48; load(); };
  prev.onclick = () => { state.offset = Math.max(0, state.offset - 48); load(); };
  toolbar.append(search, sort, h("button", { type: "button", class: "ghost small", text: "更新", onclick: load }));
  wrap.append(toolbar, chips, h("div", { class: "catalogue-layout" }, [sidebar, h("div", { class: "catalogue-results" }, [status, grid, pager])]));
  if (state.data) paint(state.data);
  queueMicrotask(() => { if (!state.loadedAt || Date.now() - state.loadedAt > 30000) load(); });
  return wrap;
}

function openCatalogueLightbox(item, opener, options = {}) {
  const items = (options.items || [item]).filter(row => itemRenderKind(row) === "image");
  let index = Math.max(0, items.findIndex(row => row.libraryId === item.libraryId));
  let zoomed = false;
  const image = h("img", { alt: "", decoding: "async", draggable: "false" });
  const message = h("span", { class: "catalogue-lightbox-message", role: "status" });
  const stage = h("div", { class: "catalogue-lightbox-stage", tabindex: "0", "aria-label": "圖片預覽，點圖片切換原尺寸" }, [image, message]);
  const caption = h("div", { class: "catalogue-lightbox-caption", "aria-live": "polite" });
  const previous = h("button", { type: "button", class: "ghost small", text: "上一張", "aria-label": "上一張圖片", onclick: () => show(index - 1) });
  const next = h("button", { type: "button", class: "ghost small", text: "下一張", "aria-label": "下一張圖片", onclick: () => show(index + 1) });
  const position = h("span", { class: "catalogue-lightbox-position" });
  const zoom = h("button", { type: "button", class: "ghost small", text: "原尺寸", "aria-pressed": "false", onclick: () => setZoom(!zoomed) });
  const detail = h("button", { type: "button", class: "ghost small", text: "分類與詳情", onclick: () => openCatalogueItem(items[index], opener, { ...options, items, index }) });
  const download = h("a", { class: "ghost small", download: "", text: "下載" });
  const actions = h("div", { class: "catalogue-lightbox-actions" }, [zoom, detail, download]);
  if (options.onSelect) actions.appendChild(h("button", { type: "button", class: "primary small", text: "使用這張圖", onclick: () => options.onSelect(items[index]) }));
  const content = h("div", { class: "catalogue-lightbox" }, [
    stage, caption, h("div", { class: "catalogue-lightbox-toolbar" }, [h("div", { class: "catalogue-lightbox-navigation" }, [previous, position, next]), actions]),
  ]);
  function setZoom(value) {
    zoomed = value;
    stage.classList.toggle("is-zoomed", zoomed);
    zoom.textContent = zoomed ? "適合畫面" : "原尺寸";
    zoom.setAttribute("aria-pressed", String(zoomed));
    stage.scrollTo(0, 0);
  }
  function show(target) {
    if (target < 0 || target >= items.length) return;
    index = target;
    const current = items[index];
    setZoom(false);
    image.hidden = true;
    message.hidden = false;
    message.textContent = "載入大圖…";
    image.alt = current.title || current.filename || "素材圖片";
    image.src = current.url;
    caption.textContent = image.alt;
    if (current.duplicateCount > 1) caption.textContent += ` · ${current.duplicateCount} 份相同檔案，原檔均保留`;
    position.textContent = `${index + 1} / ${items.length}`;
    previous.disabled = index === 0;
    next.disabled = index === items.length - 1;
    download.href = current.downloadUrl || current.url;
  }
  image.addEventListener("load", () => { image.hidden = false; message.hidden = true; });
  image.addEventListener("error", () => { message.hidden = false; message.textContent = "大圖載入失敗，請重新開啟或下載原檔。"; });
  stage.addEventListener("click", event => { if (event.target === image) setZoom(!zoomed); else if (event.target === stage) dismissTopSheet(); });
  stage.addEventListener("keydown", event => {
    if (event.target === stage && ["Enter", " "].includes(event.key)) { event.preventDefault(); setZoom(!zoomed); }
  });
  show(index);
  openSheet({ id: "catalogue-lightbox", title: "圖片預覽", opener, className: "catalogue-lightbox-sheet", content, onKeyDown: event => {
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        if (zoomed && event.target === stage) return;
        event.preventDefault();
        show(index + (event.key === "ArrowRight" ? 1 : -1));
      }
  } });
}

function openCatalogueItem(item, opener, options = {}) {
  const meta = item.classification || {};
  const details = h("div", { class: "catalogue-detail-info" });
  const fields = {};
  const form = h("form", { class: "catalogue-edit-form" });
  const addField = (key, label, value) => {
    const input = h("input", { value: value || "", maxlength: "240" });
    fields[key] = input;
    form.appendChild(h("label", { class: "catalogue-field" }, [h("span", { text: label }), input]));
  };
  addField("title", "名稱", item.title);
  for (const key of ["genre", "scene", "pose", "level", "outfit", "purpose", "collection"]) addField(key, CATALOGUE_FIELDS[key], meta[key]);
  addField("tags", "標籤（以逗號分隔）", Array.isArray(meta.tags) ? meta.tags.join(", ") : "");
  const characterSelect = h("select", { multiple: "", size: "4" }, libraryCharacters().map(c => h("option", { value: c.id, text: c.name, selected: libraryItemCharacterIds(item).includes(c.id) ? "" : null })));
  form.appendChild(h("label", { class: "catalogue-field" }, [h("span", { text: "角色分類（可複選）" }), characterSelect]));
  const saveStatus = h("p", { role: "status", class: "panel-sub" });
  const save = h("button", { type: "submit", class: "primary small", text: "儲存分類" });
  form.append(save, saveStatus);
  form.addEventListener("submit", async event => {
    event.preventDefault(); save.disabled = true;
    try {
      const classification = Object.fromEntries(Object.entries(fields).map(([k, field]) => [k, k === "tags" ? field.value.split(/[,，]/).map(v => v.trim()).filter(Boolean) : field.value.trim()]));
      classification.characterIds = [...characterSelect.selectedOptions].map(option => option.value);
      await api("/api/library/classify", { method: "POST", body: JSON.stringify({ id: item.libraryId, classification }) });
      saveStatus.textContent = "分類已儲存。";
      catalogueViewState.forEach(state => { state.loadedAt = 0; });
      if (options.onChanged) options.onChanged();
    } catch (error) { saveStatus.textContent = error.message; }
    finally { save.disabled = false; }
  });
  details.append(h("h3", { text: item.title || item.filename }), h("p", { class: "panel-sub", text: [CATALOGUE_LABELS[meta.source] || meta.source, CATALOGUE_LABELS[meta.review] || meta.review, formatBytes(item.size)].filter(Boolean).join(" · ") }));
  const actions = h("div", { class: "catalogue-detail-actions" }, [h("a", { class: "ghost small", href: item.downloadUrl || item.url, download: "", text: "下載原檔" })]);
  if (itemRenderKind(item) === "image") actions.prepend(h("button", { type: "button", class: "ghost small", text: "放大檢視", onclick: () => openCatalogueLightbox(item, opener, options) }));
  if (item.duplicateCount > 1) details.appendChild(h("p", { class: "panel-sub", text: `此圖合併顯示 ${item.duplicateCount} 份相同檔案，所有原檔均保留。` }));
  if (item.type === "output") actions.appendChild(h("button", { type: "button", class: "ghost small", text: "作品操作與生成資訊", onclick: () => sheetAction(() => openResultDetail(item, opener)) }));
  if (item.sourceUrl && /^https?:\/\//.test(item.sourceUrl)) actions.appendChild(h("a", { class: "ghost small", href: item.sourceUrl, target: "_blank", rel: "noopener noreferrer", text: "查看來源" }));
  if (options.onSelect) actions.appendChild(h("button", { type: "button", class: "primary small", text: "使用這個素材", onclick: () => options.onSelect(item) }));
  else actions.appendChild(h("button", { type: "button", class: "ghost small", text: "帶入工作流", onclick: () => openMaterialWorkflowPicker(item) }));
  details.append(actions, h("details", { class: "catalogue-edit" }, [h("summary", { text: "編輯分類" }), form]));
  const navigation = h("div", { class: "catalogue-detail-actions" });
  for (const [delta, text] of [[-1, "上一張"], [1, "下一張"]]) {
    const index = Number(options.index || 0) + delta;
    const sibling = (options.items || [])[index];
    navigation.appendChild(h("button", { type: "button", class: "ghost small", text, disabled: sibling ? null : "", onclick: () => dismissTopSheet(() => openCatalogueItem(sibling, opener, { ...options, index })) }));
  }
  details.appendChild(navigation);
  openSheet({ id: "catalogue-detail", title: "素材詳情", opener, className: "catalogue-detail-sheet", content: h("div", { class: "catalogue-detail" }, [h("div", { class: "catalogue-detail-preview" }, [renderDetailMedia(item)]), details]) });
}

function openMaterialWorkflowPicker(item) {
  const content = h("div", { class: "catalogue-workflow-picker" });
  const available = features.filter(feature => !feature.uiHidden && !feature.characterOnly && (!feature.operatorOnly || isOperator()) && (feature.uploads || []).some(upload => (upload.accept || "").includes(`${item.kind}/`)));
  const status = h("p", { role: "status" });
  for (const feature of available) {
    for (const upload of (feature.uploads || []).filter(upload => (upload.accept || "").includes(`${item.kind}/`))) {
      content.appendChild(h("button", { class: "shell-menu-row", type: "button", text: `${feature.title} · ${upload.label || upload.id}`, onclick: async event => {
        event.currentTarget.disabled = true;
        try {
          const data = await api("/api/library/import", { method: "POST", body: JSON.stringify({ id: item.libraryId, slot: upload.id }) });
          sheetAction(() => { navigate(studioRoute(feature.id, "")); serverFiles[upload.id] = data.serverFile || data.filename; renderWorkspace(); });
        } catch (error) { status.textContent = error.message; event.currentTarget.disabled = false; }
      } }));
    }
  }
  if (!available.length) content.appendChild(h("p", { text: "目前没有適用此素材類型的工作流。" }));
  content.appendChild(status);
  openSheet({ id: "material-workflow", title: "選擇工作流與輸入欄位", content });
}

function openMaterialCollect() {
  const url = h("input", { type: "url", placeholder: "貼上圖片、網頁或影片網址", required: "" });
  const mode = h("select", {}, [h("option", { value: "pictures", text: "網頁／圖片" }), h("option", { value: "video", text: "影片（FB、YouTube、IG 等）" })]);
  const purpose = h("input", { placeholder: "例如：動作參考、服裝、場景" });
  const status = h("p", { role: "status" });
  const submit = h("button", { type: "submit", class: "primary small", text: "開始採集" });
  const form = h("form", { class: "catalogue-collect" }, [h("label", {}, [h("span", { text: "來源網址" }), url]), h("label", {}, [h("span", { text: "素材類型" }), mode]), h("label", {}, [h("span", { text: "用途" }), purpose]), submit, status]);
  form.addEventListener("submit", async event => {
    event.preventDefault(); submit.disabled = true; status.textContent = "提交採集…";
    try {
      const job = await api("/api/library/collect", { method: "POST", body: JSON.stringify({ url: url.value.trim(), mode: mode.value, purpose: purpose.value.trim() }) });
      async function poll() {
        if (!form.isConnected) return;
        try {
          const data = await api(`/api/library/collect?id=${encodeURIComponent(job.id)}`);
          status.textContent = data.message || "採集中…";
          if (data.state === "running") setTimeout(poll, 2000);
          else { submit.disabled = false; catalogueViewState.clear(); }
        } catch (error) { status.textContent = error.message; submit.disabled = false; }
      }
      poll();
    } catch (error) { status.textContent = error.message; submit.disabled = false; }
  });
  openSheet({ id: "material-collect", title: "採集素材", content: form });
}

function renderCharacterMediaTabs(character, active) {
  return renderTabBar({
    label: "素材類型",
    className: "character-media-tabs",
    tabs: CHARACTER_MEDIA_TABS.map(([id, label]) => ({
      id: `character-media-tab-${id}`,
      panelId: "character-media-panel",
      label,
      active: active === id,
      onclick: () => navigate({ view: "character-library", characterId: character.id, media: id }),
    })),
  });
}

function renderCharacterMediaPanel(character, active, collections) {
  let content;
  if (active !== "text") {
    content = renderCatalogueExplorer({ characterId: character.id, kind: active });
  } else if (active === "video") {
    content = renderLibraryTopSection("影片", [renderVideoLibrary(character, collections.videos)], "生成影片、跳舞與對嘴作品集中在這裡。");
  } else if (active === "audio") {
    content = renderLibraryTopSection("聲音", [renderGeneratedLibrarySection(character, "聲音作品", collections.sounds, "目前沒有聲音作品，說話生成後會集中在這裡。")], "角色語音輸出集中在這裡。");
  } else if (active === "text") {
    content = renderLibraryTopSection("文字", [renderTextLibrary(character)], "已生成並儲存的角色貼文。");
  } else {
    content = renderPhotoLibrary(character, collections.photos);
  }
  const panel = h("div", {
    id: "character-media-panel",
    class: "character-media-panel",
    role: "tabpanel",
    tabindex: "0",
    "aria-labelledby": `character-media-tab-${active}`,
  }, [content]);
  return panel;
}

function renderCharacterLibraryRoute() {
  const character = routeCharacter();
  if (!character) {
    const ws = beginWorkspaceRender();
    ws.appendChild(h("section", { class: "panel" }, [h("div", { class: "empty", text: "找不到這個角色。" })]));
    return;
  }
  const works = worksForCharacter(character.id).filter((item) => showHiddenCharacterLibrary || !item.hidden);
  const photos = works.filter((item) => item.kind === "image");
  const videos = works.filter((item) => item.kind === "video");
  const sounds = works.filter((item) => item.kind === "audio");
  const activeMedia = ["image", "video", "audio", "text"].includes(currentRoute.media) ? currentRoute.media : "image";
  const host = ensureCharacterRouteFrame(character);
  const next = h("div", { class: "character-route-content" }, [
    h("section", { class: "panel character-library-heading" }, [
      renderSectionHeader("角色素材", "", [
          activeMedia !== "text" ? h("button", {
            class: `ghost small${showHiddenCharacterLibrary ? " active" : ""}`,
            type: "button",
            text: showHiddenCharacterLibrary ? "隱藏已隱藏" : "顯示隱藏",
            onclick: () => { showHiddenCharacterLibrary = !showHiddenCharacterLibrary; renderCharacterLibraryRoute(); },
          }) : null,
          h("button", {
            class: "ghost small",
            type: "button",
            text: "更新",
            onclick: async () => {
              try {
                catalogueViewState.forEach(state => { state.loadedAt = 0; });
                await Promise.all([
                  activeMedia === "text" ? loadCharacterPosts(character.id, true) : Promise.resolve(),
                ]);
              } catch (error) {
                showAlert(`素材更新失敗：${error.message}`, true);
              }
              if (currentRoute.view === "character-library" && currentRoute.characterId === character.id) renderCharacterLibraryRoute();
            },
          }),
      ], { titleClass: "panel-h1", level: "h1", actionsClass: "library-actions" }),
    ]),
    renderCharacterMediaTabs(character, activeMedia),
    renderCharacterMediaPanel(character, activeMedia, { photos, videos, sounds }),
  ]);
  bindCharacterRoutePanel(next, "character-local-tab-library");
  replaceCharacterRouteContent(host, next);
}

function renderCharacterWardrobeRoute() {
  const character = routeCharacter();
  if (!character) {
    const ws = beginWorkspaceRender();
    ws.appendChild(h("section", { class: "panel" }, [h("div", { class: "empty", text: "找不到這個角色。" })]));
    return;
  }
  const host = ensureCharacterRouteFrame(character);
  const next = h("div", { class: "character-route-content" }, [
    h("section", { class: "panel character-wardrobe-route" }, [
      renderSectionHeader("衣櫃", "角色只有一個共用衣櫃；各項創作功能會依相容性顯示可用造型。", [
        h("button", {
          class: "ghost small",
          type: "button",
          text: "更新",
          onclick: async () => {
            try {
              await loadWardrobe(character.id, true);
              if (currentRoute.view === "character-wardrobe" && currentRoute.characterId === character.id) {
                renderCharacterWardrobeRoute();
              }
            } catch (error) {
              showAlert(`衣櫃更新失敗：${error.message}`, true);
            }
          },
        }),
      ], { titleClass: "panel-h1", level: "h1", actionsClass: "library-actions" }),
      renderWardrobeLibrary(character),
    ]),
  ]);
  bindCharacterRoutePanel(next, "character-local-tab-wardrobe");
  replaceCharacterRouteContent(host, next);
}

function renderCharacterSettingsRoute() {
  const character = routeCharacter();
  if (!character) {
    const ws = beginWorkspaceRender();
    ws.appendChild(h("section", { class: "panel" }, [h("div", { class: "empty", text: "找不到這個角色。" })]));
    return;
  }
  const editable = canEditCharacter(character);
  const host = ensureCharacterRouteFrame(character);
  const next = h("div", { class: "character-route-content" }, [
    h("section", { class: "panel character-settings-route" }, [
      renderSectionHeader("設定", editable ? "管理角色資料、聲音與已驗證功能。" : "你可以使用這個角色，但沒有權限修改其設定。", [], {
        titleClass: "panel-h1",
        level: "h1",
      }),
      editable
        ? renderCharacterEditor(character)
        : h("div", { class: "panel-note character-settings-readonly", role: "status" }, [
          h("strong", { text: "唯讀／沒有修改權限" }),
          h("span", { text: " 只有管理員或這個實驗角色的擁有者可以儲存設定；此頁不會建立可提交表單。" }),
        ]),
    ]),
  ]);
  bindCharacterRoutePanel(next, "character-local-tab-settings");
  replaceCharacterRouteContent(host, next);
}

function enabledCharacterActions(character) {
  const ids = new Set(enabledCharacterCapabilities(character).map((capability) => String(capability.actionId || "")));
  return characterActions.filter((action) => ids.has(action.id));
}

function eligibleCharacterActions(character) {
  const ids = new Set(eligibleCharacterCapabilities(character).map((capability) => String(capability.actionId || "")));
  return characterActions.filter((action) => ids.has(action.id));
}

function renderCharacterEditor(character) {
  if (!canEditCharacter(character)) return h("div", { class: "empty compact", text: "沒有權限" });
  const style = character.style || {};
  const lm = character.languageModel || {};
  const voice = character.voice || {};
  const assets = character.assets || {};
  const form = h("form", { class: "character-editor", onsubmit: (event) => { event.preventDefault(); saveCharacter(character, event.target); } });
  const official = character.official === true;
  if (official) {
    form.appendChild(h("div", { class: "panel-note" }, [
      h("strong", { text: "官方角色的身份與分類由系統管理。" }),
      h("span", { text: " 這些設定決定要使用動漫或真人模組，因此在這裡只讀；可調整已驗證功能、聲音庫綁定與資產備註。" }),
    ]));
    const readonlyRows = [
      ["角色", character.name || "—"],
      ["資料類型（相容欄位）", characterTypeLabel(character)],
      ["視覺分類", characterVisualClassLabel(character)],
      ["內部分類", character.kind || "—"],
      ["群組", character.group || "—"],
      ["視覺模組", style.base || "—"],
      ["文字模組", [lm.provider, lm.model].filter(Boolean).join(" / ") || "—"],
      ["聲音模組", [voice.engine, voice.persona].filter(Boolean).join(" / ") || "—"],
    ];
    form.appendChild(h("div", { class: "character-editor-grid" }, readonlyRows.map(([label, value]) => (
      h("div", { class: "field role-bound-summary" }, [
        h("span", { class: "field-label", text: label }),
        h("div", { class: "bound-value", text: value }),
      ])
    ))));
    form.appendChild(labeledField("聲音庫 IDs", h("input", { name: "voiceLibraryIds", value: (voice.voiceLibraryIds || []).join(", "), maxlength: "500" })));
  } else {
  form.appendChild(h("div", { class: "character-editor-grid" }, [
    labeledField("角色名稱", h("input", { name: "name", value: character.name || "", maxlength: "120" })),
    isAdmin() ? labeledField("視覺分類（決定可用模組）", h("select", { name: "visualClass" }, [
      h("option", { value: "unknown", text: "待補資料（缺少角色素材）", selected: characterVisualClass(character) === "unknown" }),
      h("option", { value: "anime", text: "動漫", selected: characterVisualClass(character) === "anime" }),
      h("option", { value: "realistic", text: "真人／寫實", selected: characterVisualClass(character) === "realistic" }),
      h("option", { value: "illustration", text: "插畫", selected: characterVisualClass(character) === "illustration" }),
    ])) : null,
    labeledField("內部分類", h("select", { name: "kind" }, [
      h("option", { value: "human", text: "真人", selected: character.kind === "human" }),
      h("option", { value: "ai_sister", text: "AI 姊妹", selected: character.kind === "ai_sister" }),
      h("option", { value: "bestie", text: "Bestie / Guest", selected: character.kind === "bestie" }),
      h("option", { value: "custom", text: "自訂", selected: !["human", "ai_sister", "bestie"].includes(character.kind) }),
    ])),
    labeledField("群組", h("select", { name: "group" }, [
      h("option", { value: "host", text: "Host", selected: character.group === "host" }),
      h("option", { value: "sisterhood", text: "AI 姊妹", selected: character.group === "sisterhood" }),
      h("option", { value: "besties", text: "Besties", selected: character.group === "besties" }),
      h("option", { value: "custom", text: "自訂", selected: !["host", "sisterhood", "besties"].includes(character.group) }),
    ])),
    labeledField("一句話定位", h("input", { name: "tagline", value: character.tagline || "", maxlength: "240" })),
    labeledField("原產 / 來源", h("input", { name: "origin", value: character.origin || "", maxlength: "500" })),
    labeledField("代表色", h("input", { name: "color", value: character.color || "", maxlength: "32" })),
    labeledField("基本 style", h("select", { name: "styleBase" }, [
      h("option", { value: "realistic", text: "寫實", selected: style.base === "realistic" }),
      h("option", { value: "anime", text: "動漫", selected: style.base === "anime" }),
      h("option", { value: "chibi", text: "Chibi", selected: style.base === "chibi" }),
      h("option", { value: "selectable", text: "每次選擇", selected: style.base === "selectable" }),
    ])),
  ]));
  form.appendChild(labeledField("Profile", h("textarea", { name: "profile", rows: "3", text: character.profile || "" })));
  form.appendChild(labeledField("Personality", h("textarea", { name: "personality", rows: "4", text: character.personality || "" })));
  form.appendChild(labeledField("固定 prompt", h("textarea", { name: "fixedPrompt", rows: "3", text: style.fixedPrompt || "" })));
  form.appendChild(labeledField("負向 prompt", h("textarea", { name: "negativePrompt", rows: "2", text: style.negativePrompt || "" })));
  form.appendChild(labeledField("Style 細分", h("input", { name: "styleSubstyle", value: style.substyle || "", maxlength: "240" })));
  form.appendChild(h("div", { class: "character-editor-grid" }, [
    labeledField("語言模組 provider", h("input", { name: "lmProvider", value: lm.provider || "", maxlength: "120" })),
    labeledField("語言模組 model", h("input", { name: "lmModel", value: lm.model || "", maxlength: "160" })),
    labeledField("API / 設定來源", h("input", { name: "lmApiOwner", value: lm.apiOwner || "", maxlength: "160" })),
    labeledField("聲音 engine", h("input", { name: "voiceEngine", value: voice.engine || "", maxlength: "160" })),
    labeledField("Breezy persona", h("input", { name: "voicePersona", value: voice.persona || "", maxlength: "120" })),
    labeledField("聲音庫 IDs", h("input", { name: "voiceLibraryIds", value: (voice.voiceLibraryIds || []).join(", "), maxlength: "500" })),
  ]));
  form.appendChild(labeledField("聲音訓練來源", h("textarea", { name: "voiceTrainingSource", rows: "2", text: voice.trainingSource || "" })));
  }
  if (!official) {
    form.appendChild(h("div", { class: "panel-note character-editor-wardrobe-note" }, [
      h("span", { text: "服裝素材集中在角色造型庫；角色設定只保存資產備註。" }),
      h("button", {
        class: "ghost small",
        type: "button",
        text: "查看角色造型",
        onclick: () => navigate({ view: "character-wardrobe", characterId: character.id }),
      }),
    ]));
  }
  form.appendChild(labeledField("資產備註", h("textarea", { name: "assetNotes", rows: "2", text: assets.notes || "" })));
  const eligibleActions = eligibleCharacterActions(character);
  form.appendChild(h("div", { class: "character-action-checks" }, eligibleActions.map((action) => (
    (() => {
      const capability = eligibleCharacterCapabilities(character).find((item) => item.actionId === action.id) || {};
      return h("label", { class: "check-row" }, [
        h("input", { type: "checkbox", name: `action:${action.id}`, checked: (character.actions || []).includes(action.id) }),
        h("span", {}, [
          h("span", { text: action.label }),
          capability.runtimeReady === false
            ? h("small", { text: `目前不可用：${capability.runtimeReason || "服務尚未就緒"}` })
            : null,
        ]),
      ]);
    })()
  ))));
  form.appendChild(h("div", { class: "character-editor-actions" }, [
    h("button", { class: "primary", type: "submit", text: "儲存角色" }),
    character.type === "experimental" || character.group === "custom" ? h("button", { class: "job-cancel danger", type: "button", text: "刪除", onclick: () => deleteSelectedCharacter(character) }) : null,
  ]));
  return form;
}

function renderCharactersView() {
  const ws = beginWorkspaceRender();
  if (!characters.length && !providerCharacters.length && !providerCharactersError) {
    ws.appendChild(h("section", { class: "panel" }, [
      h("div", { class: "empty", text: "還沒有角色。先建立一個實驗角色，再開始創作。" }),
      renderExperimentalCreateForm(),
    ]));
    return;
  }
  ws.appendChild(h("div", { class: "characters-home-v2" }, [
    h("section", { class: "wall-head" }, [
      h("div", {}, [
        h("h1", { text: "角色卡片牆" }),
        h("p", { text: "選一個角色開始創作。" }),
      ]),
      isAdmin() ? h("button", { class: "ghost", type: "button", text: "+ 新角色", onclick: createCharacter }) : null,
    ]),
    renderCharacterCardWall(),
  ]));
}

// ---- studio view (active feature panel) -----------------------------------
function resetGetters() {
  for (const key of Object.keys(fieldGetters)) delete fieldGetters[key];
  for (const key of Object.keys(fileInputs)) delete fileInputs[key];
  for (const key of Object.keys(serverFiles)) delete serverFiles[key];
}

function readinessPill(feature) {
  if (window.WebComfyDocs) return { cls: "neutral", label: "功能說明" };
  if (interactiveReady(feature)) return { cls: "ok", label: "就緒" };
  if (feature.runnable) return { cls: "missing", label: "缺件" };
  if (feature.kind === "storage") return { cls: "neutral", label: "工具" };
  if (feature.kind === "text") return { cls: "neutral", label: "文字" };
  return { cls: "muted", label: "待開放" };
}

function openStudioToolPicker(opener) {
  const search = h("input", {
    class: "studio-tool-search",
    type: "search",
    placeholder: "搜尋創作工具",
    "aria-label": "搜尋創作工具",
    autocomplete: "off",
  });
  const groups = h("div", { class: "studio-tool-sheet-groups" });
  const empty = h("div", { class: "empty compact hidden", text: "沒有符合條件的工具。" });
  const searchable = [];
  for (const studio of studios) {
    const list = studioFeatures(studio.id);
    if (!list.length) continue;
    const rows = h("div", { class: "studio-tool-sheet-list" });
    const section = h("section", { class: "studio-tool-sheet-group" }, [
      h("h3", { text: `${studio.icon || ""} ${studio.title}`.trim() }),
      rows,
    ]);
    for (const feature of list) {
      const pill = readinessPill(feature);
      const row = h("button", {
        class: `studio-tool-sheet-row${feature.id === currentFeatureId ? " active" : ""}`,
        type: "button",
        "aria-current": feature.id === currentFeatureId ? "true" : null,
        dataset: { search: `${studioToolTitle(feature)} ${studioToolSubtitle(feature) || ""} ${studio.title || ""}`.toLocaleLowerCase("zh-Hant") },
        onclick: () => sheetAction(() => selectFeature(feature.id)),
      }, [
        h("span", { class: "studio-tool-sheet-icon", text: feature.icon || "•", "aria-hidden": "true" }),
        h("span", { class: "studio-tool-sheet-copy" }, [
          h("strong", { text: studioToolTitle(feature) }),
          h("span", { text: studioToolSubtitle(feature) || studio.title }),
        ]),
        h("span", { class: `pill ${pill.cls}`, text: feature.id === currentFeatureId ? "目前" : pill.label }),
      ]);
      rows.appendChild(row);
      searchable.push({ row, section });
    }
    groups.appendChild(section);
  }
  search.addEventListener("input", () => {
    const query = search.value.trim().toLocaleLowerCase("zh-Hant");
    let visible = 0;
    for (const item of searchable) {
      const matches = !query || item.row.dataset.search.includes(query);
      item.row.classList.toggle("hidden", !matches);
      if (matches) visible += 1;
    }
    for (const section of groups.querySelectorAll(".studio-tool-sheet-group")) {
      section.classList.toggle("hidden", !section.querySelector(".studio-tool-sheet-row:not(.hidden)"));
    }
    empty.classList.toggle("hidden", visible > 0);
  });
  openSheet({
    id: "studio-tool-picker",
    title: "選擇工具",
    opener,
    className: "studio-tool-picker-sheet",
    content: h("div", { class: "studio-tool-picker" }, [search, groups, empty]),
    onOpen: () => {
      if (!window.matchMedia("(max-width: 767px)").matches) requestAnimationFrame(() => search.focus({ preventScroll: true }));
    },
  });
}

function renderStudioToolSelector(feature) {
  return h("button", {
    id: "studioToolTrigger",
    class: "studio-tool-trigger",
    type: "button",
    "aria-haspopup": "dialog",
    "aria-expanded": "false",
    onclick: (event) => openStudioToolPicker(event.currentTarget),
  }, [
    h("span", { class: "studio-tool-trigger-icon", text: feature.icon || "•", "aria-hidden": "true" }),
    h("span", { class: "studio-tool-trigger-copy" }, [
      h("small", { text: "創作工具" }),
      h("strong", { text: studioToolTitle(feature) }),
    ]),
    h("span", { text: "切換 ▾" }),
  ]);
}

function clearStudioCharacterContext() {
  activeCharacterId = "";
  navigate(studioRoute(currentFeatureId || "", ""));
  showAlert("已切換到一般創作工具。");
}

function renderStage() {
  const ws = beginWorkspaceRender();
  resetGetters();
  const feature = currentFeature();
  if (!feature) {
    const character = activeCharacter();
    const contextualCharacterId = currentRoute.view === "studio" ? String(currentRoute.characterId || "") : "";
    const missingCharacter = Boolean(contextualCharacterId && !character);
    const fallback = studioRouteProblem ? compatibleStudioFeature("") : null;
    ws.appendChild(h("section", { class: "panel" }, [
      h("div", {
        class: "empty",
        text: missingCharacter
          ? "找不到這個角色，或你目前沒有權限使用。這個連結不會自動改成一般工具。"
          : studioRouteProblem || (character
          ? `「${character.name}」：${characterNoActionMessage(character)}`
          : "目前沒有可用的創作工具。"),
      }),
      fallback ? h("button", {
        class: "primary",
        type: "button",
        text: `改用「${fallback.title}」`,
        onclick: () => navigate(studioRoute(fallback.id, contextualCharacterId)),
      }) : null,
      contextualCharacterId ? h("button", {
        class: fallback ? "ghost" : "primary",
        type: "button",
        text: "查看一般創作工具",
        onclick: clearStudioCharacterContext,
      }) : null,
    ]));
    return;
  }
  ws.appendChild(h("div", { class: "tool-view" }, [
    renderStudioToolSelector(feature),
    renderFeaturePanel(feature),
    renderRecentPanel(),
  ]));
  paintHistory();
}

function renderActiveCharacterBanner(character) {
  const voice = character.voice || {};
  const meta = [
    characterVisualClassLabel(character),
    voice.engine || "",
  ].filter(Boolean).join("・");
  return h("div", { class: "character-context" }, [
    h("div", { class: "character-context-main" }, [
      h("div", { class: "character-context-title", text: `目前角色：${character.name}` }),
      h("div", { class: "character-context-sub", text: meta || "角色 context 會寫入這次生成。" }),
    ]),
    h("div", { class: "character-context-actions" }, [
      canEditCharacter(character) ? h("button", {
        class: "ghost small",
        type: "button",
        text: "角色設定",
        onclick: () => navigate({ view: "character-settings", characterId: character.id }),
      }) : null,
      h("button", {
        class: "ghost small",
        type: "button",
        text: "改用一般工具",
        onclick: clearStudioCharacterContext,
      }),
    ]),
  ]);
}

function renderStudioVersionSelector(feature) {
  const versions = studioToolVersions(feature);
  if (versions.length < 2 || routeCharacterContextId()) return null;
  const select = buildSelect(versions.map((item) => ({
    value: item.id,
    label: `${item.versionLabel}${interactiveReady(item) ? "" : "（尚未就緒）"}`,
  })), feature.id);
  select.id = "studioModelVersion";
  select.addEventListener("change", () => {
    if (!select.isConnected || currentFeatureId !== feature.id) return;
    const target = versions.find((item) => item.id === select.value);
    if (!target) return;
    const text = Object.fromEntries((feature.fields || [])
      .filter((field) => field.type === "textarea" && fieldGetters[field.id])
      .map((field) => [field.id, fieldGetters[field.id]() ]));
    const uploads = Object.fromEntries((feature.uploads || []).map((up) => [up.id, {
      files: fileInputs[up.id]?.files, serverFile: serverFiles[up.id],
    }]));
    selectFeature(target.id);
    for (const input of $("workspace").querySelectorAll("textarea[data-field-id]")) {
      if (Object.hasOwn(text, input.dataset.fieldId)) input.value = text[input.dataset.fieldId];
    }
    for (const up of target.uploads || []) {
      const previous = uploads[up.id];
      const input = fileInputs[up.id];
      if (!previous || !input) continue;
      if (previous.files?.length) {
        input.files = previous.files;
        input.dispatchEvent(new Event("change", { bubbles: true }));
      } else if (previous.serverFile) {
        serverFiles[up.id] = previous.serverFile;
        const preview = input.closest(".dropzone")?.querySelector(".drop-preview");
        if (preview) {
          clear(preview);
          preview.appendChild(h("span", { class: "file-name", text: `已選取：${prefilledFileLabel(previous.serverFile)}` }));
        }
      }
    }
    $("studioModelVersion")?.focus({ preventScroll: true });
  });
  return h("div", { class: "gen-form studio-version-picker" }, [
    h("label", { class: "field" }, [h("span", { class: "field-label", text: "模型版本" }), select]),
  ]);
}

function renderFeaturePanel(feature) {
  const pill = readinessPill(feature);
  const panel = h("section", { class: "panel feature-panel" });
  panel.appendChild(h("div", { class: "panel-head" }, [
    h("div", { class: "panel-head-main" }, [
      h("span", { class: "panel-ico", text: feature.icon || "" }),
      h("div", {}, [
        h("h1", { class: "panel-h1", text: studioToolTitle(feature) }),
        h("p", { class: "panel-sub", text: feature.subtitle || "" }),
      ]),
    ]),
    h("span", { class: `pill ${pill.cls}`, text: pill.label }),
  ]));

  const versionSelector = renderStudioVersionSelector(feature);
  if (versionSelector) panel.appendChild(versionSelector);
  if (window.WebComfyDocs) panel.appendChild(window.WebComfyDocs.featureButton(feature.id));

  if (feature.note) {
    panel.appendChild(h("div", { class: "panel-note" }, [
      h("span", { class: "note-ico", text: "ⓘ" }),
      h("span", { text: feature.note }),
    ]));
  }

  const character = activeCharacter();
  if (character) panel.appendChild(renderActiveCharacterBanner(character));
  const capability = character ? enabledFeatureCapability(character, feature.id) : null;
  if (character && !capability) {
    panel.appendChild(h("div", { class: "empty", text: "這個角色不能使用這項工具，已停止送出。" }));
    return panel;
  }

  if (feature.kind === "text") {
    panel.appendChild(feature.ready ? renderTextForm(feature, capability) : renderGatedPanel(feature));
    return panel;
  }
  if (feature.id === "voice-batch") {
    panel.appendChild(interactiveReady(feature) ? renderVoiceBatchPage() : renderGatedPanel(feature));
    return panel;
  }
  if (!feature.runnable) {
    panel.appendChild(feature.kind === "storage" ? renderStoragePanel(feature) : renderGatedPanel(feature));
    return panel;
  }
  if (feature.id === "voice-speak") {
    panel.appendChild(renderVoiceSpeakForm(feature, capability));
    return panel;
  }
  if (feature.id === "voice-clone") {
    panel.appendChild(renderVoiceCloneForm(feature));
    return panel;
  }
  if (feature.id === "voxcpm-tts") {
    panel.appendChild(renderVoxcpmForm(feature));
    return panel;
  }
  panel.appendChild(renderGenForm(feature, capability));
  return panel;
}

function renderAnimeSceneCardStudioForm(feature, capability, character) {
  const form = h("form", { class: "gen-form anime-scene-card-form", onsubmit: (event) => onGenerate(event, feature) });
  const sceneOptions = filterCapabilityOptions(capability, "sceneTheme", ANIME_SCENE_OPTIONS);
  const state = {
    outfit: "",
    outfitLabel: "",
    outfitUrl: "",
    sceneTheme: (sceneOptions[0] && sceneOptions[0].value) || "",
  };
  let button = null;
  const sync = () => {
    if (!button) return;
    const ready = Boolean(feature.ready && state.outfit && state.sceneTheme);
    button.disabled = !ready;
    if (!feature.ready) button.textContent = "缺件，暫不能送出";
    else if (!state.outfit) button.textContent = "請先選擇角色造型";
    else if (!state.sceneTheme) button.textContent = "請先選擇動漫場景";
    else button.textContent = "完成動漫場景寫真";
  };
  const update = (patch) => {
    Object.assign(state, patch);
    sync();
  };

  form.appendChild(renderWardrobePicker(character, state, update, {
    capability,
    required: true,
    label: "角色造型",
  }));
  form.appendChild(renderAnimeScenePicker(character, capability, state, update));
  form.appendChild(renderHint("這個動漫專用入口不使用真人寫真模型；角色與服裝保持原圖，只合成核准的同畫風場景。"));
  fieldGetters.wardrobeTheme = () => state.outfit;
  fieldGetters.sceneTheme = () => state.sceneTheme;
  fieldGetters.posePreset = () => "original";
  fieldGetters.quality = () => "standard";

  if (!feature.ready) form.appendChild(renderMissing(feature));
  button = h("button", { class: "primary generate-btn", type: "submit" });
  form.appendChild(button);
  sync();
  return form;
}

function renderAnimeI2vStudioForm(feature, capability, character) {
  const form = h("form", { class: "gen-form anime-i2v-form", onsubmit: (event) => onGenerate(event, feature) });
  const motionOptions = filterCapabilityOptions(capability, "motionPreset", [
    { value: "idle", label: "自然待機", sub: "輕微呼吸、眨眼，髮絲與衣服微動" },
    { value: "smile", label: "眨眼微笑", sub: "自然眨眼後輕輕微笑" },
  ]);
  const scaleOptions = filterCapabilityOptions(capability, "motionScale", [
    { value: "subtle", label: "輕微" },
    { value: "natural", label: "自然" },
  ]);
  const durationOptions = filterCapabilityOptions(capability, "duration", [
    { value: "3s", label: "約 3 秒" },
  ]);
  const state = {
    outfit: "",
    outfitLabel: "",
    outfitUrl: "",
    motionPreset: (motionOptions[0] && motionOptions[0].value) || "",
    motionScale: (scaleOptions.find((option) => option.value === "natural") || scaleOptions[0] || {}).value || "",
    duration: (durationOptions[0] || {}).value || "",
  };
  let button = null;
  const sync = () => {
    if (!button) return;
    const optionsReady = Boolean(state.motionPreset && state.motionScale && state.duration);
    const ready = Boolean(feature.ready && state.outfit && optionsReady);
    button.disabled = !ready;
    button.textContent = !feature.ready
      ? "缺件，暫不能送出"
      : (!optionsReady
        ? "目前沒有相符的影片選項"
        : (state.outfit ? "開始生成角色短片" : "請先選擇角色造型"));
  };
  const update = (patch) => {
    Object.assign(state, patch);
    sync();
  };

  form.appendChild(renderWardrobePicker(character, state, update, {
    capability,
    required: true,
    label: "角色造型",
  }));
  form.appendChild(renderHint("輸出約 3 秒；這個入口只使用已驗證的動漫角色造型與安全動作，不接受任意圖片或自由 prompt。"));
  if (!motionOptions.length || !scaleOptions.length || !durationOptions.length) {
    form.appendChild(h("div", { class: "missing-box", text: "角色能力與影片模組選項不一致，已停止送出。" }));
  }
  if (motionOptions.length) {
    form.appendChild(radioCards("動作", motionOptions, state.motionPreset, (value) => update({ motionPreset: value })));
  }
  if (scaleOptions.length > 1) {
    form.appendChild(radioCards("動作幅度", scaleOptions, state.motionScale, (value) => update({ motionScale: value })));
  }
  if (durationOptions.length > 1) {
    form.appendChild(radioCards("長度", durationOptions, state.duration, (value) => update({ duration: value })));
  }

  fieldGetters.wardrobeTheme = () => state.outfit;
  fieldGetters.motionPreset = () => state.motionPreset;
  fieldGetters.motionScale = () => state.motionScale;
  fieldGetters.duration = () => state.duration;
  fieldGetters.quality = () => "standard";

  if (!feature.ready) form.appendChild(renderMissing(feature));
  button = h("button", { class: "primary generate-btn", type: "submit" });
  form.appendChild(button);
  sync();
  return form;
}

function renderAnimeDanceStudioForm(feature, capability, character) {
  const form = h("form", { class: "gen-form anime-dance-form", onsubmit: (event) => onGenerate(event, feature) });
  const state = {
    outfit: "",
    outfitLabel: "",
    outfitUrl: "",
    defaultFiles: {},
    serverFiles: {},
    serverFileLabels: {},
    danceTemplateId: "",
  };
  let button = null;
  const sync = () => {
    if (!button) return;
    const hasVideo = slotHasFile("video", state);
    const ready = Boolean(feature.ready && state.outfit && hasVideo);
    button.disabled = !ready;
    if (!feature.ready) button.textContent = "缺件，暫不能送出";
    else if (!state.outfit) button.textContent = "請先選擇角色造型";
    else if (!hasVideo) button.textContent = "請先提供舞蹈動作影片";
    else button.textContent = "開始生成角色舞蹈";
  };
  const update = (patch) => {
    Object.assign(state, patch);
    sync();
  };

  form.appendChild(renderWardrobePicker(character, state, update, {
    capability,
    required: true,
    label: "角色造型",
  }));
  form.appendChild(renderHint("會使用核准的完整服裝角色圖，固定生成前約 5 秒；不能任意替換角色圖或自由修改安全 prompt。"));
  form.appendChild(renderDanceTemplatePicker(feature, state, update, { character, capability }));
  fieldGetters.wardrobeTheme = () => state.outfit;
  fieldGetters.mode = () => "animate";
  fieldGetters.quality = () => "standard";

  if (!feature.ready) form.appendChild(renderMissing(feature));
  button = h("button", { class: "primary generate-btn", type: "submit" });
  form.appendChild(button);
  sync();
  return form;
}

function renderGenForm(feature, capability = null) {
  const character = activeCharacter();
  if (
    feature.id === "anime-scene-card"
    && capability
    && capability.variantId === "photoshoot.anime.scene-card.v1"
    && character
  ) {
    return renderAnimeSceneCardStudioForm(feature, capability, character);
  }
  if (
    feature.id === "img2video"
    && capability
    && capability.variantId === "image-video.anime.wan21-i2v.v1"
    && character
  ) {
    return renderAnimeI2vStudioForm(feature, capability, character);
  }
  if (
    feature.id === "dance"
    && capability
    && capability.variantId === "dance.anime.animate.v1"
    && character
  ) {
    return renderAnimeDanceStudioForm(feature, capability, character);
  }
  const form = h("form", { class: "gen-form", onsubmit: (e) => onGenerate(e, feature) });
  const minimumUploads = Number.isInteger(feature.minimumUploads) && feature.minimumUploads > 0
    ? feature.minimumUploads
    : 0;
  let minimumUploadHint = null;
  let btn = null;
  const selectedUploadCount = () => (feature.uploads || []).filter((up) => (
    capabilityServerBindsUpload(capability, up.id) || slotHasFile(up.id, {})
  )).length;
  const syncMinimumUploads = () => {
    if (!btn || !minimumUploads) return;
    const enough = selectedUploadCount() >= minimumUploads;
    btn.disabled = !feature.ready || !enough;
    btn.textContent = !feature.ready
      ? "缺件，暫不能送出"
      : (enough ? "開始生成" : "至少選一個參考");
    if (minimumUploadHint) minimumUploadHint.classList.toggle("hidden", !feature.ready || enough);
  };

  if (feature.uploads && feature.uploads.length) {
    const grid = h("div", { class: "upload-grid" });
    for (const up of feature.uploads) {
      if (!capabilityServerBindsUpload(capability, up.id)) {
        grid.appendChild(renderDropzone({
          ...up,
          onFileChange: (...args) => {
            if (typeof up.onFileChange === "function") up.onFileChange(...args);
            syncMinimumUploads();
          },
        }));
      }
    }
    form.appendChild(grid);
    if (minimumUploads) {
      minimumUploadHint = renderHint("至少選一個參考");
      minimumUploadHint.classList.add("minimum-upload-hint");
      form.appendChild(minimumUploadHint);
    }
  }

  const permittedFields = (feature.fields || []).map((field) => capabilityField(feature, capability, field)).filter(Boolean);
  const basic = permittedFields.filter((f) => !f.advanced);
  const advanced = permittedFields.filter((f) => f.advanced);
  let lipsyncVoiceSelect = null;
  let lipsyncVoiceInput = null;
  let lipsyncVoiceHint = null;
  for (const field of basic) {
    if (feature.id === "dance" && field.id === "mode" && (field.options || []).length === 1) {
      const onlyMode = field.options[0].value;
      fieldGetters[field.id] = () => onlyMode;
      continue;
    }
    const fieldNode = renderField(field);
    form.appendChild(fieldNode);
    if (feature.id === "lipsync" && field.id === "voiceId") {
      lipsyncVoiceSelect = fieldNode.querySelector("select");
    }
  }

  if (advanced.length && !(capability && capability.hideAdvanced)) {
    const body = h("div", { class: "advanced-body" });
    for (const field of advanced) body.appendChild(renderField(field));
    form.appendChild(h("details", { class: "advanced" }, [
      h("summary", { text: "進階設定" }),
      body,
    ]));
  }

  if (feature.id === "lipsync" && lipsyncVoiceSelect && fileInputs.voice) {
    const voiceUpload = (feature.uploads || []).find((upload) => upload.id === "voice");
    const voiceInput = fileInputs.voice;
    lipsyncVoiceInput = voiceInput;
    lipsyncVoiceSelect.addEventListener("change", () => {
      if (lipsyncVoiceSelect.value && voiceUpload) clearDropzoneSelection(voiceUpload, voiceInput);
    });
    voiceInput.addEventListener("change", () => {
      if (voiceInput.files && voiceInput.files.length) lipsyncVoiceSelect.value = "";
    });
    lipsyncVoiceHint = renderHint("請從聲音庫選擇或上傳一段語音；新選擇會自動取代前一種。");
    form.appendChild(lipsyncVoiceHint);
  }

  if (!feature.ready) form.appendChild(renderMissing(feature));

  btn = h("button", {
    class: "primary generate-btn",
    type: "submit",
    text: feature.ready ? "開始生成" : "缺件，暫不能送出",
  });
  btn.disabled = !feature.ready;
  form.appendChild(btn);
  syncMinimumUploads();
  if (feature.id === "lipsync" && lipsyncVoiceSelect && lipsyncVoiceInput) {
    const syncLipsyncVoiceReady = () => {
      const hasUpload = Boolean(
        (lipsyncVoiceInput.files && lipsyncVoiceInput.files.length)
        || serverFiles.voice
      );
      const hasVoice = Boolean(lipsyncVoiceSelect.value || hasUpload);
      btn.disabled = !feature.ready || !hasVoice;
      btn.textContent = !feature.ready
        ? "缺件，暫不能送出"
        : (hasVoice ? "開始生成" : "請先選擇或上傳語音");
      const message = lipsyncVoiceHint && lipsyncVoiceHint.querySelector("span:last-child");
      if (message) {
        message.textContent = hasVoice
          ? "目前只會使用這一種聲音來源；新選擇會自動取代前一種。"
          : "請從聲音庫選擇或上傳一段語音。";
      }
    };
    lipsyncVoiceSelect.addEventListener("change", syncLipsyncVoiceReady);
    lipsyncVoiceInput.addEventListener("change", syncLipsyncVoiceReady);
    syncLipsyncVoiceReady();
  }
  return form;
}

function renderVoiceSpeakForm(feature, capability = null) {
  const fieldById = (id) => (feature.fields || []).find((f) => f.id === id);
  const voxcpmFeature = features.find((item) => item.id === "voxcpm-tts") || { fields: [] };
  const voxcpmFieldById = (id) => (voxcpmFeature.fields || []).find((f) => f.id === id);
  const textField = fieldById("text") || {};
  const sampleField = fieldById("sampleText") || {};
  const form = h("form", {
    class: "gen-form voice-speak-form",
    onsubmit: (e) => {
      if (!syncFormState(true)) {
        e.preventDefault();
        return;
      }
      delete fileInputs["voice"];
      delete serverFiles["voice"];
      onGenerate(e, targetFeature());
    },
  });

  delete fileInputs["voice"];
  delete serverFiles["voice"];

  const character = activeCharacter();
  const voxcpmCapability = character ? enabledFeatureCapability(character, "voxcpm-tts") : null;
  const allowedVoicePaths = allowedOptionValues(capability, "voicePath");
  const allowedPersonaIds = allowedOptionValues(capability, "persona");
  const allowedVoiceIds = allowedOptionValues(capability, "voiceId");
  const pathOptions = [
    ["persona", "角色聲音", "已訓練的角色聲音(台灣國語),選人即說"],
    ["clone", "聲音庫克隆", "用 聲音庫 的參考音克隆音色,逐字稿自動帶入"],
    ["custom_english", "Custom English", "自訂英文聲音（需先設定 LoRA 與參考音訊）"],
  ].filter(([value]) => {
    // A generic Studio job has no role receipt, so it may clone a library
    // sample but may not impersonate a role persona or another user’s LoRA.
    if (!character) return value === "clone";
    return (
      (!allowedVoicePaths || allowedVoicePaths.has(value))
      && (value !== "custom_english" || Boolean(voxcpmCapability))
    );
  });
  let voicePath = (pathOptions[0] && pathOptions[0][0]) || "persona";
  let cloneEngine = "taiwan";
  const pathButtons = [];
  const pathGroup = h("div", { class: "card-group" });
  for (const [value, label, sub] of pathOptions) {
    const card = h("button", {
      type: "button",
      class: `mode-card${value === voicePath ? " active" : ""}`,
      dataset: { value },
      onclick: () => setVoicePath(value),
    }, [
      h("span", { class: "mode-card-title", text: label }),
      h("span", { class: "mode-card-sub", text: sub }),
    ]);
    pathButtons.push(card);
    pathGroup.appendChild(card);
  }
  if (pathOptions.length > 1) {
    form.appendChild(h("div", { class: "field" }, [
      h("span", { class: "field-label", text: "聲音路徑" }),
      pathGroup,
    ]));
  } else if (pathOptions.length === 1) {
    form.appendChild(h("div", { class: "field role-bound-summary" }, [
      h("span", { class: "field-label", text: "聲音方式" }),
      h("div", { class: "bound-value", text: pathOptions[0][1] }),
      renderHint(pathOptions[0][2]),
    ]));
  }

  const personaSel = h("select");
  if (character && allowedPersonaIds !== null) {
    for (const personaId of allowedPersonaIds) {
      personaSel.appendChild(h("option", {
        value: personaId,
        text: `${character.name} 的角色聲音`,
      }));
    }
  } else {
    personaSel.appendChild(h("option", { value: "", text: "載入聲音中…" }));
  }
  const personaPanel = h("div", { class: "post-mode-panel" }, [
    labeledField((fieldById("persona") || {}).label || "角色聲音", personaSel),
    renderHint("已訓練的角色聲音(台灣國語),選人即說"),
  ]);

  const voiceField = fieldById("voiceId") || {};
  const libSel = h("select");
  libSel.appendChild(h("option", { value: "", text: "（請選擇聲音庫音訊）" }));
  const transcriptTa = h("textarea", {
    rows: String(sampleField.rows || 2),
    placeholder: sampleField.placeholder || "參考音訊實際說的那段話",
    maxlength: sampleField.maxLength ? String(sampleField.maxLength) : null,
  });
  const transcriptCounter = attachCounter(transcriptTa, sampleField.maxLength || 180);
  const transcriptMessage = h("div", { class: "field-hint hidden" }, [
    h("span", { class: "note-ico", text: "ⓘ" }),
    h("span", { text: "" }),
  ]);
  const transcriptField = labeledField(sampleField.label || "參考音訊逐字稿", transcriptTa, transcriptCounter);
  transcriptField.appendChild(transcriptMessage);
  const clonePanel = h("div", { class: "post-mode-panel hidden" }, [
    labeledField(voiceField.label || "聲音庫參考音訊", libSel),
    renderHint("用 聲音庫 的參考音克隆音色,逐字稿自動帶入"),
    transcriptField,
  ]);

  const custom_englishPanel = h("div", { class: "post-mode-panel hidden" }, [
    renderHint("自訂英文聲音（需先設定 LoRA 與參考音訊）"),
  ]);

  form.appendChild(personaPanel);
  form.appendChild(clonePanel);
  form.appendChild(custom_englishPanel);

  const textTa = h("textarea", {
    rows: String(textField.rows || 4),
    maxlength: textField.maxLength ? String(textField.maxLength) : null,
  });
  const textCounter = attachCounter(textTa, textField.maxLength || 2400);
  const textLabel = textField.label || "要說的話";
  form.appendChild(labeledField(textLabel, textTa, textCounter));
  form.appendChild(renderAudioFormatPreference());

  const qualityRow = renderField(fieldById("qualityGate") || { id: "qualityGate", type: "checkbox", label: "高品質 ASR Gate（較慢，會等 GPU，逐段檢查失敗重跑）", default: false });
  const qualityGateGetter = fieldGetters["qualityGate"] || (() => false);
  fieldGetters["qualityGate"] = () => (voicePath === "persona" ? Boolean(qualityGateGetter()) : false);
  const cloneEngineField = h("select");
  const allowedCloneEngines = allowedOptionValues(capability, "cloneEngine");
  const runtimeCloneEngines = feature.runtimeOptions && Array.isArray(feature.runtimeOptions.cloneEngine)
    ? new Set(feature.runtimeOptions.cloneEngine.map((value) => String(value)))
    : null;
  const cloneEngineOptions = [
    ["taiwan", "台灣腔(預設)", "BreezyVoice reference clone"],
    ["f5", "多語 F5", "要說英日等外語選這個"],
  ].filter(([value]) => (
    (!allowedCloneEngines || allowedCloneEngines.has(value))
    && (!runtimeCloneEngines || runtimeCloneEngines.has(value))
  ));
  cloneEngine = (cloneEngineOptions[0] && cloneEngineOptions[0][0]) || "";
  for (const [value, label] of cloneEngineOptions) {
    cloneEngineField.appendChild(h("option", { value, text: label, selected: value === cloneEngine }));
  }
  const cloneEngineHint = h("div", { class: "field-hint" }, [
    h("span", { class: "note-ico", text: "ⓘ" }),
    h("span", { text: "台灣腔走 BreezyVoice；要說英文、日文等外語請選多語 F5。兩者都需要參考音訊的逐字稿。" }),
  ]);
  const cloneSpeakerGateRow = renderField(voxcpmFieldById("speakerGate") || { id: "speakerGate", type: "checkbox", label: "聲紋 Gate（有 reference 時低於相似度會自動重跑）", default: true });
  const cloneSpeakerGateGetter = fieldGetters["speakerGate"] || (() => true);
  const cloneAdvancedBody = h("div", { class: "advanced-body" }, [
    labeledField("克隆方式", cloneEngineField),
    cloneEngineHint,
    cloneSpeakerGateRow,
  ]);
  const cloneAdvanced = h("details", { class: "advanced hidden" }, [
    h("summary", { text: "進階設定" }),
    cloneAdvancedBody,
  ]);

  const voiceProfileField = voxcpmFieldById("voiceProfile") || { id: "voiceProfile", type: "select", label: "LoRA Profile", default: "custom_english", options: [{ value: "custom_english", label: "custom_english" }] };
  const accentField = voxcpmFieldById("accent") || { id: "accent", type: "select", label: "口音 / 方言", default: "standard American English accent", options: [{ value: "standard American English accent", label: "standard American English accent" }] };
  const styleField = voxcpmFieldById("style") || { id: "style", type: "textarea", label: "語氣 / 風格控制", rows: 2, placeholder: "例如：calm, confident, standard American English, natural meeting voice" };
  const cfgField = voxcpmFieldById("cfg") || { id: "cfg", type: "slider", label: "CFG", default: 2.0, min: 1.0, max: 3.0, step: 0.1 };
  const stepsField = voxcpmFieldById("steps") || { id: "steps", type: "slider", label: "推理步數", default: 10, min: 4, max: 30, step: 1 };
  const seedField = voxcpmFieldById("seed") || { id: "seed", type: "number", label: "種子（-1 隨機）", default: -1, min: -1, step: 1 };
  const custom_englishProfileRow = renderField(voiceProfileField);
  const custom_englishAccentRow = renderField(accentField);
  const custom_englishStyleRow = renderField(styleField);
  const custom_englishCfgRow = renderField(cfgField);
  const custom_englishStepsRow = renderField(stepsField);
  const custom_englishSeedRow = renderField(seedField);
  const custom_englishSpeakerGateRow = renderField({ ...(voxcpmFieldById("speakerGate") || { id: "speakerGate", type: "checkbox", label: "聲紋 Gate（有 reference 時低於相似度會自動重跑）", default: true }), default: true });
  const custom_englishGetters = {
    voiceProfile: fieldGetters["voiceProfile"] || (() => "custom_english"),
    accent: fieldGetters["accent"] || (() => "standard American English accent"),
    style: fieldGetters["style"] || (() => ""),
    cfg: fieldGetters["cfg"] || (() => 2.0),
    steps: fieldGetters["steps"] || (() => 10),
    seed: fieldGetters["seed"] || (() => -1),
    speakerGate: fieldGetters["speakerGate"] || (() => true),
  };
  const custom_englishAdvanced = h("details", { class: "advanced hidden" }, [
    h("summary", { text: "進階設定" }),
    h("div", { class: "advanced-body" }, [
      custom_englishProfileRow,
      renderHint("Custom English 預設使用 strict054 LoRA Profile。"),
      custom_englishAccentRow,
      renderHint("口音預設 standard American English accent。"),
      custom_englishStyleRow,
      renderHint("語氣 / 風格可留空,需要時再指定。"),
      custom_englishCfgRow,
      renderHint("CFG 預設 2.0。"),
      custom_englishStepsRow,
      renderHint("步數預設 10。"),
      custom_englishSeedRow,
      renderHint("種子預設 -1 隨機。"),
      custom_englishSpeakerGateRow,
      renderHint("聲紋 Gate 預設開啟。"),
    ]),
  ]);

  if (!capability || !capability.hideAdvanced) {
    form.appendChild(h("details", { class: "advanced", id: "personaAdvanced" }, [
      h("summary", { text: "進階設定" }),
      h("div", { class: "advanced-body" }, [qualityRow]),
    ]));
  }
  form.appendChild(cloneAdvanced);
  form.appendChild(custom_englishAdvanced);

  const reasonLine = h("div", { class: "field-hint hidden" }, [
    h("span", { class: "note-ico", text: "ⓘ" }),
    h("span", { text: "" }),
  ]);
  const submit = h("button", {
    class: "primary generate-btn",
    type: "submit",
    text: featureReadyForContext(feature) ? "生成語音" : "缺件，暫不能送出",
  });
  form.appendChild(submit);
  form.appendChild(reasonLine);

  const personaAdvanced = form.querySelector("#personaAdvanced");
  const textPlaceholders = {
    persona: textField.placeholder || "打字，讓她用這個聲音說出來；長文會自動切段後合併",
    clone: "輸入想用這個音色說的內容；長文會自動切段後合併",
    custom_english: "Type the English text for the configured voice.",
  };
  const selectedLibraryTranscript = () => {
    const selected = libSel.selectedOptions && libSel.selectedOptions[0];
    return selected ? (selected.dataset.transcript || "").trim() : "";
  };
  const setTranscriptMessage = (message) => {
    const textNode = transcriptMessage.querySelector("span:last-child");
    if (textNode) textNode.textContent = message || "";
    transcriptMessage.classList.toggle("hidden", !message);
  };
  let lastAutoTranscript = "";
  let personaLoaded = Boolean(character && allowedPersonaIds !== null);
  let voicesLoaded = Boolean(character && allowedVoiceIds !== null);
  const personaAvailable = () => Array.from(personaSel.options || []).some((opt) => opt.value);
  const libraryAvailable = () => Array.from(libSel.options || []).some((opt) => opt.value);
  const targetFeature = () => (
    voicePath === "custom_english" || (voicePath === "clone" && (cloneEngine === "ultimate" || cloneEngine === "reference"))
      ? voxcpmFeature
      : feature
  );

  fieldGetters["voiceSource"] = () => (voicePath === "clone" ? "library" : "persona");
  fieldGetters["persona"] = () => (voicePath === "persona" ? personaSel.value : "");
  fieldGetters["voiceId"] = () => (voicePath === "clone" ? libSel.value : "");
  fieldGetters["sampleText"] = () => (voicePath === "clone" ? transcriptTa.value : "");
  fieldGetters["promptText"] = () => (voicePath === "clone" ? transcriptTa.value : "");
  fieldGetters["cloneEngine"] = () => (voicePath === "clone" ? cloneEngine : "");
  fieldGetters["text"] = () => textTa.value;
  fieldGetters["voiceEngine"] = () => (voicePath === "persona" ? "breezy" : "voxcpm");
  fieldGetters["mode"] = () => {
    if (voicePath === "custom_english") return "lora";
    if (voicePath === "clone" && cloneEngine === "ultimate") return "ultimate";
    if (voicePath === "clone" && cloneEngine === "reference") return "reference";
    return "lora";
  };
  fieldGetters["language"] = () => "English";
  fieldGetters["accent"] = () => (voicePath === "custom_english" ? custom_englishGetters.accent() : "standard American English accent");
  fieldGetters["voiceProfile"] = () => (voicePath === "custom_english" ? custom_englishGetters.voiceProfile() : "");
  fieldGetters["style"] = () => (voicePath === "custom_english" ? custom_englishGetters.style() : "");
  fieldGetters["cfg"] = () => Number(voicePath === "custom_english" ? custom_englishGetters.cfg() : 2.0);
  fieldGetters["steps"] = () => Number(voicePath === "custom_english" ? custom_englishGetters.steps() : 10);
  fieldGetters["seed"] = () => Number(voicePath === "custom_english" ? custom_englishGetters.seed() : -1);
  fieldGetters["speakerGate"] = () => {
    if (voicePath === "custom_english") return Boolean(custom_englishGetters.speakerGate());
    if (voicePath === "clone" && (cloneEngine === "ultimate" || cloneEngine === "reference")) return Boolean(cloneSpeakerGateGetter());
    return false;
  };

  function syncVoiceTranscript() {
    const transcript = selectedLibraryTranscript();
    const current = transcriptTa.value;
    if (voicePath === "clone" && (current.trim() === "" || current === lastAutoTranscript)) {
      transcriptTa.value = transcript;
      lastAutoTranscript = transcript;
      transcriptTa.dispatchEvent(new Event("input"));
      return;
    }
    syncFormState(false);
  }

  function syncFormState(focusInvalid) {
    const isClone = voicePath === "clone";
    const isPersona = voicePath === "persona";
    const isCustomEnglish = voicePath === "custom_english";
    const isVoxClone = isClone && (cloneEngine === "ultimate" || cloneEngine === "reference");
    const transcriptRequired = isClone && cloneEngine !== "reference";
    const selectedTranscript = selectedLibraryTranscript();
    const currentTranscript = transcriptTa.value.trim();
    const currentTargetFeature = targetFeature();
    const targetCapability = character ? enabledFeatureCapability(character, currentTargetFeature.id) : null;
    const targetReady = (
      isClone && currentTargetFeature.id === "voice-speak"
        ? featureReadyForContext(currentTargetFeature) && Boolean(cloneEngine) && (!runtimeCloneEngines || runtimeCloneEngines.has(cloneEngine))
        : currentTargetFeature.ready === true
    );
    const hasText = Boolean(textTa.value.trim());
    let reason = "";
    let hardTranscriptMessage = "";

    textTa.placeholder = textPlaceholders[voicePath] || textPlaceholders.persona;
    pathButtons.forEach((btn) => btn.classList.toggle("active", btn.dataset.value === voicePath));
    personaPanel.classList.toggle("hidden", !isPersona);
    clonePanel.classList.toggle("hidden", !isClone);
    custom_englishPanel.classList.toggle("hidden", !isCustomEnglish);
    if (personaAdvanced) personaAdvanced.classList.toggle("hidden", !isPersona);
    cloneAdvanced.classList.toggle("hidden", !isClone);
    custom_englishAdvanced.classList.toggle("hidden", !isCustomEnglish);
    cloneSpeakerGateRow.classList.toggle("hidden", !isVoxClone);
    transcriptField.classList.toggle("hidden", isClone && cloneEngine === "reference");

    if (character && !targetCapability) reason = "這個角色不能使用這個聲音模組";
    else if (!targetReady) reason = featureUnavailableReason(currentTargetFeature);
    else if (isPersona && !personaLoaded) reason = "載入聲音中…";
    else if (isClone && !voicesLoaded) reason = "載入聲音中…";
    else if (!hasText) reason = "請輸入要說的話";
    else if (isPersona && !personaAvailable()) reason = "沒有可用角色聲音";
    else if (isClone && !libraryAvailable()) reason = "聲音庫沒有音訊,請先到 聲音庫 上傳";
    else if (isClone && !libSel.value) reason = "請先選擇聲音庫音訊";
    else if (transcriptRequired && !currentTranscript && !selectedTranscript) {
      reason = "這個克隆模組需要逐字稿：請填入參考音檔實際講的內容";
      hardTranscriptMessage = reason;
    }

    if (hardTranscriptMessage) {
      setTranscriptMessage(hardTranscriptMessage);
      if (focusInvalid) transcriptTa.focus();
    } else if (isClone && currentTranscript && transcriptTa.value === lastAutoTranscript) {
      setTranscriptMessage("已自動帶入聲音庫逐字稿,可修改");
    } else if (transcriptRequired && !currentTranscript) {
      setTranscriptMessage("請填入參考音實際說的話；這是目前模組的必要輸入");
    } else {
      setTranscriptMessage("");
    }

    const reasonNode = reasonLine.querySelector("span:last-child");
    if (reasonNode) reasonNode.textContent = reason || "";
    reasonLine.classList.toggle("hidden", !reason);
    submit.disabled = Boolean(reason);
    submit.textContent = targetReady ? "生成語音" : "缺件，暫不能送出";
    return !reason;
  }

  function setVoicePath(next) {
    voicePath = next;
    syncVoiceTranscript();
    syncFormState(false);
  }

  const voiceFormSession = captureSessionContext();
  if (!(character && allowedPersonaIds !== null)) {
    loadPersonaOptions(personaSel).then(() => {
      if (!sessionContextIsCurrent(voiceFormSession)) return;
      personaLoaded = true;
      syncFormState(false);
    });
  }
  if (character && allowedVoiceIds !== null) {
    if (allowedVoiceIds.size) {
      voicesLoaded = false;
      loadCharacterVoiceOptions(libSel, character, "", "這個角色目前沒有已驗證的聲音庫音訊", allowedVoiceIds).then(() => {
        if (!sessionContextIsCurrent(voiceFormSession)) return;
        voicesLoaded = true;
        syncVoiceTranscript();
        syncFormState(false);
      });
    }
  } else {
    loadVoiceOptions(libSel).then(() => {
      if (!sessionContextIsCurrent(voiceFormSession)) return;
      voicesLoaded = true;
      syncVoiceTranscript();
      syncFormState(false);
    });
  }
  personaSel.addEventListener("change", () => syncFormState(false));
  libSel.addEventListener("change", () => {
    syncVoiceTranscript();
    syncFormState(false);
  });
  cloneEngineField.addEventListener("change", () => {
    cloneEngine = cloneEngineField.value;
    syncVoiceTranscript();
    syncFormState(false);
  });
  textTa.addEventListener("input", () => syncFormState(false));
  transcriptTa.addEventListener("input", () => syncFormState(false));
  form.addEventListener("change", () => syncFormState(false));
  syncFormState(false);

  if (!featureReadyForContext(feature)) form.insertBefore(renderMissing(feature), submit);
  return h("div", { class: "text-studio" }, [form]);
}

// 聲音克隆 — AutoIP-style: 語音版本 cards + 參考音來源 toggle + 文字內容 counter.
// Registers into the same fieldGetters / fileInputs / serverFiles maps so the
// shared onGenerate(feature) submit path works unchanged.
function renderVoiceCloneForm(feature) {
  const fieldById = (id) => (feature.fields || []).find((f) => f.id === id);
  const voiceUp = (feature.uploads || []).find((u) => u.id === "voice")
    || { id: "voice", label: "上傳參考音訊", accept: "audio/*" };

  const form = h("form", { class: "gen-form", onsubmit: (e) => onGenerate(e, feature) });

  // 語音版本 (engine) cards — 國語通用 (F5) / 國語台灣口音 (BreezyVoice)
  const engineField = fieldById("engine");
  let modelRow = null;
  if (engineField) form.appendChild(renderField(engineField));

  // 參考音來源 toggle: 從聲音庫選 ↔ 上傳新音訊
  let refSource = "library";
  const libBtn = h("button", { type: "button", class: "seg-btn active", text: "從聲音庫選" });
  const upBtn = h("button", { type: "button", class: "seg-btn", text: "上傳新音訊" });
  const refToggle = h("div", { class: "seg-toggle" }, [libBtn, upBtn]);

  const libSel = h("select");
  libSel.appendChild(h("option", { value: "", text: "（請選擇聲音庫音訊）" }));
  loadVoiceOptions(libSel);
  const libPanel = h("div", { class: "post-mode-panel" }, [labeledField("聲音庫參考音訊", libSel)]);

  const dz = renderDropzone(voiceUp); // registers fileInputs["voice"]
  const upInput = fileInputs["voice"];
  const upPanel = h("div", { class: "post-mode-panel hidden" }, [dz]);

  // voiceId only counts in library mode; in upload mode the dropzone wins.
  fieldGetters["voiceId"] = () => (refSource === "library" ? libSel.value : "");

  function setRef(next) {
    refSource = next;
    libBtn.classList.toggle("active", next === "library");
    upBtn.classList.toggle("active", next === "upload");
    libPanel.classList.toggle("hidden", next !== "library");
    upPanel.classList.toggle("hidden", next !== "upload");
    if (next === "library" && upInput) { upInput.value = ""; delete serverFiles["voice"]; }
  }
  libBtn.addEventListener("click", () => setRef("library"));
  upBtn.addEventListener("click", () => setRef("upload"));

  form.appendChild(h("div", { class: "field" }, [
    h("span", { class: "field-label", text: "參考音來源" }), refToggle,
  ]));
  form.appendChild(libPanel);
  form.appendChild(upPanel);

  // 參考音訊逐字稿 (both engines need it)
  const sampleField = fieldById("sampleText");
  if (sampleField) form.appendChild(renderField(sampleField));

  // 文字內容 (speech) + char counter (≤1200)
  const speechTa = h("textarea", { rows: "4", placeholder: "想用這個音色說的內容…" });
  const speechCounter = attachCounter(speechTa, 1200);
  fieldGetters["speech"] = () => speechTa.value;
  form.appendChild(h("label", { class: "field" }, [
    h("div", { class: "field-label-row" }, [
      h("span", { class: "field-label", text: "文字內容" }),
      speechCounter,
    ]),
    speechTa,
  ]));
  form.appendChild(renderAudioFormatPreference());

  // advanced: model (F5-only) + seed
  const modelField = fieldById("model");
  const seedField = fieldById("seed");
  const advBody = h("div", { class: "advanced-body" });
  if (modelField) { modelRow = renderField(modelField); advBody.appendChild(modelRow); }
  if (seedField) advBody.appendChild(renderField(seedField));
  form.appendChild(h("details", { class: "advanced" }, [h("summary", { text: "進階設定" }), advBody]));

  // hide the F5 model knob when 台灣口音 is selected
  const engineGetter = fieldGetters["engine"];
  function syncEngine() {
    if (modelRow) modelRow.classList.toggle("hidden", !!engineGetter && engineGetter() === "taiwan");
  }
  form.addEventListener("click", syncEngine);
  syncEngine();

  if (!feature.ready) form.appendChild(renderMissing(feature));
  const submit = h("button", {
    class: "primary generate-btn",
    type: "submit",
    text: feature.ready ? "生成語音" : "缺件，暫不能送出",
  });
  submit.disabled = !feature.ready;
  form.appendChild(submit);
  return h("div", { class: "text-studio" }, [form]);
}

function renderVoxcpmForm(feature) {
  const fieldById = (id) => (feature.fields || []).find((f) => f.id === id);
  const voiceUp = (feature.uploads || []).find((u) => u.id === "voice")
    || { id: "voice", label: "參考音訊", accept: "audio/*" };
  const form = h("form", {
    class: "gen-form voxcpm-form",
    onsubmit: (e) => {
      if (!validateUltimateTranscript()) {
        e.preventDefault();
        return;
      }
      onGenerate(e, feature);
    },
  });

  const engineField = fieldById("voiceEngine");
  if (engineField) form.appendChild(renderField(engineField));
  const engineGetter = fieldGetters["voiceEngine"];

  const breezyPanel = h("div", { class: "post-mode-panel breezy-mode-panel" });
  const personaField = fieldById("persona");
  const qualityGateField = fieldById("qualityGate");
  if (personaField) breezyPanel.appendChild(renderField(personaField));
  if (qualityGateField) breezyPanel.appendChild(renderField(qualityGateField));
  form.appendChild(breezyPanel);

  const voxcpmPanel = h("div", { class: "voxcpm-engine-panel" });
  const modeField = fieldById("mode");
  if (modeField) voxcpmPanel.appendChild(renderField(modeField));
  const modeGetter = fieldGetters["mode"];

  const languageRow = renderField(fieldById("language"));
  const accentRow = renderField(fieldById("accent"));
  voxcpmPanel.appendChild(h("div", { class: "voxcpm-grid" }, [languageRow, accentRow]));

  const loraPanel = h("div", { class: "post-mode-panel voxcpm-mode-panel" });
  const profileField = fieldById("voiceProfile");
  if (profileField) loraPanel.appendChild(renderField(profileField));
  const profileGetter = fieldGetters["voiceProfile"] || (() => "custom_english");
  fieldGetters["voiceProfile"] = () => (
    (engineGetter ? engineGetter() : "voxcpm") === "voxcpm"
    && (modeGetter ? modeGetter() : "lora") === "lora"
      ? profileGetter()
      : ""
  );
  voxcpmPanel.appendChild(loraPanel);

  let refSource = "library";
  const libBtn = h("button", { type: "button", class: "seg-btn active", text: "從聲音庫選" });
  const upBtn = h("button", { type: "button", class: "seg-btn", text: "上傳新音訊" });
  const libSel = h("select");
  libSel.appendChild(h("option", { value: "", text: "（請選擇聲音庫音訊）" }));
  const libPanel = h("div", { class: "post-mode-panel" }, [labeledField("聲音庫參考音訊", libSel)]);
  const dz = renderDropzone(voiceUp);
  const upInput = fileInputs["voice"];
  const upPanel = h("div", { class: "post-mode-panel hidden" }, [dz]);
  const refPanel = h("div", { class: "post-mode-panel voxcpm-mode-panel hidden" }, [
    h("div", { class: "field" }, [
      h("span", { class: "field-label", text: "參考音來源" }),
      h("div", { class: "seg-toggle" }, [libBtn, upBtn]),
    ]),
    libPanel,
    upPanel,
  ]);
  voxcpmPanel.appendChild(refPanel);
  const speakerGateRow = renderField(fieldById("speakerGate"));
  if (speakerGateRow) voxcpmPanel.appendChild(speakerGateRow);

  fieldGetters["voiceId"] = () => {
    const mode = modeGetter ? modeGetter() : "lora";
    return (mode === "reference" || mode === "ultimate") && refSource === "library" ? libSel.value : "";
  };

  function setRef(next) {
    refSource = next;
    libBtn.classList.toggle("active", next === "library");
    upBtn.classList.toggle("active", next === "upload");
    libPanel.classList.toggle("hidden", next !== "library");
    upPanel.classList.toggle("hidden", next !== "upload");
    if (next === "library" && upInput) { upInput.value = ""; delete serverFiles["voice"]; }
    if (next === "upload") libSel.value = "";
  }
  libBtn.addEventListener("click", () => setRef("library"));
  upBtn.addEventListener("click", () => setRef("upload"));

  const promptTextRow = renderField(fieldById("promptText"));
  const styleRow = renderField(fieldById("style"));
  const textRow = renderField(fieldById("text"));
  const promptTextInput = promptTextRow ? promptTextRow.querySelector("textarea") : null;
  const promptTextMessage = h("div", { class: "field-hint hidden" }, [
    h("span", { class: "note-ico", text: "ⓘ" }),
    h("span", { text: "" }),
  ]);
  if (promptTextRow) promptTextRow.appendChild(promptTextMessage);
  if (promptTextRow) voxcpmPanel.appendChild(promptTextRow);
  if (styleRow) voxcpmPanel.appendChild(styleRow);
  form.appendChild(voxcpmPanel);
  if (textRow) form.appendChild(textRow);
  form.appendChild(renderAudioFormatPreference());

  const advanced = ["cfg", "steps", "seed"].map(fieldById).filter(Boolean);
  let advancedWrap = null;
  if (advanced.length) {
    const body = h("div", { class: "advanced-body" });
    for (const field of advanced) body.appendChild(renderField(field));
    advancedWrap = h("details", { class: "advanced" }, [
      h("summary", { text: "進階設定" }),
      body,
    ]);
    form.appendChild(advancedWrap);
  }

  function selectedLibraryTranscript() {
    if (refSource !== "library") return "";
    const selected = libSel.selectedOptions && libSel.selectedOptions[0];
    return selected ? (selected.dataset.transcript || "").trim() : "";
  }

  function setPromptTextMessage(message) {
    if (!promptTextMessage) return;
    const textNode = promptTextMessage.querySelector("span:last-child");
    if (textNode) textNode.textContent = message || "";
    promptTextMessage.classList.toggle("hidden", !message);
  }

  function syncUltimateTranscript() {
    const mode = modeGetter ? modeGetter() : "lora";
    if (mode !== "ultimate" || !promptTextInput) {
      setPromptTextMessage("");
      return;
    }
    const transcript = selectedLibraryTranscript();
    if (transcript && !promptTextInput.value.trim()) {
      promptTextInput.value = transcript;
      promptTextInput.dispatchEvent(new Event("input"));
      setPromptTextMessage("已自動帶入聲音庫逐字稿,可修改");
    } else if (promptTextInput.value.trim() || transcript) {
      setPromptTextMessage("");
    }
  }

  function validateUltimateTranscript() {
    const mode = modeGetter ? modeGetter() : "lora";
    if (mode !== "ultimate" || !promptTextInput || promptTextInput.value.trim()) {
      setPromptTextMessage("");
      return true;
    }
    if (selectedLibraryTranscript()) return true;
    setPromptTextMessage("Ultimate Clone 需要逐字稿:填入參考音檔實際講的內容(英文照講的打)");
    promptTextInput.focus();
    return false;
  }

  function syncMode() {
    const engine = engineGetter ? engineGetter() : "voxcpm";
    const useBreezy = engine === "breezy";
    const mode = modeGetter ? modeGetter() : "lora";
    const needsRef = mode === "reference" || mode === "ultimate";
    breezyPanel.classList.toggle("hidden", !useBreezy);
    voxcpmPanel.classList.toggle("hidden", useBreezy);
    if (advancedWrap) advancedWrap.classList.toggle("hidden", useBreezy);
    loraPanel.classList.toggle("hidden", mode !== "lora");
    refPanel.classList.toggle("hidden", !needsRef);
    if (speakerGateRow) speakerGateRow.classList.toggle("hidden", mode === "design");
    if (promptTextRow) promptTextRow.classList.toggle("hidden", mode !== "ultimate");
    if (styleRow) styleRow.classList.toggle("hidden", mode === "ultimate" || mode === "lora");
    if (!needsRef && upInput) { upInput.value = ""; delete serverFiles["voice"]; }
    syncUltimateTranscript();
  }
  const voiceCloneSession = captureSessionContext();
  loadVoiceOptions(libSel, "custom_english_reference").then(() => {
    if (sessionContextIsCurrent(voiceCloneSession)) syncUltimateTranscript();
  });
  libSel.addEventListener("change", syncUltimateTranscript);
  form.addEventListener("click", () => setTimeout(syncMode, 0));
  form.addEventListener("change", syncMode);
  syncMode();

  if (!feature.ready) form.appendChild(renderMissing(feature));
  const submit = h("button", {
    class: "primary generate-btn",
    type: "submit",
    text: feature.ready ? "生成語音" : "缺件，暫不能送出",
  });
  submit.disabled = !feature.ready;
  form.appendChild(submit);
  return h("div", { class: "text-studio" }, [form]);
}

function renderDropzone(up) {
  const preview = h("div", { class: "drop-preview" });
  const hint = h("div", { class: "drop-hint" }, [
    h("span", { class: "drop-ico", text: "⬆️" }),
    h("span", { class: "drop-text", text: "點擊或拖曳上傳" }),
    h("span", { class: "drop-formats", text: up.hint || "支援常見格式" }),
  ]);
  preview.appendChild(hint);
  const input = h("input", {
    type: "file",
    accept: up.accept || "image/*",
    onchange: (e) => {
      delete serverFiles[up.id];
      previewFile(e.target, preview, hint);
      if (typeof up.onFileChange === "function") up.onFileChange(up.id);
    },
  });
  fileInputs[up.id] = input;
  if (serverFiles[up.id]) {
    clear(preview);
    preview.appendChild(h("span", { class: "file-name", text: `已選取：${up.serverFileLabel || prefilledFileLabel(serverFiles[up.id])}` }));
  }
  const dropzone = h("label", { class: "dropzone", dataset: { uploadId: up.id } }, [
    h("span", { class: "drop-label", text: up.label + (up.required ? " *" : "") }),
    preview,
    input,
  ]);
  const materialButton = h("button", { type: "button", class: "ghost small", text: "從素材庫選擇", onclick: () => {
    const kind = (up.accept || "image/").split("/")[0];
    const session = captureSessionContext();
    openSheet({ id: "material-picker", title: "選擇工作流素材", className: "catalogue-detail-sheet", content: renderCatalogueExplorer({ kind, onSelect: async item => {
      try {
        const data = await api("/api/library/import", { method: "POST", body: JSON.stringify({ id: item.libraryId, slot: up.id }) });
        assertSessionContext(session);
        if (useServerVideo(up, input, preview, item.title, { ...data, filename: data.serverFile || data.filename }, "")) {
          dismissTopSheet(() => dismissTopSheet());
        }
      } catch (error) { if (!isStaleSessionError(error)) showAlert(error.message, true); }
    } }) });
  } });
  if (!up.urlFetch) return h("div", { class: "upload-slot" }, [dropzone, materialButton]);
  const extraSources = [renderUrlFetch(up, input, preview, hint)];
  if ((up.accept || "").startsWith("video/")) {
    extraSources.push(renderCrawlVideoPicker(up, input, preview));
    extraSources.push(renderDriveFetch(up, input, preview));
  }
  return h("div", { class: "upload-slot" }, [
    h("div", { class: "method-tag", text: "方式一：上傳檔案" }),
    dropzone,
    materialButton,
    ...extraSources,
  ]);
}

function useServerVideo(up, fileInput, preview, label, data, icon) {
  if (fileInputs[up.id] !== fileInput || !fileInput.isConnected) return false;
  serverFiles[up.id] = data.filename;
  fileInput.value = "";
  clear(preview);
  preview.appendChild(h("span", { class: "file-name", text: `${icon || "📥"} ${label || data.name || data.filename}` }));
  if (typeof up.onFileChange === "function") {
    up.onFileChange(up.id, { serverFile: data.filename, label: label || data.name || data.filename });
  }
  return true;
}

// 方式二：從網址下載 — pull a clip via the server's yt-dlp into ComfyUI's input
// dir, then treat it exactly like an uploaded file (tracked in serverFiles).
function renderUrlFetch(up, fileInput, preview, hint) {
  const url = h("input", {
    type: "url",
    class: "url-input",
    placeholder: "貼上 YouTube / TikTok / FB 影片網址",
  });
  const status = h("div", { class: "url-status" });
  const btn = h("button", { type: "button", class: "ghost small url-btn", text: "下載" });
  btn.onclick = async () => {
    const value = url.value.trim();
    if (!value) {
      status.textContent = "請先貼上網址";
      status.className = "url-status err";
      return;
    }
    btn.disabled = true;
    btn.textContent = "下載中...";
    status.textContent = "下載中，較長的影片可能要等一下...";
    status.className = "url-status";
    try {
      const data = await api("/api/fetch-url", {
        method: "POST",
        body: JSON.stringify({ url: value, slot: up.id }),
      });
      useServerVideo(up, fileInput, preview, data.name || data.filename, data, "📥");
      status.textContent = `已下載（${formatBytes(data.size || 0)}）`;
      status.className = "url-status ok";
    } catch (error) {
      status.textContent = `下載失敗：${error.message}`;
      status.className = "url-status err";
    } finally {
      btn.disabled = false;
      btn.textContent = "下載";
    }
  };
  return h("div", { class: "url-fetch" }, [
    h("div", { class: "method-tag", text: "方式二：從網址下載" }),
    h("div", { class: "url-row" }, [url, btn]),
    status,
  ]);
}

function renderCrawlVideoPicker(up, fileInput, preview) {
  const search = h("input", {
    type: "search",
    class: "url-input",
    placeholder: "搜尋 fb_crawl 影片，例如 dance / workout / 檔名",
  });
  const sel = h("select", { class: "server-video-select" });
  const status = h("div", { class: "url-status" });
  const refresh = h("button", { type: "button", class: "ghost small url-btn", text: "搜尋" });
  const useBtn = h("button", { type: "button", class: "ghost small url-btn", text: "使用" });
  const videoPreview = h("video", {
    class: "crawl-preview-video hidden",
    controls: "",
    muted: "",
    preload: "metadata",
    playsinline: "",
  });

  function updatePreview() {
    const id = sel.value;
    if (!id) {
      videoPreview.removeAttribute("src");
      videoPreview.load();
      videoPreview.classList.add("hidden");
      return;
    }
    videoPreview.src = `/api/crawl-videos/preview?id=${encodeURIComponent(id)}`;
    videoPreview.classList.remove("hidden");
  }

  async function loadList() {
    refresh.disabled = true;
    refresh.textContent = "讀取中...";
    status.textContent = "讀取 fb_crawl 影片庫...";
    status.className = "url-status";
    try {
      const params = new URLSearchParams({ max_items: "300" });
      if (search.value.trim()) params.set("q", search.value.trim());
      const data = await api(`/api/crawl-videos?${params.toString()}`);
      sel.textContent = "";
      const videos = data.videos || [];
      if (!videos.length) {
        sel.appendChild(h("option", { value: "", text: "找不到影片" }));
        status.textContent = "找不到符合的 fb_crawl 影片";
      } else {
        for (const video of videos) {
          const folder = video.folder ? `${video.folder}/` : "";
          sel.appendChild(h("option", {
            value: video.id,
            text: `${folder}${video.name} (${formatBytes(video.size || 0)})`,
          }));
        }
        status.textContent = `找到 ${videos.length} 支影片`;
      }
      status.className = "url-status ok";
      updatePreview();
    } catch (error) {
      sel.textContent = "";
      sel.appendChild(h("option", { value: "", text: "讀取失敗" }));
      status.textContent = `讀取失敗：${error.message}`;
      status.className = "url-status err";
      updatePreview();
    } finally {
      refresh.disabled = false;
      refresh.textContent = "搜尋";
    }
  }

  sel.addEventListener("change", updatePreview);
  refresh.addEventListener("click", loadList);
  search.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      loadList();
    }
  });
  useBtn.addEventListener("click", async () => {
    const id = sel.value;
    if (!id) {
      status.textContent = "請先選擇影片";
      status.className = "url-status err";
      return;
    }
    useBtn.disabled = true;
    useBtn.textContent = "準備中...";
    status.textContent = "複製影片到 ComfyUI input...";
    status.className = "url-status";
    try {
      const data = await api("/api/crawl-videos/import", {
        method: "POST",
        body: JSON.stringify({ id, slot: up.id }),
      });
      useServerVideo(up, fileInput, preview, data.name || id, data, "🗂️");
      status.textContent = `已選用（${formatBytes(data.size || 0)}）`;
      status.className = "url-status ok";
    } catch (error) {
      status.textContent = `選用失敗：${error.message}`;
      status.className = "url-status err";
    } finally {
      useBtn.disabled = false;
      useBtn.textContent = "使用";
    }
  });
  setTimeout(loadList, 0);
  return h("div", { class: "url-fetch server-video-fetch" }, [
    h("div", { class: "method-tag", text: "方式三：fb_crawl 工作區影片" }),
    h("div", { class: "url-row" }, [search, refresh]),
    sel,
    videoPreview,
    h("div", { class: "url-row" }, [useBtn]),
    status,
  ]);
}

function renderDriveFetch(up, fileInput, preview) {
  const accountStatus = h("div", { class: "url-status" });
  const loginBtn = h("button", { type: "button", class: "ghost small url-btn", text: "登入 Google Drive" });
  const logoutBtn = h("button", { type: "button", class: "ghost small url-btn hidden", text: "登出 Drive" });
  const search = h("input", {
    type: "search",
    class: "url-input",
    placeholder: "搜尋 Drive 影片檔名",
  });
  const refresh = h("button", { type: "button", class: "ghost small url-btn", text: "搜尋" });
  const sel = h("select", { class: "server-video-select" });
  const useFileBtn = h("button", { type: "button", class: "ghost small url-btn", text: "匯入選取影片" });
  const listStatus = h("div", { class: "url-status" });
  const pickerPanel = h("div", { class: "drive-picker hidden" }, [
    h("div", { class: "url-row" }, [search, refresh]),
    sel,
    h("div", { class: "url-row" }, [useFileBtn, logoutBtn]),
    listStatus,
  ]);

  async function loadDriveStatus() {
    accountStatus.textContent = "檢查 Google Drive 狀態...";
    accountStatus.className = "url-status";
    try {
      const data = await api("/api/google/status");
      if (!data.configured) {
        loginBtn.classList.add("hidden");
        logoutBtn.classList.add("hidden");
        pickerPanel.classList.add("hidden");
        accountStatus.textContent = `需要設定 Google OAuth。Redirect URI: ${data.redirectUri || "/api/google/callback"}`;
        accountStatus.className = "url-status err";
        return;
      }
      loginBtn.classList.toggle("hidden", !!data.connected);
      logoutBtn.classList.toggle("hidden", !data.connected);
      pickerPanel.classList.toggle("hidden", !data.connected);
      accountStatus.textContent = data.connected ? "Google Drive 已登入" : "尚未登入 Google Drive";
      accountStatus.className = data.connected ? "url-status ok" : "url-status";
      if (data.connected) loadDriveFiles();
    } catch (error) {
      accountStatus.textContent = `Drive 狀態讀取失敗：${error.message}`;
      accountStatus.className = "url-status err";
    }
  }

  async function loadDriveFiles() {
    refresh.disabled = true;
    refresh.textContent = "讀取中...";
    listStatus.textContent = "讀取 Google Drive 影片...";
    listStatus.className = "url-status";
    try {
      const params = new URLSearchParams({ max_items: "50" });
      if (search.value.trim()) params.set("q", search.value.trim());
      const data = await api(`/api/google/drive/files?${params.toString()}`);
      sel.textContent = "";
      const files = data.files || [];
      if (!files.length) {
        sel.appendChild(h("option", { value: "", text: "找不到 Drive 影片" }));
        listStatus.textContent = "找不到符合的 Drive 影片";
      } else {
        for (const file of files) {
          sel.appendChild(h("option", {
            value: file.id,
            text: `${file.name} (${formatBytes(file.size || 0)})`,
          }));
        }
        listStatus.textContent = `找到 ${files.length} 支影片`;
      }
      listStatus.className = "url-status ok";
    } catch (error) {
      sel.textContent = "";
      sel.appendChild(h("option", { value: "", text: "讀取失敗" }));
      listStatus.textContent = `讀取失敗：${error.message}`;
      listStatus.className = "url-status err";
    } finally {
      refresh.disabled = false;
      refresh.textContent = "搜尋";
    }
  }

  loginBtn.addEventListener("click", () => {
    window.location.href = "/api/google/login";
  });
  logoutBtn.addEventListener("click", async () => {
    logoutBtn.disabled = true;
    try {
      await api("/api/google/logout", { method: "POST", body: "{}" });
      await loadDriveStatus();
    } catch (error) {
      accountStatus.textContent = `Drive 登出失敗：${error.message}`;
      accountStatus.className = "url-status err";
    } finally {
      logoutBtn.disabled = false;
    }
  });
  refresh.addEventListener("click", loadDriveFiles);
  search.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      loadDriveFiles();
    }
  });
  useFileBtn.addEventListener("click", async () => {
    const fileId = sel.value;
    if (!fileId) {
      listStatus.textContent = "請先選擇 Drive 影片";
      listStatus.className = "url-status err";
      return;
    }
    useFileBtn.disabled = true;
    useFileBtn.textContent = "匯入中...";
    listStatus.textContent = "從 Google Drive 下載到 ComfyUI input...";
    listStatus.className = "url-status";
    try {
      const data = await api("/api/google/drive/import", {
        method: "POST",
        body: JSON.stringify({ fileId, slot: up.id }),
      });
      useServerVideo(up, fileInput, preview, data.name || data.filename, data, "☁️");
      listStatus.textContent = `已匯入（${formatBytes(data.size || 0)}）`;
      listStatus.className = "url-status ok";
    } catch (error) {
      listStatus.textContent = `匯入失敗：${error.message}`;
      listStatus.className = "url-status err";
    } finally {
      useFileBtn.disabled = false;
      useFileBtn.textContent = "匯入選取影片";
    }
  });

  const url = h("input", {
    type: "url",
    class: "url-input",
    placeholder: "貼上 Google Drive 檔案分享連結",
  });
  const status = h("div", { class: "url-status" });
  const btn = h("button", { type: "button", class: "ghost small url-btn", text: "匯入" });
  btn.onclick = async () => {
    const value = url.value.trim();
    if (!value) {
      status.textContent = "請先貼上 Google Drive 分享連結";
      status.className = "url-status err";
      return;
    }
    btn.disabled = true;
    btn.textContent = "匯入中...";
    status.textContent = "從 Google Drive 匯入影片...";
    status.className = "url-status";
    try {
      const data = await api("/api/fetch-drive", {
        method: "POST",
        body: JSON.stringify({ url: value, slot: up.id }),
      });
      useServerVideo(up, fileInput, preview, data.name || data.filename, data, "☁️");
      status.textContent = `已匯入（${formatBytes(data.size || 0)}）`;
      status.className = "url-status ok";
    } catch (error) {
      status.textContent = `匯入失敗：${error.message}`;
      status.className = "url-status err";
    } finally {
      btn.disabled = false;
      btn.textContent = "匯入";
    }
  };
  setTimeout(loadDriveStatus, 0);
  return h("div", { class: "url-fetch drive-fetch" }, [
    h("div", { class: "method-tag", text: "方式四：Google Drive 登入 / 分享連結" }),
    h("div", { class: "url-row" }, [loginBtn]),
    accountStatus,
    pickerPanel,
    h("div", { class: "method-tag", text: "或貼上分享連結" }),
    h("div", { class: "url-row" }, [url, btn]),
    status,
  ]);
}

function clearDropzoneSelection(up, input = up && fileInputs[up.id]) {
  if (!input) return;
  input.value = "";
  delete serverFiles[up.id];
  const dropzone = input.closest(".dropzone");
  const preview = dropzone && dropzone.querySelector(".drop-preview");
  if (preview) {
    clear(preview);
    preview.appendChild(h("div", { class: "drop-hint" }, [
      h("span", { class: "drop-ico", text: "⬆️" }),
      h("span", { class: "drop-text", text: "點擊或拖曳上傳" }),
      h("span", { class: "drop-formats", text: up.hint || "支援常見格式" }),
    ]));
  }
  if (typeof up.onFileChange === "function") up.onFileChange(up.id);
}

// ---- field renderers ------------------------------------------------------
function renderField(field) {
  const id = field.id;

  if (field.type === "checkbox") {
    const input = h("input", { type: "checkbox" });
    input.checked = Boolean(field.default);
    fieldGetters[id] = () => input.checked;
    return h("label", { class: "toggle" }, [input, h("span", { text: field.label })]);
  }

  if (field.type === "textarea") {
    const ta = h("textarea", {
      dataset: { fieldId: id },
      rows: String(field.rows || 4),
      placeholder: field.placeholder || "",
      maxlength: field.maxLength ? String(field.maxLength) : null,
      required: field.required ? "" : null,
    });
    ta.value = field.default || "";
    fieldGetters[id] = () => ta.value;
    const kids = [h("span", { class: "field-label", text: field.required ? `${field.label} *` : field.label })];
    if (field.assist) kids.push(renderAssistRow(field, ta));
    if (field.presets && field.presets.length) kids.push(renderPresetRow(field, ta));
    if (field.hint) kids.push(renderHint(field.hint));
    kids.push(ta);
    return h("label", { class: "field" }, kids);
  }

  if (field.display === "cards") {
    const opts = field.options || [];
    const buttons = [];
    let current = String(field.default ?? (opts[0] ? opts[0].value : ""));
    const group = h("div", { class: "card-group" });
    for (const opt of opts) {
      const value = String(opt.value);
      const card = h("button", {
        type: "button",
        class: `mode-card${value === current ? " active" : ""}`,
        dataset: { value },
        onclick: () => {
          current = value;
          buttons.forEach((b) => b.classList.toggle("active", b.dataset.value === value));
        },
      }, [
        h("span", { class: "mode-card-title", text: opt.label || value }),
        opt.sub ? h("span", { class: "mode-card-sub", text: opt.sub }) : null,
      ]);
      buttons.push(card);
      group.appendChild(card);
    }
    fieldGetters[id] = () => current;
    return h("div", { class: "field" }, [h("span", { class: "field-label", text: field.label }), group]);
  }

  if (field.type === "select") {
    const sel = buildSelect(field.options, field.default);
    if (field.required) sel.required = true;
    fieldGetters[id] = () => sel.value;
    return h("label", { class: "field" }, [h("span", { class: "field-label", text: field.label }), sel]);
  }

  if (field.type === "voiceSelect") {
    const sel = h("select");
    sel.appendChild(h("option", { value: "", text: "（改用上方上傳的音訊）" }));
    fieldGetters[id] = () => sel.value;
    loadVoiceOptions(sel);
    return h("label", { class: "field" }, [h("span", { class: "field-label", text: field.label }), sel]);
  }

  if (field.type === "personaSelect") {
    const sel = h("select", { required: "" });
    sel.appendChild(h("option", { value: "", text: "載入聲音中…" }));
    fieldGetters[id] = () => sel.value;
    loadPersonaOptions(sel);
    return h("label", { class: "field" }, [h("span", { class: "field-label", text: field.label }), sel]);
  }

  if (field.type === "aspect") {
    const group = h("div", { class: "aspect-group" });
    const buttons = [];
    let current = String(field.default || "");
    for (const opt of field.options || []) {
      const value = `${opt.width}x${opt.height}`;
      const btn = h("button", {
        type: "button",
        class: `aspect-btn${value === current ? " active" : ""}`,
        dataset: { value },
        onclick: () => {
          current = value;
          buttons.forEach((b) => b.classList.toggle("active", b.dataset.value === value));
        },
      }, [
        h("span", { class: "aspect-glyph", style: `aspect-ratio:${opt.width}/${opt.height}` }),
        h("span", { class: "aspect-label", text: opt.label }),
      ]);
      buttons.push(btn);
      group.appendChild(btn);
    }
    if (!current && buttons[0]) {
      current = buttons[0].dataset.value;
      buttons[0].classList.add("active");
    }
    fieldGetters[id] = () => current;
    return h("div", { class: "field" }, [h("span", { class: "field-label", text: field.label }), group]);
  }

  if (field.type === "slider") {
    const input = h("input", {
      type: "range",
      min: String(field.min ?? 0),
      max: String(field.max ?? 100),
      step: String(field.step ?? 1),
    });
    input.value = String(field.default ?? field.min ?? 0);
    const out = h("b", { class: "slider-value", text: input.value });
    input.addEventListener("input", () => { out.textContent = input.value; });
    fieldGetters[id] = () => Number(input.value);
    const kids = [h("span", { class: "field-label" }, [field.label + " ", out]), input];
    if (field.minLabel || field.maxLabel) {
      kids.push(h("div", { class: "slider-labels" }, [
        h("span", { text: field.minLabel || "" }),
        h("span", { text: field.maxLabel || "" }),
      ]));
    }
    return h("label", { class: "field" }, kids);
  }

  // Keep large integer identifiers as decimal strings. Converting a uint64
  // seed through JavaScript Number would silently change values above 2^53-1.
  if (field.type === "integer-string") {
    const input = h("input", {
      type: "text",
      inputmode: field.inputMode || "numeric",
      pattern: field.pattern || "[0-9]+",
      maxlength: field.maxLength ? String(field.maxLength) : null,
      placeholder: field.placeholder || "",
      required: field.required ? "" : null,
    });
    input.value = field.default !== undefined && field.default !== null ? String(field.default) : "";
    fieldGetters[id] = () => input.value.trim();
    return h("label", { class: "field" }, [h("span", { class: "field-label", text: field.required ? `${field.label} *` : field.label }), input]);
  }

  // number / text
  const input = h("input", {
    type: field.type === "number" ? "number" : "text",
    min: field.min !== undefined ? String(field.min) : null,
    max: field.max !== undefined ? String(field.max) : null,
    step: field.step !== undefined ? String(field.step) : null,
    placeholder: field.placeholder || "",
    required: field.required ? "" : null,
  });
  input.value = field.default !== undefined && field.default !== null ? String(field.default) : "";
  fieldGetters[id] = () => input.value;
  return h("label", { class: "field" }, [h("span", { class: "field-label", text: field.required ? `${field.label} *` : field.label }), input]);
}

function buildSelect(options, def) {
  const sel = h("select");
  for (const opt of options || []) {
    const o = h("option", { value: String(opt.value), text: opt.label || String(opt.value) });
    if (String(opt.value) === String(def)) o.selected = true;
    sel.appendChild(o);
  }
  return sel;
}

function renderHint(text) {
  return h("div", { class: "field-hint" }, [
    h("span", { class: "note-ico", text: "ⓘ" }),
    h("span", { text }),
  ]);
}

function renderPresetRow(field, ta) {
  const row = h("div", { class: "preset-row" });
  for (const preset of field.presets) {
    row.appendChild(h("button", {
      type: "button",
      class: "chip",
      text: preset.label,
      onclick: () => { ta.value = preset.text; ta.focus(); },
    }));
  }
  return row;
}

function renderAssistRow(field, ta) {
  const row = h("div", { class: "assist-row" });
  const actions = [
    { mode: "brainstorm", label: "幫我想" },
    { mode: "enhance", label: "加強描述" },
    { mode: "random", label: "隨機提示詞" },
  ];
  for (const action of actions) {
    const btn = h("button", { type: "button", class: "assist-btn", text: `✨ ${action.label}` });
    btn.addEventListener("click", () => runAssist(action.mode, field.assist, ta, btn));
    row.appendChild(btn);
  }
  return row;
}

async function runAssist(mode, kind, ta, btn) {
  const session = captureSessionContext();
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = "正在讀取素材…";
  showAlert("");
  try {
    const references = await assistImageReferences(kind);
    assertSessionContext(session);
    btn.textContent = references.length ? "Gemini 正在看圖…" : "想中…";
    const result = await api("/api/assist", {
      method: "POST",
      body: JSON.stringify({ mode, kind, text: ta.value, references }),
    });
    assertSessionContext(session);
    ta.value = result.text;
    ta.focus();
  } catch (error) {
    if (isStaleSessionError(error)) return;
    showAlert(`AI 助手：${error.message}`, true);
  } finally {
    if (sessionContextIsCurrent(session)) {
      btn.disabled = false;
      btn.textContent = original;
    }
  }
}

async function assistImageReferences(kind) {
  if (kind !== "video" && kind !== "image") return [];
  const feature = currentFeature();
  const uploadSpecs = new Map((feature && feature.uploads || []).map((item) => [item.id, item]));
  const preferredSlots = ["image", "start", "end", "reference", "face", "scene", "image1", "image2", "image3"];
  const references = [];
  for (const slot of preferredSlots) {
    if (references.length >= 3) break;
    const spec = uploadSpecs.get(slot);
    if (!spec || !String(spec.accept || "").startsWith("image/")) continue;
    const input = fileInputs[slot];
    const file = input && input.files && input.files[0];
    if (file) {
      if (!String(file.type || "").startsWith("image/")) continue;
      references.push({ slot, ...(await fileToData(file)) });
      continue;
    }
    if (serverFiles[slot]) {
      references.push({ slot, serverFile: serverFiles[slot] });
    }
  }
  return references;
}

// ---- gated / storage / missing panels -------------------------------------
function renderMissing(feature) {
  const box = h("div", { class: "missing-box" });
  const reason = featureUnavailableReason(feature);
  const missingNodes = featureMissingNodesForContext(feature);
  if (feature.runtimeReason || feature.genericRuntimeReason || (feature.operatorOnly && !isOperator())) {
    box.appendChild(h("div", { text: reason }));
  }
  if (feature.missingModels && feature.missingModels.length) {
    box.appendChild(h("div", { text: "缺少模型：" + feature.missingModels.join("、") }));
  }
  if (missingNodes.length) {
    box.appendChild(h("div", { text: "缺少節點：" + missingNodes.join("、") }));
  }
  return box;
}

function renderGatedPanel(feature) {
  const box = h("div", { class: "gated-panel" });
  const missingNodes = featureMissingNodesForContext(feature);
  box.appendChild(h("div", { class: "gated-tag", text: featureUnavailableReason(feature) }));
  if (feature.missingModels && feature.missingModels.length) {
    box.appendChild(h("div", { class: "gated-row" }, [h("b", { text: "模型：" }), feature.missingModels.join("、")]));
  }
  if (missingNodes.length) {
    box.appendChild(h("div", { class: "gated-row" }, [h("b", { text: "節點：" }), missingNodes.join("、")]));
  }
  if (!(feature.missingModels || []).length && !missingNodes.length) {
    if (!feature.runtimeReason && !feature.genericRuntimeReason && !(feature.operatorOnly && !isOperator())) {
      box.appendChild(h("div", { class: "gated-row", text: "這項工具目前沒有可用的執行模組。" }));
    }
  }
  return box;
}

function renderStoragePanel(feature) {
  if (feature.id === "voice-library") return renderVoiceLibrary();
  const box = h("div", { class: "storage-panel" });
  box.appendChild(h("p", { text: "這個工具即將上線。" }));
  return box;
}

// ---- text studio (post generator) -----------------------------------------
function postOptions(feature, id, capability = null) {
  const f = (feature.fields || []).find((x) => x.id === id);
  return f && f.options ? filterCapabilityOptions(capability, id, f.options) : [];
}

function attachCounter(ta, max) {
  const counter = h("span", { class: "char-counter" });
  ta.maxLength = max;
  const update = () => {
    if (ta.value.length > max) ta.value = ta.value.slice(0, max);
    counter.textContent = `${ta.value.length}/${max}`;
    counter.classList.toggle("full", ta.value.length >= max);
  };
  ta.addEventListener("input", update);
  update();
  return counter;
}

function postAssistBtn(ta, label) {
  const btn = h("button", { type: "button", class: "assist-btn", text: `✨ ${label || "幫我想"}` });
  btn.addEventListener("click", () => runAssist("brainstorm", "post", ta, btn));
  return btn;
}

function labeledField(label, control, counter) {
  return h("label", { class: "field" }, [
    h("div", { class: "field-label-row" }, [
      h("span", { class: "field-label", text: label }),
      counter || null,
    ]),
    control,
  ]);
}

function renderTextForm(feature, capability = null) {
  const wrap = h("div", { class: "text-studio" });
  let mode = "quick";

  // segmented 快速延伸 / 進階模式 toggle
  const quickBtn = h("button", { type: "button", class: "seg-btn active", text: "快速延伸" });
  const advBtn = h("button", { type: "button", class: "seg-btn", text: "進階模式" });
  const toggle = h("div", { class: "seg-toggle" }, [quickBtn, advBtn]);

  // quick: one sentence → AI expands
  const quickTa = h("textarea", { rows: "3", placeholder: "用一句話講你想發什麼，AI 幫你延伸成完整貼文…" });
  const quickCounter = attachCounter(quickTa, 1200);
  const quickPanel = h("div", { class: "post-mode-panel" }, [
    h("label", { class: "field" }, [
      h("div", { class: "field-label-row" }, [
        h("span", { class: "field-label", text: "一句話" }),
        quickCounter,
      ]),
      h("div", { class: "assist-row" }, [postAssistBtn(quickTa)]),
      quickTa,
    ]),
  ]);

  // advanced: topic + tone + keywords
  const advTa = h("textarea", { rows: "4", placeholder: "想講的主題、背景、想傳達的重點…" });
  const advCounter = attachCounter(advTa, 2000);
  const toneSel = buildSelect(postOptions(feature, "tone", capability), "warm");
  const kwInput = h("input", { type: "text", placeholder: "用逗號分隔，例如：手沖, 週末限定, 門市", maxlength: "120" });
  const advPanel = h("div", { class: "post-mode-panel hidden" }, [
    h("label", { class: "field" }, [
      h("div", { class: "field-label-row" }, [
        h("span", { class: "field-label", text: "貼文主題" }),
        advCounter,
      ]),
      h("div", { class: "assist-row" }, [postAssistBtn(advTa)]),
      advTa,
    ]),
    labeledField("語調風格", toneSel),
    labeledField("關鍵字（可留空）", kwInput),
  ]);

  // shared controls (both modes)
  const lengthSel = buildSelect(postOptions(feature, "length", capability), "medium");
  const platformSel = buildSelect(postOptions(feature, "platform", capability), "threads");
  const sisterOptions = postOptions(feature, "sister", capability);
  const sisterSel = buildSelect(sisterOptions, "claude");
  const roleCharacter = activeCharacter();
  const exactRolePost = Boolean(
    roleCharacter
    && capability
    && capability.variantId === "post.ai-sister.provider.v1"
    && sisterOptions.length === 1
  );
  const sisterField = exactRolePost
    ? h("div", { class: "field role-bound-summary" }, [
        h("span", { class: "field-label", text: "角色寫手" }),
        h("div", { class: "bound-value", text: `${roleCharacter.name} 的原生貼文模組` }),
        renderHint("已跟角色供應商綁定，不會切換成其他姊妹或備援模型。"),
      ])
    : labeledField("哪位姊妹來寫", sisterSel);
  const shared = h("div", { class: "post-shared" }, [
    labeledField("文章長度", lengthSel),
    labeledField("目標平台", platformSel),
    sisterField,
  ]);

  function setMode(next) {
    mode = next;
    quickBtn.classList.toggle("active", next === "quick");
    advBtn.classList.toggle("active", next === "advanced");
    quickPanel.classList.toggle("hidden", next !== "quick");
    advPanel.classList.toggle("hidden", next !== "advanced");
  }
  quickBtn.addEventListener("click", () => setMode("quick"));
  advBtn.addEventListener("click", () => setMode("advanced"));

  const collect = () => ({
    mode,
    topic: (mode === "quick" ? quickTa.value : advTa.value).trim(),
    tone: toneSel.value,
    keywords: kwInput.value.trim(),
    length: lengthSel.value,
    platform: platformSel.value,
    sister: sisterSel.value,
  });

  const form = h("form", { class: "gen-form", onsubmit: (e) => onGeneratePost(e, feature, collect) });
  form.appendChild(toggle);
  form.appendChild(quickPanel);
  form.appendChild(advPanel);
  form.appendChild(shared);
  form.appendChild(h("button", { class: "primary generate-btn", type: "submit", text: "生成貼文" }));
  wrap.appendChild(form);
  wrap.appendChild(h("div", { class: "post-result hidden", id: "postResult" }));
  return wrap;
}

async function onGeneratePost(event, feature, collect) {
  event.preventDefault();
  const character = activeCharacter();
  if (activeCharacterId && !character) {
    showAlert("目前角色已不存在，請重新選擇。", true);
    return;
  }
  const capability = character ? enabledFeatureCapability(character, feature.id) : null;
  if (character && !capability) {
    showAlert("這個角色不能使用這項貼文功能。", true);
    return;
  }
  const session = captureSessionContext();
  const settings = applyCharacterDefaults(feature, collect());
  if (!settings.topic) {
    showAlert(settings.mode === "quick" ? "請先輸入一句話" : "請先輸入貼文主題", true);
    return;
  }
  const btn = submittedForm.querySelector("button[type=submit]");
  if (btn) { btn.disabled = true; btn.textContent = "生成中…"; }
  showAlert("");
  try {
    if (character && !capabilityStillEnabled(character.id, capability.actionId, capability.variantId, feature.id)) {
      throw new Error("這個角色的功能設定已變更，請重新選擇");
    }
    const result = await api("/api/post", {
      method: "POST",
      body: JSON.stringify({
        featureId: feature.id,
        characterId: character ? character.id : "",
        action: capability ? capability.actionId : "",
        variantId: capability ? capability.variantId : "",
        settings,
      }),
    });
    assertSessionContext(session);
    if (activeCharacterId) characterPostsCache.delete(activeCharacterId);
    libraryPostsLoaded = false;
    libraryPostsError = "";
    renderPostResult($("postResult"), result);
  } catch (error) {
    if (isStaleSessionError(error)) return;
    showAlert(`生成失敗：${error.message}`, true);
  } finally {
    if (sessionContextIsCurrent(session) && btn) { btn.disabled = false; btn.textContent = "生成貼文"; }
  }
}

function renderPostResult(box, result) {
  if (!box) return;
  clear(box);
  box.classList.remove("hidden");
  box.appendChild(h("div", { class: "post-result-head" }, [
    h("span", { class: "post-meta", text: [result.character && result.character.name, result.sister, result.engine].filter(Boolean).join("・") }),
    h("button", { class: "ghost copy-btn", type: "button", text: "複製", onclick: () => copyText(result.text) }),
  ]));
  box.appendChild(h("div", { class: "post-text", text: result.text }));
}

function copyText(text) {
  if (navigator.clipboard) {
    navigator.clipboard.writeText(text).then(
      () => showAlert("已複製到剪貼簿"),
      () => showAlert("複製失敗，請手動選取", true),
    );
  }
}

// ---- voice library --------------------------------------------------------
function formatBytes(n) {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / 1024 ** i).toFixed(i ? 1 : 0)} ${units[i]}`;
}

function renderVoiceLibrary() {
  const box = h("div", { class: "storage-panel voice-lib" });
  const nameInput = h("input", { type: "text", placeholder: "名稱（可留空，預設用檔名）", maxlength: "80" });
  const sourceInput = h("input", { type: "text", placeholder: "來源 / 備註（例如 Custom English clean ref、VoxCPM strict054）", maxlength: "160" });
  const transcriptTa = h("textarea", { rows: "2", placeholder: "參考音訊逐字稿（5-15 秒乾淨 reference 建議必填）", maxlength: "180" });
  const transcriptCounter = attachCounter(transcriptTa, 180);
  const fileInput = h("input", { type: "file", accept: "audio/*" });
  const status = h("div", { class: "voice-status" });
  const uploadBtn = h("button", { class: "primary", type: "button", text: "上傳音訊" });
  const listWrap = h("div", { class: "voice-list" });

  uploadBtn.addEventListener("click", async () => {
    const session = captureSessionContext();
    const file = fileInput.files && fileInput.files[0];
    if (!file) { status.textContent = "請先選擇音訊檔。"; return; }
    uploadBtn.disabled = true;
    status.textContent = "上傳中...";
    try {
      const payload = await fileToData(file);
      assertSessionContext(session);
      await api("/api/voices", {
        method: "POST",
        body: JSON.stringify({
          name: nameInput.value.trim(),
          source: sourceInput.value.trim(),
          transcript: transcriptTa.value.trim(),
          kind: "reference",
          file: payload,
        }),
      });
      assertSessionContext(session);
      nameInput.value = "";
      sourceInput.value = "";
      transcriptTa.value = "";
      transcriptTa.dispatchEvent(new Event("input"));
      fileInput.value = "";
      status.textContent = "已新增。";
      loadVoiceList(listWrap);
    } catch (error) {
      if (isStaleSessionError(error)) return;
      status.textContent = `上傳失敗：${error.message}`;
    } finally {
      if (sessionContextIsCurrent(session)) uploadBtn.disabled = false;
    }
  });

  box.appendChild(h("div", { class: "voice-uploader" }, [
    h("label", { class: "field" }, [h("span", { class: "field-label", text: "名稱" }), nameInput]),
    h("label", { class: "field" }, [h("span", { class: "field-label", text: "來源 / 備註" }), sourceInput]),
    labeledField("參考音訊逐字稿", transcriptTa, transcriptCounter),
    h("label", { class: "field" }, [h("span", { class: "field-label", text: "音訊檔（mp3 / wav / m4a / ogg / flac）" }), fileInput]),
    uploadBtn,
    status,
  ]));
  box.appendChild(listWrap);
  loadVoiceList(listWrap);
  return box;
}

async function loadVoiceList(wrap) {
  const session = captureSessionContext();
  clear(wrap);
  wrap.appendChild(h("div", { class: "voice-loading", text: "讀取中..." }));
  try {
    const data = await api("/api/voices");
    assertSessionContext(session);
    clear(wrap);
    const voices = data.voices || [];
    if (!voices.length) {
      wrap.appendChild(h("div", { class: "empty", text: "聲音庫還是空的，上傳第一段參考音訊吧。" }));
      return;
    }
    for (const voice of voices) {
      const audio = h("audio", { controls: "", preload: "none", src: voice.url });
      const del = voice.canDelete === true
        ? h("button", { class: "ghost small", type: "button", text: "刪除" })
        : null;
      const details = [
        formatBytes(voice.size),
        voice.kind || "",
        voice.engine || voice.tool || "",
        voice.source || "",
      ].filter(Boolean).join(" ・ ");
      const metaKids = [
        h("div", { class: "voice-name", text: voice.name }),
        h("div", { class: "voice-sub", text: details || formatBytes(voice.size) }),
      ];
      if (voice.transcript) {
        metaKids.push(h("div", { class: "voice-transcript", text: voice.transcript }));
      }
      if (del) {
        del.addEventListener("click", async () => {
          const deleteSession = captureSessionContext();
          if (!window.confirm(`刪除「${voice.name}」？`)) return;
          del.disabled = true;
          try {
            await api("/api/voices/delete", { method: "POST", body: JSON.stringify({ id: voice.id }) });
            assertSessionContext(deleteSession);
            loadVoiceList(wrap);
          } catch (error) {
            if (isStaleSessionError(error)) return;
            del.disabled = false;
            showAlert(`刪除失敗：${error.message}`, true);
          }
        });
      }
      wrap.appendChild(h("div", { class: "voice-row" }, [
        h("div", { class: "voice-meta" }, metaKids),
        audio,
        del,
      ]));
    }
  } catch (error) {
    if (isStaleSessionError(error)) return;
    clear(wrap);
    wrap.appendChild(h("div", { class: "missing-box", text: `讀取失敗：${error.message}` }));
  }
}

// ---- files ----------------------------------------------------------------
function fileToData(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({ name: file.name, type: file.type, dataUrl: reader.result });
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function previewFile(input, previewNode, hintNode) {
  clear(previewNode);
  const file = input.files && input.files[0];
  if (!file) {
    if (hintNode) previewNode.appendChild(hintNode);
    return;
  }
  if (file.type.startsWith("image/")) {
    const url = URL.createObjectURL(file);
    const img = h("img", { alt: "" });
    img.src = url;
    img.onload = () => URL.revokeObjectURL(url);
    previewNode.appendChild(img);
  } else {
    previewNode.appendChild(h("span", { class: "file-name", text: file.name }));
  }
}

async function loadVoiceOptions(sel, preferred = "") {
  const session = captureSessionContext();
  try {
    const data = await api("/api/voices");
    assertSessionContext(session);
    for (const voice of data.voices || []) {
      const label = voice.source ? `${voice.name} ・ ${voice.source}` : voice.name;
      const option = h("option", {
        value: voice.id,
        text: label,
        dataset: {
          transcript: voice.transcript || "",
          source: voice.source || "",
          engine: voice.engine || "",
          tool: voice.tool || "",
        },
      });
      if (preferred && String(voice.id) === String(preferred)) option.selected = true;
      sel.appendChild(option);
    }
  } catch (error) {
    if (isStaleSessionError(error)) return;
    /* leave the upload-only option in place if the library can't be read */
  }
}

async function loadCharacterVoiceOptions(sel, character, preferred = "", emptyMessage = "", allowedIds = null) {
  const session = captureSessionContext();
  let matched = false;
  try {
    const voice = (character && character.voice) || {};
    let boundIds = new Set((Array.isArray(voice.voiceLibraryIds) ? voice.voiceLibraryIds : []).map((id) => String(id)));
    if (allowedIds instanceof Set) {
      boundIds = new Set([...boundIds].filter((id) => allowedIds.has(id)));
    }
    if (!boundIds.size) {
      if (emptyMessage) sel.appendChild(h("option", { value: "", text: emptyMessage, disabled: "" }));
      return matched;
    }
    const data = await api("/api/voices");
    assertSessionContext(session);
    for (const voice of data.voices || []) {
      if (!boundIds.has(String(voice.id))) continue;
      const option = h("option", {
        value: voice.id,
        text: voice.name || voice.id,
        dataset: {
          transcript: voice.transcript || "",
          source: voice.source || "",
          engine: voice.engine || "",
          tool: voice.tool || "",
        },
      });
      if (preferred && String(voice.id) === String(preferred)) {
        option.selected = true;
        matched = true;
      }
      sel.appendChild(option);
    }
  } catch (error) {
    if (isStaleSessionError(error)) return null;
    /* keep the default/empty option available if the library can't be read */
  }
  return matched;
}

async function loadPersonaOptions(sel) {
  const session = captureSessionContext();
  try {
    const data = await api("/api/personas");
    assertSessionContext(session);
    const personas = data.personas || [];
    sel.textContent = "";
    if (!personas.length) {
      sel.appendChild(h("option", { value: "", text: "（聲音服務未啟動）" }));
      return;
    }
    for (const p of personas) {
      const accent = p.accent || "國語(台灣)";
      const source = p.source === undefined ? "AI-Sister" : p.source;
      const label = source ? `${p.name} ・ ${accent} ・ 來源 - ${source}` : `${p.name} ・ ${accent}`;
      sel.appendChild(h("option", { value: p.id, text: label }));
    }
  } catch (error) {
    if (isStaleSessionError(error)) return;
    sel.textContent = "";
    sel.appendChild(h("option", { value: "", text: "（聲音服務未啟動）" }));
  }
}

// ---- generate -------------------------------------------------------------
function collectSettings(feature) {
  const settings = {};
  for (const field of feature.fields || []) {
    const get = fieldGetters[field.id];
    if (!get) continue;
    const value = get();
    if (field.type === "aspect") {
      const [w, h2] = String(value).split("x").map(Number);
      settings.width = w;
      settings.height = h2;
    } else {
      settings[field.id] = value;
    }
  }
  return applyCharacterDefaults(feature, settings);
}

async function onGenerate(event, feature) {
  event.preventDefault();
  const submittedForm = event.currentTarget;
  const assertSubmittedFormCurrent = () => {
    if (!submittedForm || !submittedForm.isConnected) throw new Error("創作工具已切換，這次沒有送出");
  };
  assertSubmittedFormCurrent();
  const character = activeCharacter();
  if (activeCharacterId && !character) {
    showAlert("目前角色已不存在，請重新選擇。", true);
    return;
  }
  const capability = character ? enabledFeatureCapability(character, feature.id) : null;
  if (character && !capability) {
    showAlert("這個角色不能使用這項工具。", true);
    return;
  }
  const minimumUploads = Number.isInteger(feature.minimumUploads) && feature.minimumUploads > 0
    ? feature.minimumUploads
    : 0;
  if (minimumUploads) {
    const selected = (feature.uploads || []).filter((up) => (
      capabilityServerBindsUpload(capability, up.id) || slotHasFile(up.id, {})
    )).length;
    if (selected < minimumUploads) {
      showAlert("至少選一個參考", true);
      return;
    }
  }
  const session = captureSessionContext();
  const btn = event.target.querySelector("button[type=submit]");
  const originalText = btn ? btn.textContent : "";
  let requestSent = false;
  if (btn) { btn.disabled = true; btn.textContent = "送出中..."; }
  showAlert("");
  try {
    const files = {};
    const selectedDanceTemplateId = (
      capability
      && capability.variantId === "dance.anime.animate.v1"
      && fieldGetters.danceTemplateId
    ) ? String(fieldGetters.danceTemplateId() || "") : "";
    for (const up of feature.uploads || []) {
      assertSubmittedFormCurrent();
      if (capabilityServerBindsUpload(capability, up.id)) continue;
      if (feature.id === "dance" && up.id === "video" && selectedDanceTemplateId) {
        const imported = await importSelectedDanceTemplate(selectedDanceTemplateId, up.id);
        assertSessionContext(session);
        assertSubmittedFormCurrent();
        files[up.id] = { serverFile: imported.serverFile };
        continue;
      }
      if (!slotHasFile(up.id, {})) {
        if (up.required) throw new Error(`請上傳「${up.label}」`);
        continue;
      }
      const resolved = await ensureSlotServerFile(feature, {}, up.id);
      assertSessionContext(session);
      assertSubmittedFormCurrent();
      files[up.id] = { serverFile: resolved.serverFile };
    }
    assertSubmittedFormCurrent();
    if ((character && activeCharacterId !== character.id) || (!character && activeCharacterId)) {
      throw new Error("目前角色已切換，這次沒有送出；請確認後再試一次");
    }
    if (character && !capabilityStillEnabled(character.id, capability.actionId, capability.variantId, feature.id)) {
      throw new Error("這個角色的功能設定已變更，請重新選擇");
    }
    const settings = collectSettings(feature);
    const hasWardrobeContract = capability && allowedOptionValues(capability, "wardrobeTheme") !== null;
    if (hasWardrobeContract) {
      if (fieldGetters.wardrobeTheme) settings.wardrobeTheme = fieldGetters.wardrobeTheme();
      if (!String(settings.wardrobeTheme || "").trim()) throw new Error("請先選擇角色造型");
    }
    if (capability && capability.variantId === "image-video.anime.wan21-i2v.v1") {
      for (const id of ["motionPreset", "motionScale", "duration", "quality"]) {
        if (fieldGetters[id]) settings[id] = fieldGetters[id]();
      }
    }
    if (capability && capability.variantId === "photoshoot.anime.scene-card.v1") {
      for (const id of ["sceneTheme", "posePreset", "quality"]) {
        if (fieldGetters[id]) settings[id] = fieldGetters[id]();
      }
    }
    assertSubmittedFormCurrent();
    requestSent = true;
    const result = await api("/api/generate", {
      method: "POST",
      body: JSON.stringify({
        featureId: feature.id,
        characterId: character ? character.id : "",
        action: capability ? capability.actionId : "",
        variantId: capability ? capability.variantId : "",
        files,
        settings,
      }),
    });
    assertSessionContext(session);
    showAlert(`已加入佇列：${result.prompt_id || "OK"}`);
    refreshQueue();
    scheduleHistoryRefresh();
  } catch (error) {
    if (isStaleSessionError(error)) return;
    const presented = generationErrorPresentation(error, requestSent);
    showAlert(presented.detail, true, { technicalDetail: presented.technicalDetail });
  } finally {
    if (sessionContextIsCurrent(session) && submittedForm.isConnected && btn) {
      const missingBoundTheme = capability
        && allowedOptionValues(capability, "wardrobeTheme") !== null
        && (!fieldGetters.wardrobeTheme || !String(fieldGetters.wardrobeTheme() || "").trim());
      const missingBoundScene = capability
        && allowedOptionValues(capability, "sceneTheme") !== null
        && (!fieldGetters.sceneTheme || !String(fieldGetters.sceneTheme() || "").trim());
      btn.disabled = !feature.ready || Boolean(missingBoundTheme) || Boolean(missingBoundScene);
      btn.textContent = missingBoundTheme
        ? "請先選擇角色造型"
        : (missingBoundScene ? "請先選擇動漫場景" : (originalText || (feature.ready ? "開始生成" : "缺件，暫不能送出")));
    }
  }
}

// ---- voice batch custom page ---------------------------------------------
function voiceBatchRenderAgain() {
  if (currentView === "studio" && currentFeatureId === "voice-batch") renderStage();
}

function voiceBatchSet(patch) {
  voiceBatchState = { ...voiceBatchState, ...patch };
  voiceBatchRenderAgain();
}

function voiceBatchSplitText(text) {
  return String(text || "")
    .split(/\n\s*\n+/)
    .map((part) => part.trim())
    .filter(Boolean)
    .map((text, index) => ({ index: index + 1, text }));
}

function voiceBatchEstimateMinutes(segments) {
  const chars = segments.reduce((sum, segment) => sum + String(segment.text || "").length, 0);
  return Math.max(1, Math.round(chars / 2.5 / 60));
}

function voiceBatchValidation() {
  const segments = voiceBatchState.segments.map((segment) => String(segment.text || "").trim()).filter(Boolean);
  if (!segments.length) return { ok: false, reason: "請先加入至少一段文字" };
  if (segments.length > 120) return { ok: false, reason: "最多 120 段,請刪減" };
  const tooLong = voiceBatchState.segments.findIndex((segment) => String(segment.text || "").trim().length > 2400);
  if (tooLong >= 0) return { ok: false, reason: `第 ${tooLong + 1} 段太長,請拆段`, tooLong: tooLong + 1 };
  return { ok: true, reason: "" };
}

function voiceBatchStatusText(status) {
  const labels = {
    queued: "等待中",
    pending: "等待中",
    running: "生成中",
    done: "完成",
    completed: "完成",
    completed_approved: "完成",
    failed: "失敗",
    cancelled: "已取消",
    cancel_requested: "取消中",
  };
  return labels[status] || status || "等待中";
}

function voiceBatchRedoActive(status) {
  return ["pending", "queued", "running", "cancel_requested"].includes(String(status || ""));
}

function voiceBatchTerminalStatus(status) {
  return ["done", "completed", "completed_approved", "failed", "cancelled"].includes(String(status || ""));
}

function voiceBatchShouldPoll(batch) {
  if (!batch) return false;
  const activeBatch = ["queued", "pending", "running"].includes(String(batch.status || ""));
  const activeRedo = (batch.segments || []).some((segment) => voiceBatchRedoActive(segment.redoStatus));
  return activeBatch || activeRedo;
}

function voiceBatchStopPoll() {
  if (!voiceBatchState.pollTimer) return;
  clearTimeout(voiceBatchState.pollTimer);
  voiceBatchState.pollTimer = null;
}

function voiceBatchAudioUrl(url) {
  if (!url) return "";
  return `${url}${url.includes("?") ? "&" : "?"}_=${encodeURIComponent(voiceBatchState.cacheTick)}`;
}

function voiceBatchSegmentStatus(batch, segment, completedCount) {
  if (voiceBatchRedoActive(segment.redoStatus)) return "重生中(3 個候選取最佳)";
  if (segment.audioUrl && segment.passed === false) return "低於門檻";
  if (segment.audioUrl || segment.status === "完成") return "完成";
  if (["failed", "cancelled"].includes(String(batch.status || ""))) return "未完成";
  if (["running", "pending", "queued"].includes(String(batch.status || "")) && Number(segment.index) === completedCount + 1) return "生成中";
  if (voiceBatchTerminalStatus(batch.status) && !segment.audioUrl) return "未完成";
  return "等待中";
}

async function loadVoiceBatchHistory({ silent = false } = {}) {
  const session = captureSessionContext();
  try {
    const data = await api("/api/voiceover/batches");
    assertSessionContext(session);
    voiceBatchState.history = data.batches || [];
    voiceBatchState.historyLoaded = true;
    if (!silent) voiceBatchSet({});
  } catch (error) {
    if (isStaleSessionError(error)) return;
    voiceBatchState.historyLoaded = true;
    voiceBatchState.error = `歷史批次讀取失敗：${error.message}`;
    if (!silent) voiceBatchRenderAgain();
  }
}

async function loadVoiceBatch(batchId) {
  const session = captureSessionContext();
  if (!batchId) return;
  const seq = voiceBatchState.loadSeq + 1;
  voiceBatchState.loadSeq = seq;
  voiceBatchStopPoll();
  try {
    const data = await api(`/api/voiceover/batches/${encodeURIComponent(batchId)}`);
    assertSessionContext(session);
    if (voiceBatchState.loadSeq !== seq) return;
    voiceBatchState.batch = data;
    voiceBatchState.step = "result";
    voiceBatchState.error = "";
    voiceBatchState.message = "";
    voiceBatchState.cacheTick = Date.now();
    voiceBatchRenderAgain();
    voiceBatchSchedulePoll();
  } catch (error) {
    if (isStaleSessionError(error)) return;
    if (voiceBatchState.loadSeq !== seq) return;
    voiceBatchSet({ error: `批次讀取失敗：${error.message}` });
  }
}

function voiceBatchSchedulePoll() {
  voiceBatchStopPoll();
  if (document.hidden || !voiceBatchShouldPoll(voiceBatchState.batch)) return;
  const session = captureSessionContext();
  voiceBatchState.pollTimer = setTimeout(async () => {
    if (!sessionContextIsCurrent(session)) return;
    if (document.hidden) {
      voiceBatchStopPoll();
      return;
    }
    if (!(currentView === "studio" && currentFeatureId === "voice-batch")) {
      voiceBatchStopPoll();
      return;
    }
    const batchId = voiceBatchState.batch && voiceBatchState.batch.batch_id;
    if (!batchId) return;
    const seq = voiceBatchState.loadSeq;
    try {
      const oldBatch = voiceBatchState.batch || {};
      const data = await api(`/api/voiceover/batches/${encodeURIComponent(batchId)}`);
      assertSessionContext(session);
      if (voiceBatchState.loadSeq !== seq) return;
      const oldSegments = oldBatch.segments || [];
      const newSegments = data.segments || [];
      const oldCompleted = oldSegments.filter((segment) => segment.audioUrl).length;
      const newCompleted = newSegments.filter((segment) => segment.audioUrl).length;
      const newByIndex = new Map(newSegments.map((segment) => [String(segment.index), segment]));
      const redoCompleted = oldSegments.some((segment) => {
        if (!voiceBatchRedoActive(segment.redoStatus)) return false;
        const next = newByIndex.get(String(segment.index));
        return !next || !voiceBatchRedoActive(next.redoStatus);
      });
      const terminalTransition = !voiceBatchTerminalStatus(oldBatch.status) && voiceBatchTerminalStatus(data.status);
      const audioChanged = newCompleted !== oldCompleted || redoCompleted || terminalTransition;
      voiceBatchState.batch = data;
      voiceBatchState.error = "";
      if (audioChanged) voiceBatchState.cacheTick = Date.now();
      if (!mediaIsPlaying() || audioChanged) voiceBatchRenderAgain();
      voiceBatchSchedulePoll();
    } catch (error) {
      if (isStaleSessionError(error)) return;
      if (voiceBatchState.loadSeq !== seq) return;
      voiceBatchState.error = `批次更新失敗：${error.message}`;
      voiceBatchRenderAgain();
    }
  }, 4000);
}

function renderVoiceBatchHistory() {
  const rows = (voiceBatchState.history || []).map((item) => h("button", {
    class: "mode-card",
    type: "button",
    onclick: () => loadVoiceBatch(item.batch_id),
  }, [
    h("span", { class: "mode-card-title", text: formatTimestamp(Number(item.created || 0)) }),
    h("span", { class: "mode-card-sub", text: `${item.count || 0} 段・${voiceBatchStatusText(item.status)}` }),
  ]));
  return h("details", { class: "advanced" }, [
    h("summary", { text: "歷史批次" }),
    h("div", { class: "card-group" }, rows.length ? rows : [
      h("div", { class: "empty", text: voiceBatchState.historyLoaded ? "目前沒有歷史批次。" : "讀取中…" }),
    ]),
  ]);
}

function renderVoiceBatchInput() {
  const pasteActive = voiceBatchState.source === "paste";
  const pptxActive = voiceBatchState.source === "pptx";
  const pasteArea = h("textarea", {
    rows: "8",
    placeholder: "貼上長文；用空行分段。",
    oninput: (event) => { voiceBatchState.rawText = event.target.value; },
  });
  pasteArea.value = voiceBatchState.rawText || "";
  const fileInput = h("input", {
    type: "file",
    accept: ".pptx,application/vnd.openxmlformats-officedocument.presentationml.presentation",
    onchange: async (event) => {
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      voiceBatchSet({ busy: true, error: "", message: "正在抽取 PPTX 備忘稿…" });
      try {
        const payload = await fileToData(file);
        const data = await api("/api/voiceover/extract-pptx", {
          method: "POST",
          body: JSON.stringify({ file: payload }),
        });
        const segments = (data.segments || []).map((segment, index) => ({
          index: index + 1,
          text: String(segment.text || ""),
          slide: segment.slide,
        })).filter((segment) => segment.text.trim());
        const skipped = (data.skipped || []).map((slide) => `第 ${slide} 頁無備忘稿,已略過`);
        voiceBatchSet({ step: "confirm", source: "pptx", segments, skipped, busy: false, error: "", message: "" });
      } catch (error) {
        voiceBatchSet({ busy: false, error: error.message, message: "" });
      }
    },
  });
  const chooseSource = (source) => voiceBatchSet({ source, error: "", message: "" });
  return h("div", { class: "gen-form" }, [
    h("div", { class: "card-group" }, [
      h("button", {
        type: "button",
        class: `mode-card${pasteActive ? " active" : ""}`,
        onclick: () => chooseSource("paste"),
      }, [
        h("span", { class: "mode-card-title", text: "貼上文字" }),
        h("span", { class: "mode-card-sub", text: "長文貼進來,空行分段;每段一個音檔" }),
      ]),
      h("button", {
        type: "button",
        class: `mode-card${pptxActive ? " active" : ""}`,
        onclick: () => chooseSource("pptx"),
      }, [
        h("span", { class: "mode-card-title", text: "上傳 PPTX" }),
        h("span", { class: "mode-card-sub", text: "抽出每頁備忘稿(notes),一頁一段" }),
      ]),
    ]),
    pasteActive ? h("label", { class: "field" }, [
      h("span", { class: "field-label", text: "貼上文字" }),
      pasteArea,
      h("button", {
        class: "primary",
        type: "button",
        text: "檢查段落",
        onclick: () => {
          const segments = voiceBatchSplitText(voiceBatchState.rawText);
          voiceBatchSet({ step: "confirm", segments, skipped: [], error: "", message: "" });
        },
      }),
    ]) : null,
    pptxActive ? h("label", { class: "field" }, [
      h("span", { class: "field-label", text: "PPTX 檔案" }),
      fileInput,
    ]) : null,
  ]);
}

function renderVoiceBatchConfirm() {
  const validation = voiceBatchValidation();
  const segments = voiceBatchState.segments || [];
  const header = h("h2", { class: "panel-h2" });
  const textareas = [];
  let startBtn = null;
  const refreshControls = () => {
    const current = voiceBatchValidation();
    header.textContent = `共 ${voiceBatchState.segments.length} 段 / 預估總長 ~${voiceBatchEstimateMinutes(voiceBatchState.segments)} 分鐘`;
    if (startBtn) {
      startBtn.disabled = !current.ok || voiceBatchState.busy;
      startBtn.title = current.reason || "";
      startBtn.textContent = current.ok ? (voiceBatchState.busy ? "送出中…" : "開始生成") : current.reason;
    }
    textareas.forEach((ta, index) => {
      ta.style.borderColor = current.tooLong === index + 1 ? "#d93025" : "";
    });
  };
  const updateSegment = (index, text) => {
    voiceBatchState.segments = voiceBatchState.segments.map((segment, i) => i === index ? { ...segment, text } : segment);
    refreshControls();
  };
  const removeSegment = (index) => {
    const next = voiceBatchState.segments.filter((_, i) => i !== index).map((segment, i) => ({ ...segment, index: i + 1 }));
    voiceBatchSet({ segments: next });
  };
  const rows = segments.map((segment, index) => {
    const text = String(segment.text || "");
    const tooLong = validation.tooLong === index + 1;
    const ta = h("textarea", {
      rows: "4",
      style: tooLong ? "border-color:#d93025;" : "",
      oninput: (event) => {
        count.textContent = `${event.target.value.length} 字`;
        updateSegment(index, event.target.value);
      },
    });
    const count = h("span", { class: "job-title", text: `${text.length} 字` });
    ta.value = text;
    textareas.push(ta);
    return h("div", { class: "job-row" }, [
      h("div", { class: "job-main" }, [
        h("div", { class: "job-title-row" }, [
          h("span", { class: "job-state", text: `第 ${index + 1} 段` }),
          count,
        ]),
        ta,
      ]),
      h("button", { class: "job-cancel danger", type: "button", text: "刪除", onclick: () => removeSegment(index) }),
    ]);
  });
  startBtn = h("button", {
    class: "primary",
    type: "button",
    disabled: !validation.ok || voiceBatchState.busy,
    title: validation.reason || "",
    text: validation.ok ? (voiceBatchState.busy ? "送出中…" : "開始生成") : validation.reason,
    onclick: async () => {
      const valid = voiceBatchValidation();
      if (!valid.ok) {
        refreshControls();
        return;
      }
      voiceBatchSet({ busy: true, error: "", message: "" });
      try {
        const payload = voiceBatchState.segments.map((segment, index) => ({ index: index + 1, text: String(segment.text || "").trim() })).filter((segment) => segment.text);
        const data = await api("/api/voiceover/batches", {
          method: "POST",
          body: JSON.stringify({ segments: payload }),
        });
        voiceBatchState.busy = false;
        await loadVoiceBatch(data.batch_id);
        loadVoiceBatchHistory({ silent: true });
      } catch (error) {
        voiceBatchSet({ busy: false, error: `送出失敗：${error.message}` });
      }
    },
  });
  const body = h("div", { class: "gen-form" }, [
    h("div", { class: "panel-head-row" }, [
      header,
      h("button", { class: "ghost small", type: "button", text: "回到輸入", onclick: () => voiceBatchSet({ step: "input", error: "", message: "" }) }),
    ]),
    ...voiceBatchState.skipped.map((line) => h("div", { class: "panel-note", text: line })),
    ...rows,
    startBtn,
  ]);
  refreshControls();
  return body;
}

function renderVoiceBatchResult() {
  const batch = voiceBatchState.batch || {};
  const segments = batch.segments || [];
  const completed = segments.filter((segment) => segment.audioUrl).length;
  const count = batch.count || segments.length || 0;
  const pct = count ? Math.round((completed / count) * 100) : 0;
  const batchDone = String(batch.status || "") === "done";
  const batchTerminal = ["done", "failed", "cancelled"].includes(String(batch.status || ""));
  const redoAllowedBatch = ["done", "cancelled"].includes(String(batch.status || ""));
  const activeBatch = !batchTerminal;
  const rows = segments.map((segment) => {
    const activeRedo = voiceBatchRedoActive(segment.redoStatus);
    const rowStatus = voiceBatchSegmentStatus(batch, segment, completed);
    const text = String(segment.text || "");
    const canRedo = redoAllowedBatch && !activeRedo;
    const canRestore = batchTerminal && !activeRedo;
    const redoReason = !batchTerminal ? "批次完成後才能重生" : (activeRedo ? "重生進行中" : "");
    const audioItem = segment.audioUrl ? {
      kind: "audio",
      filename: segment.file || `seg_${String(segment.index).padStart(2, "0")}.wav`,
      url: voiceBatchAudioUrl(segment.audioUrl),
      downloadUrl: voiceBatchAudioUrl(segment.audioUrl),
      audioExportUrls: segment.audioExportUrls || {},
    } : null;
    return h("div", { class: "job-row" }, [
      h("div", { class: "job-main" }, [
        h("div", { class: "job-title-row" }, [
          h("span", { class: `job-state ${segment.passed === false ? "error" : ""}`, text: rowStatus }),
          h("span", { class: "job-title", text: `第 ${segment.index} 段` }),
          segment.similarity !== null && segment.similarity !== undefined
            ? h("span", { class: `pill ${segment.passed === false ? "missing" : "ok"}`, text: segment.passed === false ? "低於門檻" : `sim ${Number(segment.similarity).toFixed(2)}` })
            : null,
        ]),
        h("div", { class: "job-meta", text: text.length > 140 ? `${text.slice(0, 140)}…` : text }),
        segment.audioUrl ? h("audio", { controls: "", src: voiceBatchAudioUrl(segment.audioUrl) }) : null,
        audioItem ? renderAudioExportActions(audioItem, { compact: true }) : null,
        activeRedo ? h("div", { class: "job-node", text: "重生中(3 個候選取最佳)" }) : null,
      ]),
      h("div", { class: "job-progress" }, [
        h("button", {
          class: "ghost small",
          type: "button",
          disabled: !canRedo,
          title: redoReason,
          text: canRedo ? "重生" : redoReason,
          onclick: async () => {
            if (!canRedo) return;
            voiceBatchState.loadSeq += 1;
            voiceBatchStopPoll();
            try {
              await api(`/api/voiceover/batches/${encodeURIComponent(batch.batch_id)}/segments/${encodeURIComponent(segment.index)}/redo`, { method: "POST", body: "{}" });
              voiceBatchState.cacheTick = Date.now();
              voiceBatchState.message = "已送出重生。";
              voiceBatchRenderAgain();
              loadVoiceBatch(batch.batch_id);
            } catch (error) {
              voiceBatchSet({ error: error.message });
            }
          },
        }),
        segment.hasOld ? h("button", {
          class: "ghost small",
          type: "button",
          disabled: !canRestore,
          title: canRestore ? "" : "批次或重生進行中",
          text: "還原上一版",
          onclick: async () => {
            if (!canRestore) return;
            voiceBatchState.loadSeq += 1;
            voiceBatchStopPoll();
            try {
              await api(`/api/voiceover/batches/${encodeURIComponent(batch.batch_id)}/segments/${encodeURIComponent(segment.index)}/restore`, { method: "POST", body: "{}" });
              voiceBatchState.cacheTick = Date.now();
              voiceBatchState.message = "已還原上一版。";
              voiceBatchRenderAgain();
              loadVoiceBatch(batch.batch_id);
            } catch (error) {
              voiceBatchSet({ error: error.message });
            }
          },
        }) : null,
      ]),
    ]);
  });
  return h("div", { class: "gen-form" }, [
    ["failed", "cancelled"].includes(String(batch.status || "")) ? h("div", { class: "alert error", text: batch.status === "failed" ? "批次失敗" : "已取消" }) : null,
    h("div", { class: "panel-head-row" }, [
      h("h2", { class: "panel-h2", text: `${voiceBatchStatusText(batch.status)}・${completed}/${count}` }),
      h("button", {
        class: "ghost small",
        type: "button",
        text: "回到輸入",
        onclick: () => {
          voiceBatchStopPoll();
          voiceBatchSet({ step: "input", batch: null, error: "", message: "" });
        },
      }),
    ]),
    h("div", { class: "job-progress" }, [
      h("div", { class: "job-progress-label", text: `${completed}/${count}` }),
      h("div", { class: "job-bar" }, [h("span", { style: `width:${pct}%` })]),
    ]),
    batchDone
      ? h("a", { class: "primary", href: `/api/voiceover/batches/${encodeURIComponent(batch.batch_id)}/zip`, text: "全部下載 WAV（ZIP）" })
      : h("button", { class: "primary", type: "button", disabled: "", title: "批次完成後才能下載", text: "批次完成後才能下載" }),
    activeBatch ? h("div", { class: "panel-note", text: "批次進行中，約每 4 秒更新。" }) : null,
    ...rows,
  ]);
}

function renderVoiceBatchPage() {
  if (!voiceBatchState.historyLoaded && !voiceBatchState.busy) loadVoiceBatchHistory({ silent: true }).then(voiceBatchRenderAgain);
  return h("div", { class: "voice-batch-page" }, [
    renderVoiceBatchHistory(),
    voiceBatchState.error ? h("div", { class: "alert error", text: voiceBatchState.error }) : null,
    voiceBatchState.message ? h("div", { class: "panel-note", text: voiceBatchState.message }) : null,
    renderAudioFormatPreference(),
    voiceBatchState.step === "confirm"
      ? renderVoiceBatchConfirm()
      : (voiceBatchState.step === "result" ? renderVoiceBatchResult() : renderVoiceBatchInput()),
  ]);
}

// ---- library view + recent strip ------------------------------------------
function renderRecentPanel() {
  const feature = currentFeature();
  return h("section", { class: "panel recent-panel" }, [
    h("div", { class: "panel-head-row" }, [
      h("h2", { class: "panel-h2", text: feature ? `最近輸出 · ${feature.title}` : "最近輸出" }),
      h("button", { class: "ghost small", type: "button", text: "查看全部", onclick: () => setView("library") }),
    ]),
    h("div", { class: "gallery recent", id: "recentStrip" }),
  ]);
}

const GLOBAL_LIBRARY_KIND_TABS = [
  ["all", "全部"],
  ["image", "圖片"],
  ["video", "影片"],
  ["audio", "聲音"],
  ["text", "文字"],
];

function libraryRouteSource(route = currentRoute) {
  return route && route.view === "library" && route.source === "provider" ? "provider" : "castle";
}

function currentLibrarySource() {
  return libraryRouteSource(currentRoute);
}

function currentLibraryCharacterId() {
  return currentRoute.view === "library" ? (currentRoute.characterId || "") : "";
}

function currentLibraryKind() {
  return currentRoute.view === "library" && ["image", "video", "audio", "text"].includes(currentRoute.kind)
    ? currentRoute.kind
    : "all";
}

function currentLibraryWorkflowScope(route = currentRoute) {
  if (!route || route.view !== "library" || route.source === "provider") return null;
  const featureId = String(route.featureId || "");
  const variantId = String(route.variantId || "");
  if (
    !/^[a-z0-9][a-z0-9._:-]{0,127}$/.test(featureId)
    || !/^[a-z0-9][a-z0-9._:-]{0,127}$/.test(variantId)
  ) return null;
  return { featureId, variantId };
}

function historyItemMatchesLibraryWorkflow(item, scope = currentLibraryWorkflowScope()) {
  return !scope || (
    historyItemFeatureId(item) === scope.featureId
    && historyItemVariantId(item) === scope.variantId
  );
}

function libraryContentCount(characterId = "", kind = currentLibraryKind()) {
  const workflowScope = currentLibraryWorkflowScope();
  const media = historyItems.filter((item) => {
    if (!showHiddenGlobalLibrary && item.hidden) return false;
    const bucket = libraryItemCharacterBucket(item);
    if (characterId && bucket !== characterId) return false;
    if (!historyItemMatchesLibraryWorkflow(item)) return false;
    return kind === "all" || kind === item.kind;
  }).length;
  if (kind !== "all" && kind !== "text") return media;
  const posts = workflowScope || ["_unassigned", "_removed"].includes(characterId)
    ? 0
    : libraryPosts.filter((post) => !characterId || post.characterId === characterId).length;
  return (kind === "text" ? 0 : media) + posts;
}

function libraryCharacterName(characterId = currentLibraryCharacterId()) {
  if (!characterId) return "全部角色";
  if (characterId === "_unassigned") return "未標示角色";
  if (characterId === "_removed") return "已移除角色";
  const character = libraryCharacterById(characterId);
  return character ? character.name : "未知角色";
}

function navigateLibraryFilter(characterId, kind = currentLibraryKind()) {
  libraryVisibleLimit = 24;
  navigate({
    view: "library",
    characterId: characterId || "",
    kind,
    ...(currentLibraryWorkflowScope() || {}),
  });
}

function navigateLibraryKind(kind) {
  libraryVisibleLimit = 24;
  navigate({
    view: "library",
    characterId: currentLibraryCharacterId(),
    kind,
    ...(currentLibraryWorkflowScope() || {}),
  });
}

function navigateLibrarySource(source) {
  libraryVisibleLimit = 24;
  const kind = currentLibraryKind();
  if (source === "provider") {
    navigate({
      view: "library",
      characterId: "",
      kind: ["image", "video", "audio", "text"].includes(kind) ? kind : "image",
      source: "provider",
    });
    return;
  }
  navigate({ view: "library", characterId: "", kind });
}

function renderLibrarySourceTabs() {
  const source = currentLibrarySource();
  return renderTabBar({
    label: "媒體來源",
    className: "library-source-tabs",
    tabs: [
      {
        id: "library-source-castle",
        panelId: "librarySourcePanel",
        label: "Castle 作品",
        active: source === "castle",
        onclick: () => navigateLibrarySource("castle"),
      },
      {
        id: "library-source-provider",
        panelId: "librarySourcePanel",
        label: "外部目錄",
        active: source === "provider",
        onclick: () => navigateLibrarySource("provider"),
      },
    ],
  });
}

function openLibraryCharacterFilter(opener) {
  const selected = currentLibraryCharacterId();
  const availableCharacters = libraryCharacters();
  const rows = [
    { id: "", label: "全部角色", count: libraryContentCount("") },
    ...availableCharacters.map((character) => ({ id: character.id, label: character.name, character, count: libraryContentCount(character.id) })),
    { id: "_unassigned", label: "未標示角色", count: libraryContentCount("_unassigned") },
  ];
  if (libraryContentCount("_removed") > 0) rows.push({ id: "_removed", label: "已移除角色", count: libraryContentCount("_removed") });
  const selectedCharacter = libraryCharacterById(selected);
  openSheet({
    id: "library-character-filter",
    title: "篩選角色",
    opener,
    className: "library-filter-sheet",
    content: h("div", { class: "library-filter-list" }, [
      ...rows.map((row) => h("button", {
        class: `library-filter-row${row.id === selected ? " active" : ""}`,
        type: "button",
        "aria-current": row.id === selected ? "true" : null,
        onclick: () => sheetAction(() => navigateLibraryFilter(row.id)),
      }, [
        row.character ? profilePicNode(row.character, "library-filter-avatar") : h("span", { class: "library-filter-avatar fallback", text: row.id ? "?" : "全" }),
        h("span", { class: "library-filter-copy" }, [
          h("strong", { text: row.label }),
          h("span", { text: `${row.count} 件內容` }),
        ]),
        row.id === selected ? h("span", { class: "library-filter-current", text: "目前" }) : null,
      ])),
      selectedCharacter ? h("button", {
        class: "ghost library-filter-deep-link",
        type: "button",
        text: `開啟 ${selectedCharacter.name} 的角色素材`,
        onclick: () => sheetAction(() => {
          if (isProviderLibraryCharacter(selectedCharacter)) {
            openProviderCharacterMaterials(selectedCharacter);
            return;
          }
          navigate({
            view: "character-library",
            characterId: selectedCharacter.id,
            media: ["image", "video", "audio", "text"].includes(currentLibraryKind()) ? currentLibraryKind() : "image",
          });
        }),
      }) : null,
    ]),
  });
}

function renderGlobalLibraryFilters() {
  const selected = currentLibraryCharacterId();
  const availableCharacters = libraryCharacters();
  const selectedCharacter = libraryCharacterById(selected);
  const workflowScope = currentLibraryWorkflowScope();
  const select = h("select", {
    class: "library-character-select",
    "aria-label": "篩選角色",
    onchange: (event) => navigateLibraryFilter(event.target.value),
  }, [
    h("option", { value: "", text: "全部角色", selected: !selected }),
    ...availableCharacters.map((character) => h("option", { value: character.id, text: character.name, selected: selected === character.id })),
    h("option", { value: "_unassigned", text: "未標示角色", selected: selected === "_unassigned" }),
    libraryContentCount("_removed") > 0 ? h("option", { value: "_removed", text: "已移除角色", selected: selected === "_removed" }) : null,
  ]);
  return h("div", { class: "library-filter-stack" }, [
    workflowScope ? h("div", { class: "library-workflow-scope", role: "status" }, [
      h("span", { text: "目前只顯示：這個角色寫真工作流" }),
      h("button", {
        class: "ghost small",
        type: "button",
        text: "顯示這個角色全部 Castle 作品",
        onclick: () => navigate({
          view: "library",
          characterId: currentLibraryCharacterId(),
          kind: currentLibraryKind(),
        }),
      }),
    ]) : null,
    h("div", { class: "library-filter-toolbar" }, [
      h("label", { class: "library-filter-select-wrap" }, [h("span", { text: "角色" }), select]),
    h("button", {
      id: "libraryCharacterFilterTrigger",
      class: "ghost library-filter-phone",
      type: "button",
      text: `篩選：${libraryCharacterName(selected)} ▾`,
      "aria-haspopup": "dialog",
      "aria-expanded": "false",
      onclick: (event) => openLibraryCharacterFilter(event.currentTarget),
    }),
    selectedCharacter ? h("button", {
      class: "ghost library-character-deep-link",
      type: "button",
      text: `前往${selectedCharacter.name}素材 →`,
      onclick: () => {
        if (isProviderLibraryCharacter(selectedCharacter)) {
          openProviderCharacterMaterials(selectedCharacter);
          return;
        }
        navigate({
          view: "character-library",
          characterId: selectedCharacter.id,
          media: ["image", "video", "audio", "text"].includes(currentLibraryKind()) ? currentLibraryKind() : "image",
        });
      },
    }) : null,
    h("label", { class: "library-search" }, [
      h("span", { class: "sr-only", text: "搜尋全部內容" }),
      h("input", {
        type: "search",
        value: librarySearchQuery,
        placeholder: "搜尋檔名、角色或動作",
        "aria-label": "搜尋全部內容",
        oninput: (event) => {
          librarySearchQuery = event.target.value;
          libraryVisibleLimit = 24;
          if (librarySearchTimer) clearTimeout(librarySearchTimer);
          librarySearchTimer = setTimeout(() => {
            librarySearchTimer = null;
            paintHistory();
          }, 150);
        },
      }),
    ]),
    ]),
  ]);
}

function openLibraryOptions(opener) {
  openSheet({
    id: "library-options",
    title: "內容選項",
    opener,
    className: "library-options-sheet",
    content: h("div", { class: "shell-menu-list" }, [
      currentLibraryKind() !== "text" ? h("button", {
        class: "shell-menu-row",
        type: "button",
        text: showHiddenGlobalLibrary ? "隱藏已隱藏內容" : "顯示已隱藏內容",
        onclick: () => sheetAction(() => {
          showHiddenGlobalLibrary = !showHiddenGlobalLibrary;
          renderLibraryView();
        }),
      }) : null,
      h("button", {
        class: "shell-menu-row",
        type: "button",
        text: "更新全部內容",
        onclick: () => sheetAction(refreshLibraryContent),
      }),
    ]),
  });
}

function renderProviderMediaLibraryView() {
  const ws = beginWorkspaceRender("workspace-route-root unified-library-route");
  const session = captureSessionContext();
  const host = h("div", {
    id: "librarySourcePanel",
    class: "provider-media-library-host",
    role: "tabpanel",
    "aria-labelledby": "library-source-provider",
  });
  ws.appendChild(h("section", { class: "panel library-view provider-library-view" }, [
    h("div", { class: "panel-head-row" }, [
      h("div", {}, [
        h("h1", { class: "panel-h1", text: "媒體庫" }),
        h("p", { class: "panel-sub", text: "在 Castle 作品與外部唯讀目錄之間切換；兩邊同名或同識別碼的內容不會混在一起。" }),
      ]),
    ]),
    renderLibrarySourceTabs(),
    host,
  ]));
  if (!window.CastleProviderFabric || typeof window.CastleProviderFabric.mount !== "function") {
    host.appendChild(h("div", { class: "empty", text: "外部目錄載入失敗，請重新整理頁面。" }));
    return;
  }
  const component = window.CastleProviderFabric.mount(host, {
    mediaOnly: true,
    lockedProviderScope: true,
    initialKind: currentLibraryKind(),
    characterId: currentRoute.characterId || "",
    characterScope: "provider",
    isAdmin: false,
    isOperator: false,
  });
  component.addEventListener("castle-provider-media-kind-change", (event) => {
    const kind = event && event.detail && event.detail.kind;
    if (
      !sessionContextIsCurrent(session)
      || !component.isConnected
      || currentRoute.view !== "library"
      || currentLibrarySource() !== "provider"
      || !["image", "video", "audio", "text"].includes(kind)
    ) return;
    navigate({
      view: "library",
      characterId: currentRoute.characterId || "",
      kind,
      source: "provider",
    });
  });
  component.addEventListener("castle-provider-asset-handoff", (event) => {
    const intent = event?.detail;
    if (!sessionContextIsCurrent(session) || !component.isConnected
      || currentRoute.view !== "library" || currentLibrarySource() !== "provider"
      || !intent || typeof intent !== "object" || Array.isArray(intent)) return;
    pendingProviderIntent = intent;
    pendingProviderAutoStart = false;
    navigate({ view: "resources" });
  });
}

function renderLibraryView() {
  if (currentLibrarySource() === "provider") {
    renderProviderMediaLibraryView();
    return;
  }
  if (currentLibraryKind() !== "text" && !currentLibraryWorkflowScope()) {
    const ws = beginWorkspaceRender("workspace-route-root catalogue-route");
    ws.appendChild(h("section", { class: "panel catalogue-heading" }, [
      renderSectionHeader("素材庫", "依角色、場景與用途整理作品和採集素材。", [
        h("button", { type: "button", class: "ghost small", text: "文字作品", onclick: () => navigate({ view: "library", kind: "text", characterId: currentRoute.characterId || "" }) }),
        h("button", { type: "button", class: "ghost small", text: showHiddenGlobalLibrary ? "隱藏已隱藏素材" : "顯示隱藏", onclick: () => { showHiddenGlobalLibrary = !showHiddenGlobalLibrary; catalogueViewState.forEach(state => { state.loadedAt = 0; }); renderLibraryView(); } }),
        isAdmin() ? h("button", { type: "button", class: "primary small", text: "採集素材", onclick: openMaterialCollect }) : null,
      ], { titleClass: "panel-h1", level: "h1" }),
      renderLibrarySourceTabs(),
    ]));
    ws.appendChild(renderCatalogueExplorer({ characterId: currentRoute.characterId || "", kind: currentLibraryKind() === "all" ? "" : currentLibraryKind(), global: true }));
    return;
  }
  const ws = beginWorkspaceRender();
  const toggleHiddenText = showHiddenGlobalLibrary ? "隱藏已隱藏內容" : "顯示隱藏內容";
  const kind = currentLibraryKind();
  const workflowScope = currentLibraryWorkflowScope();
  ws.appendChild(h("section", { class: "panel library-view" }, [
    h("div", { class: "panel-head-row" }, [
      h("div", {}, [
        h("h1", { class: "panel-h1", text: workflowScope ? "此工作流生成作品" : "全部內容" }),
        h("p", {
          class: "panel-sub",
          text: workflowScope
            ? "只顯示這個角色由目前角色寫真工作流產生的作品。"
            : "集中查看、搜尋與管理圖片、影片、聲音和文字；點內容即可查看與管理。",
        }),
      ]),
      h("div", { class: "library-actions library-actions-desktop" }, [
        kind !== "text" ? h("button", {
          class: `ghost small${showHiddenGlobalLibrary ? " active" : ""}`,
          type: "button",
          text: toggleHiddenText,
          onclick: () => {
            showHiddenGlobalLibrary = !showHiddenGlobalLibrary;
            renderLibraryView();
          },
        }) : null,
        h("button", { class: "ghost small", type: "button", text: "更新", onclick: refreshLibraryContent }),
      ]),
      h("button", {
        class: "ghost small library-options-phone",
        type: "button",
        text: "內容選項",
        "aria-haspopup": "dialog",
        "aria-expanded": "false",
        onclick: (event) => openLibraryOptions(event.currentTarget),
      }),
    ]),
    renderLibrarySourceTabs(),
    h("div", {
      id: "librarySourcePanel",
      class: "castle-library-source-panel",
      role: "tabpanel",
      "aria-labelledby": "library-source-castle",
    }, [
      renderGlobalLibraryFilters(),
    renderTabBar({
      label: "內容類型",
      className: "global-library-kind-tabs",
      tabs: GLOBAL_LIBRARY_KIND_TABS.map(([id, label]) => ({
        id: `global-library-kind-${id}`,
        panelId: "libraryResults",
        label,
        active: kind === id,
        onclick: () => navigateLibraryKind(id),
      })),
    }),
    h("div", { id: "libraryResultCount", class: "library-result-count", role: "status", "aria-live": "polite" }),
    h("div", { id: "libraryResults", role: "tabpanel", tabindex: "0", "aria-labelledby": `global-library-kind-${kind}` }, [
      h("section", { id: "libraryMediaSection", class: "library-result-section" }, [
        h("h2", { id: "libraryMediaHeading", class: "panel-h2 hidden", text: "媒體" }),
        h("div", { class: "gallery library", id: "libraryGallery" }),
      ]),
      h("section", { id: "libraryTextSection", class: "library-result-section hidden" }, [
        h("h2", { id: "libraryTextHeading", class: "panel-h2 hidden", text: "文字" }),
        h("div", { id: "libraryPostList", class: "post-library-list" }),
      ]),
    ]),
    h("button", {
      id: "libraryLoadMore",
      class: "ghost library-load-more hidden",
      type: "button",
      text: "顯示更多",
      onclick: () => { libraryVisibleLimit += 24; paintHistory(); },
    }),
    ]),
  ]));
  paintHistory();
  loadLibraryPosts().then(() => {
    if (currentView === "library") paintHistory();
  }).catch((error) => {
    if (!isStaleSessionError(error) && currentView === "library") paintHistory();
  });
}

async function refreshLibraryContent() {
  await Promise.all([refreshHistory(), loadLibraryPosts(true).catch(() => [])]);
  if (currentView === "library") paintHistory();
}

function historyKey(item) {
  return `${item.type || "output"}\n${item.subfolder || ""}\n${item.filename || ""}`;
}

function formatTimestamp(seconds) {
  if (!seconds) return "未知";
  return new Date(seconds * 1000).toLocaleString();
}

function mediaIsPlaying() {
  return Array.from(document.querySelectorAll("audio, video")).some(
    (media) => !media.paused && !media.ended && media.readyState > 1,
  );
}

function historyFingerprint(items) {
  return JSON.stringify(items.map((item) => [
    item.promptId, item.type, item.subfolder || "", item.filename,
    item.modified || 0, item.size || 0, !!item.hidden, item.kind || "",
    item.renderKind || "", item.featureId || "", item.feature || "", item.workflow || "",
    (item.character && item.character.id) || "", (item.action && item.action.id) || "",
    item.provenanceTrusted === true, item.characterAttribution || "", item.variantId || "",
  ]));
}

function manageBar(item) {
  const actions = [
    h("button", {
      class: "card-act",
      type: "button",
      title: item.hidden ? "顯示" : "隱藏",
      text: item.hidden ? "顯示" : "隱藏",
      onclick: (event) => { event.stopPropagation(); toggleHidden(item); },
    }),
    h("button", {
      class: "card-act",
      type: "button",
      title: "重新命名",
      text: "改名",
      onclick: (event) => { event.stopPropagation(); renameItem(item); },
    }),
  ];
  if (item.kind === "audio") {
    actions.push(h("button", {
      class: "card-act",
      type: "button",
      title: "加入聲音庫",
      text: "加入聲音庫",
      onclick: (event) => { event.stopPropagation(); importVoiceItem(item); },
    }));
  }
  actions.push(
    h("button", {
      class: "card-act danger",
      type: "button",
      title: "刪除",
      text: "刪除",
      onclick: (event) => { event.stopPropagation(); deleteItem(item); },
    }),
  );
  return h("div", { class: "card-actions" }, actions);
}

function captionWithDownload(item, opts = {}) {
  const caption = typeof opts.captionFor === "function" ? opts.captionFor(item) : (opts.caption || item.filename);
  if (opts.previewOnly) return h("figcaption", {}, [h("span", { class: "fname", text: caption || item.filename })]);
  const preferredAudio = item.kind === "audio" ? audioExportDownload(item, preferredAudioExportFormat) : null;
  const downloadHref = preferredAudio ? preferredAudio.href : (item.downloadUrl || item.url);
  const downloadName = preferredAudio ? preferredAudio.filename : item.filename;
  return h("figcaption", {}, [
    h("span", { class: "fname", text: caption || item.filename }),
    h("a", {
      class: "dl",
      href: downloadHref,
      download: downloadName,
      title: preferredAudio ? `下載 ${preferredAudio.format.toUpperCase()}` : "下載原始檔",
      "aria-label": `下載${downloadName}`,
      text: "⬇",
      dataset: preferredAudio ? { audioItemKey: historyKey(item) } : null,
      onclick: preferredAudio
        ? (event) => downloadAudioExport(event, item, preferredAudioExportFormat, event.currentTarget)
        : null,
    }),
  ]);
}

function thumbNode(item, opts = {}) {
  const cls = (base) =>
    `${base}${item.hidden ? " is-hidden" : ""}${opts.manage ? " manage" : ""}${historyKey(item) === selectedHistoryKey ? " selected" : ""}`;
  const onOpen = typeof opts.onOpen === "function" ? opts.onOpen : openHistoryItem;
  const open = (event) => {
    if (event.target && event.target.closest && event.target.closest("button, a")) return;
    if (!opts.previewOnly && event.target && event.target.closest && event.target.closest("audio, video")) return;
    onOpen(item, figure);
  };
  let figure;
  if (item.kind === "audio") {
    figure = h("figure", { class: cls("thumb audio"), tabindex: "0", role: "button", "aria-label": opts.accessibleLabel ? opts.accessibleLabel(item) : `查看${item.filename}`, onclick: open }, [
      opts.previewOnly
        ? h("span", { class: "thumb-audio-preview", text: "♫", "aria-hidden": "true" })
        : h("audio", { controls: "", preload: "none", src: item.url }),
      captionWithDownload(item, opts),
    ]);
  } else if (itemRenderKind(item) === "video") {
    figure = h("figure", { class: cls("thumb video"), tabindex: "0", role: "button", "aria-label": opts.accessibleLabel ? opts.accessibleLabel(item) : `查看${item.filename}`, onclick: open }, [
      h("video", { controls: opts.previewOnly ? null : "", muted: opts.previewOnly ? "" : null, tabindex: opts.previewOnly ? "-1" : null, "aria-hidden": opts.previewOnly ? "true" : null, preload: "metadata", playsinline: "", src: item.url }),
      captionWithDownload(item, opts),
    ]);
  } else {
    const img = h("img", { loading: "lazy", alt: "" });
    img.src = item.url; // server-validated /api/view URL
    figure = h("figure", { class: cls("thumb"), tabindex: "0", role: "button", "aria-label": opts.accessibleLabel ? opts.accessibleLabel(item) : `查看${item.filename}`, onclick: open }, [
      img,
      captionWithDownload(item, opts),
    ]);
  }
  figure.addEventListener("keydown", (event) => {
    if (event.target && event.target !== figure && event.target.closest && event.target.closest("button, a, audio, video")) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onOpen(item, figure);
    }
  });
  if (opts.manage) figure.appendChild(manageBar(item));
  return figure;
}

function fillGallery(node, items, emptyText, opts = {}) {
  clear(node);
  if (!items.length) {
    node.appendChild(h("div", { class: "empty", text: emptyText }));
    return;
  }
  for (const item of items) node.appendChild(thumbNode(item, opts));
}

function historyItemFeatureId(item) {
  if (!item || typeof item !== "object") return "";
  const provenance = item.provenance && typeof item.provenance === "object" ? item.provenance : {};
  const action = item.action && typeof item.action === "object" ? item.action : {};
  for (const value of [item.featureId, provenance.featureId, action.featureId]) {
    const featureId = String(value || "").trim();
    if (featureId) return featureId;
  }
  const workflow = String(item.workflow || "").trim();
  if (workflow.startsWith("castle-") && workflow.endsWith("-web")) {
    const encoded = workflow.slice("castle-".length, -"-web".length);
    if (featureById(encoded)) return encoded;
  }
  return "";
}

function historyItemMatchesFeature(item, feature) {
  if (!feature || !feature.id) return false;
  const featureId = historyItemFeatureId(item);
  if (featureId) return featureId === feature.id;
  // Legacy direct outputs may only retain the server-issued display title.
  // Exact title equality is the sole fallback; unknown outputs stay out of a
  // feature-scoped recent strip instead of being guessed from media type.
  return Boolean(item && item.feature && String(item.feature) === String(feature.title || ""));
}

function recentItemsForCurrentFeature() {
  const feature = currentFeature();
  if (!feature) return [];
  return historyItems.filter((item) => !item.hidden && historyItemMatchesFeature(item, feature));
}

function librarySnapshotMessage() {
  if (historySnapshotLoaded) return "";
  return historyLoadError ? "作品讀取失敗，請按「更新」重試。" : "正在讀取作品…";
}

function paintHistory() {
  const strip = $("recentStrip");
  if (strip) {
    const limit = window.matchMedia("(max-width: 767px)").matches ? 2 : 4;
    const feature = currentFeature();
    const shown = recentItemsForCurrentFeature().slice(0, limit);
    fillGallery(strip, shown, feature ? `還沒有「${feature.title}」的輸出。` : "還沒有輸出。", {
      previewOnly: true,
      captionFor: (item) => resultMetaLine(item, resultCharacter(item)),
      accessibleLabel: (item) => `查看${resultMetaLine(item, resultCharacter(item))}`,
    });
  }
  const grid = $("libraryGallery");
  if (grid) {
    const selectedCharacter = currentLibraryCharacterId();
    const selectedKind = currentLibraryKind();
    const query = librarySearchQuery.trim().toLocaleLowerCase("zh-Hant");
    const libraryItems = historyItems.filter((item) => {
      if (!showHiddenGlobalLibrary && item.hidden) return false;
      const characterBucket = libraryItemCharacterBucket(item);
      if (selectedCharacter && characterBucket !== selectedCharacter) return false;
      if (!historyItemMatchesLibraryWorkflow(item)) return false;
      if (selectedKind === "text") return false;
      if (selectedKind !== "all" && item.kind !== selectedKind) return false;
      if (!query) return true;
      const haystack = [item.filename, item.feature, item.workflow, actionLabelFromItem(item), item.character && item.character.name]
        .filter(Boolean).join(" ").toLocaleLowerCase("zh-Hant");
      return haystack.includes(query);
    });
    const libraryTextItems = !currentLibraryWorkflowScope() && (selectedKind === "all" || selectedKind === "text")
      ? libraryPosts.filter((post) => {
        if (selectedCharacter === "_unassigned") return false;
        if (selectedCharacter && post.characterId !== selectedCharacter) return false;
        if (!query) return true;
        const character = libraryCharacterById(post.characterId);
        const haystack = [post.text, post.topic, post.platform, post.engine, character && character.name]
          .filter(Boolean).join(" ").toLocaleLowerCase("zh-Hant");
        return haystack.includes(query);
      })
      : [];
    const visibleEntries = [
      ...libraryItems.map((item) => ({ kind: "media", item, ts: Number(item.modified || 0) })),
      ...libraryTextItems.map((item) => ({ kind: "text", item, ts: Number(item.ts || 0) })),
    ].sort((a, b) => b.ts - a.ts).slice(0, libraryVisibleLimit);
    const visibleMediaItems = visibleEntries.filter((entry) => entry.kind === "media").map((entry) => entry.item);
    const visibleTextItems = visibleEntries.filter((entry) => entry.kind === "text").map((entry) => entry.item);
    const totalItems = libraryItems.length + libraryTextItems.length;
    const snapshotMessage = selectedKind !== "text" && !historyItems.length ? librarySnapshotMessage() : "";
    const count = $("libraryResultCount");
    if (count) count.textContent = snapshotMessage || (totalItems > visibleEntries.length
      ? `顯示 ${visibleEntries.length} / ${totalItems} 件內容`
      : `顯示 ${totalItems} 件內容`);
    fillGallery(
      grid,
      visibleMediaItems,
      snapshotMessage || "沒有符合目前條件的內容。",
      {
        previewOnly: true,
        captionFor: (item) => resultMetaLine(item, resultCharacter(item)),
        accessibleLabel: (item) => `查看${resultMetaLine(item, resultCharacter(item))}`,
      },
    );
    const mediaSection = $("libraryMediaSection");
    const textSection = $("libraryTextSection");
    const mediaHeading = $("libraryMediaHeading");
    const textHeading = $("libraryTextHeading");
    const postList = $("libraryPostList");
    const showMedia = selectedKind !== "text" && (selectedKind !== "all" || visibleMediaItems.length > 0 || !visibleTextItems.length);
    const showText = selectedKind === "text" || (
      selectedKind === "all"
      && (visibleTextItems.length > 0 || libraryPostsError || !libraryPostsLoaded)
    );
    if (mediaSection) mediaSection.classList.toggle("hidden", !showMedia);
    if (textSection) textSection.classList.toggle("hidden", !showText);
    if (mediaHeading) mediaHeading.classList.toggle("hidden", selectedKind !== "all" || !showMedia);
    if (textHeading) textHeading.classList.toggle("hidden", selectedKind !== "all" || !showText);
    if (postList) {
      clear(postList);
      if (libraryPostsError) {
        postList.appendChild(h("div", { class: "empty compact", text: `文字內容讀取失敗：${libraryPostsError}` }));
      } else if (!libraryPostsLoaded) {
        postList.appendChild(h("div", { class: "empty compact", text: "讀取文字內容中…" }));
      } else if (!libraryTextItems.length) {
        postList.appendChild(h("div", { class: "empty compact", text: "沒有符合目前條件的文字內容。" }));
      } else {
        for (const post of visibleTextItems) {
          const character = libraryCharacterById(post.characterId);
          postList.appendChild(renderPostCard(character, post, { showCharacter: true }));
        }
      }
    }
    const more = $("libraryLoadMore");
    if (more) {
      more.classList.toggle("hidden", visibleEntries.length >= totalItems);
      more.textContent = `再顯示 ${Math.min(24, Math.max(0, totalItems - visibleEntries.length))} 件`;
    }
  }
}

function openHistoryItem(item) {
  openResultDetail(item, document.activeElement);
}

function renderDetailMedia(item) {
  if (itemRenderKind(item) === "audio") return h("audio", { controls: "", preload: "metadata", src: item.url });
  if (itemRenderKind(item) === "video") {
    return h("video", { controls: "", preload: "metadata", playsinline: "", src: item.url });
  }
  const img = h("img", { alt: "" });
  img.src = item.url;
  return img;
}

function detailRow(label, value) {
  return h("div", { class: "detail-row" }, [
    h("span", { class: "detail-label", text: label }),
    h("span", { class: "detail-value", text: value || "—" }),
  ]);
}

function valueText(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function renderDetailNodes(box, detail) {
  clear(box);
  const nodes = (detail && detail.nodes) || [];
  const useful = nodes.filter((node) => (node.inputs || []).length);
  if (!useful.length) {
    box.appendChild(h("div", { class: "empty compact", text: "這筆 history 沒有可顯示的 primitive inputs。" }));
    return;
  }
  for (const node of useful) {
    const rows = (node.inputs || []).map((input) =>
      h("div", { class: "node-input" }, [
        h("span", { class: "node-input-name", text: input.name }),
        h("span", { class: "node-input-value", text: valueText(input.value) }),
      ])
    );
    box.appendChild(h("details", { class: "detail-node", open: "" }, [
      h("summary", {}, [
        h("span", { text: `#${node.id}` }),
        h("b", { text: node.type || "Node" }),
      ]),
      h("div", { class: "node-inputs" }, rows),
    ]));
  }
}

function renderGenerationSummary(box, item, detail = null, message = "") {
  if (!box) return;
  clear(box);
  const feature = String((detail && detail.feature) || item.feature || "").trim();
  const workflow = String((detail && detail.workflow) || item.workflow || "").trim();
  const rows = [];
  if (feature) rows.push(detailRow("工作流", feature));
  if (workflow && workflow !== feature) rows.push(detailRow(feature ? "工作流 ID" : "工作流", workflow));
  if (detail && detail.metadataSource === "embedded") {
    rows.push(detailRow("資料來源", "媒體檔內嵌的 ComfyUI metadata"));
  }
  const parameters = detail && Array.isArray(detail.parameters) ? detail.parameters : [];
  for (const parameter of parameters) {
    if (!parameter || typeof parameter !== "object") continue;
    const label = String(parameter.label || "").trim();
    const value = valueText(parameter.value);
    if (label && value) rows.push(detailRow(label, value));
  }
  box.appendChild(h("h3", { class: "generation-summary-title", text: "這份作品的生成資料" }));
  if (rows.length) box.appendChild(h("div", { class: "generation-summary-rows" }, rows));
  if (message) box.appendChild(h("div", { class: "generation-summary-note", text: message }));
}

async function loadHistoryDetail(item, nodesBox, metaBox, summaryBox = null) {
  const session = captureSessionContext();
  const promptId = item.promptId || "disk";
  if (!item.filename && promptId === "disk") {
    clear(nodesBox);
    nodesBox.appendChild(h("div", { class: "empty compact", text: "這筆作品沒有可查詢的檔案識別。" }));
    renderGenerationSummary(summaryBox, item, null, "無法讀取這份作品的生成資料。");
    return;
  }
  const detailCacheKey = [promptId, item.type || "output", item.subfolder || "", item.filename || ""].join("\u0000");
  if (historyDetailCache.has(detailCacheKey)) {
    const cached = historyDetailCache.get(detailCacheKey);
    if (cached.available) {
      if (metaBox) metaBox.appendChild(detailRow("狀態", `${cached.detail.status || "未知"}${cached.detail.completed ? " / completed" : ""}`));
      renderGenerationSummary(summaryBox, item, cached.detail);
      renderDetailNodes(nodesBox, cached.detail);
    } else {
      clear(nodesBox);
      nodesBox.appendChild(h("div", { class: "empty compact", text: cached.error || "找不到生成條件。" }));
      renderGenerationSummary(summaryBox, item, null, cached.error || "這份作品沒有保留生成資料。");
    }
    return;
  }
  clear(nodesBox);
  nodesBox.appendChild(h("div", { class: "empty compact", text: "讀取生成條件中..." }));
  renderGenerationSummary(summaryBox, item, null, "正在讀取 prompt 與生成設定…");
  try {
    const detailQuery = new URLSearchParams({
      prompt_id: promptId,
      filename: item.filename || "",
      subfolder: item.subfolder || "",
      type: item.type || "output",
    });
    const data = await api(`/api/history/detail?${detailQuery.toString()}`);
    assertSessionContext(session);
    historyDetailCache.set(detailCacheKey, data);
    clear(nodesBox);
    if (!data.available) {
      nodesBox.appendChild(h("div", { class: "empty compact", text: data.error || "找不到生成條件。" }));
      renderGenerationSummary(summaryBox, item, null, data.error || "這份作品沒有保留生成資料。");
      return;
    }
    if (metaBox) metaBox.appendChild(detailRow("狀態", `${data.detail.status || "未知"}${data.detail.completed ? " / completed" : ""}`));
    renderGenerationSummary(summaryBox, item, data.detail);
    renderDetailNodes(nodesBox, data.detail);
  } catch (error) {
    if (isStaleSessionError(error)) return;
    clear(nodesBox);
    nodesBox.appendChild(h("div", { class: "missing-box", text: `讀取生成條件失敗：${error.message}` }));
    renderGenerationSummary(summaryBox, item, null, `生成資料讀取失敗：${error.message}`);
  }
}

function itemBody(item) {
  return { filename: item.filename, subfolder: item.subfolder || "", type: item.type || "output", promptId: item.promptId || "" };
}

async function importVoiceItem(item) {
  if (item.kind !== "audio") return;
  const stem = (item.filename || "voice").replace(/\.[^.]+$/, "");
  const name = window.prompt("聲音庫名稱", stem);
  if (name === null) return;
  const transcript = window.prompt("參考逐字稿（短 reference 才需要；可留空）", "");
  if (transcript === null) return;
  try {
    await api("/api/voices/import-history", {
      method: "POST",
      body: JSON.stringify({
        ...itemBody(item),
        promptId: item.promptId || "",
        workflow: item.workflow || "",
        feature: item.feature || "",
        source: item.feature || item.workflow || "generated output",
        name: name.trim(),
        transcript: transcript.trim(),
      }),
    });
    showAlert("已加入聲音庫。");
  } catch (error) {
    showAlert(`加入聲音庫失敗：${error.message}`, true);
  }
}

async function adoptHistoryArtifact(item) {
  try {
    const result = await api("/api/orchestrator/artifacts/adopt", {
      method: "POST",
      body: JSON.stringify({
        ...itemBody(item),
        promptId: item.promptId || "",
        workflow: item.workflow || "",
        feature: item.feature || "",
        artifact_type: item.kind || "file",
        tags: [
          { family: "project", name: "castle-comfy" },
          { family: "source", name: item.source || "gallery" },
        ],
      }),
    });
    const id = result.artifact && result.artifact.id ? result.artifact.id : "";
    showAlert(id ? `已加入工作流 artifact：${id}` : "已加入工作流 artifact。");
    await refreshOrchestrator();
  } catch (error) {
    showAlert(`加入工作流庫失敗：${error.message}`, true);
  }
}

async function toggleHidden(item, options = {}) {
  const next = !item.hidden;
  if (next && !options.confirmed && !window.confirm("確定隱藏此作品？")) return false;
  try {
    await api("/api/history/hide", {
      method: "POST",
      body: JSON.stringify({ ...itemBody(item), hidden: next }),
    });
    item.hidden = next;
    const hiddenVisible = currentRoute.view === "character-library" ? showHiddenCharacterLibrary : showHiddenGlobalLibrary;
    if (next && !hiddenVisible && selectedHistoryKey === historyKey(item)) selectedHistoryKey = "";
    paintHistory();
    refreshHistoryDrivenSurfaces();
    return true;
  } catch (error) {
    showAlert(`更新失敗：${error.message}`, true);
    return false;
  }
}

async function deleteItem(item) {
  if (!window.confirm(`確定刪除「${item.filename}」？此操作會從硬碟移除檔案，無法復原。`)) return false;
  try {
    const result = await api("/api/history/delete", {
      method: "POST",
      body: JSON.stringify(itemBody(item)),
    });
    historyItems = historyItems.filter((it) => it !== item);
    paintHistory();
    if (result.warning) showAlert(result.warning, true);
    return true;
  } catch (error) {
    showAlert(`刪除失敗：${error.message}`, true);
    return false;
  }
}

async function renameItem(item) {
  const next = window.prompt("新的檔名（副檔名會保留）", item.filename);
  if (next === null) return false;
  const name = next.trim();
  if (!name || name === item.filename) return false;
  const oldKey = historyKey(item);
  try {
    const result = await api("/api/history/rename", {
      method: "POST",
      body: JSON.stringify({ ...itemBody(item), name }),
    });
    if (result.item) {
      const index = historyItems.indexOf(item);
      if (index >= 0) historyItems[index] = result.item;
      else Object.assign(item, result.item);
      if (selectedHistoryKey === oldKey) selectedHistoryKey = historyKey(result.item);
    } else {
      await refreshHistory();
    }
    paintHistory();
    if (result.warning) showAlert(result.warning, true);
    return true;
  } catch (error) {
    showAlert(`重新命名失敗：${error.message}`, true);
    return false;
  }
}

// ---- durable workflow orchestrator ----------------------------------------
function orchStateLabel(state) {
  const labels = {
    pending: "等待",
    running: "執行中",
    completed: "完成",
    completed_approved: "已核准完成",
    needs_review: "需檢查",
    failed: "失敗",
    cancelled: "已取消",
    cancel_requested: "取消中",
    skipped: "已跳過",
  };
  return labels[state] || state || "unknown";
}

function orchCountsText(counts) {
  counts = counts || {};
  const running = counts.running || 0;
  const pending = counts.pending || 0;
  const failed = counts.failed || 0;
  const completed = counts.completed || 0;
  const approved = counts.completed_approved || 0;
  const needsReview = counts.needs_review || 0;
  const cancelled = counts.cancelled || 0;
  const skipped = counts.skipped || 0;
  return `執行 ${running}・等待 ${pending}・完成 ${completed}・核准 ${approved}・需檢查 ${needsReview}・失敗 ${failed}・取消 ${cancelled}・跳過 ${skipped}`;
}

function orchGateLabel(state) {
  const labels = {
    not_required: "免審",
    pending: "待審",
    passed: "通過",
    failed: "未過",
    needs_review: "需檢查",
    approved: "核准",
    rejected: "退回",
  };
  return labels[state] || state || "免審";
}

function orchGateClass(state) {
  if (state === "approved" || state === "passed" || state === "not_required") return "completed";
  if (state === "rejected" || state === "failed") return "failed";
  return "pending";
}

function artifactFilterParams() {
  const params = new URLSearchParams();
  params.set("limit", "40");
  if (orchestratorArtifactFilters.q) params.set("q", orchestratorArtifactFilters.q);
  if (orchestratorArtifactFilters.tag) params.set("tag", orchestratorArtifactFilters.tag);
  if (orchestratorArtifactFilters.artifact_type) params.set("artifact_type", orchestratorArtifactFilters.artifact_type);
  return params.toString();
}

function parseArtifactTags(raw) {
  return String(raw || "")
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean);
}

async function refreshOrchestrator(options = {}) {
  const session = captureSessionContext();
  if (document.hidden || !isAdmin() || currentView !== "orchestrator") return;
  try {
    const [status, resources, runs, artifacts] = await Promise.all([
      api("/api/orchestrator/status"),
      api("/api/orchestrator/resources"),
      api("/api/orchestrator/runs?limit=30"),
      api(`/api/orchestrator/artifacts?${artifactFilterParams()}`),
    ]);
    assertSessionContext(session);
    orchestratorSummary = status;
    orchestratorResources = resources;
    orchestratorRuns = runs.runs || [];
    orchestratorArtifacts = artifacts.artifacts || [];
    if (selectedOrchestratorRunId) {
      const detailRunId = selectedOrchestratorRunId;
      try {
        const detail = await api(`/api/orchestrator/runs/${encodeURIComponent(detailRunId)}`);
        assertSessionContext(session);
        if (selectedOrchestratorRunId === detailRunId) orchestratorRunDetail = detail.run;
      } catch (error) {
        if (isStaleSessionError(error)) throw error;
        if (selectedOrchestratorRunId === detailRunId) {
          orchestratorRunDetail = null;
          selectedOrchestratorRunId = "";
        }
      }
    }
    if (!options.silent && currentView === "orchestrator") renderOrchestratorView();
  } catch (error) {
    if (isStaleSessionError(error)) return;
    if (currentView === "orchestrator") showAlert(`工作流讀取失敗：${error.message}`, true);
  }
}

async function applyArtifactFilters() {
  orchestratorArtifactFilters = {
    q: ($("orchArtifactQ") && $("orchArtifactQ").value.trim()) || "",
    tag: ($("orchArtifactTag") && $("orchArtifactTag").value.trim()) || "",
    artifact_type: ($("orchArtifactType") && $("orchArtifactType").value.trim()) || "",
  };
  await refreshOrchestrator();
}

async function clearArtifactFilters() {
  orchestratorArtifactFilters = { q: "", tag: "", artifact_type: "" };
  await refreshOrchestrator();
}

async function pollOrchestratorEvents() {
  if (document.hidden || !isAdmin() || currentView !== "orchestrator") return;
  try {
    const data = await api(`/api/orchestrator/events?after=${orchestratorCursor}&limit=100`);
    const events = data.events || [];
    if (events.length) {
      orchestratorCursor = data.cursor || events[events.length - 1].id || orchestratorCursor;
      await refreshOrchestrator({ silent: true });
      if (currentView === "orchestrator") renderOrchestratorView();
    }
  } catch (_) {
    /* keep polling light; refreshOrchestrator handles visible errors */
  }
}

function revealOrchestratorRun(runId, startedHash) {
  if (runId && currentRoute.view === "orchestrator" && window.location.hash === startedHash) {
    navigate({ view: "orchestrator", pane: "detail", runId });
  }
}

async function submitMockWorkflow() {
  const session = captureSessionContext();
  const startedHash = window.location.hash;
  const message = window.prompt("Mock task 要寫進 artifact 的文字", "orchestrator ok");
  if (message === null) return;
  try {
    const result = await api("/api/orchestrator/runs", {
      method: "POST",
      body: JSON.stringify({
        workflow_type: "mock.echo",
        title: "UI mock echo",
        idempotency_key: `ui-mock-${Date.now()}-${Math.random().toString(16).slice(2)}`,
        input: { message, sleep_seconds: 1 },
      }),
    });
    assertSessionContext(session);
    const newRunId = result.run && result.run.id ? result.run.id : "";
    if (currentRoute.view === "orchestrator" && window.location.hash === startedHash) {
      selectedOrchestratorRunId = newRunId;
      orchestratorRunDetail = result.run || null;
    }
    showAlert("已送出 mock workflow。");
    await refreshOrchestrator();
    assertSessionContext(session);
    revealOrchestratorRun(newRunId, startedHash);
  } catch (error) {
    if (isStaleSessionError(error)) return;
    showAlert(`送出失敗：${error.message}`, true);
  }
}

async function submitMockDagWorkflow() {
  const session = captureSessionContext();
  const startedHash = window.location.hash;
  const message = window.prompt("DAG 第一段要寫進 artifact 的文字", "dag first");
  if (message === null) return;
  try {
    const result = await api("/api/orchestrator/runs", {
      method: "POST",
      body: JSON.stringify({
        workflow_type: "custom.dag",
        title: "UI custom DAG",
        idempotency_key: `ui-dag-${Date.now()}-${Math.random().toString(16).slice(2)}`,
        max_attempts: 2,
        tasks: [
          { key: "first", task_type: "mock.echo", input: { message, sleep_seconds: 1 } },
          { key: "second", task_type: "mock.echo", deps: ["first"], input: { message: `${message} / second`, sleep_seconds: 1 } },
        ],
      }),
    });
    assertSessionContext(session);
    const newRunId = result.run && result.run.id ? result.run.id : "";
    if (currentRoute.view === "orchestrator" && window.location.hash === startedHash) {
      selectedOrchestratorRunId = newRunId;
      orchestratorRunDetail = result.run || null;
    }
    showAlert("已送出 DAG workflow。");
    await refreshOrchestrator();
    assertSessionContext(session);
    revealOrchestratorRun(newRunId, startedHash);
  } catch (error) {
    if (isStaleSessionError(error)) return;
    showAlert(`送出失敗：${error.message}`, true);
  }
}

async function submitReelPipelineWorkflow(event) {
  const session = captureSessionContext();
  const startedHash = window.location.hash;
  if (event) event.preventDefault();
  const episode = ($("reelEpisode") && $("reelEpisode").value.trim()) || "";
  const preset = ($("reelPreset") && $("reelPreset").value) || "single-rerun";
  const sig = ($("reelSig") && $("reelSig").value.trim()) || "";
  if (!/^[0-9]{1,8}$/.test(episode)) {
    showAlert("請輸入單一數字 episode。", true);
    return;
  }
  const input = { episode, preset };
  if (sig) input.sig = sig;
  const mode = preset === "rough-cut" ? "rough-cut" : "full-quality";
  const idBits = [episode, mode, sig || `auto-${Math.floor(Date.now() / 600000)}`];
  const presetLabel = mode === "rough-cut" ? "Rough cut" : "Full quality";
  try {
    const result = await api("/api/orchestrator/runs", {
      method: "POST",
      body: JSON.stringify({
        workflow_type: "ai_sister.reel_pipeline",
        title: `AI-Sister reel ${episode} (${presetLabel})`,
        idempotency_key: `reel-pipeline:${idBits.join(":")}`,
        input,
      }),
    });
    assertSessionContext(session);
    const newRunId = result.run && result.run.id ? result.run.id : "";
    if (currentRoute.view === "orchestrator" && window.location.hash === startedHash) {
      selectedOrchestratorRunId = newRunId;
      orchestratorRunDetail = result.run || null;
    }
    showAlert(result.idempotent ? "已找到相同 reel run。" : "已送出 reel run。");
    await refreshOrchestrator();
    assertSessionContext(session);
    revealOrchestratorRun(newRunId, startedHash);
  } catch (error) {
    if (isStaleSessionError(error)) return;
    showAlert(`送出 reel run 失敗：${error.message}`, true);
  }
}

function renderReelRunForm() {
  return h("form", { class: "orch-reel-form", onsubmit: submitReelPipelineWorkflow }, [
    h("div", { class: "detail-section-title", text: "New reel run" }),
    h("div", { class: "orch-artifact-controls" }, [
      h("input", { id: "reelEpisode", type: "text", inputmode: "numeric", pattern: "[0-9]*", placeholder: "Episode", autocomplete: "off" }),
      h("select", { id: "reelPreset" }, [
        h("option", { value: "single-rerun", text: "Full quality" }),
        h("option", { value: "rough-cut", text: "Rough cut" }),
      ]),
      h("input", { id: "reelSig", type: "text", placeholder: "Signature (optional)", autocomplete: "off" }),
      h("button", { class: "ghost small", type: "submit", text: "Start reel" }),
    ]),
  ]);
}

async function loadOrchestratorRun(runId) {
  const session = captureSessionContext();
  selectedOrchestratorRunId = runId;
  try {
    const data = await api(`/api/orchestrator/runs/${encodeURIComponent(runId)}`);
    assertSessionContext(session);
    if (selectedOrchestratorRunId !== runId) return;
    orchestratorRunDetail = data.run;
    if (currentRoute.view === "orchestrator" && currentRoute.runId === runId) renderOrchestratorView();
  } catch (error) {
    if (isStaleSessionError(error)) return;
    if (currentRoute.view === "orchestrator" && currentRoute.runId === runId) showAlert(`讀取 run 失敗：${error.message}`, true);
  }
}

async function cancelOrchestratorRun(runId) {
  if (!window.confirm("確定取消這個 workflow？執行中的 task 會收到 process group terminate。")) return;
  try {
    await api(`/api/orchestrator/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST", body: "{}" });
    showAlert("已送出取消要求。");
    await refreshOrchestrator();
  } catch (error) {
    showAlert(`取消失敗：${error.message}`, true);
  }
}

async function rerunOrchestratorRun(runId) {
  const startedHash = window.location.hash;
  if (!window.confirm("重新送出這個 workflow 成為新的 run？")) return;
  try {
    const result = await api(`/api/orchestrator/runs/${encodeURIComponent(runId)}/rerun`, {
      method: "POST",
      body: JSON.stringify({ title: `${(orchestratorRunDetail && orchestratorRunDetail.title) || "workflow"} rerun` }),
    });
    const newRunId = result.run && result.run.id ? result.run.id : "";
    if (currentRoute.view === "orchestrator" && window.location.hash === startedHash) {
      selectedOrchestratorRunId = newRunId;
      orchestratorRunDetail = result.run || null;
    }
    showAlert("已重新送出 workflow。");
    await refreshOrchestrator();
    revealOrchestratorRun(newRunId, startedHash);
  } catch (error) {
    showAlert(`重跑失敗：${error.message}`, true);
  }
}

async function retryOrchestratorTask(task) {
  const maxAttempts = Math.max(Number(task.max_attempts || 1), 2);
  if (!window.confirm(`重試 task「${task.key || task.task_type || task.id}」？\n\n下游因它失敗而取消的 task 會重新排隊。`)) return;
  try {
    const data = await api(`/api/orchestrator/tasks/${encodeURIComponent(task.id)}/retry`, {
      method: "POST",
      body: JSON.stringify({ max_attempts: maxAttempts, reset_downstream: true }),
    });
    orchestratorRunDetail = data.run || orchestratorRunDetail;
    showAlert("已重新排入 task。");
    await refreshOrchestrator();
  } catch (error) {
    showAlert(`重試失敗：${error.message}`, true);
  }
}

async function cancelOrchestratorTask(task) {
  if (!window.confirm(`取消 task「${task.key || task.task_type || task.id}」？\n\n它的下游 pending task 也會取消，其他獨立 task 會繼續。`)) return;
  try {
    const data = await api(`/api/orchestrator/tasks/${encodeURIComponent(task.id)}/cancel`, {
      method: "POST",
      body: "{}",
    });
    orchestratorRunDetail = data.run || orchestratorRunDetail;
    showAlert("已送出 task 取消要求。");
    await refreshOrchestrator();
  } catch (error) {
    showAlert(`取消 task 失敗：${error.message}`, true);
  }
}

async function markOrchestratorTaskSkipped(task) {
  const reason = window.prompt(`跳過 task「${task.key || task.task_type || task.id}」的原因`, "operator skipped");
  if (reason === null) return;
  try {
    const data = await api(`/api/orchestrator/tasks/${encodeURIComponent(task.id)}/mark-skipped`, {
      method: "POST",
      body: JSON.stringify({ reason, actor: "operator" }),
    });
    orchestratorRunDetail = data.run || orchestratorRunDetail;
    showAlert("Task 已標成跳過，下游 task 已重新檢查。");
    await refreshOrchestrator();
  } catch (error) {
    showAlert(`跳過 task 失敗：${error.message}`, true);
  }
}

async function toggleSchedulerPause() {
  const scheduler = orchestratorSummary.scheduler || {};
  const paused = Boolean(scheduler.paused);
  const path = paused ? "/api/orchestrator/scheduler/resume" : "/api/orchestrator/scheduler/pause";
  const body = paused ? "{}" : JSON.stringify({ reason: "paused from Castle UI" });
  try {
    await api(path, { method: "POST", body });
    showAlert(paused ? "Scheduler 已恢復。" : "Scheduler 已暫停。");
    await refreshOrchestrator();
  } catch (error) {
    showAlert(`Scheduler 操作失敗：${error.message}`, true);
  }
}

async function reserveGpuResource() {
  const reason = window.prompt("保留 GPU 的原因", "operator manual GPU reservation");
  if (reason === null) return;
  try {
    await api("/api/orchestrator/resources/gpu%3Artx5090/reserve", {
      method: "POST",
      body: JSON.stringify({ reason, actor: "operator", ttl_seconds: 3600 }),
    });
    showAlert("GPU 已保留，新 GPU task 會先排隊。");
    await refreshOrchestrator();
  } catch (error) {
    showAlert(`保留 GPU 失敗：${error.message}`, true);
  }
}

async function releaseGpuResource() {
  if (!window.confirm("釋放目前由 UI 建立的 GPU reservation？")) return;
  try {
    await api("/api/orchestrator/resources/gpu%3Artx5090/release", {
      method: "POST",
      body: JSON.stringify({ actor: "operator" }),
    });
    showAlert("GPU reservation 已釋放。");
    await refreshOrchestrator();
  } catch (error) {
    showAlert(`釋放 GPU 失敗：${error.message}`, true);
  }
}

async function editArtifactTags(artifact) {
  const current = (artifact.tags || []).map((tag) => `${tag.family}:${tag.name}`).join(", ");
  const raw = window.prompt("標籤 family:name，用逗號分隔", current);
  if (raw === null) return;
  try {
    await api(`/api/orchestrator/artifacts/${encodeURIComponent(artifact.id)}/tags`, {
      method: "POST",
      body: JSON.stringify({ replace: true, tags: parseArtifactTags(raw) }),
    });
    showAlert("Artifact 標籤已更新。");
    await refreshOrchestrator();
  } catch (error) {
    showAlert(`標籤更新失敗：${error.message}`, true);
  }
}

async function editArtifactMetadata(artifact) {
  const current = JSON.stringify(artifact.metadata || {}, null, 2);
  const raw = window.prompt("metadata JSON", current);
  if (raw === null) return;
  let metadata = {};
  try {
    metadata = raw.trim() ? JSON.parse(raw) : {};
    if (!metadata || Array.isArray(metadata) || typeof metadata !== "object") {
      throw new Error("metadata 必須是 JSON object");
    }
  } catch (error) {
    showAlert(`metadata JSON 無效：${error.message}`, true);
    return;
  }
  try {
    await api(`/api/orchestrator/artifacts/${encodeURIComponent(artifact.id)}/metadata`, {
      method: "POST",
      body: JSON.stringify({ replace: true, metadata }),
    });
    showAlert("Artifact metadata 已更新。");
    await refreshOrchestrator();
  } catch (error) {
    showAlert(`metadata 更新失敗：${error.message}`, true);
  }
}

async function bulkAdoptArtifacts() {
  const rawPaths = window.prompt("每行一個 server path（必須在允許的 root 裡）", "");
  if (rawPaths === null) return;
  const paths = rawPaths.split(/\r?\n/).map((path) => path.trim()).filter(Boolean);
  if (!paths.length) {
    showAlert("沒有可加入的 path。", true);
    return;
  }
  const typeRaw = window.prompt("artifact type", orchestratorArtifactFilters.artifact_type || "video");
  if (typeRaw === null) return;
  const tagsRaw = window.prompt("共用標籤 family:name，用逗號分隔", "project:ai-sister");
  if (tagsRaw === null) return;
  const metadataRaw = window.prompt("共用 metadata JSON", "{}");
  if (metadataRaw === null) return;
  let metadata = {};
  try {
    metadata = metadataRaw.trim() ? JSON.parse(metadataRaw) : {};
    if (!metadata || Array.isArray(metadata) || typeof metadata !== "object") {
      throw new Error("metadata 必須是 JSON object");
    }
  } catch (error) {
    showAlert(`metadata JSON 無效：${error.message}`, true);
    return;
  }
  try {
    const result = await api("/api/orchestrator/artifacts/adopt-batch", {
      method: "POST",
      body: JSON.stringify({
        items: paths.map((sourcePath) => ({ source_path: sourcePath })),
        artifact_type: typeRaw.trim() || "file",
        tags: parseArtifactTags(tagsRaw),
        metadata,
      }),
    });
    const errors = result.errors || [];
    const firstError = errors[0] ? `；第一個錯誤：${errors[0].source_path || errors[0].index} ${errors[0].error}` : "";
    showAlert(`批次加入完成：${result.count || 0} 成功，${result.error_count || 0} 失敗${firstError}`, Boolean(errors.length));
    await refreshOrchestrator();
  } catch (error) {
    showAlert(`批次加入失敗：${error.message}`, true);
  }
}

async function approveArtifact(artifact) {
  if (!window.confirm(`核准 artifact ${artifact.id}？`)) return;
  try {
    await api(`/api/orchestrator/artifacts/${encodeURIComponent(artifact.id)}/approve`, {
      method: "POST",
      body: JSON.stringify({ actor: "operator" }),
    });
    showAlert("Artifact 已核准。");
    await refreshOrchestrator();
  } catch (error) {
    showAlert(`核准失敗：${error.message}`, true);
  }
}

async function rejectArtifact(artifact) {
  const note = window.prompt(`退回 artifact ${artifact.id} 的原因`, "");
  if (note === null) return;
  try {
    await api(`/api/orchestrator/artifacts/${encodeURIComponent(artifact.id)}/reject`, {
      method: "POST",
      body: JSON.stringify({ actor: "operator", note }),
    });
    showAlert("Artifact 已退回。");
    await refreshOrchestrator();
  } catch (error) {
    showAlert(`退回失敗：${error.message}`, true);
  }
}

async function makeArtifactProxy(artifact) {
  const startedHash = window.location.hash;
  try {
    const result = await api(`/api/orchestrator/artifacts/${encodeURIComponent(artifact.id)}/make-proxy`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    const newRunId = result.run && result.run.id ? result.run.id : "";
    if (newRunId && currentRoute.view === "orchestrator" && window.location.hash === startedHash) {
      selectedOrchestratorRunId = newRunId;
      orchestratorRunDetail = result.run || orchestratorRunDetail;
    }
    showAlert("已送出 review proxy task。");
    await refreshOrchestrator();
    revealOrchestratorRun(newRunId, startedHash);
  } catch (error) {
    showAlert(`建立 proxy 失敗：${error.message}`, true);
  }
}

function renderReelReviewPanel(run) {
  if (run.workflow_type !== "ai_sister.reel_pipeline") return null;
  const artifacts = run.artifacts || [];
  const requiredRoles = ["final", "proxy", "cover", "qc"];
  const byRole = new Map();
  for (const artifact of artifacts) {
    for (const tag of artifact.tags || []) {
      if (tag.family === "role" && requiredRoles.includes(tag.name) && !byRole.has(tag.name)) {
        byRole.set(tag.name, artifact);
      }
    }
  }
  const gateRows = requiredRoles.map((role) => {
    const artifact = byRole.get(role);
    return h("div", { class: "orch-artifact compact" }, [
      h("div", { class: "orch-artifact-main" }, [
        h("div", { class: "orch-task-title", text: role }),
        h("div", { class: "orch-run-meta", text: artifact ? shortPromptId(artifact.id) : "missing" }),
      ]),
      h("span", {
        class: `job-state ${artifact ? orchGateClass(artifact.gate_state) : "failed"}`,
        text: artifact ? orchGateLabel(artifact.gate_state) : "missing",
      }),
    ]);
  });
  return h("div", { class: "orch-section" }, [
    h("div", { class: "panel-head-row compact" }, [
      h("div", {}, [
        h("div", { class: "detail-section-title", text: "Castle Reel 診斷" }),
        h("div", { class: "panel-sub", text: "僅顯示 final/proxy/cover/QC 狀態。裁決請在 AI-Sister 後台送出。" }),
      ]),
    ]),
    h("div", { class: "orch-task-list" }, gateRows),
  ]);
}

function renderOrchestratorRunRow(run) {
  const active = run.id === selectedOrchestratorRunId;
  const counts = run.task_counts || {};
  const canCancel = ["pending", "running", "cancel_requested"].includes(run.status);
  return h("div", { class: `orch-run-row ${run.status || ""}${active ? " selected" : ""}` }, [
    h("button", { class: "orch-run-main", type: "button", onclick: () => navigate({ view: "orchestrator", pane: "detail", runId: run.id }) }, [
      h("div", { class: "orch-run-title", text: run.title || run.workflow_type || run.id }),
      h("div", { class: "orch-run-meta", text: `${run.workflow_type}・${shortPromptId(run.id)}・${formatTimestamp(run.created_at || 0)}` }),
      h("div", { class: "orch-run-meta", text: orchCountsText(counts) }),
    ]),
    h("div", { class: "orch-run-actions" }, [
      h("span", { class: `job-state ${run.status || "pending"}`, text: orchStateLabel(run.status) }),
      canCancel ? h("button", { class: "job-cancel danger", type: "button", text: "取消", onclick: () => cancelOrchestratorRun(run.id) }) : null,
    ]),
  ]);
}

function renderOrchestratorTask(task) {
  const canOpenLog = Boolean(task.log_url);
  const canRetry = ["failed", "cancelled"].includes(task.status);
  const canCancel = ["pending", "running"].includes(task.status);
  const canSkip = ["pending", "failed", "cancelled"].includes(task.status);
  const attempt = Number(task.attempt || 0);
  const maxAttempts = Number(task.max_attempts || 1);
  const meta = [task.lane || "cpu", shortPromptId(task.id)];
  if (maxAttempts > 1) meta.push(`try ${attempt + 1}/${maxAttempts}`);
  if (task.reuse_policy === "input_hash") meta.push(task.reused_from_task_id ? "hash reuse" : "reuse on");
  if (task.error) meta.push(task.error);
  const progress = task.progress && task.progress.label ? task.progress.label : "";
  return h("div", { class: `orch-task ${task.status || ""}` }, [
    h("div", { class: "orch-task-head" }, [
      h("div", {}, [
        h("div", { class: "orch-task-title", text: `${task.key || "task"} · ${task.task_type || ""}` }),
        h("div", { class: "orch-run-meta", text: meta.join("・") }),
        progress ? h("div", { class: "orch-run-meta", text: progress }) : null,
      ]),
      h("div", { class: "orch-run-actions" }, [
        h("span", { class: `job-state ${task.status || "pending"}`, text: orchStateLabel(task.status) }),
        canCancel ? h("button", { class: "job-cancel danger", type: "button", text: "取消 task", onclick: () => cancelOrchestratorTask(task) }) : null,
        canRetry ? h("button", { class: "ghost small", type: "button", text: "重試", onclick: () => retryOrchestratorTask(task) }) : null,
        canSkip ? h("button", { class: "ghost small", type: "button", text: "跳過", onclick: () => markOrchestratorTaskSkipped(task) }) : null,
      ]),
    ]),
    canOpenLog ? h("a", { class: "ghost small", href: task.log_url, target: "_blank", rel: "noreferrer", text: "log" }) : null,
  ]);
}

function renderArtifactTags(tags) {
  tags = tags || [];
  if (!tags.length) return h("div", { class: "orch-run-meta", text: "無標籤" });
  return h("div", { class: "orch-tags" }, tags.map((tag) => (
    h("button", {
      class: "orch-tag",
      type: "button",
      text: `${tag.family}:${tag.name}`,
      onclick: async () => {
        orchestratorArtifactFilters = { ...orchestratorArtifactFilters, tag: `${tag.family}:${tag.name}` };
        await refreshOrchestrator();
      },
    })
  )));
}

function renderOrchestratorArtifact(artifact) {
  const metadata = artifact.metadata || {};
  const label = metadata.title || metadata.filename || metadata.source_name || metadata.source || shortPromptId(artifact.id);
  const source = [artifact.source_type || "", artifact.uri || artifact.relative_path || artifact.content_hash || ""].filter(Boolean).join("・");
  const approval = artifact.latest_approval;
  const approvalText = approval ? `${approval.decision}・${approval.actor}・${formatTimestamp(approval.created_at || 0)}` : "";
  return h("div", { class: `orch-artifact ${artifact.gate_state || "not_required"}` }, [
    h("div", { class: "orch-artifact-main" }, [
      h("div", { class: "orch-task-title", text: `${artifact.artifact_type || "file"} · ${label}` }),
      h("div", { class: "orch-run-meta", text: source }),
      renderArtifactTags(artifact.tags || []),
      approvalText ? h("div", { class: "orch-run-meta", text: approvalText }) : null,
    ]),
    h("div", { class: "orch-run-actions" }, [
      h("span", { class: `job-state ${orchGateClass(artifact.gate_state)}`, text: orchGateLabel(artifact.gate_state) }),
      artifact.file_url ? h("a", { class: "ghost small", href: artifact.file_url, target: "_blank", rel: "noreferrer", text: "開啟" }) : null,
      artifact.file_url ? h("button", { class: "ghost small", type: "button", text: "proxy", onclick: () => makeArtifactProxy(artifact) }) : null,
      h("button", { class: "ghost small", type: "button", text: "標籤", onclick: () => editArtifactTags(artifact) }),
      h("button", { class: "ghost small", type: "button", text: "metadata", onclick: () => editArtifactMetadata(artifact) }),
      h("button", { class: "ghost small", type: "button", text: "核准", onclick: () => approveArtifact(artifact) }),
      h("button", { class: "job-cancel danger", type: "button", text: "退回", onclick: () => rejectArtifact(artifact) }),
    ]),
  ]);
}

function renderArtifactLibraryPanel() {
  const artifactList = orchestratorArtifacts.length
    ? h("div", { class: "orch-task-list" }, orchestratorArtifacts.map(renderOrchestratorArtifact))
    : h("div", { class: "queue-empty", text: "沒有符合條件的 artifact。" });
  return h("div", { class: "orch-section orch-artifact-library" }, [
    h("div", { class: "panel-head-row compact" }, [
      h("div", {}, [
        h("div", { class: "detail-section-title", text: "素材庫" }),
        h("div", { class: "panel-sub", text: `顯示 ${orchestratorArtifacts.length} 筆` }),
      ]),
      h("div", { class: "orch-run-actions" }, [
        h("button", { class: "ghost small", type: "button", text: "批次加入", onclick: bulkAdoptArtifacts }),
        h("button", { class: "ghost small", type: "button", text: "更新", onclick: refreshOrchestrator }),
      ]),
    ]),
    h("div", { class: "orch-artifact-controls" }, [
      h("input", { id: "orchArtifactQ", type: "search", placeholder: "搜尋", value: orchestratorArtifactFilters.q }),
      h("input", { id: "orchArtifactTag", type: "search", placeholder: "tag", value: orchestratorArtifactFilters.tag }),
      h("select", { id: "orchArtifactType" }, [
        h("option", { value: "", text: "全部" }),
        h("option", { value: "audio", text: "audio", selected: orchestratorArtifactFilters.artifact_type === "audio" }),
        h("option", { value: "video", text: "video", selected: orchestratorArtifactFilters.artifact_type === "video" }),
        h("option", { value: "image", text: "image", selected: orchestratorArtifactFilters.artifact_type === "image" }),
        h("option", { value: "text", text: "text", selected: orchestratorArtifactFilters.artifact_type === "text" }),
        h("option", { value: "file", text: "file", selected: orchestratorArtifactFilters.artifact_type === "file" }),
      ]),
      h("button", { class: "ghost small", type: "button", text: "查詢", onclick: applyArtifactFilters }),
      h("button", { class: "ghost small", type: "button", text: "清除", onclick: clearArtifactFilters }),
    ]),
    artifactList,
  ]);
}

function renderOrchestratorDetail() {
  const run = orchestratorRunDetail;
  if (!run) {
    return h("section", { class: "panel" }, [
      h("div", { class: "empty", text: selectedOrchestratorRunId ? "正在讀取 workflow 詳細資料…" : "選擇左側 workflow 查看詳細狀態、task log 和 artifacts。" }),
    ]);
  }
  const events = (run.events || []).slice(-8).reverse();
  const canCancel = ["pending", "running", "cancel_requested"].includes(run.status);
  const canRerun = ["completed", "failed", "cancelled"].includes(run.status);
  return h("section", { class: "panel orch-detail" }, [
    h("div", { class: "panel-head-row" }, [
      h("div", {}, [
        h("h1", { class: "panel-h1", text: run.title || run.id }),
        h("div", { class: "panel-sub", text: `${run.workflow_type}・${run.id}` }),
      ]),
      h("div", { class: "orch-run-actions" }, [
        h("span", { class: `job-state ${run.status || "pending"}`, text: orchStateLabel(run.status) }),
        canCancel ? h("button", { class: "job-cancel danger", type: "button", text: "取消", onclick: () => cancelOrchestratorRun(run.id) }) : null,
        canRerun ? h("button", { class: "ghost small", type: "button", text: "重跑", onclick: () => rerunOrchestratorRun(run.id) }) : null,
      ]),
    ]),
    h("div", { class: "orch-json" }, [
      h("div", { class: "detail-section-title", text: "提交條件" }),
      h("pre", { text: JSON.stringify(run.input || {}, null, 2) }),
    ]),
    h("div", { class: "orch-section" }, [
      h("div", { class: "detail-section-title", text: "Tasks" }),
      h("div", { class: "orch-task-list" }, (run.tasks || []).map(renderOrchestratorTask)),
    ]),
    renderReelReviewPanel(run),
    h("div", { class: "orch-section" }, [
      h("div", { class: "detail-section-title", text: "Artifacts" }),
      (run.artifacts || []).length
        ? h("div", { class: "orch-task-list" }, (run.artifacts || []).map(renderOrchestratorArtifact))
        : h("div", { class: "queue-empty", text: "還沒有 artifact。" }),
    ]),
    h("div", { class: "orch-section" }, [
      h("div", { class: "detail-section-title", text: "Events" }),
      events.length
        ? h("div", { class: "orch-events" }, events.map((ev) => h("div", { class: `orch-event ${ev.level || "info"}` }, [
            h("span", { text: formatTimestamp(ev.created_at || 0) }),
            h("strong", { text: ev.event_type || "" }),
            h("span", { text: ev.message || "" }),
          ])))
        : h("div", { class: "queue-empty", text: "還沒有 event。" }),
    ]),
  ]);
}

function renderOrchestratorView() {
  const ws = beginWorkspaceRender();
  const runCounts = orchestratorSummary.runs || {};
  const taskCounts = orchestratorSummary.tasks || {};
  const laneCounts = orchestratorSummary.lanes || {};
  const laneText = Object.entries(laneCounts).map(([lane, counts]) => `${lane}: ${orchCountsText(counts)}`).join(" / ");
  const gpuQueue = orchestratorSummary.gpu_queue || {};
  const scheduler = orchestratorSummary.scheduler || {};
  const resourceSummary = orchestratorResources.summary || {};
  const gpuResource = (orchestratorResources.resources || []).find((item) => item.id === "gpu:rtx5090") || {};
  const gpuObserved = gpuResource.observed || {};
  const reservations = orchestratorResources.reservations || [];
  const gpuReserved = reservations.some((item) => item.resource_id === "gpu:rtx5090");
  const active = orchestratorSummary.active || [];
  const runList = h("div", { class: "orch-run-list" });
  if (orchestratorRuns.length) {
    for (const run of orchestratorRuns) runList.appendChild(renderOrchestratorRunRow(run));
  } else {
    runList.appendChild(h("div", { class: "queue-empty", text: "目前沒有 workflow run。" }));
  }
  const setMobilePane = (pane) => {
    if (pane === "detail" && selectedOrchestratorRunId) {
      navigate({ view: "orchestrator", pane: "detail", runId: selectedOrchestratorRunId });
    } else {
      navigate({ view: "orchestrator", pane });
    }
  };
  const metric = (label, value, detail, tone = "") => h("div", { class: `orch-summary-card ${tone}`.trim() }, [
    h("span", { text: label }),
    h("strong", { text: String(value) }),
    h("small", { text: detail }),
  ]);
  const summaryPanel = h("section", { class: "panel orch-overview-pane" }, [
    h("div", { class: "panel-head-row" }, [
      h("div", {}, [
        h("h1", { class: "panel-h1", text: "工作流監控" }),
        h("div", { class: "panel-sub", text: "先看整體狀態，再進入執行紀錄與詳細資料。" }),
      ]),
      h("button", { class: "ghost small", type: "button", text: "更新", onclick: refreshOrchestrator }),
    ]),
    h("div", { class: "orch-summary-grid" }, [
      metric("進行中的工作流", Number(runCounts.running || 0) + Number(runCounts.pending || 0), orchCountsText(runCounts), Number(runCounts.failed || 0) ? "warning" : ""),
      metric("進行中的任務", Number(taskCounts.running || 0) + Number(taskCounts.pending || 0), orchCountsText(taskCounts), Number(taskCounts.failed || 0) ? "warning" : ""),
      metric("GPU", resourceSummary.gpu_state || "未知", `${gpuObserved.memory_free_mb !== undefined ? `可用 ${gpuObserved.memory_free_mb} MB` : "尚無記憶體資料"}${gpuObserved.utilization_gpu_pct !== undefined ? ` · 使用率 ${gpuObserved.utilization_gpu_pct}%` : ""}`, gpuReserved ? "warning" : ""),
      metric("排程器", scheduler.paused ? "已暫停" : "運作中", scheduler.reason || `${active.length} 個 active task`, scheduler.paused ? "warning" : "ok"),
    ]),
    active.length ? h("div", { class: "panel-note" }, [
      h("span", { class: "note-ico", text: "!" }),
      h("span", { text: `目前執行：${active.map((t) => `${t.lane || "cpu"} · ${t.task_type} · ${orchStateLabel(t.status)}${t.progress && t.progress.label ? ` · ${t.progress.label}` : ""}`).join("、")}` }),
    ]) : null,
    (resourceSummary.gpu_blocked_by || []).length ? h("div", { class: "panel-note" }, [
      h("span", { class: "note-ico", text: "!" }),
      h("span", { text: `GPU 正由 ${(resourceSummary.gpu_blocked_by || []).join("、")} 使用` }),
    ]) : null,
    h("details", { class: "orch-status-details" }, [
      h("summary", { text: "查看技術狀態" }),
      h("div", { class: "orch-status-lines" }, [
        laneText ? h("p", { text: `Lanes：${laneText}` }) : null,
        h("p", { text: `ComfyUI：${resourceSummary.comfyui_state || "unknown"} · 執行 ${resourceSummary.comfyui_running || 0} · 等待 ${resourceSummary.comfyui_pending || 0} · 外部 ${resourceSummary.comfyui_external || 0}` }),
        gpuQueue.script ? h("p", { text: `GPU queue：${gpuQueue.script_exists ? "ready" : "missing"} · free ${gpuQueue.min_free_mb}MB · util ${gpuQueue.max_util}%` }) : null,
        reservations.length ? h("p", { text: `Reservations：${reservations.map((item) => `${item.resource_id} ${item.reason || ""}`).join(" / ")}` }) : null,
      ]),
    ]),
    h("details", { class: "orch-operator-tools" }, [
      h("summary", { text: "進階操作與新工作流" }),
      h("div", { class: "orch-operator-actions" }, [
        h("button", { class: gpuReserved ? "ghost small" : "job-cancel danger", type: "button", text: gpuReserved ? "釋放 GPU" : "保留 GPU", onclick: gpuReserved ? releaseGpuResource : reserveGpuResource }),
        h("button", { class: scheduler.paused ? "ghost small" : "job-cancel danger", type: "button", text: scheduler.paused ? "恢復排程" : "暫停排程", onclick: toggleSchedulerPause }),
        h("button", { class: "ghost small", type: "button", text: "送出 Mock", onclick: submitMockWorkflow }),
        h("button", { class: "ghost small", type: "button", text: "送出 DAG", onclick: submitMockDagWorkflow }),
      ]),
      renderReelRunForm(),
    ]),
  ]);
  const runsPanel = h("section", { class: "panel orch-runs-pane" }, [
    h("button", { class: "ghost small orch-mobile-back", type: "button", text: "← 總覽", onclick: () => setMobilePane("overview") }),
    renderSectionHeader("執行紀錄", `最近 ${orchestratorRuns.length} 筆工作流；點一筆查看任務與產物。`, [], { titleClass: "panel-h1", level: "h2" }),
    runList,
  ]);
  const detailPane = h("div", { class: "orch-detail-pane" }, [
    h("button", { class: "ghost small orch-mobile-back", type: "button", text: "← 執行紀錄", onclick: () => setMobilePane("runs") }),
    renderOrchestratorDetail(),
  ]);
  const mobileNav = h("nav", { class: "orch-mobile-nav", "aria-label": "工作流監控頁面" }, [
    ["overview", "總覽"], ["runs", "執行紀錄"], ["detail", "詳細資料"],
  ].map(([id, label]) => h("button", {
    id: `orchestratorPane-${id}`,
    class: orchestratorMobilePane === id ? "active" : "",
    type: "button",
    disabled: id === "detail" && !orchestratorRunDetail,
    "aria-current": orchestratorMobilePane === id ? "page" : null,
    text: label,
    onclick: () => setMobilePane(id),
  })));
  ws.appendChild(h("div", { class: "orchestrator-view", dataset: { mobilePane: orchestratorMobilePane } }, [
    mobileNav,
    summaryPanel,
    h("div", { class: "orch-workspace-grid" }, [runsPanel, detailPane]),
    h("section", { class: "panel orch-overview-pane orch-global-artifacts" }, [renderArtifactLibraryPanel()]),
  ]));
}

// ---- admin users ----------------------------------------------------------

function renderAdminHome() {
  const ws = beginWorkspaceRender();
  ws.appendChild(h("section", { class: "panel" }, [
    renderSectionHeader("管理中心", "管理角色、素材、使用者與工作流。", [], { titleClass: "panel-h1", level: "h1" }),
    h("div", { class: "catalogue-admin-links" }, [
      h("button", { class: "shell-menu-row", text: "角色與素材分類", onclick: () => navigate({ view: "library" }) }),
      h("button", { class: "shell-menu-row", text: "採集圖片與影片", onclick: openMaterialCollect }),
      h("button", { class: "shell-menu-row", text: "外部角色與資源", onclick: () => navigate({ view: "resources" }) }),
      h("button", { class: "shell-menu-row", text: "使用者管理", onclick: () => navigate({ view: "admin-users" }) }),
      h("button", { class: "shell-menu-row", text: "工作流監控", onclick: () => navigate({ view: "orchestrator" }) }),
      h("a", { class: "shell-menu-row", href: "http://127.0.0.1:8188/", target: "_blank", rel: "noopener noreferrer", text: "開啟 ComfyUI 節點編輯器 ↗" }),
    ]),
  ]));
}
function formatUserDate(ts) {
  if (!ts) return "";
  try {
    return new Date(Number(ts) * 1000).toLocaleString("zh-TW");
  } catch (_) {
    return "";
  }
}

async function loadAdminUsers() {
  const session = captureSessionContext();
  if (!isAdmin()) return;
  try {
    const data = await api("/api/admin/users");
    assertSessionContext(session);
    adminUsers = data.users || [];
    adminUsersError = "";
  } catch (error) {
    if (isStaleSessionError(error)) return;
    adminUsersError = error.message;
  }
}

async function adminUserUpdate(email, patch) {
  try {
    adminUsersError = "";
    await api("/api/admin/users/update", { method: "POST", body: JSON.stringify({ email, ...patch }) });
    await loadAdminUsers();
    if (currentRoute.view === "admin-users") renderAdminUsersView();
  } catch (error) {
    if (isStaleSessionError(error)) return;
    adminUsersError = error.message;
    if (currentRoute.view === "admin-users") renderAdminUsersView();
  }
}

async function adminResetPassword(email) {
  if (!window.confirm(`重設 ${email} 的密碼？對方下次登入需要重新設定。`)) return;
  try {
    adminUsersError = "";
    await api("/api/admin/users/reset-password", { method: "POST", body: JSON.stringify({ email }) });
    await loadAdminUsers();
    if (currentRoute.view === "admin-users") renderAdminUsersView();
  } catch (error) {
    if (isStaleSessionError(error)) return;
    adminUsersError = error.message;
    if (currentRoute.view === "admin-users") renderAdminUsersView();
  }
}

async function submitAdminUser(event) {
  event.preventDefault();
  const session = captureSessionContext();
  const email = ($("adminUserEmail") && $("adminUserEmail").value.trim()) || "";
  const displayName = ($("adminUserName") && $("adminUserName").value.trim()) || "";
  const role = ($("adminUserRole") && $("adminUserRole").value) || "user";
  try {
    adminUsersError = "";
    const data = await api("/api/admin/users", {
      method: "POST",
      body: JSON.stringify({ email, displayName, role }),
    });
    assertSessionContext(session);
    const createdEmail = data.user && data.user.email ? data.user.email : email.toLowerCase();
    adminUsersMessage = `已新增 ${createdEmail}。還需要在 Cloudflare Access 政策加入這個 email，對方才能通過信箱驗證。`;
    await loadAdminUsers();
    assertSessionContext(session);
    if (currentRoute.view === "admin-users" && sheetStack.length && sheetStack[sheetStack.length - 1].id === "admin-user-create") {
      dismissTopSheet(() => {
        if (currentRoute.view === "admin-users") renderAdminUsersView();
      });
    } else if (currentRoute.view === "admin-users") {
      renderAdminUsersView();
    }
  } catch (error) {
    if (isStaleSessionError(error)) return;
    adminUsersError = error.message;
    const formError = $("adminUserFormError");
    if (formError) formError.textContent = error.message;
    else if (currentRoute.view === "admin-users") renderAdminUsersView();
  }
}

function renderAdminUserForm() {
  return h("form", { class: "admin-add-form", onsubmit: submitAdminUser }, [
    h("label", { class: "field" }, [
      h("span", { text: "Email" }),
      h("input", { id: "adminUserEmail", type: "email", required: true, autocomplete: "off", placeholder: "name@example.com" }),
    ]),
    h("label", { class: "field" }, [
      h("span", { text: "顯示名稱" }),
      h("input", { id: "adminUserName", type: "text", required: true, autocomplete: "off", placeholder: "使用者名稱" }),
    ]),
    h("label", { class: "field" }, [
      h("span", { text: "權限" }),
      h("select", { id: "adminUserRole" }, [
        h("option", { value: "user", text: "使用者" }),
        h("option", { value: "admin", text: "管理員" }),
      ]),
    ]),
    h("div", { id: "adminUserFormError", class: "admin-error-inline", role: "alert", "aria-live": "assertive" }),
    h("button", { class: "primary", type: "submit", text: "新增使用者" }),
  ]);
}

function openAdminUserSheet(opener) {
  adminUsersError = "";
  openSheet({
    id: "admin-user-create",
    title: "新增使用者",
    opener,
    className: "admin-user-sheet",
    content: h("div", { class: "admin-user-create" }, [
      h("p", { class: "panel-sub", text: "建立平台帳號後，仍需把 Email 加入 Cloudflare Access 政策。" }),
      renderAdminUserForm(),
    ]),
    onOpen: () => {
      if (!window.matchMedia("(max-width: 767px)").matches) requestAnimationFrame(() => $("adminUserEmail")?.focus({ preventScroll: true }));
    },
  });
}

function renderRecentJobs(user) {
  const jobs = user.recentJobs || [];
  return h("details", { class: "admin-recent" }, [
    h("summary", { text: `最近任務 ${jobs.length}` }),
    jobs.length ? h("div", { class: "admin-job-list" }, jobs.map((job) => h("div", { class: "admin-job-row" }, [
      h("span", { text: job.feature || "生成" }),
      h("span", { text: job.characterId || "-" }),
      h("span", { text: formatUserDate(job.ts) }),
    ]))) : h("div", { class: "admin-empty", text: "沒有任務紀錄" }),
  ]);
}

function renderAdminUserRow(user) {
  return h("div", { class: "admin-user-row" }, [
    h("div", { class: "admin-user-main admin-user-cell", dataset: { label: "使用者" } }, [
      h("strong", { text: user.displayName || user.email }),
      h("span", { text: user.email }),
    ]),
    h("div", { class: "admin-user-cell", dataset: { label: "設定權限" } }, [h("select", {
        "aria-label": `設定${user.displayName || user.email}的權限`,
        onchange: (event) => adminUserUpdate(user.email, { role: event.target.value }),
      }, [
        h("option", { value: "user", text: "使用者", selected: user.role === "user" }),
        h("option", { value: "admin", text: "管理員", selected: user.role === "admin" }),
      ])]),
    h("div", { class: "admin-user-cell", dataset: { label: "目前權限" } }, [h("span", { class: `role-badge ${user.role === "admin" ? "admin" : "user"}`, text: user.role === "admin" ? "管理員" : "使用者" })]),
    h("div", { class: "admin-user-cell", dataset: { label: "狀態" } }, [h("span", { class: `state-badge ${user.disabled ? "off" : "on"}`, text: user.disabled ? "停用" : "啟用" })]),
    h("div", { class: "admin-user-cell", dataset: { label: "密碼" }, text: user.hasPassword ? "已設定" : "待設定" }),
    h("div", { class: "admin-user-cell", dataset: { label: "建立時間" }, text: formatUserDate(user.created) }),
    h("div", { class: "admin-row-actions admin-user-cell", dataset: { label: "操作" } }, [
      h("button", { class: "ghost small", type: "button", text: user.disabled ? "啟用" : "停用", onclick: () => adminUserUpdate(user.email, { disabled: !user.disabled }) }),
      h("button", { class: "ghost small", type: "button", text: "重設密碼", onclick: () => adminResetPassword(user.email) }),
    ]),
    renderRecentJobs(user),
  ]);
}

async function renderAdminUsersView() {
  const ws = beginWorkspaceRender();
  if (!adminUsers.length && !adminUsersError) await loadAdminUsers();
  ws.appendChild(h("div", { class: "admin-users-view" }, [
    h("section", { class: "panel" }, [
      h("div", { class: "panel-head-row" }, [
        h("div", {}, [
          h("h1", { class: "panel-h1", text: "使用者管理" }),
          h("div", { class: "panel-sub", text: "管理 Castle 平台帳號、角色與初次密碼設定狀態。" }),
        ]),
        h("div", { class: "section-header-actions" }, [
          h("button", { class: "primary small admin-add-trigger", type: "button", text: "新增使用者", "aria-haspopup": "dialog", "aria-expanded": "false", onclick: (event) => openAdminUserSheet(event.currentTarget) }),
          h("button", {
            class: "ghost small",
            type: "button",
            text: "更新",
            onclick: async () => {
              await loadAdminUsers();
              if (currentRoute.view === "admin-users") renderAdminUsersView();
            },
          }),
        ]),
      ]),
      adminUsersMessage ? h("div", { class: "admin-message", text: adminUsersMessage }) : null,
      adminUsersError ? h("div", { class: "admin-error", text: adminUsersError }) : null,
      h("div", { class: "admin-user-head" }, [
        h("span", { text: "使用者" }),
        h("span", { text: "設定權限" }),
        h("span", { text: "目前權限" }),
        h("span", { text: "狀態" }),
        h("span", { text: "密碼" }),
        h("span", { text: "建立時間" }),
        h("span", { text: "操作" }),
      ]),
      h("div", { class: "admin-user-list" }, adminUsers.map(renderAdminUserRow)),
    ]),
  ]));
}

// ---- consumer 任務 tray ---------------------------------------------------
function taskTimestampMs(job) {
  for (const raw of [job.timestamp, job.modified, job.updated, job.created, job.ts, job.localTimestamp]) {
    const numeric = Number(raw);
    if (Number.isFinite(numeric) && numeric > 0) return numeric < 1e12 ? numeric * 1000 : numeric;
    const parsed = typeof raw === "string" ? Date.parse(raw) : 0;
    if (Number.isFinite(parsed) && parsed > 0) return parsed;
  }
  return 0;
}

function relativeTaskTime(job) {
  const timestamp = taskTimestampMs(job);
  if (!timestamp) return "時間未知";
  const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
  if (seconds < 60) return "剛剛";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分鐘前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小時前`;
  return `${Math.floor(seconds / 86400)} 天前`;
}

function annotateServerJob(job) {
  const annotation = activeJobsTable().find((item) => item.promptId === job.promptId) || {};
  const character = characters.find((item) => item.id === (job.characterId || annotation.characterId));
  const presentedError = job.state === "error" && !job.errorCode
    ? runtimeGenerationErrorPresentation(job.message || job.error, job.type)
    : null;
  return {
    ...job,
    ...(presentedError ? { message: presentedError.message, errorCode: presentedError.code } : {}),
    characterId: job.characterId || annotation.characterId || "",
    characterName: job.characterName || (character && character.name) || "",
    actionId: job.actionId || annotation.action || "",
    actionLabel: job.actionLabel || actionVerb(job.actionId || annotation.action || "") || "",
    localTimestamp: Number(annotation.ts || 0),
  };
}

function terminalTaskRows() {
  const cutoff = Date.now() - ACTIVE_JOB_ERROR_TTL_MS;
  const rows = new Map();
  for (const job of [...terminalJobs, ...queueTerminalJobs]) {
    const row = annotateServerJob(job);
    const timestamp = taskTimestampMs(row);
    if (!job.promptId || !timestamp || timestamp < cutoff) continue;
    rows.set(job.promptId, row);
  }
  for (const error of queueRecentErrors) {
    const row = annotateServerJob({ ...rows.get(error.promptId), ...error, state: "error" });
    const timestamp = taskTimestampMs(row);
    if (!error.promptId || !timestamp || timestamp < cutoff) continue;
    rows.set(error.promptId, row);
  }
  return Array.from(rows.values()).sort((a, b) => taskTimestampMs(b) - taskTimestampMs(a)).slice(0, 20);
}

function taskTitle(job) {
  const character = job.characterName || "";
  const action = job.actionLabel || actionVerb(job.actionId || "");
  if (character) return `${character} · ${action || "生成"}`;
  const source = job.source === "external" ? "外部來源" : (job.source === "orchestrator" ? "工作流" : "工具");
  return `${source} · ${job.feature || job.workflow || "生成"}`;
}

function taskStateLabel(job) {
  if (job.state === "running") return "執行中";
  if (job.state === "cancel_requested") return "取消中";
  if (job.state === "pending") return "等待中";
  if (job.state === "completed") return "完成";
  if (job.state === "cancelled") return "已取消";
  return "失敗";
}

function taskResultForPrompt(promptId) {
  return historyItems.find((item) => item.promptId === promptId) || null;
}

function openTaskResult(job) {
  const item = taskResultForPrompt(job.promptId);
  if (!item) {
    showAlert(job.state === "error" ? (job.message || job.error || "任務失敗") : "結果仍在同步，請稍後重試。", true);
    return;
  }
  openResultDetail(item, $("statusPill"));
}

function renderConsumerTaskRow(serverJob, terminal = false) {
  const job = annotateServerJob(serverJob);
  const progress = jobProgress(job);
  const cancellable = isAdmin() && (job.state === "running" || job.state === "pending");
  const title = h("button", {
    class: "consumer-task-title",
    type: "button",
    text: taskTitle(job),
    disabled: !terminal || job.state !== "completed" || !taskResultForPrompt(job.promptId),
    onclick: () => openTaskResult(job),
  });
  return h("article", { class: `consumer-task-row ${job.state || "pending"}`, dataset: { promptId: job.promptId, terminal: terminal ? "true" : "false" } }, [
    h("div", { class: "consumer-task-main" }, [
      title,
      h("div", { class: "consumer-task-meta" }, [
        h("span", { class: `consumer-task-state ${job.state || "pending"}`, text: taskStateLabel(job) }),
        h("span", { text: terminal ? relativeTaskTime(job) : `${relativeTaskTime(job)} · #${shortPromptId(job.promptId)}` }),
      ]),
      job.state === "error" ? h("p", { class: "consumer-task-error", text: job.message || job.error || "生成失敗" }) : null,
    ]),
    !terminal ? h("div", { class: "consumer-task-progress" }, [
      h("span", { text: progress.label }),
      h("div", { class: "job-bar", role: "progressbar", "aria-label": progress.label, "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(progress.pct) }, [
        h("span", { style: `width:${progress.pct}%` }),
      ]),
      cancellable ? h("button", { class: `job-cancel ${job.state === "running" ? "danger" : ""}`, type: "button", text: job.state === "running" ? "中止" : "取消", onclick: () => cancelJob(job) }) : null,
    ]) : null,
  ]);
}

function markRenderedTerminalsSeen(rows) {
  if (!rows.length) return;
  const seen = taskSeenTable();
  const now = Date.now();
  for (const row of rows) seen[row.promptId] = now;
  if (taskScope === "all" || !isAdmin()) {
    const serverIds = new Set(terminalTaskRows().map((row) => row.promptId));
    for (const promptId of Object.keys(seen)) if (!serverIds.has(promptId)) delete seen[promptId];
  }
  saveTaskSeen(seen);
}

function markMountedTerminalsSeen(dialog) {
  if (!dialog || !dialog.isConnected) return;
  const mountedIds = new Set(Array.from(dialog.querySelectorAll('.consumer-task-row[data-terminal="true"]')).map((row) => row.dataset.promptId).filter(Boolean));
  const mountedRows = terminalTaskRows().filter((row) => mountedIds.has(row.promptId));
  markRenderedTerminalsSeen(mountedRows);
  renderTaskChip();
}

function renderTaskTrayContent() {
  const activeRows = queueJobs.map(annotateServerJob);
  const terminals = terminalTaskRows();
  const wrap = h("div", { class: "consumer-task-tray" });
  if ($("servicePill").classList.contains("error")) {
    wrap.appendChild(h("div", { class: "task-recovery" }, [
      h("span", { text: "任務服務暫時無法連線，草稿與目前畫面已保留。" }),
      h("button", { class: "ghost", type: "button", text: "重新連線", onclick: refreshAllData }),
    ]));
  }
  if (isAdmin()) {
    wrap.appendChild(h("div", { class: "task-scope", role: "group", "aria-label": "任務範圍" }, [
      h("button", { class: taskScope === "own" ? "active" : "", type: "button", text: "我的任務", "aria-pressed": String(taskScope === "own"), onclick: () => changeTaskScope("own") }),
      h("button", { class: taskScope === "all" ? "active" : "", type: "button", text: "全部任務", "aria-pressed": String(taskScope === "all"), onclick: () => changeTaskScope("all") }),
    ]));
  }
  wrap.appendChild(h("section", { class: "task-section" }, [
    h("h3", { text: "進行中" }),
    activeRows.length ? h("div", { class: "consumer-task-list" }, activeRows.map((job) => renderConsumerTaskRow(job))) : h("div", { class: "task-empty", text: "目前沒有進行中的任務。" }),
  ]));
  wrap.appendChild(h("section", { class: "task-section" }, [
    h("h3", { text: "最近 48 小時" }),
    terminals.length ? h("div", { class: "consumer-task-list" }, terminals.map((job) => renderConsumerTaskRow(job, true))) : h("div", { class: "task-empty", text: "最近沒有完成或失敗的任務。" }),
  ]));
  return wrap;
}

async function changeTaskScope(scope) {
  if (scope === "all" && !isAdmin()) return;
  const session = captureSessionContext();
  taskScope = scope;
  queueJobs = [];
  queueTerminalJobs = [];
  terminalJobs = [];
  queueRecentErrors = [];
  queueSummary = { running: 0, pending: 0, total: 0, external: 0 };
  queueSnapshotLoaded = false;
  historySnapshotLoaded = false;
  trackedPromptMissing = { promptId: "", since: 0 };
  renderTaskChip();
  refreshOpenTaskTray();
  await Promise.all([refreshQueue(), refreshHistory()]);
  assertSessionContext(session);
  refreshOpenTaskTray();
}

function openTaskTray(opener) {
  const content = renderTaskTrayContent();
  openSheet({
    id: "tasks",
    title: `任務 ${queueSummary.running + queueSummary.pending}`,
    opener,
    content,
    className: "task-sheet",
    onOpen: ({ dialog }) => markMountedTerminalsSeen(dialog),
  });
}

function refreshOpenTaskTray() {
  const entry = sheetStack[sheetStack.length - 1];
  if (!entry || entry.id !== "tasks") return;
  const body = entry.dialog.querySelector(".sheet-body");
  if (!body) return;
  const active = document.activeElement;
  let focusTarget = null;
  if (active && entry.dialog.contains(active)) {
    const row = active.closest && active.closest(".consumer-task-row");
    const scope = active.closest && active.closest(".task-scope");
    if (row) {
      focusTarget = {
        type: "row",
        promptId: row.dataset.promptId || "",
        action: active.classList.contains("consumer-task-title") ? "title" : (active.classList.contains("job-cancel") ? "cancel" : "row"),
      };
    } else if (scope && active.matches("button")) {
      focusTarget = { type: "scope", index: Array.from(scope.querySelectorAll("button")).indexOf(active) };
    } else if (active.closest && active.closest(".task-recovery")) {
      focusTarget = { type: "recovery" };
    }
  }
  const content = renderTaskTrayContent();
  const current = body.firstElementChild;
  const oldRows = current ? Array.from(current.querySelectorAll(".consumer-task-row")) : [];
  const nextRows = Array.from(content.querySelectorAll(".consumer-task-row"));
  const oldIds = oldRows.map((row) => row.dataset.promptId).join("\n");
  const nextIds = nextRows.map((row) => row.dataset.promptId).join("\n");
  if (current && oldIds === nextIds) {
    oldRows.forEach((row, index) => row.parentElement.replaceChild(nextRows[index], row));
    const oldScope = current.querySelector(".task-scope");
    const nextScope = content.querySelector(".task-scope");
    if (oldScope && nextScope) oldScope.parentElement.replaceChild(nextScope, oldScope);
    const oldRecovery = current.querySelector(".task-recovery");
    const nextRecovery = content.querySelector(".task-recovery");
    if (oldRecovery && nextRecovery) oldRecovery.parentElement.replaceChild(nextRecovery, oldRecovery);
    else if (oldRecovery && !nextRecovery) oldRecovery.remove();
    else if (!oldRecovery && nextRecovery) current.insertBefore(nextRecovery, current.firstChild);
  } else if (current) body.replaceChild(content, current);
  else body.appendChild(content);
  markMountedTerminalsSeen(entry.dialog);
  if (focusTarget) {
    let replacement = null;
    if (focusTarget.type === "row") {
      const row = Array.from(entry.dialog.querySelectorAll(".consumer-task-row")).find((item) => item.dataset.promptId === focusTarget.promptId);
      if (row) {
        replacement = focusTarget.action === "title"
          ? row.querySelector(".consumer-task-title")
          : (focusTarget.action === "cancel" ? row.querySelector(".job-cancel") : row);
      }
    } else if (focusTarget.type === "scope") {
      replacement = entry.dialog.querySelectorAll(".task-scope button")[focusTarget.index] || null;
    } else if (focusTarget.type === "recovery") {
      replacement = entry.dialog.querySelector(".task-recovery button");
    }
    (replacement || entry.dialog.querySelector(".sheet-close"))?.focus({ preventScroll: true });
  }
}

function renderTaskChip() {
  const pill = $("statusPill");
  const count = $("taskCount");
  const dot = $("taskUnreadDot");
  if (!pill || !count || !dot) return;
  const total = Number(queueSummary.running || 0) + Number(queueSummary.pending || 0);
  count.textContent = String(total);
  pill.className = `status-pill ${total ? "busy" : "idle"}`;
  pill.setAttribute("aria-label", `開啟任務，${total} 個進行中`);
  const seen = taskSeenTable();
  const unseen = terminalTaskRows().filter((row) => !seen[row.promptId]);
  const hasFail = unseen.some((row) => row.state === "error");
  const hasDone = unseen.some((row) => row.state === "completed");
  dot.className = `task-unread-dot${hasFail ? " fail" : (hasDone ? " done" : " hidden")}`;
  if (hasFail) pill.setAttribute("aria-label", `${pill.getAttribute("aria-label")}，有未讀失敗`);
  else if (hasDone) pill.setAttribute("aria-label", `${pill.getAttribute("aria-label")}，有未讀完成`);
}

// ---- jobs status ----------------------------------------------------------
function shortPromptId(id) {
  return id ? String(id).slice(0, 8) : "unknown";
}

function jobEvent(promptId) {
  return jobEvents.get(promptId) || {};
}

function updateJobEvent(message) {
  const data = message && message.data ? message.data : {};
  const promptId = data.prompt_id || data.promptId;
  if (!promptId) return;
  const current = jobEvents.get(promptId) || {};
  current.type = message.type || current.type || "";
  current.updated = Date.now();
  if (message.type === "progress") {
    current.value = Number(data.value || 0);
    current.max = Number(data.max || 0);
    current.node = data.node || current.node || "";
  } else if (message.type === "executing") {
    current.node = data.node || "";
    current.executing = Boolean(data.node);
  } else if (message.type === "execution_start") {
    current.started = true;
  } else if (message.type === "execution_error") {
    current.error = runtimeGenerationErrorPresentation(
      data.exception_message,
      data.exception_type,
    ).message;
  } else if (message.type === "execution_success" || message.type === "executed") {
    current.done = true;
  }
  jobEvents.set(promptId, current);
  syncTrackedGenerationFromServer();
}

function trackedPromptServerPresence(promptId) {
  if (!promptId) return "";
  if (queueJobs.some((job) => job && job.promptId === promptId)) return "queue";
  if (queueRecentErrors.some((job) => job && job.promptId === promptId) || jobEvent(promptId).error) return "error";
  if ([...terminalJobs, ...queueTerminalJobs].some((job) => job && job.promptId === promptId)) return "terminal";
  if (historyItems.some((item) => item && item.promptId === promptId)) return "history";
  return "";
}

function resetTrackedPromptMissing(promptId = "") {
  if (!promptId || trackedPromptMissing.promptId === promptId) {
    trackedPromptMissing = { promptId: "", since: 0 };
  }
}

function trackedPromptMissingExpired(promptId, now = Date.now()) {
  if (!promptId || trackedPromptServerPresence(promptId)) {
    resetTrackedPromptMissing(promptId);
    return false;
  }
  if (
    !queueSnapshotLoaded
    || !historySnapshotLoaded
    || queueRefreshesInFlight > 0
    || historyRefreshesInFlight > 0
  ) return false;
  const recentEventAt = Math.min(now, Number(jobEvent(promptId).updated || 0));
  if (trackedPromptMissing.promptId !== promptId) {
    trackedPromptMissing = { promptId, since: Math.max(now, recentEventAt) };
    return false;
  }
  if (recentEventAt > trackedPromptMissing.since) {
    trackedPromptMissing = { promptId, since: recentEventAt };
    return false;
  }
  return now - trackedPromptMissing.since >= TRACKED_JOB_MISSING_GRACE_MS;
}

function syncTrackedGenerationFromServer() {
  if (!trackedPromptId || !composerBinding) {
    resetTrackedPromptMissing();
    return;
  }
  if (generationFsm.composerKey !== composerBinding.key) return;
  const promptId = trackedPromptId;
  const queued = queueJobs.find((job) => job.promptId === promptId);
  const error = queueRecentErrors.find((job) => job.promptId === promptId);
  const eventError = jobEvent(promptId).error;
  if (error || eventError) {
    resetTrackedPromptMissing(promptId);
    const message = (error && (error.message || error.type)) || eventError || "生成失敗";
    setGenerationFsm("error", { promptId, detail: message, reason: message });
    return;
  }
  if (queued) {
    resetTrackedPromptMissing(promptId);
    const annotation = activeJobsTable().find((job) => job.promptId === promptId) || {};
    const progress = jobProgress({ ...annotation, ...queued });
    if (queued.state === "pending") {
      setGenerationFsm("queued", { promptId, detail: "已加入佇列 · 等待中", pct: 0 });
    } else {
      const label = annotation.chainedJob ? characterJobLabel({ ...annotation, ...queued }) : "執行中";
      setGenerationFsm("progress", { promptId, detail: `${label} ${progress.pct}%`, pct: progress.pct });
    }
    return;
  }
  const annotation = activeJobsTable().find((job) => job.promptId === promptId) || {};
  const chainStage = annotation.chainedJob && annotation.chainedJob.stage;
  const output = historyItems.find((item) => item && item.promptId === promptId);
  if (output && chainStage !== "voice" && chainStage !== "continuing") {
    resetTrackedPromptMissing(promptId);
    setGenerationFsm("done", {
      promptId,
      resultPromptId: promptId,
      detail: "完成",
      pct: 100,
      composerKey: composerBinding.key,
    });
    return;
  }
  const terminal = terminalJobForPrompt(promptId);
  if (!terminal) {
    if (!trackedPromptMissingExpired(promptId)) return;
    const message = "伺服器已無法確認這個任務，可能已中斷或紀錄已清除。你可以重試。";
    updateActiveJob(promptId, { state: "error", error: message });
    setGenerationFsm("error", {
      promptId,
      detail: message,
      reason: "任務狀態失聯",
      composerKey: composerBinding.key,
    }, { force: true });
    trackedPromptId = "";
    resetTrackedPromptMissing(promptId);
    return;
  }
  resetTrackedPromptMissing(promptId);
  if (terminal.state === "cancelled") {
    updateActiveJob(promptId, {
      state: "cancelled",
      error: "",
      chainedJob: annotation.chainedJob ? { ...annotation.chainedJob, stage: "cancelled" } : annotation.chainedJob,
    });
    setGenerationFsm("error", {
      promptId,
      detail: "已取消，可重新生成。",
      reason: "已取消",
      composerKey: composerBinding.key,
    }, { force: true });
    trackedPromptId = "";
    return;
  }
  if (chainStage === "voice" || chainStage === "continuing") return;
  if (chainStage === "error" || annotation.state === "error" || terminal.state === "error") {
    const message = annotation.error || terminal.message || "生成失敗";
    setGenerationFsm("error", { promptId, detail: message, reason: message, composerKey: composerBinding.key }, { force: true });
    return;
  }
  if (terminal.state === "completed" && (!annotation.chainedJob || !chainStage || chainStage === "lipsync")) {
    setGenerationFsm("done", { promptId, resultPromptId: promptId, detail: "完成", pct: 100, composerKey: composerBinding.key });
  }
}

function jobProgress(job) {
  if (job.state === "error") {
    const message = job.errorCode && job.message
      ? job.message
      : runtimeGenerationErrorPresentation(job.message || job.error, job.type).message;
    return { label: `失敗：${message}`, pct: 100, node: "" };
  }
  if (job.chainedJob && job.chainedJob.stage === "voice" && !queueJobs.some((item) => item.promptId === job.promptId)) {
    return { label: "等待語音輸出", pct: 55, node: "" };
  }
  if (job.chainedJob && job.chainedJob.stage === "continuing") {
    return { label: "送出對嘴中", pct: 70, node: "" };
  }
  if (job.progress) {
    return {
      label: job.progress.label || (job.state === "completed" ? "完成" : "執行中"),
      pct: Math.max(0, Math.min(100, Number(job.progress.pct || 0))),
      node: job.progress.node || "",
    };
  }
  if (job.state === "completed") return { label: "完成", pct: 100, node: "" };
  if (job.state === "cancelled") return { label: "已取消", pct: 100, node: "" };
  const event = jobEvent(job.promptId);
  if (job.state === "pending") {
    return { label: `等待第 ${job.position}`, pct: 0, node: "" };
  }
  if (event.error) {
    return { label: `失敗：${event.error}`, pct: 100, node: event.node || "" };
  }
  if (event.max > 0) {
    const pct = Math.max(0, Math.min(100, Math.round((event.value / event.max) * 100)));
    return { label: `${event.value}/${event.max}・${pct}%`, pct, node: event.node || "" };
  }
  if (event.node) {
    return { label: "執行中", pct: 18, node: event.node };
  }
  return { label: "執行中，等待進度事件", pct: 10, node: "" };
}

function renderJobRow(job) {
  const progress = jobProgress(job);
  const nodeType = progress.node && job.nodeTypes ? job.nodeTypes[progress.node] : "";
  const source = job.source === "external" ? "外部" : (job.source === "orchestrator" ? "工作流" : "Castle");
  const cancelText = job.state === "running" ? "中止" : "取消";
  const stateLabels = {
    running: "執行中",
    cancel_requested: "取消中",
    pending: `等待 ${job.position || ""}`.trim(),
    completed: "完成",
    error: "失敗",
    cancelled: "已取消",
  };
  const cancellable = job.state === "running" || job.state === "pending";
  return h("div", { class: `job-row ${job.state} ${job.source}` }, [
    h("div", { class: "job-main" }, [
      h("div", { class: "job-title-row" }, [
        h("span", { class: `job-state ${job.state}`, text: stateLabels[job.state] || job.state || "任務" }),
        h("span", { class: "job-title", text: job.feature || "ComfyUI job" }),
        job.source === "external" ? h("span", { class: "job-source", text: "外部" }) : null,
        job.source === "orchestrator" ? h("span", { class: "job-source", text: job.lane === "gpu" ? "GPU lane" : "工作流" }) : null,
      ]),
      h("div", { class: "job-meta", text: `#${shortPromptId(job.promptId)}・${source}・${job.nodeCount || 0} nodes・${job.outputCount || 0} outputs` }),
      progress.node ? h("div", { class: "job-node", text: `目前節點 #${progress.node}${nodeType ? ` ${nodeType}` : ""}` }) : null,
    ]),
    h("div", { class: "job-progress" }, [
      h("div", { class: "job-progress-label", text: progress.label }),
      h("div", { class: "job-bar" }, [
        h("span", { style: `width:${progress.pct}%` }),
      ]),
      cancellable ? h("button", {
        class: `job-cancel ${job.state === "running" ? "danger" : ""}`,
        type: "button",
        text: cancelText,
        onclick: () => cancelJob(job),
      }) : null,
    ]),
  ]);
}

async function cancelJob(job) {
  const verb = job.state === "running" ? "中止正在執行的" : "取消等待中的";
  const note = job.state === "running" ? "正在執行的節點可能要等到下一個可中斷點才會停。" : "等待中的任務會直接從佇列移除。";
  if (!window.confirm(`${verb}「${job.feature || "ComfyUI job"}」？\n\n${note}`)) return;
  try {
    if (job.source === "orchestrator") {
      await api(`/api/orchestrator/runs/${encodeURIComponent(job.promptId)}/cancel`, { method: "POST", body: "{}" });
    } else {
      await api("/api/jobs/cancel", {
        method: "POST",
        body: JSON.stringify({ promptId: job.promptId, state: job.state }),
      });
    }
    showAlert(job.state === "running" ? "已送出中止要求。" : "已取消等待中的任務。");
    await refreshQueue();
    await refreshOrchestrator({ silent: true });
  } catch (error) {
    showAlert(`取消失敗：${error.message}`, true);
  }
}

function renderQueuePanel() {
  const panel = $("queuePanel");
  if (!panel) return;
  clear(panel);
  panel.classList.toggle("hidden", !queuePanelOpen || !isAdmin());
  if (!isAdmin()) return;
  const total = queueSummary.total || queueJobs.length;
  const running = queueSummary.running || queueJobs.filter((job) => ["running", "cancel_requested"].includes(job.state)).length;
  const pending = queueSummary.pending || queueJobs.filter((job) => job.state === "pending").length;
  const external = queueSummary.external || queueJobs.filter((job) => job.source === "external").length;
  panel.appendChild(h("div", { class: "queue-head" }, [
    h("div", {}, [
      h("div", { class: "queue-title", text: "工作佇列" }),
      h("div", { class: "queue-sub", text: `全部 ${total}・執行 ${running}・等待 ${pending}・外部 ${external}` }),
    ]),
    h("button", { class: "ghost small", type: "button", text: "更新佇列", onclick: refreshQueue }),
  ]));
  const list = h("div", { class: "job-list" });
  if (queueJobs.length) {
    for (const job of queueJobs) list.appendChild(renderJobRow(job));
  } else {
    list.appendChild(h("div", { class: "queue-empty", text: "目前沒有執行中或排隊中的 ComfyUI job。" }));
  }
  panel.appendChild(list);
  if (queueRecentErrors.length) {
    const newest = queueRecentErrors[0];
    panel.appendChild(h("div", { class: "queue-error-line", text: `最近失敗：${newest.feature || newest.node || "ComfyUI"}・${newest.message || newest.type || ""}` }));
  }
}

function startQueueEvents() {
  if (!currentUser || !authStatus.authenticated) return;
  queueEventsWanted = true;
  if (document.hidden) return;
  if (queueReconnectTimer) {
    clearTimeout(queueReconnectTimer);
    queueReconnectTimer = null;
  }
  if (queueEventSource) return;
  queueEventSource = new EventSource("/api/events");
  queueEventSource.onmessage = (event) => {
    try {
      const message = JSON.parse(event.data);
      updateJobEvent(message);
      renderQueuePanel();
      refreshOpenTaskTray();
    } catch (_) {
      /* ignore malformed event */
    }
  };
  queueEventSource.onerror = () => {
    if (queueEventSource) {
      queueEventSource.close();
      queueEventSource = null;
    }
    if (queueEventsWanted && !document.hidden && currentUser && authStatus.authenticated) {
      queueReconnectTimer = setTimeout(() => {
        queueReconnectTimer = null;
        if (queueEventsWanted && !document.hidden && currentUser && authStatus.authenticated) startQueueEvents();
      }, 3000);
    }
  };
}

function pauseQueueEventsForHiddenPage() {
  if (queueReconnectTimer) {
    clearTimeout(queueReconnectTimer);
    queueReconnectTimer = null;
  }
  if (queueEventSource) {
    queueEventSource.close();
    queueEventSource = null;
  }
}

async function refreshQueue() {
  const session = captureSessionContext();
  const requestedScope = taskScope;
  let snapshotCompleted = false;
  queueRefreshesInFlight += 1;
  try {
    const data = await api(`/api/queue?scope=${encodeURIComponent(requestedScope)}`);
    assertSessionContext(session);
    if (requestedScope !== taskScope) return;
    const serverJobs = data.jobs || [];
    queueJobs = serverJobs.filter((job) => ["running", "pending", "cancel_requested"].includes(job.state));
    queueTerminalJobs = serverJobs.filter((job) => ["completed", "error", "cancelled"].includes(job.state));
    queueSummary = data.summary || {
      running: queueJobs.filter((job) => ["running", "cancel_requested"].includes(job.state)).length,
      pending: queueJobs.filter((job) => job.state === "pending").length,
      total: queueJobs.length,
      external: queueJobs.filter((job) => job.source === "external").length,
    };
    queueRecentErrors = data.recent_errors || [];
    queueSnapshotLoaded = true;
    snapshotCompleted = true;
    reconcileActiveJobs();
    const running = queueSummary.running || 0;
    const pending = queueSummary.pending || 0;
    queueSummary.running = running;
    queueSummary.pending = pending;
    renderTaskChip();
    refreshOpenTaskTray();
    syncTrackedGenerationFromServer();
    renderQueuePanel();
    setServiceStatus(true, "服務正常");
  } catch (error) {
    if (isStaleSessionError(error)) return;
    queueSnapshotLoaded = false;
    setServiceStatus(false, "服務連線異常");
    renderTaskChip();
    renderQueuePanel();
  } finally {
    queueRefreshesInFlight = Math.max(0, queueRefreshesInFlight - 1);
    if (snapshotCompleted) syncTrackedGenerationFromServer();
  }
}

function scheduleHistoryRefresh(delay = 1500) {
  const session = captureSessionContext();
  const timer = setTimeout(() => {
    historyRefreshTimers.delete(timer);
    if (document.hidden || !routeNeedsHistory(currentRoute)
      || !sessionContextIsCurrent(session) || !currentUser || !authStatus.authenticated) return;
    runForegroundRefresh("history", () => refreshHistory({ auto: true }));
  }, delay);
  historyRefreshTimers.add(timer);
}

async function refreshHistory(options = {}) {
  const session = captureSessionContext();
  if (options.auto && (document.hidden || !routeNeedsHistory(currentRoute) || mediaIsPlaying())) return;
  const requestedScope = taskScope;
  const hadHistorySnapshot = historySnapshotLoaded;
  let snapshotCompleted = false;
  historyRefreshesInFlight += 1;
  historyLoadError = "";
  try {
    if (!historySnapshotLoaded) paintHistory();
    const taskRequest = api(`/api/history?max_items=500&${requestedScope === "all" ? "balanced=1&" : ""}scope=${encodeURIComponent(requestedScope)}`);
    // The task tray can stay owner-only while the admin gallery includes
    // legacy outputs. Background refreshes must use that same gallery scope.
    const galleryRequest = isAdmin() && requestedScope === "own"
      ? api("/api/history?max_items=500&balanced=1&scope=all")
      : taskRequest;
    const [data, galleryData] = await Promise.all([taskRequest, galleryRequest]);
    assertSessionContext(session);
    if (requestedScope !== taskScope) return;
    const nextItems = galleryData.images || [];
    terminalJobs = data.jobs || [];
    const errors = new Map(queueRecentErrors.map((item) => [item.promptId, item]));
    for (const item of data.recent_errors || []) errors.set(item.promptId, item);
    queueRecentErrors = Array.from(errors.values()).filter((item) => item && item.promptId);
    historySnapshotLoaded = true;
    snapshotCompleted = true;
    renderTaskChip();
    refreshOpenTaskTray();
    const nextFingerprint = historyFingerprint(nextItems);
    if (options.auto && nextFingerprint === lastHistoryFingerprint) {
      if (!hadHistorySnapshot) paintHistory();
      syncTrackedGenerationFromServer();
      return;
    }
    historyItems = nextItems;
    lastHistoryFingerprint = nextFingerprint;
    await reconcileChainedJobs();
    assertSessionContext(session);
    reconcileActiveJobs();
    syncTrackedGenerationFromServer();
    if (selectedHistoryKey && !historyItems.some((item) => historyKey(item) === selectedHistoryKey)) {
      selectedHistoryKey = "";
    }
    paintHistory();
    refreshHistoryDrivenSurfaces();
  } catch (error) {
    if (isStaleSessionError(error)) return;
    historySnapshotLoaded = false;
    historyLoadError = error.message;
    paintHistory();
    showAlert(`生成紀錄讀取失敗：${error.message}`, true);
  } finally {
    historyRefreshesInFlight = Math.max(0, historyRefreshesInFlight - 1);
    if (snapshotCompleted) syncTrackedGenerationFromServer();
  }
}

// ---- foreground refresh policy --------------------------------------------
function routeNeedsHistory(route = currentRoute) {
  if (route.view === "character-library") return false;
  if (route.view === "library" && !route.featureId) return false;
  return [
    "characters",
    "studio",
    "library",
    "character-workspace",
    "character-library",
    "provider-character-studio",
  ].includes(String(route && route.view || ""));
}

function runForegroundRefresh(key, callback) {
  if (document.hidden || !currentUser || !authStatus.authenticated || foregroundRefreshes.has(key)) {
    return Promise.resolve(false);
  }
  foregroundRefreshes.add(key);
  return Promise.resolve()
    .then(callback)
    .then(() => true)
    .finally(() => foregroundRefreshes.delete(key));
}

function refreshVisibleViewOnce() {
  if (document.hidden || !currentUser || !authStatus.authenticated) return;
  startQueueEvents();
  runForegroundRefresh("queue", refreshQueue);
  if (routeNeedsHistory(currentRoute)) {
    runForegroundRefresh("history", () => refreshHistory({ auto: true }));
  }
  if (currentView === "orchestrator") {
    runForegroundRefresh("orchestrator", () => refreshOrchestrator({ silent: true }));
  }
  if (activeCharacterId) {
    runForegroundRefresh("character-capabilities", refreshActiveCharacterCapabilities);
  }
  if (currentView === "studio" && currentFeatureId === "voice-batch") voiceBatchSchedulePoll();
}

function handleDocumentVisibilityChange() {
  if (document.hidden) {
    pauseQueueEventsForHiddenPage();
    voiceBatchStopPoll();
    return;
  }
  refreshVisibleViewOnce();
}

// ---- boot -----------------------------------------------------------------
async function startAppData() {
  const authenticated = await checkAuth();
  if (!authenticated) return;
  const session = captureSessionContext();
  await Promise.all([loadRegistry(), loadCharacters()]);
  assertSessionContext(session);
  if (routePath(currentRoute) !== routePath(parseRoute()) || !$("workspace").firstElementChild) applyRoute();
  refreshVisibleViewOnce();
  if (!window.castleTimers) {
    window.castleTimers = [
      setInterval(() => runForegroundRefresh("queue", refreshQueue), 4000),
      setInterval(() => runForegroundRefresh("orchestrator-events", pollOrchestratorEvents), 3000),
      setInterval(() => runForegroundRefresh("orchestrator", () => refreshOrchestrator({ silent: true })), 10000),
      setInterval(() => {
        if (routeNeedsHistory(currentRoute)) {
          runForegroundRefresh("history", () => refreshHistory({ auto: true }));
        }
      }, 12000),
      setInterval(() => runForegroundRefresh("character-capabilities", refreshActiveCharacterCapabilities), 15000),
    ];
  }
}

function primeRouteBeforePaint() {
  currentRoute = parseRoute();
  currentView = ["studio", "projects", "library", "resources", "workflows", "dance-library", "orchestrator", "admin-users", "admin"].includes(currentRoute.view) ? currentRoute.view : "characters";
  activeCharacterId = routeCharacterContextId(currentRoute);
  if (activeCharacterId) selectedCharacterId = activeCharacterId;
  if (currentRoute.view === "studio" && currentRoute.featureId) currentFeatureId = currentRoute.featureId;
  if (currentRoute.view === "orchestrator") {
    orchestratorMobilePane = currentRoute.pane || "overview";
    if (currentRoute.runId) selectedOrchestratorRunId = currentRoute.runId;
  }
  if (currentRoute.actionId) selectedCharacterActionId = currentRoute.actionId;
  document.body.dataset.view = currentView;
  syncViewTabs();
}

function updateVisualViewportState() {
  const viewport = window.visualViewport;
  if (viewport) document.documentElement.style.setProperty("--visual-viewport-height", `${viewport.height}px`);
  if (!composerHasFocus || !viewport) {
    document.body.classList.remove("kb-open");
    document.documentElement.style.setProperty("--visual-viewport-bottom", "0px");
    return;
  }
  const bottom = Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop);
  document.documentElement.style.setProperty("--visual-viewport-bottom", `${bottom}px`);
  document.body.classList.toggle("kb-open", bottom > 48);
}

function syncVisualViewportFocusState() {
  composerHasFocus = Boolean(
    document.activeElement
    && document.activeElement.matches
    && document.activeElement.matches("input, textarea, select, [contenteditable='true']")
  );
  updateVisualViewportState();
}

function scheduleVisualViewportFocusSync() {
  setTimeout(syncVisualViewportFocusState, 0);
}

function setupVisualViewport() {
  document.addEventListener("focusin", (event) => {
    if (!event.target.matches || !event.target.matches("input, textarea, select, [contenteditable='true']")) return;
    if (event.target.closest && event.target.closest(".gen-form")) {
      lastComposerFieldState = {
        focusId: event.target.id || "",
        selectionStart: typeof event.target.selectionStart === "number" ? event.target.selectionStart : null,
        selectionEnd: typeof event.target.selectionEnd === "number" ? event.target.selectionEnd : null,
      };
    }
    composerHasFocus = true;
    updateVisualViewportState();
  });
  document.addEventListener("focusout", (event) => {
    if (event.target && event.target.matches && event.target.matches("input, textarea, select") && event.target.closest(".gen-form")) {
      lastComposerFieldState = {
        focusId: event.target.id || "",
        selectionStart: typeof event.target.selectionStart === "number" ? event.target.selectionStart : null,
        selectionEnd: typeof event.target.selectionEnd === "number" ? event.target.selectionEnd : null,
      };
    }
    scheduleVisualViewportFocusSync();
  });
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", updateVisualViewportState);
    window.visualViewport.addEventListener("scroll", updateVisualViewportState);
  }
}

function setupChromeMeasurements() {
  if (!("ResizeObserver" in window)) return;
  const bindings = [
    [$("appTopbar"), "--topbar-h"],
    [$("phoneTabBar"), "--tabbar-h"],
    [$("stickyGenerateHost"), "--sticky-generate-h"],
  ];
  const observer = new ResizeObserver((entries) => {
    for (const entry of entries) {
      const binding = bindings.find(([node]) => node === entry.target);
      if (binding) document.documentElement.style.setProperty(binding[1], `${Math.ceil(entry.borderBoxSize?.[0]?.blockSize || entry.contentRect.height)}px`);
    }
  });
  for (const [node] of bindings) if (node) observer.observe(node);
}

function boot() {
  primeRouteBeforePaint();
  $("authForm").addEventListener("submit", submitAuth);
  $("statusPill").addEventListener("click", (event) => openTaskTray(event.currentTarget));
  $("identityChip").addEventListener("click", (event) => openAccountMenu(event.currentTarget, false));
  $("managementMenuBtn").addEventListener("click", (event) => openManagementMenu(event.currentTarget));
  $("moreMenuBtn").addEventListener("click", (event) => openAccountMenu(event.currentTarget, true));
  window.addEventListener("popstate", () => {
    if (!sheetStack.length) return;
    closeTopSheetFromPop();
    const after = sheetAfterPop;
    sheetAfterPop = null;
    if (after) after();
  });
  window.addEventListener("hashchange", applyRoute);
  window.addEventListener("castle-auth-required", () => {
    resetSessionUi();
    showAuth("login");
  });
  setupLocalHistoryContinuationBridge();
  window.addEventListener("pagehide", () => {
    if (typeof draftFlushNow === "function") draftFlushNow();
  });
  document.addEventListener("visibilitychange", handleDocumentVisibilityChange);
  document.querySelectorAll(".view-tab, .phone-tab[data-view]").forEach((tab) =>
    tab.addEventListener("click", () => setView(tab.dataset.view === "library" ? "library" : tab.dataset.view))
  );
  window.addEventListener("online", () => { setServiceStatus(true, "服務正常"); updateGenerateReadiness(); });
  window.addEventListener("offline", () => { setServiceStatus(false, "目前離線"); updateGenerateReadiness(); });
  setupVisualViewport();
  setupChromeMeasurements();
  renderTaskChip();
  renderStickyGenerate();
  checkAuth().then((ok) => { if (ok && !redirectToLoginReturn()) startAppData(); }).catch((error) => {
    showAuth("login");
    $("authMessage").textContent = error.message;
  });
}

boot();

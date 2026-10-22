/*
 * Castle ClipMode browser backup helper (canonical static copy)
 *
 * Run this file as a DevTools Snippet while signed in at
 * https://clipmode.ai/gallery.  Authenticated API requests are allow-listed,
 * same-origin GETs.  Reference-media URLs returned by that API may receive a
 * credential-free GET.  The login token is read into a local closure and is
 * never logged, persisted, or sent to another origin.
 */
(function clipModeBrowserBackupModule(root, factory) {
  "use strict";

  const api = factory();
  if (typeof module === "object" && module && module.exports) {
    module.exports = api;
  }
  if (root && root.document) {
    root.CastleClipModeBackup = api;
    api.install(root);
  }
})(typeof globalThis === "object" ? globalThis : this, function factory() {
  "use strict";

  const TOOL_VERSION = "1.2.1";
  const SOURCE_SCHEMA = "castle.clipmode-browser-backup.v1";
  const PACK_SCHEMA = "castle.clipmode-pack-input.v2";
  const PACK_SCHEMA_VERSION = 2;
  const EXPECTED_ORIGINS = Object.freeze([
    "https://clipmode.ai",
    "https://www.clipmode.ai",
  ]);
  const BACKUP_DIRECTORY = "clipmode-castle-backup";
  const PANEL_ID = "castle-clipmode-backup-panel";
  const PAGE_SIZE = 20;
  const MAX_PAGES = 10000;
  const PACK_BATCH_MAX_BYTES = 50 * 1024 * 1024;
  const PACK_BATCH_MAX_MEDIA = 1900;
  const MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024;
  const GALLERY_MODEL_IDS_PER_REQUEST = 40;
  const MAX_GALLERY_QUERY_LENGTH = 1800;
  const ALLOWED_PATHS = Object.freeze([
    "/api/users/generation-model-ids",
    "/api/users/gallery-creations",
    "/api/users/private-models",
    "/api/classic/users/private-models",
    "/api/images/downloadgeneration",
  ]);
  const REFERENCE_IMAGES_PATH = /^\/api\/models\/[^/]+\/reference-images$/;
  const MIME_EXTENSIONS = Object.freeze({
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/ogg": ".ogg",
    "audio/flac": ".flac",
  });
  const EXTENSION_MIMES = Object.freeze({
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".avif": "image/avif",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".flac": "audio/flac",
  });

  class BackupError extends Error {
    constructor(code, detail, status) {
      super(detail ? `${code}: ${detail}` : code);
      this.name = "BackupError";
      this.code = code;
      this.status = status;
    }
  }

  function isPlainObject(value) {
    if (value === null || typeof value !== "object" || Array.isArray(value)) {
      return false;
    }
    const prototype = Object.getPrototypeOf(value);
    return prototype === Object.prototype || prototype === null;
  }

  function requirePlainObject(value, code) {
    if (!isPlainObject(value)) {
      throw new BackupError(code, "expected a JSON object");
    }
    return value;
  }

  function validateModelIndexPayload(value) {
    const payload = requirePlainObject(value, "MODEL_INDEX_SHAPE_CHANGED");
    if (!Array.isArray(payload.model_ids) || !Array.isArray(payload.models)) {
      throw new BackupError(
        "MODEL_INDEX_SHAPE_CHANGED",
        "expected model_ids and models arrays",
      );
    }
    if (payload.model_ids.some((item) => typeof item !== "string" || !item.trim())) {
      throw new BackupError(
        "MODEL_INDEX_SHAPE_CHANGED",
        "model_ids contains a non-string identifier",
      );
    }
    if (payload.models.some((item) => !isPlainObject(item))) {
      throw new BackupError(
        "MODEL_INDEX_SHAPE_CHANGED",
        "models contains a non-object item",
      );
    }
    if (payload.models.some((item) => !modelRecordId(item))) {
      throw new BackupError(
        "MODEL_INDEX_SHAPE_CHANGED",
        "models contains an item without id/_id/model_id",
      );
    }
    return payload;
  }

  function validatePrivateModelsPage(value) {
    const payload = requirePlainObject(value, "PRIVATE_MODELS_SHAPE_CHANGED");
    if (!Array.isArray(payload.models)) {
      throw new BackupError(
        "PRIVATE_MODELS_SHAPE_CHANGED",
        "expected a models array",
      );
    }
    if (!Number.isSafeInteger(payload.total_models) || payload.total_models < 0) {
      throw new BackupError(
        "PRIVATE_MODELS_SHAPE_CHANGED",
        "expected a non-negative total_models number",
      );
    }
    if (payload.models.some((item) => !isPlainObject(item))) {
      throw new BackupError(
        "PRIVATE_MODELS_SHAPE_CHANGED",
        "models contains a non-object item",
      );
    }
    if (payload.models.some((item) => !modelRecordId(item))) {
      throw new BackupError(
        "PRIVATE_MODELS_SHAPE_CHANGED",
        "models contains an item without id/_id/model_id",
      );
    }
    if (payload.total_models < payload.models.length) {
      throw new BackupError(
        "PRIVATE_MODELS_SHAPE_CHANGED",
        "total_models is smaller than the current page",
      );
    }
    return payload;
  }

  function validateClassicPrivateModelsPage(value) {
    const payload = requirePlainObject(value, "CLASSIC_PRIVATE_MODELS_SHAPE_CHANGED");
    if (!Array.isArray(payload.models)) {
      throw new BackupError(
        "CLASSIC_PRIVATE_MODELS_SHAPE_CHANGED",
        "expected a models array",
      );
    }
    if (
      payload.total_models !== undefined &&
      (!Number.isSafeInteger(payload.total_models) || payload.total_models < 0)
    ) {
      throw new BackupError(
        "CLASSIC_PRIVATE_MODELS_SHAPE_CHANGED",
        "total_models must be a non-negative number when present",
      );
    }
    if (payload.models.some((item) => !isPlainObject(item) || !modelRecordId(item))) {
      throw new BackupError(
        "CLASSIC_PRIVATE_MODELS_SHAPE_CHANGED",
        "models contains a non-object or an item without id/_id/model_id",
      );
    }
    if (
      Number.isSafeInteger(payload.total_models) &&
      payload.total_models < payload.models.length
    ) {
      throw new BackupError(
        "CLASSIC_PRIVATE_MODELS_SHAPE_CHANGED",
        "total_models is smaller than the current page",
      );
    }
    return payload;
  }

  function validateGalleryPage(value) {
    const payload = requirePlainObject(value, "GALLERY_SHAPE_CHANGED");
    if (!Array.isArray(payload.image_generations)) {
      throw new BackupError(
        "GALLERY_SHAPE_CHANGED",
        "expected an image_generations array",
      );
    }
    if (
      !Number.isSafeInteger(payload.total_image_generations) ||
      payload.total_image_generations < 0
    ) {
      throw new BackupError(
        "GALLERY_SHAPE_CHANGED",
        "expected a non-negative total_image_generations number",
      );
    }
    if (
      payload.hasMore !== undefined &&
      typeof payload.hasMore !== "boolean"
    ) {
      throw new BackupError(
        "GALLERY_SHAPE_CHANGED",
        "hasMore must be a boolean when present",
      );
    }
    if (payload.image_generations.some((item) => !isPlainObject(item))) {
      throw new BackupError(
        "GALLERY_SHAPE_CHANGED",
        "image_generations contains a non-object item",
      );
    }
    return payload;
  }

  function referenceCandidateLabel(item, index) {
    if (isPlainObject(item)) {
      for (const key of ["id", "_id", "reference_id", "file_name", "filename"]) {
        const value = item[key];
        if (typeof value === "string" && value.trim()) {
          if (key === "file_name" || key === "filename") {
            return leafName(value) || `reference-${index + 1}`;
          }
          return value.trim();
        }
      }
    }
    return `reference-${index + 1}`;
  }

  function referenceUrl(item) {
    if (typeof item === "string" && item.trim()) return item.trim();
    if (!isPlainObject(item)) return null;
    for (const key of ["url", "sas_url", "image_url", "download_url", "src"]) {
      const value = item[key];
      if (typeof value === "string" && value.trim()) return value.trim();
    }
    return null;
  }

  function normalizedHost(url) {
    return url.hostname.toLowerCase().replace(/^\[|\]$/g, "").replace(/\.$/, "");
  }

  function isPrivateIpv4(host) {
    if (!/^\d{1,3}(?:\.\d{1,3}){3}$/.test(host)) return false;
    const parts = host.split(".").map(Number);
    if (parts.some((part) => part > 255)) return true;
    const [first, second] = parts;
    return first === 0 || first === 10 || first === 127 || first >= 224 ||
      (first === 100 && second >= 64 && second <= 127) ||
      (first === 169 && second === 254) ||
      (first === 172 && second >= 16 && second <= 31) ||
      (first === 192 && (second === 0 || second === 168)) ||
      (first === 198 && (second === 18 || second === 19));
  }

  function isPrivateIpv6(host) {
    const normalized = host.toLowerCase();
    if (!normalized.includes(":")) return false;
    if (normalized === "::" || normalized === "::1") return true;
    if (/^(?:fc|fd)/.test(normalized) || /^fe[89ab]/.test(normalized)) return true;
    const mapped = normalized.match(/::ffff:(\d{1,3}(?:\.\d{1,3}){3})$/);
    return mapped ? isPrivateIpv4(mapped[1]) : false;
  }

  function publicHttpsUrl(input, base) {
    let parsed;
    try {
      parsed = new URL(input, base);
    } catch (_error) {
      throw new BackupError("REFERENCE_IMAGES_URL_INVALID");
    }
    const host = normalizedHost(parsed);
    if (
      parsed.protocol !== "https:" || parsed.username || parsed.password || !host ||
      host === "localhost" || host.endsWith(".localhost") || host.endsWith(".local") ||
      isPrivateIpv4(host) || isPrivateIpv6(host)
    ) {
      throw new BackupError(
        "REFERENCE_IMAGES_URL_UNSAFE",
        "expected public credential-free HTTPS",
      );
    }
    return parsed;
  }

  function validateReferenceImagesPayload(
    value,
    origin = EXPECTED_ORIGINS[0],
    modelId = "unknown-model",
  ) {
    let items;
    let envelope = null;
    if (Array.isArray(value)) {
      items = value;
    } else {
      const payload = requirePlainObject(value, "REFERENCE_IMAGES_SHAPE_CHANGED");
      for (const key of ["reference_images", "referenceImages", "images", "items"]) {
        if (payload[key] === null) {
          items = [];
          envelope = key;
          break;
        }
        if (Array.isArray(payload[key])) {
          items = payload[key];
          envelope = key;
          break;
        }
      }
      if (!items) {
        throw new BackupError(
          "REFERENCE_IMAGES_SHAPE_CHANGED",
          "expected an array or reference_images/referenceImages/images/items array",
        );
      }
    }
    const candidates = items.map((item, index) => {
      if (typeof item !== "string" && !isPlainObject(item)) {
        throw new BackupError(
          "REFERENCE_IMAGES_SHAPE_CHANGED",
          `candidate ${index + 1} is not a URL string or object`,
        );
      }
      const url = referenceUrl(item);
      if (!url) {
        throw new BackupError(
          "REFERENCE_IMAGES_SHAPE_CHANGED",
          `candidate ${index + 1} has no recognized URL field`,
        );
      }
      try {
        const parsed = publicHttpsUrl(url, origin);
        const ordinal = String(index + 1).padStart(4, "0");
        const urlIdentity = fnv1a(`${parsed.origin}${parsed.pathname}`);
        const modelIdentity = fnv1a(String(modelId));
        const label = safePathComponent(referenceCandidateLabel(item, index)).slice(0, 48);
        return {
          id: `${modelIdentity}-${ordinal}-${label}-${urlIdentity}`,
          index,
          urlIdentity,
          url: parsed.href,
          raw: item,
          modelIdentity,
        };
      } catch (error) {
        if (error instanceof BackupError) throw error;
        throw new BackupError("REFERENCE_IMAGES_SHAPE_CHANGED", `candidate ${index + 1} URL is invalid`);
      }
    });
    return { envelope, candidates };
  }

  function stableRecordId(record) {
    if (!isPlainObject(record)) return null;
    for (const key of ["id", "_id", "file_name"]) {
      const value = record[key];
      if (typeof value === "string" && value.trim()) return value.trim();
    }
    return null;
  }

  function modelRecordId(record) {
    if (!isPlainObject(record)) return null;
    for (const key of ["id", "_id", "model_id"]) {
      const value = record[key];
      if (typeof value === "string" && value.trim()) return value.trim();
    }
    return null;
  }

  function fnv1a(value) {
    let hash = 0x811c9dc5;
    for (let index = 0; index < value.length; index += 1) {
      hash ^= value.charCodeAt(index);
      hash = Math.imul(hash, 0x01000193);
    }
    return (hash >>> 0).toString(16).padStart(8, "0");
  }

  function safePathComponent(value) {
    const original = String(value || "item");
    let cleaned = original
      .normalize("NFKD")
      .replace(/[\\/:*?"<>|\u0000-\u001f\u007f]+/g, "-")
      .replace(/\s+/g, "-")
      .replace(/[^A-Za-z0-9._-]+/g, "-")
      .replace(/^[. -]+|[. -]+$/g, "")
      .slice(0, 63);
    if (!cleaned) cleaned = "item";
    return `${cleaned.slice(0, 63)}-${fnv1a(original)}`;
  }

  function stableJson(value) {
    const seen = new Set();
    function normalize(item) {
      if (item === null || typeof item !== "object") return item;
      if (seen.has(item)) throw new BackupError("JSON_CYCLE", "cannot persist cyclic data");
      seen.add(item);
      let result;
      if (Array.isArray(item)) {
        result = item.map(normalize);
      } else {
        result = Object.create(null);
        for (const key of Object.keys(item).sort()) result[key] = normalize(item[key]);
      }
      seen.delete(item);
      return result;
    }
    return `${JSON.stringify(normalize(value), null, 2)}\n`;
  }

  function pathIsAllowed(input, origin) {
    let url;
    try {
      url = new URL(input, origin);
    } catch (_error) {
      return false;
    }
    return url.origin === origin && (
      ALLOWED_PATHS.includes(url.pathname) || REFERENCE_IMAGES_PATH.test(url.pathname)
    );
  }

  function shouldContinuePage(payload, page, pageSize, itemCount, totalKey) {
    if (payload.hasMore === false) return false;
    if (payload.hasMore === true) return true;
    const total = payload[totalKey];
    return itemCount > 0 && page * pageSize < total;
  }

  function headerFilename(value) {
    if (typeof value !== "string" || !value) return null;
    const utf = value.match(/filename\*=UTF-8''([^;]+)/i);
    if (utf) {
      try {
        return decodeURIComponent(utf[1].replace(/^"|"$/g, ""));
      } catch (_error) {
        return utf[1].replace(/^"|"$/g, "");
      }
    }
    const basic = value.match(/filename\s*=\s*"([^"]+)"/i) ||
      value.match(/filename\s*=\s*([^;]+)/i);
    return basic ? basic[1].trim() : null;
  }

  function extensionFromName(value) {
    if (typeof value !== "string") return null;
    const withoutQuery = value.split(/[?#]/, 1)[0];
    const match = withoutQuery.match(/(\.[A-Za-z0-9]{1,8})$/);
    return match ? match[1].toLowerCase() : null;
  }

  function normalizedMime(value) {
    if (typeof value !== "string") return null;
    const mime = value.split(";", 1)[0].trim().toLowerCase();
    if (mime === "audio/x-wav") return "audio/wav";
    return Object.prototype.hasOwnProperty.call(MIME_EXTENSIONS, mime) ? mime : null;
  }

  function ascii(bytes, start, end) {
    return String.fromCharCode(...bytes.slice(start, end));
  }

  function sniffIsoBmffMime(bytes) {
    if (bytes.length < 16 || ascii(bytes, 4, 8) !== "ftyp") return null;
    const boxSize = ((bytes[0] << 24) >>> 0) + (bytes[1] << 16) + (bytes[2] << 8) + bytes[3];
    if (boxSize < 16) return null;
    const brands = [ascii(bytes, 8, 12)];
    for (let offset = 16; offset + 4 <= Math.min(boxSize, bytes.length); offset += 4) {
      brands.push(ascii(bytes, offset, offset + 4));
    }
    if (brands.some((brand) => brand === "avif" || brand === "avis")) return "image/avif";
    if (brands.includes("qt  ")) return "video/quicktime";
    if (brands.some((brand) => /^(?:M4[ABP] |F4[ABP] )$/.test(brand))) return "audio/mp4";
    if (brands.some((brand) => /^(?:isom|iso[2-9]|mp4[12]|avc1|dash|MSNV|3g\w\w)$/.test(brand))) {
      return "video/mp4";
    }
    return null;
  }

  function sniffMime(bytes) {
    const b = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes || []);
    if (b.length >= 8 && b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4e && b[3] === 0x47) {
      return "image/png";
    }
    if (b.length >= 3 && b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) {
      return "image/jpeg";
    }
    if (
      b.length >= 12 &&
      ascii(b, 0, 4) === "RIFF" &&
      ascii(b, 8, 12) === "WEBP"
    ) {
      return "image/webp";
    }
    if (b.length >= 6) {
      const gif = ascii(b, 0, 6);
      if (gif === "GIF87a" || gif === "GIF89a") return "image/gif";
    }
    const bmff = sniffIsoBmffMime(b);
    if (bmff) return bmff;
    if (b.length >= 4 && b[0] === 0x1a && b[1] === 0x45 && b[2] === 0xdf && b[3] === 0xa3) {
      return "video/webm";
    }
    if (b.length >= 12 && ascii(b, 0, 4) === "RIFF" && ascii(b, 8, 12) === "WAVE") {
      return "audio/wav";
    }
    if (b.length >= 4 && ascii(b, 0, 4) === "OggS") return "audio/ogg";
    if (b.length >= 4 && ascii(b, 0, 4) === "fLaC") return "audio/flac";
    if (b.length >= 3 && ascii(b, 0, 3) === "ID3") return "audio/mpeg";
    return null;
  }

  function mediaKind(mime) {
    if (mime.startsWith("image/")) return "image";
    if (mime.startsWith("video/")) return "video";
    if (mime.startsWith("audio/")) return "audio";
    throw new BackupError("UNSUPPORTED_MEDIA_TYPE", mime);
  }

  function validIsoOrNull(value) {
    if (value === null || value === undefined || value === "") return null;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date.toISOString();
  }

  function boundedText(value, fallback, maximum) {
    const text = typeof value === "string" ? value.trim() : "";
    return (text || fallback).slice(0, maximum);
  }

  function leafName(value) {
    if (typeof value !== "string" || !value.trim()) return null;
    const text = value.trim();
    try {
      const url = new URL(text);
      const parts = url.pathname.split("/").filter(Boolean);
      return decodeURIComponent(parts[parts.length - 1] || "") || null;
    } catch (_error) {
      const withoutQuery = text.split(/[?#]/, 1)[0];
      const parts = withoutQuery.split(/[\\/]/).filter(Boolean);
      const leaf = parts[parts.length - 1] || "";
      try {
        return decodeURIComponent(leaf) || null;
      } catch (_decodeError) {
        return leaf || null;
      }
    }
  }

  function externalId(prefix, value) {
    return `${prefix}-${safePathComponent(boundedText(value, "unknown", 240))}`;
  }

  function decodeJwtSubject(token) {
    if (typeof token !== "string") return null;
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    try {
      const base64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
      const padded = base64.padEnd(Math.ceil(base64.length / 4) * 4, "=");
      const payload = JSON.parse(atob(padded));
      for (const key of ["id", "sub", "user_id"]) {
        if (typeof payload[key] === "string" && payload[key].trim()) {
          return payload[key].trim();
        }
      }
    } catch (_error) {
      return null;
    }
    return null;
  }

  async function deriveAccountFingerprint(token, cryptoObject) {
    if (typeof token !== "string" || !token) {
      throw new BackupError("LOGIN_REQUIRED", "missing ClipMode login token");
    }
    const subject = decodeJwtSubject(token);
    const method = subject ? "jwt_subject" : "token_sha256_fallback";
    const domainSeparated = subject
      ? `clipmode-user:${subject}`
      : `clipmode-token-fallback-v1\u0000${token}`;
    return {
      fingerprint: (await sha256Hex(new TextEncoder().encode(domainSeparated), cryptoObject)).slice(0, 24),
      method,
    };
  }

  async function sha256Hex(value, cryptoObject) {
    const bytes = value instanceof ArrayBuffer
      ? value
      : value instanceof Uint8Array
        ? value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength)
        : await value.arrayBuffer();
    const digest = await cryptoObject.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(digest), (item) => item.toString(16).padStart(2, "0")).join("");
  }

  function sanitizeError(error) {
    if (error instanceof BackupError) return error.message.slice(0, 500);
    if (error && error.name === "AbortError") return "STOPPED_BY_USER";
    if (error && typeof error.message === "string") {
      return `UNEXPECTED_ERROR: ${error.message}`.slice(0, 500);
    }
    return "UNEXPECTED_ERROR";
  }

  async function delay(milliseconds, signal) {
    if (milliseconds <= 0) return;
    await new Promise((resolve, reject) => {
      const finished = () => {
        signal.removeEventListener("abort", stopped);
        resolve();
      };
      const timer = setTimeout(finished, milliseconds);
      const stopped = () => {
        clearTimeout(timer);
        signal.removeEventListener("abort", stopped);
        reject(new DOMException("Stopped", "AbortError"));
      };
      if (signal.aborted) stopped();
      else signal.addEventListener("abort", stopped, { once: true });
    });
  }

  async function readOnlyFetch(fetchFunction, origin, token, input, signal) {
    if (!pathIsAllowed(input, origin)) {
      throw new BackupError("REQUEST_NOT_ALLOWLISTED", String(input).slice(0, 180));
    }
    const url = new URL(input, origin);
    let response;
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      try {
        response = await fetchFunction(url.href, {
          method: "GET",
          headers: {
            Authorization: `Bearer ${token}`,
            "Accept-Language": "en",
          },
          credentials: "same-origin",
          cache: "no-store",
          redirect: "error",
          signal,
        });
      } catch (error) {
        if (signal.aborted || attempt === 3) throw error;
        await delay(250 * 2 ** (attempt - 1), signal);
        continue;
      }
      if (response.ok) return response;
      if (response.status === 401 || response.status === 403) {
        throw new BackupError("LOGIN_REQUIRED", `HTTP ${response.status}`, response.status);
      }
      if ((response.status === 429 || response.status >= 500) && attempt < 3) {
        const retryAfter = Number(response.headers.get("Retry-After"));
        const wait = Number.isFinite(retryAfter)
          ? Math.min(10000, Math.max(250, retryAfter * 1000))
          : 500 * 2 ** (attempt - 1);
        await delay(wait, signal);
        continue;
      }
      throw new BackupError("CLIPMODE_HTTP_ERROR", `HTTP ${response.status}`, response.status);
    }
    throw new BackupError("CLIPMODE_HTTP_ERROR", "request retries exhausted");
  }

  async function readOnlyJson(fetchFunction, origin, token, input, signal) {
    const response = await readOnlyFetch(fetchFunction, origin, token, input, signal);
    try {
      return await response.json();
    } catch (_error) {
      throw new BackupError("CLIPMODE_JSON_INVALID", response.url.replace(origin, ""));
    }
  }

  async function externalReadOnlyFetch(fetchFunction, input, signal) {
    const url = publicHttpsUrl(input);
    let response;
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      try {
        response = await fetchFunction(url.href, {
          method: "GET",
          headers: { Accept: "image/*,video/*,audio/*,application/octet-stream" },
          credentials: "omit",
          cache: "no-store",
          referrerPolicy: "no-referrer",
          redirect: "follow",
          signal,
        });
      } catch (error) {
        if (signal.aborted || attempt === 3) throw error;
        await delay(250 * 2 ** (attempt - 1), signal);
        continue;
      }
      if (response.ok) {
        if (!response.url) {
          throw new BackupError("REFERENCE_MEDIA_FINAL_URL_MISSING");
        }
        publicHttpsUrl(response.url);
        return response;
      }
      if ((response.status === 429 || response.status >= 500) && attempt < 3) {
        await delay(500 * 2 ** (attempt - 1), signal);
        continue;
      }
      throw new BackupError("REFERENCE_MEDIA_HTTP_ERROR", `HTTP ${response.status}`, response.status);
    }
    throw new BackupError("REFERENCE_MEDIA_HTTP_ERROR", "request retries exhausted");
  }

  function contentLengthOrNull(response) {
    const raw = response.headers.get("Content-Length");
    if (raw === null || raw === "") return null;
    if (!/^\d+$/.test(raw)) throw new BackupError("MEDIA_CONTENT_LENGTH_INVALID");
    const value = Number(raw);
    if (!Number.isSafeInteger(value) || value < 0) {
      throw new BackupError("MEDIA_CONTENT_LENGTH_INVALID");
    }
    return value;
  }

  async function boundedResponseBlob(response, maximum = MAX_DOWNLOAD_BYTES) {
    if (!Number.isSafeInteger(maximum) || maximum <= 0) {
      throw new BackupError("MEDIA_DOWNLOAD_LIMIT_INVALID");
    }
    const declared = contentLengthOrNull(response);
    if (declared !== null && declared > maximum) {
      if (response.body && typeof response.body.cancel === "function") {
        await response.body.cancel().catch(() => undefined);
      }
      throw new BackupError(
        "MEDIA_DOWNLOAD_TOO_LARGE",
        `Content-Length ${declared} exceeds ${maximum} byte limit; metadata retained, bytes unarchived`,
      );
    }
    if (!response.body || typeof response.body.getReader !== "function") {
      throw new BackupError(
        "MEDIA_STREAM_UNAVAILABLE",
        "bounded streaming is required; metadata retained, bytes unarchived",
      );
    }
    const reader = response.body.getReader();
    const chunks = [];
    let total = 0;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        if (!(value instanceof Uint8Array)) {
          throw new BackupError("MEDIA_STREAM_CHUNK_INVALID");
        }
        total += value.byteLength;
        if (total > maximum) {
          await reader.cancel().catch(() => undefined);
          throw new BackupError(
            "MEDIA_DOWNLOAD_TOO_LARGE",
            `stream exceeds ${maximum} byte limit; metadata retained, bytes unarchived`,
          );
        }
        chunks.push(value);
      }
    } finally {
      reader.releaseLock();
    }
    if (declared !== null && total !== declared) {
      throw new BackupError("MEDIA_CONTENT_LENGTH_MISMATCH");
    }
    return new Blob(chunks, { type: response.headers.get("Content-Type") || "" });
  }

  async function directoryAt(rootHandle, parts, create) {
    let current = rootHandle;
    for (const part of parts) {
      current = await current.getDirectoryHandle(part, { create });
    }
    return current;
  }

  function splitRelativePath(relativePath) {
    const parts = String(relativePath).split("/");
    if (
      !parts.length ||
      parts.some((part) => !part || part === "." || part === ".." || part.includes("\\"))
    ) {
      throw new BackupError("LOCAL_PATH_INVALID", String(relativePath).slice(0, 180));
    }
    return parts;
  }

  async function writeFile(rootHandle, relativePath, contents) {
    const parts = splitRelativePath(relativePath);
    const fileName = parts.pop();
    const directory = await directoryAt(rootHandle, parts, true);
    const handle = await directory.getFileHandle(fileName, { create: true });
    const writable = await handle.createWritable();
    try {
      await writable.write(contents);
      await writable.close();
    } catch (error) {
      await writable.abort().catch(() => undefined);
      throw error;
    }
  }

  async function readJsonFile(rootHandle, relativePath) {
    try {
      const parts = splitRelativePath(relativePath);
      const fileName = parts.pop();
      const directory = await directoryAt(rootHandle, parts, false);
      const handle = await directory.getFileHandle(fileName, { create: false });
      const file = await handle.getFile();
      return JSON.parse(await file.text());
    } catch (error) {
      if (error && error.name === "NotFoundError") return null;
      if (error instanceof SyntaxError) {
        throw new BackupError("CHECKPOINT_INVALID", relativePath);
      }
      throw error;
    }
  }

  async function localFileMatches(rootHandle, item, cryptoObject) {
    try {
      const parts = splitRelativePath(item.localPath);
      const fileName = parts.pop();
      const directory = await directoryAt(rootHandle, parts, false);
      const handle = await directory.getFileHandle(fileName, { create: false });
      const file = await handle.getFile();
      if (file.size !== item.bytes) return false;
      return (await sha256Hex(file, cryptoObject)) === item.sha256;
    } catch (error) {
      if (error && error.name === "NotFoundError") return false;
      throw error;
    }
  }

  async function readLocalFile(rootHandle, relativePath) {
    const parts = splitRelativePath(relativePath);
    const fileName = parts.pop();
    const directory = await directoryAt(rootHandle, parts, false);
    const handle = await directory.getFileHandle(fileName, { create: false });
    return handle.getFile();
  }

  function contentAddressedBlobPath(digest, mime) {
    if (!/^[0-9a-f]{64}$/.test(digest) || !MIME_EXTENSIONS[mime]) {
      throw new BackupError("CONTENT_ADDRESSED_BLOB_ID_INVALID");
    }
    return `blobs/sha256/${digest.slice(0, 2)}/${digest}${MIME_EXTENSIONS[mime]}`;
  }

  async function storeContentAddressedBlob(
    rootHandle,
    blob,
    digest,
    mime,
    cryptoObject,
  ) {
    const localPath = contentAddressedBlobPath(digest, mime);
    const expected = { localPath, bytes: blob.size, sha256: digest };
    try {
      const existing = await readLocalFile(rootHandle, localPath);
      if (
        existing.size !== blob.size ||
        await sha256Hex(existing, cryptoObject) !== digest
      ) {
        throw new BackupError(
          "CONTENT_ADDRESSED_BLOB_MISMATCH",
          `${localPath} exists with bytes that do not match its digest; it was not overwritten`,
        );
      }
      return localPath;
    } catch (error) {
      if (!error || error.name !== "NotFoundError") throw error;
    }
    await writeFile(rootHandle, localPath, blob);
    if (!await localFileMatches(rootHandle, expected, cryptoObject)) {
      throw new BackupError("CONTENT_ADDRESSED_BLOB_WRITE_VERIFY_FAILED", localPath);
    }
    return localPath;
  }

  async function migrateCompletedToContentAddress(context, completed) {
    if (!completed || !completed.localPath || !completed.sha256 || !completed.mime) return null;
    if (!await localFileMatches(context.rootHandle, completed, context.cryptoObject)) return null;
    const immutablePath = contentAddressedBlobPath(completed.sha256, completed.mime);
    if (completed.localPath === immutablePath) return completed;
    const source = await readLocalFile(context.rootHandle, completed.localPath);
    await storeContentAddressedBlob(
      context.rootHandle,
      source,
      completed.sha256,
      completed.mime,
      context.cryptoObject,
    );
    return { ...completed, localPath: immutablePath };
  }

  function freshCheckpoint(origin, accountFingerprint, accountFingerprintMethod, now) {
    return {
      schema: "castle.clipmode-browser-checkpoint.v1",
      schemaVersion: 1,
      toolVersion: TOOL_VERSION,
      sourceOrigin: origin,
      accountFingerprint,
      accountFingerprintMethod,
      startedAt: now,
      updatedAt: now,
      status: "running",
      scannedPrivateModelPages: 0,
      scannedClassicPrivateModelPages: 0,
      scannedReferenceModels: 0,
      scannedGalleryPages: 0,
      completedMedia: {},
      completedReferenceMedia: {},
      errors: [],
    };
  }

  function validateCheckpoint(value, origin, accountFingerprint) {
    if (value === null) return null;
    if (
      !isPlainObject(value) ||
      value.schema !== "castle.clipmode-browser-checkpoint.v1" ||
      value.schemaVersion !== 1 ||
      value.sourceOrigin !== origin ||
      typeof value.startedAt !== "string" ||
      !isPlainObject(value.completedMedia) ||
      !Array.isArray(value.errors)
    ) {
      throw new BackupError("CHECKPOINT_INVALID", "select a clean directory or repair checkpoint.json");
    }
    if (!value.accountFingerprint || !accountFingerprint) {
      throw new BackupError(
        "ACCOUNT_FINGERPRINT_REQUIRED_SELECT_CLEAN_DIRECTORY",
        "legacy checkpoint is not account-bound; select a clean directory",
      );
    }
    if (value.accountFingerprint !== accountFingerprint) {
      throw new BackupError(
        "ACCOUNT_MISMATCH",
        "the selected directory belongs to another ClipMode account",
      );
    }
    if (value.completedReferenceMedia === undefined) value.completedReferenceMedia = {};
    if (value.scannedClassicPrivateModelPages === undefined) {
      value.scannedClassicPrivateModelPages = 0;
    }
    if (!isPlainObject(value.completedReferenceMedia)) {
      throw new BackupError("CHECKPOINT_INVALID", "completedReferenceMedia is not an object");
    }
    return value;
  }

  function pageFingerprint(records, idFunction) {
    return fnv1a(records.map((item, index) => idFunction(item) || `missing-${index}`).join("\n"));
  }

  async function scanPrivateModels(context) {
    const items = [];
    let previousFingerprint = null;
    for (let page = 1; page <= MAX_PAGES; page += 1) {
      if (context.signal.aborted) throw new DOMException("Stopped", "AbortError");
      context.report(`讀取角色清單，第 ${page} 頁…`);
      const query = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
      const payload = validatePrivateModelsPage(
        await readOnlyJson(
          context.fetchFunction,
          context.origin,
          context.token,
          `/api/users/private-models?${query}`,
          context.signal,
        ),
      );
      const fingerprint = pageFingerprint(payload.models, modelRecordId);
      if (payload.models.length && fingerprint === previousFingerprint) {
        throw new BackupError("PRIVATE_MODELS_PAGINATION_REPEATED", `page ${page}`);
      }
      previousFingerprint = fingerprint;
      await writeFile(
        context.rootHandle,
        `metadata/private-models/page-${String(page).padStart(6, "0")}.json`,
        stableJson(payload),
      );
      items.push(...payload.models);
      context.checkpoint.scannedPrivateModelPages = page;
      await context.saveCheckpoint();
      if (!payload.models.length && page * PAGE_SIZE < payload.total_models) {
        throw new BackupError("PRIVATE_MODELS_PAGINATION_EMPTY", `page ${page}`);
      }
      if (!shouldContinuePage(payload, page, PAGE_SIZE, payload.models.length, "total_models")) {
        return items;
      }
      await delay(50, context.signal);
    }
    throw new BackupError("PRIVATE_MODELS_PAGE_LIMIT", String(MAX_PAGES));
  }

  async function scanClassicPrivateModels(context) {
    const items = [];
    let previousFingerprint = null;
    for (let page = 1; page <= MAX_PAGES; page += 1) {
      if (context.signal.aborted) throw new DOMException("Stopped", "AbortError");
      context.report(`讀取 Classic 角色清單，第 ${page} 頁…`);
      const query = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
      const payload = validateClassicPrivateModelsPage(
        await readOnlyJson(
          context.fetchFunction,
          context.origin,
          context.token,
          `/api/classic/users/private-models?${query}`,
          context.signal,
        ),
      );
      const fingerprint = pageFingerprint(payload.models, modelRecordId);
      if (payload.models.length && fingerprint === previousFingerprint) {
        throw new BackupError("CLASSIC_PRIVATE_MODELS_PAGINATION_REPEATED", `page ${page}`);
      }
      previousFingerprint = fingerprint;
      await writeFile(
        context.rootHandle,
        `metadata/classic-private-models/page-${String(page).padStart(6, "0")}.json`,
        stableJson(payload),
      );
      items.push(...payload.models);
      context.checkpoint.scannedClassicPrivateModelPages = page;
      await context.saveCheckpoint();
      const hasKnownTotal = Number.isSafeInteger(payload.total_models);
      const shouldContinue = hasKnownTotal
        ? page * PAGE_SIZE < payload.total_models
        : payload.models.length >= PAGE_SIZE;
      if (!shouldContinue) return items;
      if (!payload.models.length) {
        throw new BackupError("CLASSIC_PRIVATE_MODELS_PAGINATION_EMPTY", `page ${page}`);
      }
      await delay(50, context.signal);
    }
    throw new BackupError("CLASSIC_PRIVATE_MODELS_PAGE_LIMIT", String(MAX_PAGES));
  }

  function mergeModelCatalogs(groups) {
    const byId = new Map();
    for (const group of groups) {
      for (const record of group.records) {
        const id = modelRecordId(record);
        if (!id) continue;
        const previous = byId.get(id);
        const sources = previous && Array.isArray(previous.__castleCatalogSources)
          ? previous.__castleCatalogSources
          : [];
        byId.set(id, {
          ...(previous || {}),
          ...record,
          __castleCatalogSources: Array.from(new Set([...sources, group.source])).sort(),
        });
      }
    }
    return Array.from(byId.values())
      .map((record) => ({
        ...record,
        __castleMetadataOnlyFromModelId:
          record.__castleCatalogSources.length === 1 &&
          record.__castleCatalogSources[0] === "generation-model-ids.model_ids",
      }))
      .sort((left, right) => modelRecordId(left).localeCompare(modelRecordId(right)));
  }

  function referenceCheckpointKey(modelId, candidate) {
    return [
      "reference",
      safePathComponent(modelId),
      String(candidate.index + 1).padStart(4, "0"),
      candidate.urlIdentity,
    ].join("-");
  }

  async function downloadReferenceCandidate(context, modelId, candidate, ordinal, total) {
    const key = referenceCheckpointKey(modelId, candidate);
    const previous = context.checkpoint.completedReferenceMedia[key];
    const verifiedPrevious = await migrateCompletedToContentAddress(context, previous);
    if (verifiedPrevious) {
      if (verifiedPrevious.localPath !== previous.localPath) {
        context.checkpoint.completedReferenceMedia[key] = verifiedPrevious;
        await context.saveCheckpoint();
      }
      context.report(`已驗證角色參考素材 ${ordinal}/${total}：${candidate.id}`);
      return verifiedPrevious;
    }
    delete context.checkpoint.completedReferenceMedia[key];
    context.removeErrorsFor("reference-media", key);
    context.report(`下載角色參考素材 ${ordinal}/${total}：${candidate.id}`);
    const response = await externalReadOnlyFetch(
      context.fetchFunction,
      candidate.url,
      context.signal,
    );
    const blob = await boundedResponseBlob(response);
    if (!blob.size) throw new BackupError("EMPTY_REFERENCE_MEDIA", candidate.id);
    const mime = await resolveMediaType(blob, response, {
      id: candidate.id,
      file_name: headerFilename(response.headers.get("Content-Disposition")) || candidate.url,
    });
    const digest = await sha256Hex(blob, context.cryptoObject);
    const localPath = await storeContentAddressedBlob(
      context.rootHandle,
      blob,
      digest,
      mime,
      context.cryptoObject,
    );
    const completed = {
      externalId: externalId("reference", `${modelId}-${candidate.id}`),
      sourceId: candidate.id,
      sourceModelId: modelId,
      localPath,
      sourceMetadataPath: `metadata/reference-images/${safePathComponent(modelId)}/response.json`,
      serverFilename: headerFilename(response.headers.get("Content-Disposition")),
      mime,
      kind: mediaKind(mime),
      bytes: blob.size,
      sha256: digest,
      createdAt: null,
      downloadedAt: new Date().toISOString(),
    };
    context.checkpoint.completedReferenceMedia[key] = completed;
    await context.saveCheckpoint();
    return completed;
  }

  async function scanReferenceMedia(context, models) {
    const references = [];
    const uniqueModels = [];
    const seenModels = new Set();
    for (const model of models) {
      const modelId = modelRecordId(model);
      if (!modelId || seenModels.has(modelId)) continue;
      seenModels.add(modelId);
      uniqueModels.push({ model, modelId });
    }
    for (let modelIndex = 0; modelIndex < uniqueModels.length; modelIndex += 1) {
      if (context.signal.aborted) throw new DOMException("Stopped", "AbortError");
      const { modelId } = uniqueModels[modelIndex];
      context.report(`讀取角色參考素材 ${modelIndex + 1}/${uniqueModels.length}…`);
      context.removeErrorsFor("reference-index", modelId);
      const responsePath = `metadata/reference-images/${safePathComponent(modelId)}/response.json`;
      let payload;
      try {
        payload = await readOnlyJson(
          context.fetchFunction,
          context.origin,
          context.token,
          `/api/models/${encodeURIComponent(modelId)}/reference-images`,
          context.signal,
        );
        await writeFile(context.rootHandle, responsePath, stableJson(payload));
      } catch (error) {
        if (context.signal.aborted || (error instanceof BackupError && error.code === "LOGIN_REQUIRED")) {
          throw error;
        }
        context.addError("reference-index", modelId, sanitizeError(error));
        await context.saveCheckpoint();
        continue;
      }

      let validated;
      try {
        validated = validateReferenceImagesPayload(payload, context.origin, modelId);
      } catch (error) {
        context.addError("reference-index", modelId, sanitizeError(error));
        await context.saveCheckpoint();
        continue;
      }
      context.removeErrorsFor("reference-index", modelId);
      for (let index = 0; index < validated.candidates.length; index += 1) {
        const candidate = validated.candidates[index];
        const key = referenceCheckpointKey(modelId, candidate);
        let download = null;
        try {
          download = await downloadReferenceCandidate(
            context,
            modelId,
            candidate,
            index + 1,
            validated.candidates.length,
          );
        } catch (error) {
          if (context.signal.aborted) throw error;
          context.addError("reference-media", key, sanitizeError(error));
          await context.saveCheckpoint();
        }
        references.push({
          modelId,
          candidateId: candidate.id,
          candidateIndex: candidate.index,
          checkpointKey: key,
          sourceUrlPresent: true,
          sourceMetadataPath: responsePath,
          download,
        });
        await delay(125, context.signal);
      }
      context.checkpoint.scannedReferenceModels = modelIndex + 1;
      await context.saveCheckpoint();
      await delay(50, context.signal);
    }
    return references;
  }

  function groupGalleryModelIds(modelIds) {
    const groups = [];
    let current = [];
    const ordered = Array.from(new Set(modelIds.map((item) => item.trim()).filter(Boolean))).sort();
    for (const modelId of ordered) {
      const candidate = [...current, modelId];
      const query = new URLSearchParams({
        page: "1",
        page_size: String(PAGE_SIZE),
        model_ids: candidate.join(","),
      });
      if (
        current.length &&
        (candidate.length > GALLERY_MODEL_IDS_PER_REQUEST || query.toString().length > MAX_GALLERY_QUERY_LENGTH)
      ) {
        groups.push(current);
        current = [modelId];
      } else {
        current = candidate;
      }
      const singleQuery = new URLSearchParams({
        page: "1",
        page_size: String(PAGE_SIZE),
        model_ids: current.join(","),
      });
      if (singleQuery.toString().length > MAX_GALLERY_QUERY_LENGTH) {
        throw new BackupError("GALLERY_MODEL_ID_TOO_LONG", safePathComponent(modelId));
      }
    }
    if (current.length) groups.push(current);
    return groups;
  }

  function validateGalleryPageProgress(payload, group, page) {
    if (payload.hasMore === true && payload.image_generations.length === 0) {
      throw new BackupError(
        "GALLERY_PAGINATION_EMPTY_WITH_HAS_MORE",
        `group ${group}, page ${page}`,
      );
    }
    return payload;
  }

  async function scanGallery(context, modelIds) {
    const items = [];
    const seenIds = new Set();
    context.removeErrorsFor("gallery", null);
    if (!modelIds.length) return items;
    const groups = groupGalleryModelIds(modelIds);
    let totalPages = 0;
    for (let groupIndex = 0; groupIndex < groups.length; groupIndex += 1) {
      let previousFingerprint = null;
      for (let page = 1; page <= MAX_PAGES; page += 1) {
        if (context.signal.aborted) throw new DOMException("Stopped", "AbortError");
        context.report(`讀取 Gallery，群組 ${groupIndex + 1}/${groups.length}，第 ${page} 頁…`);
        const query = new URLSearchParams({
          page: String(page),
          page_size: String(PAGE_SIZE),
          model_ids: groups[groupIndex].join(","),
        });
        const payload = validateGalleryPage(
          await readOnlyJson(
            context.fetchFunction,
            context.origin,
            context.token,
            `/api/users/gallery-creations?${query}`,
            context.signal,
          ),
        );
        validateGalleryPageProgress(payload, groupIndex + 1, page);
        const fingerprint = pageFingerprint(payload.image_generations, stableRecordId);
        if (payload.image_generations.length && fingerprint === previousFingerprint) {
          throw new BackupError(
            "GALLERY_PAGINATION_REPEATED",
            `group ${groupIndex + 1}, page ${page}`,
          );
        }
        previousFingerprint = fingerprint;
        await writeFile(
          context.rootHandle,
          `metadata/gallery/group-${String(groupIndex + 1).padStart(4, "0")}/` +
            `page-${String(page).padStart(6, "0")}.json`,
          stableJson(payload),
        );
        for (const item of payload.image_generations) {
          const id = stableRecordId(item);
          if (!id) {
            context.addError("gallery", null, "GALLERY_ITEM_WITHOUT_ID");
            continue;
          }
          if (seenIds.has(id)) continue;
          seenIds.add(id);
          items.push(item);
          await writeFile(
            context.rootHandle,
            `metadata/items/${safePathComponent(id)}.json`,
            stableJson(item),
          );
        }
        totalPages += 1;
        context.checkpoint.scannedGalleryPages = totalPages;
        await context.saveCheckpoint();
        if (
          !shouldContinuePage(
            payload,
            page,
            PAGE_SIZE,
            payload.image_generations.length,
            "total_image_generations",
          )
        ) break;
        await delay(50, context.signal);
        if (page === MAX_PAGES) {
          throw new BackupError("GALLERY_PAGE_LIMIT", `${MAX_PAGES} in group ${groupIndex + 1}`);
        }
      }
    }
    return items;
  }

  async function resolveMediaType(blob, response, record) {
    const declared = normalizedMime(response.headers.get("Content-Type")) ||
      normalizedMime(blob.type);
    const signature = sniffMime(new Uint8Array(await blob.slice(0, 256).arrayBuffer()));
    const serverName = headerFilename(response.headers.get("Content-Disposition"));
    let extensionMime = null;
    for (const candidate of [serverName, record.file_name]) {
      const extension = extensionFromName(candidate);
      if (extension && EXTENSION_MIMES[extension]) {
        extensionMime = EXTENSION_MIMES[extension];
        break;
      }
    }
    if (signature) {
      if (declared && mediaKind(declared) !== mediaKind(signature)) {
        throw new BackupError(
          "MEDIA_MIME_SIGNATURE_MISMATCH",
          `declared ${declared}, signature ${signature}`,
        );
      }
      if (extensionMime && mediaKind(extensionMime) !== mediaKind(signature)) {
        throw new BackupError(
          "MEDIA_EXTENSION_SIGNATURE_MISMATCH",
          `extension ${extensionMime}, signature ${signature}`,
        );
      }
      return signature;
    }
    if (declared && extensionMime && declared !== extensionMime) {
      throw new BackupError(
        "MEDIA_MIME_EXTENSION_MISMATCH",
        `declared ${declared}, extension ${extensionMime}`,
      );
    }
    if (declared) return declared;
    if (extensionMime) return extensionMime;
    throw new BackupError("UNSUPPORTED_MEDIA_TYPE", `creation ${stableRecordId(record)}`);
  }

  async function downloadCreation(context, record, index, total) {
    const id = stableRecordId(record);
    if (!id) return null;
    const previous = context.checkpoint.completedMedia[id];
    const verifiedPrevious = await migrateCompletedToContentAddress(context, previous);
    if (verifiedPrevious) {
      if (verifiedPrevious.localPath !== previous.localPath) {
        context.checkpoint.completedMedia[id] = verifiedPrevious;
        await context.saveCheckpoint();
      }
      context.report(`已驗證 ${index}/${total}：${id}`);
      return verifiedPrevious;
    }
    delete context.checkpoint.completedMedia[id];
    context.removeErrorsFor("media", id);
    context.report(`下載 ${index}/${total}：${id}`);
    const query = new URLSearchParams({ id });
    const response = await readOnlyFetch(
      context.fetchFunction,
      context.origin,
      context.token,
      `/api/images/downloadgeneration?${query}`,
      context.signal,
    );
    const blob = await boundedResponseBlob(response);
    if (!blob.size) throw new BackupError("EMPTY_MEDIA", id);
    const mime = await resolveMediaType(blob, response, record);
    const digest = await sha256Hex(blob, context.cryptoObject);
    const modelId = typeof record.model_id === "string" && record.model_id.trim()
      ? record.model_id.trim()
      : "unassigned";
    const localPath = await storeContentAddressedBlob(
      context.rootHandle,
      blob,
      digest,
      mime,
      context.cryptoObject,
    );
    const completed = {
      externalId: externalId("generation", id),
      sourceId: id,
      sourceModelId: modelId === "unassigned" ? null : modelId,
      localPath,
      sourceMetadataPath: `metadata/items/${safePathComponent(id)}.json`,
      serverFilename: headerFilename(response.headers.get("Content-Disposition")),
      mime,
      kind: mediaKind(mime),
      bytes: blob.size,
      sha256: digest,
      createdAt: validIsoOrNull(record.created_at),
      prompt: typeof record.prompt === "string" ? record.prompt : null,
      downloadedAt: new Date().toISOString(),
    };
    context.checkpoint.completedMedia[id] = completed;
    await context.saveCheckpoint();
    return completed;
  }

  function normalizedCharacter(record) {
    const id = modelRecordId(record);
    if (!id) return null;
    const fallback = `ClipMode ${id.slice(-8)}`;
    return {
      externalId: externalId("character", id),
      version: "v1",
      externalModelId: boundedText(id, "unknown", 300),
      displayName: boundedText(record.display_name || record.username, fallback, 200),
      visualClass: "unknown",
      profileMedia: null,
      createdAt: validIsoOrNull(record.created_at),
      updatedAt: validIsoOrNull(record.updated_at),
    };
  }

  function versionedRefKey(value) {
    if (!isPlainObject(value)) return null;
    if (typeof value.externalId !== "string" || typeof value.version !== "string") return null;
    return `${value.externalId}\u0000${value.version}`;
  }

  function buildPackInputBatches(packInput, mediaByteSizes, options = {}) {
    const maxBytes = options.maxBytes || PACK_BATCH_MAX_BYTES;
    const maxMedia = options.maxMedia || PACK_BATCH_MAX_MEDIA;
    if (!Number.isSafeInteger(maxBytes) || maxBytes <= 0) {
      throw new BackupError("PACK_BATCH_LIMIT_INVALID", "maxBytes");
    }
    if (!Number.isSafeInteger(maxMedia) || maxMedia <= 0 || maxMedia > 1900) {
      throw new BackupError("PACK_BATCH_LIMIT_INVALID", "maxMedia");
    }
    if (
      !isPlainObject(packInput) ||
      packInput.schema !== PACK_SCHEMA ||
      packInput.schemaVersion !== PACK_SCHEMA_VERSION ||
      !Array.isArray(packInput.characters) ||
      !Array.isArray(packInput.media) ||
      !Array.isArray(packInput.lineage)
    ) {
      throw new BackupError("PACK_BATCH_INPUT_INVALID");
    }
    const sizeFor = (media) => {
      if (
        !isPlainObject(media) ||
        !Number.isSafeInteger(media.bytes) ||
        media.bytes <= 0 ||
        typeof media.sha256 !== "string" ||
        !/^[0-9a-f]{64}$/.test(media.sha256) ||
        typeof media.localPath !== "string" ||
        media.localPath !== contentAddressedBlobPath(media.sha256, media.mime)
      ) {
        throw new BackupError("PACK_BATCH_MEDIA_IDENTITY_INVALID", media?.externalId || "unknown");
      }
      const key = versionedRefKey(media);
      const value = mediaByteSizes instanceof Map
        ? mediaByteSizes.get(key)
        : isPlainObject(mediaByteSizes)
          ? mediaByteSizes[key]
          : undefined;
      if (!Number.isSafeInteger(value) || value <= 0) {
        throw new BackupError("PACK_BATCH_MEDIA_SIZE_MISSING", media.externalId);
      }
      if (value !== media.bytes) {
        throw new BackupError("PACK_BATCH_MEDIA_SIZE_MISMATCH", media.externalId);
      }
      return media.bytes;
    };
    const ordered = [...packInput.media].sort((left, right) =>
      versionedRefKey(left).localeCompare(versionedRefKey(right)));
    const groups = [];
    const unbatchable = [];
    let current = [];
    let currentBytes = 0;
    const flush = () => {
      if (!current.length) return;
      groups.push({ media: current, bytes: currentBytes });
      current = [];
      currentBytes = 0;
    };
    for (const media of ordered) {
      const bytes = sizeFor(media);
      if (bytes > maxBytes) {
        flush();
        unbatchable.push({
          externalId: media.externalId,
          version: media.version,
          bytes,
          reason: "single_media_exceeds_batch_limit",
          characterRefs: [...(media.characterRefs || [])].sort((left, right) =>
            versionedRefKey(left).localeCompare(versionedRefKey(right))),
        });
        continue;
      }
      if (current.length && (current.length >= maxMedia || currentBytes + bytes > maxBytes)) {
        flush();
      }
      current.push(media);
      currentBytes += bytes;
    }
    flush();

    const mediaFiles = groups.map((group, index) => {
      const mediaRefs = new Set(group.media.map(versionedRefKey));
      const characterRefs = new Set(
        group.media.flatMap((item) => item.characterRefs || []).map(versionedRefKey),
      );
      const characters = packInput.characters
        .filter((item) => characterRefs.has(versionedRefKey(item)))
        .map((item) => ({
          ...item,
          // Identical character definitions make repeated character refs
          // idempotent across independently packed batches.
          profileMedia: null,
        }));
      const lineage = packInput.lineage.filter((edge) =>
        mediaRefs.has(versionedRefKey(edge.parent)) && mediaRefs.has(versionedRefKey(edge.child)));
      const number = String(index + 1).padStart(4, "0");
      const filename = `batch-${number}.json`;
      return {
        filename,
        bytes: group.bytes,
        mediaCount: group.media.length,
        characterCount: characters.length,
        manifest: {
          schema: PACK_SCHEMA,
          schemaVersion: PACK_SCHEMA_VERSION,
          accountScope: packInput.accountScope,
          exportId: `${String(packInput.exportId).slice(0, 188)}-batch-${number}`,
          exportedAt: packInput.exportedAt,
          characters,
          media: group.media,
          lineage,
        },
      };
    });

    const regularFilesByCharacter = new Map();
    const batchableMediaByCharacter = new Map();
    const unbatchableMediaByCharacter = new Map();
    const addMapping = (mapping, key, value) => {
      if (!mapping.has(key)) mapping.set(key, []);
      mapping.get(key).push(value);
    };
    for (const file of mediaFiles) {
      for (const character of file.manifest.characters) {
        addMapping(regularFilesByCharacter, versionedRefKey(character), file.filename);
      }
      for (const media of file.manifest.media) {
        for (const characterRef of media.characterRefs || []) {
          addMapping(batchableMediaByCharacter, versionedRefKey(characterRef), {
            externalId: media.externalId,
            version: media.version,
            batchFile: file.filename,
          });
        }
      }
    }
    for (const media of unbatchable) {
      for (const characterRef of media.characterRefs) {
        addMapping(unbatchableMediaByCharacter, versionedRefKey(characterRef), {
          externalId: media.externalId,
          version: media.version,
          bytes: media.bytes,
          reason: media.reason,
        });
      }
    }
    const metadataOnlyCharacters = packInput.characters
      .filter((item) => !(regularFilesByCharacter.get(versionedRefKey(item)) || []).length)
      .map((item) => ({ ...item, profileMedia: null }))
      .sort((left, right) => versionedRefKey(left).localeCompare(versionedRefKey(right)));
    const metadataOnlyFile = metadataOnlyCharacters.length
      ? {
          filename: "batch-0000-characters.json",
          bytes: 0,
          mediaCount: 0,
          characterCount: metadataOnlyCharacters.length,
          kind: "character_inventory",
          manifest: {
            schema: PACK_SCHEMA,
            schemaVersion: PACK_SCHEMA_VERSION,
            accountScope: packInput.accountScope,
            exportId: `${String(packInput.exportId).slice(0, 177)}-characters-0000`,
            exportedAt: packInput.exportedAt,
            characters: metadataOnlyCharacters,
            media: [],
            lineage: [],
          },
        }
      : null;
    const files = metadataOnlyFile ? [metadataOnlyFile, ...mediaFiles] : mediaFiles;
    const characterInventory = [...packInput.characters]
      .sort((left, right) => versionedRefKey(left).localeCompare(versionedRefKey(right)))
      .map((character) => {
        const key = versionedRefKey(character);
        const regularFiles = regularFilesByCharacter.get(key) || [];
        const batchFiles = regularFiles.length
          ? regularFiles
          : metadataOnlyFile
            ? [metadataOnlyFile.filename]
            : [];
        return {
          externalId: character.externalId,
          version: character.version,
          externalModelId: character.externalModelId,
          canonicalBatch: batchFiles[0] || null,
          repeatedIn: batchFiles.slice(1),
          batchFiles,
          batchableMedia: batchableMediaByCharacter.get(key) || [],
          unbatchableMedia: unbatchableMediaByCharacter.get(key) || [],
          inventoryOnly: regularFiles.length === 0,
        };
      });
    const unassignedBatchable = [];
    for (const file of mediaFiles) {
      for (const media of file.manifest.media) {
        if (!(media.characterRefs || []).length) {
          unassignedBatchable.push({
            externalId: media.externalId,
            version: media.version,
            batchFile: file.filename,
          });
        }
      }
    }
    const unassignedUnbatchable = unbatchable
      .filter((media) => !media.characterRefs.length)
      .map(({ externalId, version, bytes, reason }) => ({ externalId, version, bytes, reason }));
    return {
      index: {
        schema: "castle.clipmode-pack-batch-index.v1",
        schemaVersion: 1,
        sourceExportId: packInput.exportId,
        generatedAt: packInput.exportedAt,
        limits: { maxBytes, maxMedia },
        characterIdentityPolicy: "same accountScope + externalId + version + identical metadata",
        characters: characterInventory,
        metadataOnlyBatch: metadataOnlyFile ? metadataOnlyFile.filename : null,
        batches: files.map((item) => ({
          file: item.filename,
          kind: item.kind || "media",
          bytes: item.bytes,
          media: item.mediaCount,
          characters: item.characterCount,
        })),
        unassigned: {
          batchableMedia: unassignedBatchable,
          unbatchableMedia: unassignedUnbatchable,
        },
        unbatchable,
      },
      files,
      unbatchable,
    };
  }

  function buildManifests(
    origin,
    accountFingerprint,
    checkpoint,
    models,
    creations,
    references,
    finishedAt,
  ) {
    const packCharacters = [];
    const sourceCharacters = [];
    const characterByModel = new Map();
    for (const model of models) {
      const character = normalizedCharacter(model);
      if (!character || characterByModel.has(character.externalModelId)) continue;
      characterByModel.set(character.externalModelId, character);
      packCharacters.push(character);
      sourceCharacters.push({
        ...character,
        catalogSources: Array.isArray(model.__castleCatalogSources)
          ? [...model.__castleCatalogSources]
          : [],
        catalogRecordStatus: model.__castleMetadataOnlyFromModelId
          ? "metadata_only_from_model_id"
          : "catalog_record",
        trainedWeightExport:
          typeof model.safetensor_url === "string" && model.safetensor_url.trim()
            ? "candidate_url_present"
            : "unavailable",
      });
    }
    packCharacters.sort((left, right) => left.externalId.localeCompare(right.externalId));
    sourceCharacters.sort((left, right) => left.externalId.localeCompare(right.externalId));

    const sourceMedia = [];
    const packMedia = [];
    const mediaByteSizes = new Map();
    for (const record of creations) {
      const id = stableRecordId(record);
      if (!id) continue;
      const completed = checkpoint.completedMedia[id] || null;
      const modelId = typeof record.model_id === "string" ? record.model_id.trim() : "";
      const character = characterByModel.get(modelId);
      const displayName = boundedText(
        leafName(record.file_name),
        `ClipMode generation ${id.slice(-8)}`,
        300,
      );
      sourceMedia.push({
        externalId: externalId("generation", id),
        version: "v1",
        sourceId: id,
        sourceModelId: modelId || null,
        sourceMetadataPath: `metadata/items/${safePathComponent(id)}.json`,
        displayName,
        createdAt: validIsoOrNull(record.created_at),
        prompt: typeof record.prompt === "string" ? record.prompt : null,
        archiveStatus: completed ? "archived" : "unarchived",
        unarchivedError: completed
          ? null
          : [...checkpoint.errors].reverse().find(
              (item) => item.phase === "media" && item.id === id,
            )?.error || "MEDIA_NOT_ARCHIVED",
        download: completed,
      });
      if (completed) {
        mediaByteSizes.set(`${completed.externalId}\u0000v1`, completed.bytes);
        packMedia.push({
          externalId: completed.externalId,
          version: "v1",
          displayName,
          kind: completed.kind,
          visualClass: "unknown",
          characterRefs: character
            ? [{ externalId: character.externalId, version: character.version }]
            : [],
          mime: completed.mime,
          localPath: completed.localPath,
          bytes: completed.bytes,
          sha256: completed.sha256,
          createdAt: completed.createdAt,
        });
      }
    }
    sourceMedia.sort((left, right) => left.externalId.localeCompare(right.externalId));
    packMedia.sort((left, right) => left.externalId.localeCompare(right.externalId));

    const sourceReferences = [];
    for (const reference of references) {
      const character = characterByModel.get(reference.modelId);
      const downloaded = reference.download || null;
      sourceReferences.push({
        modelId: reference.modelId,
        candidateId: reference.candidateId,
        candidateIndex: reference.candidateIndex,
        sourceUrlPresent: reference.sourceUrlPresent,
        sourceMetadataPath: reference.sourceMetadataPath,
        archiveStatus: downloaded ? "archived" : "unarchived",
        unarchivedError: downloaded
          ? null
          : [...checkpoint.errors].reverse().find(
              (item) => item.phase === "reference-media" && item.id === reference.checkpointKey,
            )?.error || "REFERENCE_MEDIA_NOT_ARCHIVED",
        download: downloaded,
      });
      if (downloaded) {
        mediaByteSizes.set(`${downloaded.externalId}\u0000v1`, downloaded.bytes);
        packMedia.push({
          externalId: downloaded.externalId,
          version: "v1",
          displayName: boundedText(
            leafName(downloaded.serverFilename),
            `ClipMode reference ${reference.candidateIndex + 1}`,
            300,
          ),
          kind: downloaded.kind,
          visualClass: "unknown",
          characterRefs: character
            ? [{ externalId: character.externalId, version: character.version }]
            : [],
          mime: downloaded.mime,
          localPath: downloaded.localPath,
          bytes: downloaded.bytes,
          sha256: downloaded.sha256,
          createdAt: downloaded.createdAt,
        });
      }
    }
    sourceReferences.sort((left, right) =>
      `${left.modelId}\n${left.candidateId}`.localeCompare(`${right.modelId}\n${right.candidateId}`));
    packMedia.sort((left, right) => left.externalId.localeCompare(right.externalId));
    const timestamp = checkpoint.startedAt.replace(/[^0-9]/g, "").slice(0, 14);
    const scope = `clipmode-${(accountFingerprint || "primary").slice(0, 16)}`;
    const pack = {
      schema: PACK_SCHEMA,
      schemaVersion: PACK_SCHEMA_VERSION,
      accountScope: scope,
      exportId: `clipmode-browser-${timestamp || "backup"}`,
      exportedAt: finishedAt,
      characters: packCharacters,
      media: packMedia,
      lineage: [],
    };
    const batches = buildPackInputBatches(pack, mediaByteSizes);
    return {
      source: {
        schema: SOURCE_SCHEMA,
        schemaVersion: 1,
        toolVersion: TOOL_VERSION,
        provider: "clipmode",
        sourceOrigin: origin,
        accountFingerprint,
        maximumArchivedMediaBytes: MAX_DOWNLOAD_BYTES,
        startedAt: checkpoint.startedAt,
        completedAt: finishedAt,
        characters: sourceCharacters,
        media: sourceMedia,
        referenceMedia: sourceReferences,
        errors: checkpoint.errors,
        summary: {
          characters: sourceCharacters.length,
          galleryItems: creations.length,
          downloaded: sourceMedia.filter((item) => item.download).length,
          failed: sourceMedia.filter((item) => !item.download).length,
          referenceCandidates: sourceReferences.length,
          referencesDownloaded: sourceReferences.filter((item) => item.download).length,
          unbatchableForPacking: batches.unbatchable.length,
          errors: checkpoint.errors.length,
        },
        packaging: {
          batchIndex: "pack-input-batches/index.json",
          batches: batches.index.batches,
          unbatchable: batches.unbatchable,
        },
      },
      pack,
      batches,
    };
  }

  async function publishSuccessfulManifests(rootHandle, manifests, completedAt) {
    const releaseId = safePathComponent(
      `${manifests.pack.exportId}\u0000${completedAt}\u0000${manifests.source.accountFingerprint}`,
    );
    const releaseRoot = `releases/${releaseId}`;
    const releaseFiles = [
      ["source-manifest.json", stableJson(manifests.source)],
      ["castle-pack-input.json", stableJson(manifests.pack)],
      ["pack-input-batches/index.json", stableJson(manifests.batches.index)],
    ];
    for (const batch of manifests.batches.files) {
      releaseFiles.push([
        `pack-input-batches/${batch.filename}`,
        stableJson(batch.manifest),
      ]);
    }

    // A versioned release is staging until complete.json exists. Consumers
    // must treat latest-success.json as the commit pointer.
    for (const [relativePath, contents] of releaseFiles) {
      await writeFile(rootHandle, `${releaseRoot}/${relativePath}`, contents);
    }
    await writeFile(rootHandle, `${releaseRoot}/complete.json`, stableJson({
      schema: "castle.clipmode-browser-release.v1",
      schemaVersion: 1,
      releaseId,
      completedAt,
      sourceManifest: `${releaseRoot}/source-manifest.json`,
      packInput: `${releaseRoot}/castle-pack-input.json`,
      batchIndex: `${releaseRoot}/pack-input-batches/index.json`,
    }));

    const pointer = {
      schema: "castle.clipmode-browser-latest-success.v1",
      schemaVersion: 1,
      releaseId,
      completedAt,
      sourceManifest: `${releaseRoot}/source-manifest.json`,
      packInput: `${releaseRoot}/castle-pack-input.json`,
      batchIndex: `${releaseRoot}/pack-input-batches/index.json`,
      rootCompatibilityCopiesPublished: false,
    };
    await writeFile(rootHandle, "latest-success.json", stableJson(pointer));
    return pointer;
  }

  async function runBackup(browser, selectedHandle, report) {
    if (!EXPECTED_ORIGINS.includes(browser.location.origin)) {
      throw new BackupError("WRONG_ORIGIN", "open https://clipmode.ai/gallery first");
    }
    if (!browser.isSecureContext || !browser.showDirectoryPicker) {
      throw new BackupError("FILE_SYSTEM_ACCESS_UNAVAILABLE", "use desktop Chrome or Edge");
    }
    if (!browser.crypto || !browser.crypto.subtle) {
      throw new BackupError("WEB_CRYPTO_UNAVAILABLE");
    }
    const token = browser.localStorage.getItem("o_a") || browser.localStorage.getItem("token");
    if (!token) throw new BackupError("LOGIN_REQUIRED", "sign in to ClipMode and retry");
    const accountIdentity = await deriveAccountFingerprint(token, browser.crypto);
    const accountFingerprint = accountIdentity.fingerprint;
    const rootHandle = selectedHandle.name === BACKUP_DIRECTORY
      ? selectedHandle
      : await selectedHandle.getDirectoryHandle(BACKUP_DIRECTORY, { create: true });
    const controller = new AbortController();
    const now = new Date().toISOString();
    const previous = validateCheckpoint(
      await readJsonFile(rootHandle, "checkpoint.json"),
      browser.location.origin,
      accountFingerprint,
    );
    const checkpoint = previous || freshCheckpoint(
      browser.location.origin,
      accountFingerprint,
      accountIdentity.method,
      now,
    );
    checkpoint.accountFingerprintMethod = accountIdentity.method;
    checkpoint.errors = [];
    checkpoint.status = "running";
    checkpoint.updatedAt = now;
    checkpoint.currentAttemptId = safePathComponent(`${now}\u0000${accountFingerprint}`);

    const context = {
      origin: browser.location.origin,
      fetchFunction: browser.fetch.bind(browser),
      token,
      rootHandle,
      signal: controller.signal,
      cryptoObject: browser.crypto,
      checkpoint,
      report,
      saveCheckpoint: async () => {
        checkpoint.updatedAt = new Date().toISOString();
        await writeFile(rootHandle, "checkpoint.json", stableJson(checkpoint));
      },
      addError: (phase, id, message) => {
        checkpoint.errors.push({
          at: new Date().toISOString(),
          phase,
          id,
          error: String(message).slice(0, 500),
        });
      },
      removeErrorsFor: (phase, id) => {
        checkpoint.errors = checkpoint.errors.filter(
          (item) => item.phase !== phase || item.id !== id,
        );
      },
    };
    browser.__castleClipModeBackupController = controller;
    await context.saveCheckpoint();

    let summary = null;
    try {
      report("確認 ClipMode 帳號與 Gallery 索引…");
      const modelIndex = validateModelIndexPayload(
        await readOnlyJson(
          context.fetchFunction,
          context.origin,
          token,
          "/api/users/generation-model-ids",
          context.signal,
        ),
      );
      await writeFile(rootHandle, "metadata/generation-model-ids.json", stableJson(modelIndex));
      const privateModels = await scanPrivateModels(context);
      const classicPrivateModels = await scanClassicPrivateModels(context);
      const models = mergeModelCatalogs([
        {
          source: "generation-model-ids.model_ids",
          records: modelIndex.model_ids.map((id) => ({
            id: id.trim(),
            __castleMetadataOnlyFromModelId: true,
          })),
        },
        { source: "generation-model-ids.models", records: modelIndex.models },
        { source: "classic-private-models", records: classicPrivateModels },
        { source: "private-models", records: privateModels },
      ]);
      const references = await scanReferenceMedia(context, models);
      const modelIds = Array.from(new Set([
        ...modelIndex.model_ids.map((item) => item.trim()),
        ...models.map(modelRecordId).filter(Boolean),
      ])).sort();
      const creations = await scanGallery(context, modelIds);
      for (let index = 0; index < creations.length; index += 1) {
        if (context.signal.aborted) throw new DOMException("Stopped", "AbortError");
        const record = creations[index];
        const id = stableRecordId(record);
        try {
          await downloadCreation(context, record, index + 1, creations.length);
        } catch (error) {
          if (
            context.signal.aborted ||
            (error instanceof BackupError && error.code === "LOGIN_REQUIRED")
          ) {
            throw error;
          }
          context.addError("media", id, sanitizeError(error));
          await context.saveCheckpoint();
        }
        await delay(125, context.signal);
      }
      const finishedAt = new Date().toISOString();
      const manifests = buildManifests(
        context.origin,
        accountFingerprint,
        checkpoint,
        models,
        creations,
        references,
        finishedAt,
      );
      const release = await publishSuccessfulManifests(rootHandle, manifests, finishedAt);
      checkpoint.status = checkpoint.errors.length ? "completed_with_errors" : "completed";
      checkpoint.lastSuccessfulRelease = release;
      summary = manifests.source.summary;
    } catch (error) {
      checkpoint.status = context.signal.aborted ? "stopped" : "failed";
      if (!context.signal.aborted) context.addError("run", null, sanitizeError(error));
      throw error;
    } finally {
      checkpoint.updatedAt = new Date().toISOString();
      await writeFile(rootHandle, "errors.json", stableJson(checkpoint.errors));
      await writeFile(rootHandle, "last-attempt.json", stableJson({
        schema: "castle.clipmode-browser-attempt.v1",
        schemaVersion: 1,
        attemptId: checkpoint.currentAttemptId,
        status: checkpoint.status,
        updatedAt: checkpoint.updatedAt,
        lastSuccessfulRelease: checkpoint.lastSuccessfulRelease || null,
        errors: checkpoint.errors.length,
      }));
      await context.saveCheckpoint();
      delete browser.__castleClipModeBackupController;
    }
    return summary;
  }

  function install(browser) {
    if (!browser.document || browser.document.getElementById(PANEL_ID)) return;
    const panel = browser.document.createElement("section");
    panel.id = PANEL_ID;
    panel.setAttribute("role", "region");
    panel.setAttribute("aria-label", "Castle ClipMode backup");
    panel.style.cssText = [
      "position:fixed",
      "right:18px",
      "bottom:18px",
      "z-index:2147483647",
      "width:min(390px,calc(100vw - 36px))",
      "padding:16px",
      "border:1px solid #4f67a8",
      "border-radius:14px",
      "background:#101827",
      "color:#f8fafc",
      "box-shadow:0 18px 50px rgba(0,0,0,.45)",
      "font:13px/1.45 system-ui,sans-serif",
    ].join(";");
    panel.innerHTML = [
      '<div style="font-size:16px;font-weight:700;margin-bottom:6px">Castle · ClipMode 唯讀備份</div>',
      '<div style="color:#a5b4fc;margin-bottom:10px">API 只送同源 GET；外部參考檔 GET 不帶登入資料。</div>',
      '<div data-role="status" style="min-height:42px;padding:9px;border-radius:8px;background:#172033;margin-bottom:10px">準備就緒。選同一個資料夾即可續跑。</div>',
      '<div style="display:flex;gap:8px">',
      '<button data-role="start" type="button" style="flex:1;border:0;border-radius:8px;padding:10px 12px;background:#4f6bed;color:white;font-weight:700;cursor:pointer">開始／續跑</button>',
      '<button data-role="stop" type="button" disabled style="border:1px solid #64748b;border-radius:8px;padding:10px 12px;background:#1e293b;color:white;cursor:pointer">停止</button>',
      '<button data-role="close" type="button" style="border:1px solid #64748b;border-radius:8px;padding:10px 12px;background:#1e293b;color:white;cursor:pointer">關閉</button>',
      "</div>",
    ].join("");
    browser.document.body.appendChild(panel);
    const status = panel.querySelector('[data-role="status"]');
    const start = panel.querySelector('[data-role="start"]');
    const stop = panel.querySelector('[data-role="stop"]');
    const close = panel.querySelector('[data-role="close"]');
    let running = false;
    const report = (message) => {
      status.textContent = message;
    };
    start.addEventListener("click", async () => {
      if (running) return;
      if (!EXPECTED_ORIGINS.includes(browser.location.origin)) {
        report("請先開啟 https://clipmode.ai/gallery");
        return;
      }
      if (!browser.showDirectoryPicker) {
        report("此瀏覽器沒有 File System Access API；請改用桌面版 Chrome 或 Edge。");
        return;
      }
      try {
        const directory = await browser.showDirectoryPicker({
          id: "castle-clipmode-backup",
          mode: "readwrite",
        });
        running = true;
        start.disabled = true;
        stop.disabled = false;
        close.disabled = true;
        const summary = await runBackup(browser, directory, report);
        const oversized = summary.unbatchableForPacking
          ? `；${summary.unbatchableForPacking} 件超過單批上限，已保留並列入 index`
          : "";
        const errors = summary.errors
          ? `；${summary.errors} 個項目待重試，詳見 errors.json`
          : "";
        report(
          `完成：${summary.characters} 個角色、Gallery ${summary.downloaded}/${summary.galleryItems}、` +
          `參考素材 ${summary.referencesDownloaded}/${summary.referenceCandidates}${oversized}${errors}。`,
        );
      } catch (error) {
        if (error && error.name === "AbortError") report("已停止。下次選同一個資料夾會續跑。");
        else report(`備份未完成：${sanitizeError(error)}`);
      } finally {
        running = false;
        start.disabled = false;
        stop.disabled = true;
        close.disabled = false;
      }
    });
    stop.addEventListener("click", () => {
      const controller = browser.__castleClipModeBackupController;
      if (controller) controller.abort();
      report("正在安全停止並寫入 checkpoint…");
    });
    close.addEventListener("click", () => {
      if (!running) panel.remove();
    });
  }

  return Object.freeze({
    ALLOWED_PATHS,
    BACKUP_DIRECTORY,
    BackupError,
    EXPECTED_ORIGINS,
    MAX_DOWNLOAD_BYTES,
    PACK_BATCH_MAX_BYTES,
    PACK_BATCH_MAX_MEDIA,
    PACK_SCHEMA,
    PACK_SCHEMA_VERSION,
    SOURCE_SCHEMA,
    TOOL_VERSION,
    buildManifests,
    buildPackInputBatches,
    boundedResponseBlob,
    contentAddressedBlobPath,
    decodeJwtSubject,
    deriveAccountFingerprint,
    externalReadOnlyFetch,
    extensionFromName,
    headerFilename,
    groupGalleryModelIds,
    install,
    modelRecordId,
    mergeModelCatalogs,
    normalizedMime,
    pathIsAllowed,
    publicHttpsUrl,
    publishSuccessfulManifests,
    resolveMediaType,
    runBackup,
    safePathComponent,
    shouldContinuePage,
    sniffMime,
    stableJson,
    stableRecordId,
    storeContentAddressedBlob,
    validateCheckpoint,
    validateClassicPrivateModelsPage,
    validateGalleryPage,
    validateGalleryPageProgress,
    validateModelIndexPayload,
    validatePrivateModelsPage,
    validateReferenceImagesPayload,
  });
});

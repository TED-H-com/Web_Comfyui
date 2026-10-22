"use strict";

(function attachCastleLoginReturn(globalObject) {
  const REVIEW_PATH_RE =
    /^\/api\/orchestrator\/runs\/run_[A-Za-z0-9](?:[A-Za-z0-9_-]{0,74}[A-Za-z0-9])?\/review-page$/;
  const ARTIFACT_ID_RE = /^art_[A-Za-z0-9]{8,80}$/;
  const SHA256_RE = /^[0-9a-f]{64}$/;
  const CREATED_AT_US_RE = /^[1-9][0-9]{0,15}$/;
  const QUERY_KEYS = new Set([
    "binding",
    "proxy_artifact_id",
    "proxy_artifact_created_at_us",
    "proxy_sha256",
    "cover_artifact_id",
    "cover_artifact_created_at_us",
    "cover_sha256",
    "media_revision",
    "release_binding_sha256",
  ]);

  function hasOneMatchingValue(params, key, predicate) {
    const values = params.getAll(key);
    return values.length === 1 && predicate(values[0]);
  }

  function hasExactReviewBinding(params) {
    const keys = [...params.keys()];
    if (keys.length !== QUERY_KEYS.size || keys.some((key) => !QUERY_KEYS.has(key))) return false;
    if (!hasOneMatchingValue(params, "binding", (value) => value === "v2")) return false;
    if (!hasOneMatchingValue(params, "proxy_artifact_id", (value) => ARTIFACT_ID_RE.test(value))) {
      return false;
    }
    if (!hasOneMatchingValue(params, "proxy_artifact_created_at_us", (value) => {
      if (!CREATED_AT_US_RE.test(value)) return false;
      const parsed = Number(value);
      return Number.isSafeInteger(parsed) && parsed > 0;
    })) {
      return false;
    }
    if (!hasOneMatchingValue(params, "proxy_sha256", (value) => SHA256_RE.test(value))) {
      return false;
    }
    if (!hasOneMatchingValue(params, "cover_artifact_id", (value) => ARTIFACT_ID_RE.test(value))) {
      return false;
    }
    if (!hasOneMatchingValue(params, "cover_artifact_created_at_us", (value) => {
      if (!CREATED_AT_US_RE.test(value)) return false;
      const parsed = Number(value);
      return Number.isSafeInteger(parsed) && parsed > 0;
    })) {
      return false;
    }
    return ["cover_sha256", "media_revision", "release_binding_sha256"].every((key) =>
      hasOneMatchingValue(params, key, (value) => SHA256_RE.test(value)),
    );
  }

  function safeReviewReturnPath(rawPath, origin) {
    if (
      typeof rawPath !== "string" ||
      !rawPath ||
      rawPath.length > 4096 ||
      rawPath.trim() !== rawPath ||
      !rawPath.startsWith("/") ||
      rawPath.startsWith("//")
    ) {
      return "";
    }
    try {
      const base = new URL(origin);
      if (
        (base.protocol !== "https:" && base.protocol !== "http:") ||
        base.username ||
        base.password ||
        base.origin !== origin
      ) {
        return "";
      }
      const target = new URL(rawPath, base);
      if (
        target.origin !== base.origin ||
        target.username ||
        target.password ||
        target.hash ||
        !REVIEW_PATH_RE.test(target.pathname) ||
        !hasExactReviewBinding(target.searchParams)
      ) {
        return "";
      }
      return `${target.pathname}${target.search}`;
    } catch (_) {
      return "";
    }
  }

  function reviewReturnPathFromLocation(locationLike) {
    if (!locationLike) return "";
    try {
      const params = new URLSearchParams(String(locationLike.search || ""));
      const values = params.getAll("next");
      if (values.length !== 1) return "";
      return safeReviewReturnPath(values[0], String(locationLike.origin || ""));
    } catch (_) {
      return "";
    }
  }

  globalObject.CastleLoginReturn = Object.freeze({
    safeReviewReturnPath,
    reviewReturnPathFromLocation,
  });
})(globalThis);

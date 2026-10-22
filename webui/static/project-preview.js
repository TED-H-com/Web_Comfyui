(() => {
  "use strict";

  const ENDPOINT = "/api/projects/dry-run";
  const BRIEF_ENDPOINT = "/api/projects/brief";
  const ROUTE_REVIEW_ENDPOINT = "/api/projects/brief/routes";
  const FORMAL_PACK_ENDPOINT = "/api/projects/brief/formal-pack";
  const PRODUCTION_ENDPOINT = "/api/productions";
  const PROVIDERS_ENDPOINT = "/api/providers";
  const AUTOIP_MULTI_SUBJECT_LIMIT = 4;
  const STALE_SUBMISSION_UNCERTAIN_MS = 48 * 60 * 60 * 1000;
  const HIDDEN_RECOVERED_INTENT_PREFIX = "castle.project-preview.hidden-recovered-intent.v1";
  const INTENT_STATES = Object.freeze([
    "pending", "preparing", "submission_uncertain", "submitted", "queued", "running",
    "completed", "failed", "cancelled", "timeout", "abandoned",
  ]);
  const TERMINAL_INTENT_STATES = Object.freeze(["completed", "failed", "cancelled", "timeout", "abandoned"]);
  const INTENT_COPY = Object.freeze({
    pending: ["等待處理", "工作已保存，正在等待 Castle worker。"],
    preparing: ["安全準備中", "Castle 正在準備工作；尚未證明供應商已收到。"],
    submission_uncertain: ["送出狀態不確定", "請勿重新送出；先讓 reconciliation 查清楚，避免重複扣點。"],
    submitted: ["已送出", "持久紀錄顯示工作已送交執行資源。"],
    queued: ["排隊中", "執行資源已接收，目前正在佇列等待。"],
    running: ["生成中", "執行資源正在處理這個鏡頭。"],
    completed: ["執行完成", "等待 Castle 驗證輸出並綁回這個鏡頭。"],
    failed: ["工作失敗", "這裡不直接重送；先查明原因，再建立新的明確版本。"],
    cancelled: ["工作已取消", "工作已停止，這裡不會自動重新送出。"],
    timeout: ["工作逾時", "供應商狀態仍可能不確定，請勿直接重送。"],
    abandoned: ["停止追蹤", "Castle 已停止追蹤；這裡不會重新送出。"],
  });
  const BRIEF_SCHEMA = "castle.production-brief.v1";
  const REQUEST_SCHEMA = "castle.project-dry-run.request.v1";
  const RESPONSE_SCHEMA = "castle.project-dry-run.response.v1";
  const ERROR_SCHEMA = "castle.project-dry-run.error.v1";
  const MAX_REQUEST_BYTES = (2 * 1024 * 1024) + (64 * 1024);
  const TOP_LEVEL_RESPONSE_KEYS = Object.freeze([
    "schema_version", "ok", "mode", "static_plan_ready", "execution_authorized",
    "pack", "authority", "side_effects", "gate_report", "summary",
    "route_candidates", "expected_expensive_nodes", "dag", "preview_hash",
  ]);
  const ZERO_SIDE_EFFECT_KEYS = Object.freeze([
    "project_records_created", "orchestrator_runs_created", "tasks_scheduled",
    "scheduler_state_changes", "comfy_prompts_queued", "provider_transports_attempted",
    "credits_authorized", "credits_spent", "publishing_attempts",
  ]);
  const FINDING_COPY = Object.freeze({
    unknown_provider: "包含目前無法辨識的生成資源",
    provider_domain_mismatch: "生成資源的本機／外部分類不正確",
    external_provider_forbidden: "專案尚未允許外部供應商",
    route_cost_unknown: "外部路線沒有可控的點數估算",
    route_exceeds_hard_cap: "候選路線超過專案點數上限",
    external_budget_zero: "外部路線不能使用零點數預算",
    route_not_release_ready: "候選模組目前尚未啟用或無法連線",
    golden_evidence_missing: "候選模組缺少目前有效的可用性證明",
    golden_evidence_wrong_kind: "可用性證明資料類型不正確",
    entity_kind_unknown: "有角色／主體尚未確認類型",
    visual_class_unknown: "有角色尚未分類為動漫、寫實或插畫",
    consent_revoked: "真人或素材的同意已撤銷",
    consent_unverified: "真人或素材的使用權尚未核准",
    person_consent_required: "真人角色缺少可執行的同意紀錄",
    consent_ref_missing: "核准同意缺少內部參照",
    consent_evidence_required: "核准同意缺少私密證據",
    person_profile_required: "真人角色缺少精確 profile 圖",
    mixed_identity_class: "同一鏡頭混用了動漫與寫實身份管線",
    route_visual_class_mismatch: "候選模組不支援鏡頭中的角色類型",
    route_capability_mismatch: "候選模組和鏡頭需求不相符",
    route_options_missing: "候選模組缺少鏡頭要求的選項",
    required_input_missing: "鏡頭缺少必要的圖片、影片或聲音素材",
    unknown_route_variant: "鏡頭指向不存在的候選模組",
    scene_state_chain_broken: "前後場景的角色／服裝狀態接不起來",
    duration_mismatch: "鏡頭總長和專案目標長度不一致",
    dialogue_timing_invalid: "對白時間超出鏡頭或互相重疊",
    factual_claim_evidence_missing: "內容中的事實敘述缺少來源",
  });
  const CAPABILITY_COPY = Object.freeze({
    text_to_image: "文字生圖",
    image_to_image: "圖片重繪",
    image_to_video: "圖片生影片",
    text_to_video: "文字生影片",
    lip_sync: "對嘴",
    dance: "跳舞",
    multi_subject_video: "參考圖影片",
    text_to_speech: "文字轉語音",
    voice_clone: "音色模仿",
  });
  const TASK_COPY = Object.freeze({
    "project.validate_pack": "檢查專案",
    "project.resolve_assets": "整理素材",
    "project.resolve_routes": "解析候選模組",
    "project.prepare_timeline": "建立時間軸",
    "project.assemble": "組合成片",
    "project.quality_gate": "品質檢查",
    "project.human_review": "人工確認",
    "project.package": "輸出封裝",
    "shot.resolve_inputs": "解析鏡頭素材",
    "shot.resolve_route": "選擇鏡頭模組",
    "shot.visual_generate": "生成畫面",
    "shot.voice_generate": "生成聲音",
    "shot.audio_prepare": "整理聲音",
    "shot.compose": "合成鏡頭",
    "shot.quality_gate": "鏡頭品質檢查",
  });
  const ERROR_COPY = Object.freeze({
    authentication_required: "登入已失效，請重新登入後再試。",
    preview_forbidden: "只有管理員或指定操作員可以檢查專案。",
    forbidden_origin: "這個頁面來源未通過安全檢查。",
    unsupported_media_type: "專案必須使用 JSON 格式。",
    request_too_large: "專案太大，請拆成較小的 production pack。",
    dry_run_request_invalid: "JSON 或最外層格式不正確。",
    production_pack_invalid: "Production pack 的欄位或資料型別不正確。",
    preview_limit_exceeded: "專案超過此預覽工具的鏡頭、路線或複雜度上限。",
    dry_run_unavailable: "專案檢查暫時無法使用，請稍後再試。",
  });

  function el(tag, options = {}, children = []) {
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

  function exactKeys(value, keys) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return false;
    const observed = Object.keys(value).sort();
    const expected = [...keys].sort();
    return observed.length === expected.length
      && observed.every((key, index) => key === expected[index]);
  }

  function safeText(value, fallback = "", maximum = 500) {
    if (typeof value !== "string") return fallback;
    const normalized = value.trim();
    return normalized && normalized.length <= maximum ? normalized : fallback;
  }

  function safeId(value) {
    const normalized = safeText(value, "", 128);
    return /^[a-z0-9][a-z0-9._:-]{0,127}$/.test(normalized) ? normalized : "";
  }

  function pathId(value) {
    const normalized = safeId(value);
    if (!normalized) throw new TypeError("Invalid typed path identifier");
    return normalized;
  }

  function safeHash(value) {
    return typeof value === "string" && /^[0-9a-f]{64}$/.test(value) ? value : "";
  }

  function safeCount(value, maximum = 100000) {
    return Number.isSafeInteger(value) && value >= 0 && value <= maximum ? value : null;
  }

  function safeArray(value, maximum) {
    return Array.isArray(value) && value.length <= maximum ? value : null;
  }

  function containsSensitiveField(value, depth = 0) {
    if (depth > 18) return true;
    if (Array.isArray(value)) return value.some((item) => containsSensitiveField(item, depth + 1));
    if (!value || typeof value !== "object") return false;
    return Object.entries(value).some(([key, child]) => {
      const normalized = String(key).toLowerCase().replace(/[^a-z0-9]+/g, "_");
      if (
        ["api_key", "apikey", "authorization", "cookie", "cookies", "credential",
          "password", "private_key", "refresh_token", "secret", "session", "token"].includes(normalized)
        || normalized.endsWith("_api_key")
        || normalized.endsWith("_cookie")
        || normalized.endsWith("_password")
        || normalized.endsWith("_secret")
        || normalized.endsWith("_token")
      ) return true;
      return containsSensitiveField(child, depth + 1);
    });
  }

  function normalizeRouteReviewResponse(raw, expectedDraftHash) {
    if (
      !raw || raw.schema_version !== "castle.storyboard-route-review.response.v1"
      || raw.ok !== true || containsSensitiveField(raw)
      || raw.authority?.computed_by !== "castle_server"
      || raw.authority?.runtime_catalog_checked !== true
      || raw.authority?.selection_authorized !== false
      || raw.authority?.execution_authorized !== false
      || raw.authority?.credentials_accessed !== false
      || Object.values(raw.side_effects || {}).some((value) => value !== 0)
      || !raw.route_review || raw.route_review.schema_version !== "castle.storyboard-route-review.v1"
      || raw.route_review.draft_hash !== expectedDraftHash
      || raw.route_review.selection_authorized !== false
      || raw.route_review.execution_authorized !== false
      || !safeHash(raw.route_review.review_hash)
      || !Array.isArray(raw.route_review.shots)
    ) return null;
    const shots = [];
    for (const shot of raw.route_review.shots) {
      const shotId = safeId(shot?.shot_id);
      const visualClass = shot?.visual_class === null ? null : safeId(shot?.visual_class);
      const capability = safeId(shot?.required_capability);
      const blockers = safeArray(shot?.blockers, 64);
      const candidateProfiles = safeArray(shot?.candidate_profiles, 256);
      const selectableProfileKeys = safeArray(shot?.selectable_profile_keys, 256);
      if (
        !shotId || !["blocked", "ready_to_prepare", "awaiting_route_readiness"].includes(shot.status)
        || !capability || !blockers || blockers.some((item) => !safeId(item))
        || !candidateProfiles || !selectableProfileKeys
        || selectableProfileKeys.some((item) => !safeText(item, "", 257))
        || new Set(selectableProfileKeys).size !== selectableProfileKeys.length
        || shot.selected_profile_key !== null
        || (visualClass !== null && !["anime", "realistic", "illustration"].includes(visualClass))
      ) return null;
      const selectableKeys = new Set(selectableProfileKeys);
      const candidates = [];
      for (const profile of candidateProfiles) {
        const reasons = safeArray(profile?.unavailable_reason_codes, 64);
        const optionEntries = profile?.option_values
          && typeof profile.option_values === "object"
          && !Array.isArray(profile.option_values)
          ? Object.entries(profile.option_values)
          : null;
        if (
          !safeText(profile?.profile_key, "", 257) || !safeId(profile.provider_id)
          || !safeId(profile.variant_id) || !safeId(profile.action_id)
          || !safeId(profile.feature_id) || !safeId(profile.capability)
          || !["anime", "realistic", "illustration"].includes(profile.visual_class)
          || profile.visual_class !== visualClass || profile.capability !== capability
          || typeof profile.selectable_for_preparation !== "boolean"
          || !reasons || reasons.some((item) => !safeId(item))
          || !optionEntries || optionEntries.length > 64
          || optionEntries.some(([key, values]) => (
            !safeId(key) || !Array.isArray(values) || !values.length || values.length > 64
            || values.some((value) => !["string", "number", "boolean"].includes(typeof value)
              || (typeof value === "number" && !Number.isFinite(value)))
          ))
          || containsSensitiveField(profile.option_values)
          || profile.selectable_for_preparation !== selectableKeys.has(profile.profile_key)
        ) return null;
        const options = {};
        for (const [key, values] of optionEntries) options[key] = Object.freeze([...values]);
        candidates.push(Object.freeze({
          key: profile.profile_key,
          providerId: profile.provider_id,
          variantId: profile.variant_id,
          actionId: safeId(profile.action_id),
          featureId: safeId(profile.feature_id),
          capability: profile.capability,
          visualClass: profile.visual_class,
          selectable: profile.selectable_for_preparation,
          reasons: Object.freeze([...reasons]),
          options: Object.freeze(options),
        }));
      }
      if (
        new Set(candidates.map((profile) => profile.key)).size !== candidates.length
        || [...selectableKeys].some((key) => !candidates.some((profile) => profile.key === key))
        || (shot.status === "blocked") !== (blockers.length > 0)
        || (shot.status === "ready_to_prepare") !== (selectableKeys.size > 0)
      ) return null;
      shots.push(Object.freeze({
        shotId, status: shot.status, visualClass, capability,
        blockers: [...blockers], candidates,
        selectableKeys: Object.freeze([...selectableKeys]),
      }));
    }
    if (new Set(shots.map((shot) => shot.shotId)).size !== shots.length) return null;
    return Object.freeze({ shots, reviewHash: raw.route_review.review_hash });
  }

  function normalizePackProposal(raw, draftHash, reviewHash) {
    if (
      !raw || raw.schema_version !== "castle.production-pack-proposal.v1"
      || raw.draft_hash !== draftHash || raw.route_review_hash !== reviewHash
      || !safeHash(raw.director_plan_hash) || !safeHash(raw.proposal_hash)
      || typeof raw.pack_compilation_authorized !== "boolean"
      || raw.execution_authorized !== false || !Array.isArray(raw.blockers)
      || !Array.isArray(raw.entities) || !Array.isArray(raw.shots)
      || !Array.isArray(raw.route_variants) || containsSensitiveField(raw)
    ) return null;
    const blockers = raw.blockers.map((item) => ({
      code: safeId(item?.code), path: safeText(item?.path, "", 300),
    }));
    if (blockers.some((item) => !item.code || !item.path)) return null;
    return Object.freeze({
      ready: raw.pack_compilation_authorized,
      blockers: Object.freeze(blockers),
      entityCount: raw.entities.length,
      shotCount: raw.shots.length,
      routeCount: raw.route_variants.length,
      proposalHash: raw.proposal_hash,
      directorPlanHash: raw.director_plan_hash,
      mode: safeId(raw.project?.mode),
      seriesId: raw.project?.series_id === null ? "" : safeId(raw.project?.series_id),
    });
  }

  function normalizeSeriesResponse(raw, expectedSeriesId) {
    const bible = raw?.bible;
    if (
      raw?.schema_version !== "castle.series.response.v1" || raw.ok !== true
      || raw.authority?.owner_scoped !== true || raw.authority?.continuity_enforced !== true
      || raw.authority?.provider_submission_authorized !== false
      || raw.authority?.gpu_execution_authorized !== false
      || raw.authority?.credits_authorized !== 0 || raw.authority?.publishing_authorized !== false
      || bible?.schema_version !== "castle.series-bible.v1"
      || bible.series_id !== expectedSeriesId || !safeHash(bible.bible_hash)
      || !Number.isSafeInteger(bible.revision) || bible.revision < 1
      || !Array.isArray(bible.entity_ids) || !bible.canon || typeof bible.canon !== "object"
      || containsSensitiveField(raw)
    ) return null;
    const canon = {};
    for (const [key, value] of Object.entries(bible.canon)) {
      if (!safeId(key) || !safeText(value, "", 500)) return null;
      canon[key] = value;
    }
    return Object.freeze({
      seriesId: bible.series_id, title: safeText(bible.title, bible.series_id, 200),
      entityIds: Object.freeze(bible.entity_ids.map(safeId).filter(Boolean)),
      canon: Object.freeze(canon), revision: bible.revision, bibleHash: bible.bible_hash,
    });
  }

  function normalizeFinding(raw) {
    if (!exactKeys(raw, ["code", "path"])) return null;
    const code = safeId(raw.code);
    const path = safeText(raw.path, "", 300);
    return code && path ? Object.freeze({ code, path }) : null;
  }

  function normalizeRoute(raw) {
    const keys = [
      "route_variant_id", "provider_id", "execution_domain_declared", "capability",
      "visual_classes", "option_keys", "release_ready_declared",
      "estimated_credits_declared", "resource_lane_declared", "used_by",
      "runtime_verified", "quote_verified",
    ];
    if (!exactKeys(raw, keys)) return null;
    const routeId = safeId(raw.route_variant_id);
    const providerId = safeId(raw.provider_id);
    const capability = safeId(raw.capability);
    const visualClasses = safeArray(raw.visual_classes, 4);
    const optionKeys = safeArray(raw.option_keys, 128);
    const usedBy = safeArray(raw.used_by, 400);
    const credits = raw.estimated_credits_declared;
    if (
      !routeId || !providerId || !capability
      || !["local", "external"].includes(raw.execution_domain_declared)
      || !visualClasses
      || visualClasses.some((item) => !["anime", "realistic", "illustration", "unknown"].includes(item))
      || !optionKeys || optionKeys.some((item) => !safeId(item))
      || !usedBy
      || usedBy.some((item) => (
        !exactKeys(item, ["shot_id", "purpose"])
        || !safeId(item.shot_id)
        || !["visual", "voice"].includes(item.purpose)
      ))
      || typeof raw.release_ready_declared !== "boolean"
      || (credits !== null && safeCount(credits, 10000000) === null)
      || !safeText(raw.resource_lane_declared, "", 160)
      || raw.runtime_verified !== false
      || raw.quote_verified !== false
    ) return null;
    return Object.freeze({
      routeId,
      providerId,
      domain: raw.execution_domain_declared,
      capability,
      visualClasses: [...visualClasses],
      optionKeys: [...optionKeys],
      releaseReadyDeclared: raw.release_ready_declared,
      credits,
      usedBy: usedBy.map((item) => Object.freeze({
        shotId: safeId(item.shot_id),
        purpose: item.purpose,
      })),
    });
  }

  function normalizeExpensiveNode(raw) {
    const keys = [
      "shot_id", "task_key", "task_type", "purpose",
      "eligible_route_variant_ids", "requires_separate_execution_authorization",
    ];
    if (!exactKeys(raw, keys)) return null;
    const routeIds = safeArray(raw.eligible_route_variant_ids, 256);
    const shotId = safeId(raw.shot_id);
    const taskKey = safeId(raw.task_key);
    const taskType = safeId(raw.task_type);
    if (
      !shotId || !taskKey || !taskType
      || !["visual", "voice"].includes(raw.purpose)
      || !routeIds || routeIds.some((item) => !safeId(item))
      || raw.requires_separate_execution_authorization !== true
    ) return null;
    return Object.freeze({
      shotId,
      taskKey,
      taskType,
      purpose: raw.purpose,
      routeIds: [...routeIds],
    });
  }

  function normalizeDag(raw, expectedReady, expectedProjectId) {
    if (!expectedReady) return raw === null ? null : undefined;
    if (!exactKeys(raw, ["project_id", "compiler_version", "node_count", "nodes"])) return undefined;
    const nodes = safeArray(raw.nodes, 10000);
    const nodeCount = safeCount(raw.node_count, 10000);
    if (
      !safeId(raw.project_id)
      || raw.project_id !== expectedProjectId
      || !safeText(raw.compiler_version, "", 120)
      || !nodes
      || nodeCount !== nodes.length
    ) {
      return undefined;
    }
    const normalized = [];
    const observedKeys = new Set();
    for (const item of nodes) {
      const keys = [
        "key", "task_type", "lane", "depends_on", "input_hash", "route_binding",
        "gate_id", "human_required", "materialization_required",
      ];
      if (!exactKeys(item, keys)) return undefined;
      const dependsOn = safeArray(item.depends_on, 1000);
      const key = safeText(item.key, "", 260);
      const taskType = safeId(item.task_type);
      if (
        !key || !taskType || !safeText(item.lane, "", 120)
        || !dependsOn || dependsOn.some((value) => !safeText(value, "", 260))
        || !safeHash(item.input_hash)
        || ![null, "visual", "voice"].includes(item.route_binding)
        || (item.gate_id !== null && !safeText(item.gate_id, "", 160))
        || typeof item.human_required !== "boolean"
        || typeof item.materialization_required !== "boolean"
      ) return undefined;
      if (observedKeys.has(key)) return undefined;
      observedKeys.add(key);
      normalized.push(Object.freeze({ taskType }));
    }
    return Object.freeze({ nodeCount, nodes: normalized });
  }

  function normalizePreview(raw) {
    if (
      !exactKeys(raw, TOP_LEVEL_RESPONSE_KEYS)
      || raw.schema_version !== RESPONSE_SCHEMA
      || raw.ok !== true
      || raw.mode !== "preview_only"
      || typeof raw.static_plan_ready !== "boolean"
      || raw.execution_authorized !== false
      || containsSensitiveField(raw)
    ) return null;
    if (!exactKeys(raw.pack, ["version", "project_id", "hash"])) return null;
    const projectId = safeId(raw.pack.project_id);
    const packHash = safeHash(raw.pack.hash);
    if (raw.pack.version !== "castle.production-pack.v1" || !projectId || !packHash) return null;

    const authorityKeys = [
      "computed_by", "static_contract_checked", "dag_compiled",
      "route_metadata_source", "runtime_catalog_checked", "provider_health_checked",
      "runtime_readiness_checked", "authoritative_quotes_checked", "credentials_accessed",
    ];
    if (
      !exactKeys(raw.authority, authorityKeys)
      || raw.authority.computed_by !== "castle_server"
      || raw.authority.static_contract_checked !== true
      || raw.authority.dag_compiled !== raw.static_plan_ready
      || raw.authority.route_metadata_source !== "production_pack_declaration"
      || raw.authority.runtime_catalog_checked !== false
      || raw.authority.provider_health_checked !== false
      || raw.authority.runtime_readiness_checked !== false
      || raw.authority.authoritative_quotes_checked !== false
      || raw.authority.credentials_accessed !== false
      || !exactKeys(raw.side_effects, ZERO_SIDE_EFFECT_KEYS)
      || ZERO_SIDE_EFFECT_KEYS.some((key) => raw.side_effects[key] !== 0)
    ) return null;

    const gateKeys = [
      "ok", "fails", "warns", "fail_count", "warn_count",
      "findings_truncated", "pack_hash", "minimum_estimated_credits", "contract_version",
    ];
    if (!exactKeys(raw.gate_report, gateKeys)) return null;
    const failsRaw = safeArray(raw.gate_report.fails, 100);
    const warnsRaw = safeArray(raw.gate_report.warns, 50);
    const fails = failsRaw && failsRaw.map(normalizeFinding);
    const warns = warnsRaw && warnsRaw.map(normalizeFinding);
    const failCount = safeCount(raw.gate_report.fail_count, 100000);
    const warnCount = safeCount(raw.gate_report.warn_count, 100000);
    const minimumCredits = raw.gate_report.minimum_estimated_credits;
    if (
      typeof raw.gate_report.ok !== "boolean"
      || raw.gate_report.ok !== raw.static_plan_ready
      || !fails || fails.some((item) => !item)
      || !warns || warns.some((item) => !item)
      || failCount === null || warnCount === null
      || failCount < fails.length || warnCount < warns.length
      || typeof raw.gate_report.findings_truncated !== "boolean"
      || raw.gate_report.findings_truncated !== (
        failCount > fails.length || warnCount > warns.length
      )
      || raw.static_plan_ready !== (failCount === 0)
      || (failCount === 0 ? fails.length !== 0 : fails.length === 0)
      || (failCount === fails.length && warnCount === warns.length
        && raw.gate_report.findings_truncated)
      || raw.gate_report.pack_hash !== packHash
      || (minimumCredits !== null && safeCount(minimumCredits, 4000000000) === null)
      || raw.gate_report.contract_version !== "castle.production-pack-gate.v1"
    ) return null;

    const summaryKeys = [
      "entity_count", "scene_count", "shot_count", "route_variant_count",
      "parent_task_count", "shot_task_count", "expected_expensive_node_count",
      "minimum_declared_estimated_credits",
    ];
    if (!exactKeys(raw.summary, summaryKeys)) return null;
    const summary = {};
    for (const key of summaryKeys.slice(0, -1)) {
      const count = safeCount(raw.summary[key], 100000);
      if (count === null) return null;
      summary[key] = count;
    }
    const summaryCredits = raw.summary.minimum_declared_estimated_credits;
    if (summaryCredits !== null && safeCount(summaryCredits, 4000000000) === null) return null;
    summary.minimum_declared_estimated_credits = summaryCredits;

    const routesRaw = safeArray(raw.route_candidates, 256);
    const expensiveRaw = safeArray(raw.expected_expensive_nodes, 400);
    const routes = routesRaw && routesRaw.map(normalizeRoute);
    const expensiveNodes = expensiveRaw && expensiveRaw.map(normalizeExpensiveNode);
    const dag = normalizeDag(raw.dag, raw.static_plan_ready, projectId);
    if (
      !routes || routes.some((item) => !item)
      || !expensiveNodes || expensiveNodes.some((item) => !item)
      || dag === undefined
      || summary.route_variant_count !== routes.length
      || summary.expected_expensive_node_count !== expensiveNodes.length
      || summary.minimum_declared_estimated_credits !== minimumCredits
      || (dag && summary.parent_task_count + summary.shot_task_count !== dag.nodeCount)
      || (!dag && (summary.parent_task_count !== 0 || summary.shot_task_count !== 0))
      || new Set(routes.map((item) => item.routeId)).size !== routes.length
      || !safeHash(raw.preview_hash)
    ) return null;
    return Object.freeze({
      staticPlanReady: raw.static_plan_ready,
      projectId,
      packHash,
      previewHash: raw.preview_hash,
      fails,
      warns,
      failCount,
      warnCount,
      findingsTruncated: raw.gate_report.findings_truncated,
      minimumCredits,
      summary: Object.freeze(summary),
      routes,
      expensiveNodes,
      dag,
    });
  }

  function errorCopy(code) {
    return ERROR_COPY[code] || "專案檢查沒有完成；沒有執行任何生成。";
  }

  function normalizeErrorCode(raw) {
    if (
      !exactKeys(raw, ["schema_version", "ok", "error"])
      || raw.schema_version !== ERROR_SCHEMA
      || raw.ok !== false
      || !exactKeys(raw.error, ["code", "message"])
      || !safeText(raw.error.message, "", 500)
    ) return "";
    return safeId(raw.error.code);
  }

  function canonicalRequestId(value) {
    return typeof value === "string" ? safeId(value.trim().toLowerCase()) : "";
  }

  function providerLabel(value) {
    return {
      local_comfy: "我的 5090",
      autoip: "AutoIP",
      clipmode: "ClipMode",
    }[value] || value;
  }

  function routeCostCopy(providerId) {
    if (providerId === "local_comfy") return Object.freeze({
      label: "0 點",
      detail: "使用本機 RTX 5090，不會使用 AutoIP 點數。",
    });
    if (providerId === "autoip") return Object.freeze({
      label: "AutoIP 會員點數",
      detail: "使用已連接的 AutoIP；送出時會依帳號當下價格自動鎖定點數。",
    });
    return Object.freeze({
      label: "依供應商當下價格",
      detail: "送出時會由 Castle 鎖定這條路線的當下價格。",
    });
  }

  function routeChoiceButtonCopy(profile, readyChoiceCount) {
    if (readyChoiceCount <= 1) return "立即試做這個鏡頭";
    if (profile.providerId === "local_comfy") return "用我的 5090（0 點）試做";
    if (profile.providerId === "autoip") return "用 AutoIP 會員點數試做";
    return `用 ${providerLabel(profile.providerId)} 試做`;
  }

  function routeOptionLabel(key) {
    return {
      aspect: "畫面方向",
      aspect_ratio: "畫面比例",
      duration_seconds: "影片秒數",
      motion_profile: "動作幅度",
      native_audio: "原生聲音",
      quality: "品質",
      resolution: "解析度",
    }[key] || key;
  }

  function routeOptionValueLabel(key, value) {
    const text = String(value);
    const labels = {
      aspect: { portrait: "直式", landscape: "橫式", square: "方形" },
      aspect_ratio: { "9:16": "直式 9:16", "16:9": "橫式 16:9", "1:1": "方形 1:1" },
      motion_profile: { gentle: "輕微動作", balanced: "自然動作", dynamic: "較大動作" },
      native_audio: { true: "開啟", false: "關閉" },
      quality: { fast: "快速", balanced: "平衡", quality: "品質優先" },
    };
    return labels[key]?.[text] || text;
  }

  function preferredRouteOption(key, values, draftShot, brief) {
    if (!Array.isArray(values) || !values.length) return undefined;
    let authored;
    if (key === "duration_seconds") authored = draftShot?.duration_seconds;
    else if (key === "aspect_ratio") authored = brief?.aspect_ratio;
    else if (key === "aspect") {
      authored = { "9:16": "portrait", "16:9": "landscape", "1:1": "square" }[
        brief?.aspect_ratio
      ];
    }
    const matched = values.find((value) => String(value) === String(authored));
    return matched === undefined ? values[0] : matched;
  }

  function routeReadinessReason(providerId, reason) {
    const generic = {
      release_not_approved: "帳號或模組尚未啟用",
      runtime_not_ready: "服務連線或執行環境尚未就緒",
      technical_compatibility_missing: "尚未連接目前可用的服務帳號",
      quality_golden_missing: "缺少目前有效的相容性／可用性檢查",
      provider_connection_required: "尚未完成服務連線檢查",
      compatibility_receipt_missing: "尚未完成目前版本的相容性檢查",
      compatibility_receipt_expired: "相容性檢查已過期，請重新檢查連線",
    };
    const autoip = {
      release_not_approved: "AutoIP 帳號尚未啟用，請檢查 key 與連線",
      runtime_not_ready: "AutoIP API 無法連線或相容性檢查已過期",
      technical_compatibility_missing: "AutoIP 的零扣點連線檢查需要更新",
      quality_golden_missing: "AutoIP 尚缺目前有效的相容性檢查",
    };
    return (providerId === "autoip" ? autoip[reason] : "") || generic[reason] || reason;
  }

  function normalizeAutoIPProvider(raw) {
    if (
      !raw || raw.schema_version !== 1 || !Array.isArray(raw.providers)
      || raw.providers.length > 32 || containsSensitiveField(raw.providers)
    ) return null;
    const matches = raw.providers.filter((item) => item?.provider_id === "autoip");
    if (matches.length !== 1) return null;
    const provider = matches[0];
    const accountId = provider.account_id === null ? "" : safeId(provider.account_id);
    const statuses = new Set([
      "needs_credential", "account_selection_required", "configured_disabled",
      "credential_unavailable", "compatibility_check_required", "ready",
    ]);
    if (
      !statuses.has(provider.status)
      || typeof provider.configured !== "boolean"
      || typeof provider.execution_enabled !== "boolean"
      || provider.credential_readback !== false
      || (provider.configured && !accountId)
      || (!provider.configured && accountId)
    ) return null;
    return Object.freeze({
      accountId,
      configured: provider.configured,
      status: provider.status,
      ready: provider.status === "ready" && provider.execution_enabled === true,
    });
  }

  function normalizeAutoIPActivation(raw, expectedAccountId) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
    const receipt = raw.compatibility_receipt;
    const health = raw.health;
    const credits = raw.available_credits;
    if (
      raw.schema_version !== 1
      || raw.provider_id !== "autoip"
      || safeId(raw.account_id) !== expectedAccountId
      || raw.ready !== true
      || raw.generation_enabled !== true
      || raw.provider_write_performed !== false
      || raw.credits_spent !== 0
      || !health || typeof health !== "object" || Array.isArray(health)
      || health.provider_id !== "autoip"
      || health.provider_reachable !== true
      || health.api_healthy !== true
      || health.authenticated !== true
      || (credits !== null && (!Number.isSafeInteger(credits) || credits < 0))
      || !receipt || receipt.schema_version !== "castle.autoip-compatibility-receipt.v1"
      || !/^autoip-probe:[0-9a-f]{32}$/.test(receipt.receipt_id || "")
      || receipt.persisted !== true
      || containsSensitiveField(raw)
    ) return null;
    return Object.freeze({ availableCredits: credits, receiptId: receipt.receipt_id });
  }

  function visualLabel(value) {
    return {
      anime: "動漫",
      realistic: "寫實",
      illustration: "插畫",
      unknown: "未分類",
    }[value] || value;
  }

  function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
    return `${(bytes / (1024 * 1024)).toFixed(2)} MiB`;
  }

  function intentUpdatedAtMs(value) {
    const timestamp = Date.parse(safeText(value, "", 80));
    return Number.isFinite(timestamp) && timestamp > 0 ? timestamp : 0;
  }

  function relativeIntentTime(timestamp, now = Date.now()) {
    if (!timestamp) return "時間未知";
    const seconds = Math.max(0, Math.floor((now - timestamp) / 1000));
    if (seconds < 60) return "剛剛";
    if (seconds < 3600) return `${Math.floor(seconds / 60)} 分鐘前`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小時前`;
    return `${Math.floor(seconds / 86400)} 天前`;
  }

  function intentUpdatedAtLabel(value, now = Date.now()) {
    const timestamp = intentUpdatedAtMs(value);
    if (!timestamp) return "最後更新：時間未知";
    let absolute;
    try {
      absolute = new Intl.DateTimeFormat("zh-TW", {
        year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", hour12: false,
      }).format(new Date(timestamp));
    } catch (_error) {
      absolute = new Date(timestamp).toISOString();
    }
    return `最後更新：${absolute}（${relativeIntentTime(timestamp, now)}）`;
  }

  function staleSubmissionUncertain(intent, now = Date.now()) {
    const updatedAt = intentUpdatedAtMs(intent && intent.updatedAt);
    return Boolean(
      intent && intent.state === "submission_uncertain" && updatedAt
      && now - updatedAt >= STALE_SUBMISSION_UNCERTAIN_MS
    );
  }

  function localPreferenceDigest(value) {
    let left = 0x811c9dc5;
    let right = 0x9e3779b9;
    for (const character of String(value || "")) {
      const code = character.codePointAt(0);
      left = Math.imul(left ^ code, 0x01000193) >>> 0;
      right = Math.imul(right ^ code, 0x85ebca6b) >>> 0;
    }
    return `${left.toString(16).padStart(8, "0")}${right.toString(16).padStart(8, "0")}`;
  }

  function hiddenRecoveredIntentKey(preferenceScope, production, intent) {
    const scope = safeText(preferenceScope, "", 200);
    const projectId = safeId(production && production.projectId);
    const packHash = safeHash(production && production.packHash);
    const intentId = safeId(intent && intent.intentId);
    return scope && projectId && packHash && intentId
      ? `${HIDDEN_RECOVERED_INTENT_PREFIX}:${localPreferenceDigest(`${scope}\u0000${projectId}\u0000${packHash}\u0000${intentId}`)}`
      : "";
  }

  function recoveredIntentIsHidden(preferenceScope, production, intent) {
    const key = hiddenRecoveredIntentKey(preferenceScope, production, intent);
    if (!key) return false;
    try {
      return window.localStorage.getItem(key) === "hidden";
    } catch (_error) {
      return false;
    }
  }

  function setRecoveredIntentHidden(preferenceScope, production, intent, hidden) {
    const key = hiddenRecoveredIntentKey(preferenceScope, production, intent);
    if (!key) return false;
    try {
      if (hidden) window.localStorage.setItem(key, "hidden");
      else window.localStorage.removeItem(key);
      return true;
    } catch (_error) {
      return false;
    }
  }

  function preparedRequest(rawText) {
    const source = String(rawText || "").trim();
    if (!source) throw new Error("請選擇或貼上 production-pack JSON。");
    let parsed;
    try {
      parsed = JSON.parse(source);
    } catch (_error) {
      throw new Error("JSON 格式無法解析，請檢查括號、逗號與引號。");
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("JSON 最外層必須是物件。");
    }
    const pack = parsed.version === "castle.production-pack.v1"
      ? parsed
      : (parsed.production_pack && typeof parsed.production_pack === "object" ? parsed.production_pack : null);
    const projectId = canonicalRequestId(pack && pack.project && pack.project.project_id);
    if (parsed.version === "castle.production-pack.v1") {
      return Object.freeze({
        body: `{"schema_version":"${REQUEST_SCHEMA}","production_pack":${source}}`,
        projectId, pack,
      });
    }
    return Object.freeze({ body: source, projectId, pack });
  }

  function normalizeProduction(raw, expectedProjectId) {
    const production = raw && raw.production;
    const phaseOrder = ["draft", "planned", "assets_locked", "generating", "story_review", "editing", "delivery_review", "ready"];
    if (
      !raw || raw.schema_version !== "castle.production.response.v1" || raw.ok !== true
      || containsSensitiveField(raw) || !production
      || safeId(production.project_id) !== expectedProjectId
      || !safeHash(production.pack_hash) || !safeHash(production.state_hash)
      || !phaseOrder.includes(production.phase)
      || !Number.isSafeInteger(production.revision) || production.revision < 1
      || !Array.isArray(production.completed_gates)
      || production.completed_gates.length > phaseOrder.length - 1
      || production.completed_gates.some((item) => !phaseOrder.slice(1).includes(item))
      || new Set(production.completed_gates).size !== production.completed_gates.length
      || (production.phase !== "draft" && !production.completed_gates.includes(production.phase))
      || !Array.isArray(production.shots) || production.shots.length > 400
      || raw.authority?.owner_scoped !== true
      || raw.authority?.provider_submission_authorized !== false
      || raw.authority?.gpu_execution_authorized !== false
      || raw.authority?.publishing_authorized !== false
    ) return null;
    const shots = [];
    for (const shot of production.shots) {
      if (
        !exactKeys(shot, ["shot_id", "accepted_version", "versions"])
        || !safeId(shot.shot_id) || !Array.isArray(shot.versions) || shot.versions.length > 100
        || (shot.accepted_version !== null && (!Number.isSafeInteger(shot.accepted_version) || shot.accepted_version < 1))
      ) return null;
      const versions = [];
      for (const item of shot.versions) {
        if (
          !exactKeys(item, ["version", "asset_version_id", "route_variant_id", "decision", "review_note"])
          || !Number.isSafeInteger(item.version) || item.version < 1
          || !safeId(item.asset_version_id) || !safeId(item.route_variant_id)
          || ![null, "accepted", "rejected"].includes(item.decision)
          || safeText(item.review_note, "", 1000) !== item.review_note
        ) return null;
        versions.push(Object.freeze({ ...item }));
      }
      shots.push(Object.freeze({
        shotId: shot.shot_id, acceptedVersion: shot.accepted_version,
        versions: Object.freeze(versions),
      }));
    }
    let postproduction = null;
    if (production.postproduction !== null) {
      const post = production.postproduction;
      const receipt = post?.receipt;
      const review = post?.delivery_review;
      if (
        !exactKeys(post, ["receipt", "delivery_review"])
        || !receipt || !["castle.production-edit-receipt.v1", "castle.production-edit-receipt.v2", "castle.production-edit-receipt.v3", "castle.production-edit-receipt.v4", "castle.production-edit-receipt.v5"].includes(receipt.schema_version)
        || receipt.project_id !== expectedProjectId
        || !safeHash(receipt.production_state_hash) || !safeHash(receipt.timeline_hash)
        || !safeHash(receipt.output_sha256) || !safeHash(receipt.receipt_hash)
        || !safeId(receipt.output_asset_version_id)
        || !Number.isSafeInteger(receipt.byte_size) || receipt.byte_size <= 0
        || typeof receipt.duration_seconds !== "number" || receipt.duration_seconds <= 0
        || receipt.mechanical_qa_passed !== true
        || typeof receipt.subtitle_emitted !== "boolean"
        || typeof receipt.subtitle_burned_in !== "boolean"
        || receipt.subtitle_burned_in !== receipt.subtitle_emitted
        || (["castle.production-edit-receipt.v2", "castle.production-edit-receipt.v3", "castle.production-edit-receipt.v4", "castle.production-edit-receipt.v5"].includes(receipt.schema_version) && (
          !receipt.contact_sheet || !safeHash(receipt.contact_sheet.sha256)
          || !Number.isSafeInteger(receipt.contact_sheet.byte_size) || receipt.contact_sheet.byte_size <= 0
          || receipt.contact_sheet.width !== 960
          || !Number.isSafeInteger(receipt.contact_sheet.height) || receipt.contact_sheet.height <= 0
        ))
        || !receipt.video || !Number.isSafeInteger(receipt.video.width)
        || !Number.isSafeInteger(receipt.video.height) || typeof receipt.video.fps !== "number"
        || receipt.output_url !== `/api/productions/${expectedProjectId}/edits/${receipt.receipt_hash}/output`
        || (["castle.production-edit-receipt.v2", "castle.production-edit-receipt.v3", "castle.production-edit-receipt.v4", "castle.production-edit-receipt.v5"].includes(receipt.schema_version)
          && receipt.contact_sheet_url !== `/api/productions/${expectedProjectId}/edits/${receipt.receipt_hash}/contact-sheet`)
        || (["castle.production-edit-receipt.v3", "castle.production-edit-receipt.v4", "castle.production-edit-receipt.v5"].includes(receipt.schema_version)
          && typeof receipt.soundtrack_mixed !== "boolean")
        || (["castle.production-edit-receipt.v4", "castle.production-edit-receipt.v5"].includes(receipt.schema_version)
          && (typeof receipt.soundtrack_ducking_applied !== "boolean"
            || receipt.soundtrack_ducking_applied !== receipt.soundtrack_mixed))
        || (receipt.schema_version === "castle.production-edit-receipt.v5"
          && (!Number.isSafeInteger(receipt.sound_effect_count)
            || receipt.sound_effect_count < 0 || receipt.sound_effect_count > 8000))
      ) return null;
      let normalizedReview = null;
      if (review !== null) {
        if (
          review?.schema_version !== "castle.production-delivery-review.v1"
          || review.project_id !== expectedProjectId
          || review.receipt_hash !== receipt.receipt_hash
          || !["accepted", "rejected"].includes(review.decision)
          || safeText(review.note, "", 1000) !== review.note
          || !safeHash(review.production_state_hash) || !safeHash(review.review_hash)
        ) return null;
        normalizedReview = Object.freeze({
          decision: review.decision, note: review.note, hash: review.review_hash,
        });
      }
      postproduction = Object.freeze({
        receipt: Object.freeze({
          hash: receipt.receipt_hash, timelineHash: receipt.timeline_hash,
          outputSha256: receipt.output_sha256, outputAssetVersionId: receipt.output_asset_version_id,
          outputUrl: receipt.output_url, byteSize: receipt.byte_size,
          duration: receipt.duration_seconds, width: receipt.video.width,
          height: receipt.video.height, fps: receipt.video.fps,
          subtitleEmitted: receipt.subtitle_emitted,
          subtitleBurnedIn: receipt.subtitle_burned_in,
          contactSheet: ["castle.production-edit-receipt.v2", "castle.production-edit-receipt.v3", "castle.production-edit-receipt.v4", "castle.production-edit-receipt.v5"].includes(receipt.schema_version)
            ? Object.freeze({ ...receipt.contact_sheet, url: receipt.contact_sheet_url })
            : null,
          soundtrackMixed: ["castle.production-edit-receipt.v3", "castle.production-edit-receipt.v4", "castle.production-edit-receipt.v5"].includes(receipt.schema_version)
            ? receipt.soundtrack_mixed : null,
          soundtrackDuckingApplied: ["castle.production-edit-receipt.v4", "castle.production-edit-receipt.v5"].includes(receipt.schema_version)
            ? receipt.soundtrack_ducking_applied : null,
          soundEffectCount: receipt.schema_version === "castle.production-edit-receipt.v5"
            ? receipt.sound_effect_count : null,
        }),
        review: normalizedReview,
      });
    }
    return Object.freeze({
      projectId: production.project_id,
      packHash: production.pack_hash,
      stateHash: production.state_hash,
      phase: production.phase,
      revision: production.revision,
      completedGates: Object.freeze([...production.completed_gates]),
      shots: Object.freeze(shots),
      postproduction,
    });
  }

  function normalizeProductionList(raw) {
    const phases = ["draft", "planned", "assets_locked", "generating", "story_review", "editing", "delivery_review", "ready"];
    if (
      !raw || raw.schema_version !== "castle.production-list.response.v1" || raw.ok !== true
      || containsSensitiveField(raw) || !Array.isArray(raw.items) || raw.items.length > 100
      || raw.authority?.owner_scoped !== true
      || raw.authority?.provider_submission_authorized !== false
      || raw.authority?.gpu_execution_authorized !== false
      || raw.authority?.publishing_authorized !== false
    ) return null;
    const seen = new Set();
    const items = [];
    for (const item of raw.items) {
      const projectId = safeId(item?.project_id);
      if (
        !projectId || seen.has(projectId) || !phases.includes(item?.phase)
        || !safeHash(item?.state_hash) || !safeHash(item?.pack_hash)
        || !Number.isSafeInteger(item?.revision) || item.revision < 1
        || !Number.isSafeInteger(item?.shot_count) || item.shot_count < 1 || item.shot_count > 400
        || !Number.isSafeInteger(item?.completed_shot_count) || item.completed_shot_count < 0
        || !Number.isSafeInteger(item?.accepted_shot_count) || item.accepted_shot_count < 0
        || item.completed_shot_count > item.shot_count
        || item.accepted_shot_count > item.completed_shot_count
        || typeof item?.updated_at !== "string" || item.updated_at.length > 40
        || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/.test(item.updated_at)
      ) return null;
      seen.add(projectId);
      items.push(Object.freeze({
        projectId, title: safeText(item.title, projectId, 200), phase: item.phase,
        revision: item.revision, stateHash: item.state_hash, packHash: item.pack_hash,
        shotCount: item.shot_count, completedCount: item.completed_shot_count,
        acceptedCount: item.accepted_shot_count, updatedAt: item.updated_at,
      }));
    }
    return Object.freeze(items);
  }

  function normalizePreparationOptions(raw, production, shotId) {
    if (
      !raw || raw.schema_version !== "castle.production-shot-preparation-options.v1"
      || raw.ok !== true || containsSensitiveField(raw)
      || raw.project_id !== production.projectId || raw.shot_id !== shotId
      || raw.pack_hash !== production.packHash || raw.state_hash !== production.stateHash
      || raw.phase !== production.phase || !Array.isArray(raw.candidates)
      || raw.authority?.owner_scoped !== true
      || raw.authority?.runtime_catalog_checked !== true
      || raw.authority?.asset_versions_resolved !== false
      || raw.authority?.provider_submission_authorized !== false
      || raw.authority?.gpu_queue_authorized !== false
      || raw.authority?.credits_authorized !== 0
    ) return null;
    const candidates = raw.candidates.map((item) => {
      const profile = item?.profile;
      if (
        !safeId(item?.route_variant_id) || typeof item.qualified !== "boolean"
        || !Array.isArray(item.reason_codes) || !profile
        || profile.schema_version !== "castle.provider-variant-profile.v1"
        || !safeText(profile.profile_revision, "", 128)
        || !safeId(profile.provider_id) || !safeId(profile.variant_id)
        || !safeText(`${profile.provider_id}:${profile.variant_id}`, "", 257)
        || !safeId(profile.capability)
        || !["anime", "realistic", "illustration"].includes(profile.visual_class)
        || !profile.option_values || typeof profile.option_values !== "object"
        || Array.isArray(profile.option_values) || containsSensitiveField(profile.option_values)
        || !Array.isArray(profile.input_slots)
      ) return null;
      const slots = profile.input_slots.map((slot) => {
        if (
          !safeId(slot?.slot_id) || !["image", "video", "audio", "text"].includes(slot.media_kind)
          || typeof slot.required !== "boolean" || typeof slot.identity_bearing !== "boolean"
          || !Number.isSafeInteger(slot.minimum_count) || !Number.isSafeInteger(slot.maximum_count)
          || slot.minimum_count < 0 || slot.maximum_count < 1 || slot.maximum_count > 16
        ) return null;
        return Object.freeze({
          id: slot.slot_id, kind: slot.media_kind, required: slot.required,
          identityBearing: slot.identity_bearing,
          minimum: slot.minimum_count, maximum: slot.maximum_count,
        });
      });
      if (slots.some((slot) => !slot)) return null;
      const options = {};
      for (const [key, values] of Object.entries(profile.option_values)) {
        if (!safeId(key) || !Array.isArray(values) || !values.length || values.length > 128) return null;
        options[key] = Object.freeze([...values]);
      }
      let materialResolved = false;
      const materialAssets = [];
      const materialSlots = [];
      if (item.material_selection !== undefined) {
        const selection = item.material_selection;
        if (
          selection?.schema_version !== "castle.production-material-selection.v1"
          || selection.profile_key !== `${profile.provider_id}:${profile.variant_id}`
          || selection.preparation_authorized !== false
          || selection.execution_authorized !== false
          || selection.credits_authorized !== 0
          || !Array.isArray(selection.character_ids) || !Array.isArray(selection.slots)
        ) return null;
        materialResolved = true;
        for (const materialSlot of selection.slots) {
          const slotId = safeId(materialSlot?.slot_id);
          const profileSlot = slots.find((slot) => slot.id === slotId);
          if (
            !slotId || !profileSlot || materialSlot.media_kind !== profileSlot.kind
            || materialSlot.required !== profileSlot.required
            || materialSlot.identity_bearing !== profileSlot.identityBearing
            || !Array.isArray(materialSlot.candidates)
          ) return null;
          const recommendedProjectionId = materialSlot.recommended === null
            ? "" : safeId(materialSlot.recommended?.projection_id);
          const recommendedVersionId = materialSlot.recommended === null
            ? "" : safeId(materialSlot.recommended?.asset_version_id);
          if (materialSlot.recommended !== null && (!recommendedProjectionId || !recommendedVersionId)) return null;
          const slotAssets = [];
          for (const asset of materialSlot.candidates) {
            const projectionId = safeId(asset?.projection_id);
            const versionId = safeId(asset?.asset_version_id);
            if (
              !projectionId || !versionId || !safeId(asset.provider_id)
              || !Array.isArray(asset.character_ids) || !Number.isSafeInteger(asset.score)
              || !Array.isArray(asset.recommendation_reasons)
              || asset.recommendation_reasons.some((reason) => !safeText(reason, "", 160))
              || !safeId(asset.catalog_role)
            ) return null;
            const normalizedAsset = Object.freeze({
              projectionId, versionId, kind: materialSlot.media_kind,
              visualClass: profile.visual_class,
              characterIds: Object.freeze(asset.character_ids.map(safeId).filter(Boolean)),
              title: safeText(asset.title, projectionId, 500), previewUrl: "",
              providerId: asset.provider_id, slotId, score: asset.score,
              catalogRole: asset.catalog_role,
              reasons: Object.freeze(asset.recommendation_reasons.map((reason) => safeText(reason, "", 160))),
              serverRecommended: projectionId === recommendedProjectionId && versionId === recommendedVersionId,
            });
            slotAssets.push(normalizedAsset);
            materialAssets.push(normalizedAsset);
          }
          if (
            (recommendedProjectionId || recommendedVersionId)
            && slotAssets.filter((asset) => asset.serverRecommended).length !== 1
          ) return null;
          slotAssets.sort((left, right) => Number(right.serverRecommended) - Number(left.serverRecommended)
            || right.score - left.score
            || left.projectionId.localeCompare(right.projectionId)
            || left.versionId.localeCompare(right.versionId));
          materialSlots.push(Object.freeze({
            id: slotId, recommended: recommendedProjectionId
              ? Object.freeze({ projectionId: recommendedProjectionId, versionId: recommendedVersionId })
              : null,
            assets: Object.freeze(slotAssets),
          }));
        }
      }
      return Object.freeze({
        routeId: item.route_variant_id,
        qualified: item.qualified,
        reasons: item.reason_codes.map(safeId).filter(Boolean),
        profileKey: `${profile.provider_id}:${profile.variant_id}`,
        providerId: profile.provider_id,
        variantId: profile.variant_id,
        capability: profile.capability,
        visualClass: profile.visual_class,
        options: Object.freeze(options), slots: Object.freeze(slots),
        materialResolved, materialAssets: Object.freeze(materialAssets),
        materialSlots: Object.freeze(materialSlots),
      });
    });
    return candidates.some((item) => !item) ? null : Object.freeze(candidates);
  }

  function normalizePreparationAssets(raw) {
    if (
      !raw || raw.provider_transport_performed !== false || raw.credits_spent !== 0
      || !Array.isArray(raw.assets || raw.items) || containsSensitiveField(raw)
    ) return null;
    return (raw.assets || raw.items).map((item) => {
      const projectionId = safeId(item?.asset_id);
      const versionId = safeId(item?.asset_version_id);
      if (
        !projectionId || !versionId || item.preparation_available !== true
        || !["image", "video", "audio", "text"].includes(item.kind)
        || !["anime", "realistic", "illustration", "unknown"].includes(item.visual_class)
        || !Array.isArray(item.character_ids)
      ) return null;
      return Object.freeze({
        projectionId, versionId, kind: item.kind, visualClass: item.visual_class,
        characterIds: Object.freeze(item.character_ids.map(safeId).filter(Boolean)),
        title: safeText(item.title, projectionId, 500),
        previewUrl: safeText(item.preview_url, "", 1000),
        providerId: safeId(item.provider_id) || "unknown",
      });
    }).filter(Boolean);
  }

  function rankPreparationAssets(assets, candidate, shotCharacters) {
    const shotSet = new Set(shotCharacters);
    return [...assets].sort((left, right) => {
      const score = (asset) => {
        const linked = new Set(asset.characterIds);
        const coverage = [...shotSet].filter((id) => linked.has(id)).length;
        const exact = linked.size === shotSet.size && coverage === shotSet.size;
        return (exact ? 400 : coverage ? 300 : 0)
          + (coverage * 20)
          + (asset.providerId === candidate.providerId ? 10 : 0)
          + (asset.previewUrl ? 1 : 0);
      };
      return score(right) - score(left)
        || left.projectionId.localeCompare(right.projectionId)
        || left.versionId.localeCompare(right.versionId);
    });
  }

  class CastleProjectPreview extends HTMLElement {
    constructor() {
      super();
      this._authorized = false;
      this._canConfigureProviders = false;
      this._preferenceScope = "";
      this._rawText = "";
      this._fileName = "";
      this._result = null;
      this._message = "";
      this._error = "";
      this._busy = false;
      this._readingFile = false;
      this._request = null;
      this._revision = 0;
      this._textarea = null;
      this._submitButton = null;
      this._status = null;
      this._characters = [];
      this._briefBusy = false;
      this._draft = null;
      this._directorPlan = null;
      this._brief = null;
      this._routeReview = null;
      this._packProposal = null;
      this._seriesBible = null;
      this._seriesChecked = false;
      this._seriesBusy = false;
      this._routeBusy = false;
      this._formalBusy = false;
      this._productionPack = null;
      this._production = null;
      this._productionBusy = false;
      this._shotPreparation = new Map();
      this._recoveredIntents = new Map();
      this._productionOutputMedia = new Map();
      this._productionList = [];
      this._productionListBusy = false;
      this._productionListLoaded = false;
      this._soundtracks = [];
      this._soundtracksBusy = false;
      this._soundtracksLoaded = false;
      this._autoipSetupBusy = false;
      this._autoipSetupMessage = "";
      this._autoipSetupAccountId = "local-autoip";
      this._autoipProvider = null;
    }

    configure(options = {}) {
      const authorized = options && options.authorized === true;
      if (!authorized) {
        this._revision += 1;
        if (this._request) this._request.abort();
        this._request = null;
        this._busy = false;
        this._readingFile = false;
        this._rawText = "";
        this._fileName = "";
        this._result = null;
        this._message = "";
        this._error = "";
        this._productionPack = null;
        this._production = null;
        this._shotPreparation.clear();
        this._recoveredIntents.clear();
        this._productionOutputMedia.clear();
        this._productionList = [];
        this._productionListBusy = false;
        this._productionListLoaded = false;
        this._soundtracks = [];
        this._soundtracksBusy = false;
        this._soundtracksLoaded = false;
        this._autoipProvider = null;
        this._preferenceScope = "";
      }
      this._authorized = authorized;
      this._canConfigureProviders = authorized && options.canConfigureProviders === true;
      this._preferenceScope = authorized ? safeText(options.preferenceScope, "", 200) : "";
      this._characters = Array.isArray(options.characters) ? options.characters.map((item) => {
        const id = safeId(item && item.id);
        const visualClass = safeText(item && item.visualClass, "", 24);
        const profileAssetId = safeId(item && item.profileAssetId);
        if (!id || !profileAssetId || !["anime", "realistic", "illustration"].includes(visualClass)) return null;
        return Object.freeze({
          id,
          label: safeText(item.label, id, 160),
          visualClass,
          profileAssetId,
          avatarUrl: safeText(item.avatarUrl, "", 1000),
        });
      }).filter(Boolean) : [];
      this.render();
      if (authorized && !this._productionListLoaded && !this._productionListBusy) {
        queueMicrotask(() => this._loadProductionList());
      }
      if (authorized && !this._soundtracksLoaded && !this._soundtracksBusy) {
        queueMicrotask(() => this._loadSoundtracks());
      }
    }

    connectedCallback() {
      this.render();
    }

    disconnectedCallback() {
      this._revision += 1;
      if (this._request) this._request.abort();
      this._request = null;
      this._busy = false;
      this._readingFile = false;
    }

    async _loadSoundtracks() {
      if (!this._authorized || this._soundtracksBusy || this._soundtracksLoaded) return;
      this._soundtracksBusy = true;
      try {
        const response = await fetch("/api/assets?kind=audio", {
          method: "GET", credentials: "same-origin", cache: "no-store",
          headers: { Accept: "application/json" },
        });
        const raw = await response.json().catch(() => null);
        if (
          !response.ok || raw?.schema_version !== 1 || !Array.isArray(raw.assets)
          || raw.assets.length > 200 || raw.provider_transport_performed !== false
          || raw.credits_spent !== 0 || raw.filters?.kind !== "audio"
          || containsSensitiveField(raw)
        ) throw new Error("背景音樂庫目前無法安全載入。");
        const soundtracks = [];
        for (const asset of raw.assets) {
          const assetId = safeId(asset?.asset_id);
          const versionId = safeId(asset?.asset_version_id);
          const sourceUrl = safeText(asset?.source_url, "", 1000);
          if (
            asset?.kind !== "audio" || asset.source_available !== true
            || asset.preparation_available !== true || !assetId || !versionId
            || sourceUrl !== `/api/assets/${assetId}/source`
          ) continue;
          soundtracks.push(Object.freeze({
            assetId, versionId, sourceUrl,
            title: safeText(asset.title, "未命名音訊", 500),
            role: ["unclassified", "music", "sound_effect", "voice", "ambience"].includes(asset.catalog?.audio_role)
              ? asset.catalog.audio_role : "unclassified",
            duration: typeof asset.duration_seconds === "number" && asset.duration_seconds > 0
              ? asset.duration_seconds : null,
          }));
        }
        this._soundtracks = Object.freeze(soundtracks);
      } catch (error) {
        this._soundtracks = [];
        this._error = safeText(error?.message, "背景音樂庫目前無法安全載入。", 300);
      } finally {
        this._soundtracksBusy = false;
        this._soundtracksLoaded = true;
        this.render();
      }
    }

    _invalidateOutcome() {
      this._revision += 1;
      if (this._request) this._request.abort();
      this._request = null;
      this._busy = false;
      this._readingFile = false;
      this._result = null;
      this._productionPack = null;
      this._production = null;
      this._shotPreparation.clear();
      this._recoveredIntents.clear();
      this._productionOutputMedia.clear();
      this._message = "";
      this._error = "";
      const outcome = this.querySelector(".project-preview__outcome");
      if (outcome) outcome.remove();
      if (this._status) {
        this._status.textContent = "";
        this._status.classList.remove("is-error");
      }
      if (this._submitButton) {
        this._submitButton.disabled = this._readingFile
          || !String(this._textarea && this._textarea.value || "").trim();
        this._submitButton.textContent = this._readingFile ? "讀檔中…" : "檢查規劃（不執行）";
      }
    }

    async _selectFile(file) {
      if (!file) return;
      const revision = ++this._revision;
      if (this._request) this._request.abort();
      this._request = null;
      this._busy = false;
      this._readingFile = true;
      this._rawText = "";
      this._fileName = safeText(file.name, "production-pack.json", 240);
      this._result = null;
      this._error = "";
      this._message = `正在讀取 ${this._fileName}；完成前不會送出。`;
      this.render();
      if (file.size > MAX_REQUEST_BYTES) {
        if (revision !== this._revision) return;
        this._readingFile = false;
        this._error = `檔案太大（${formatBytes(file.size)}）；上限是 ${formatBytes(MAX_REQUEST_BYTES)}。`;
        this._message = "";
        this._result = null;
        this.render();
        return;
      }
      try {
        const text = await file.text();
        if (revision !== this._revision) return;
        this._rawText = text;
        this._readingFile = false;
        this._result = null;
        this._message = `已載入 ${this._fileName} · ${formatBytes(new TextEncoder().encode(text).length)}`;
        this._error = "";
      } catch (_error) {
        if (revision !== this._revision) return;
        this._readingFile = false;
        this._rawText = "";
        this._error = "檔案讀取失敗，沒有送出任何資料。";
        this._message = "";
      }
      this.render();
    }

    async _preview() {
      if (!this._authorized || this._busy || this._readingFile) return;
      const source = this._textarea ? this._textarea.value : this._rawText;
      let prepared;
      try {
        prepared = preparedRequest(source);
      } catch (error) {
        this._error = error.message;
        this._message = "";
        this._result = null;
        this.render();
        return;
      }
      const bytes = new TextEncoder().encode(prepared.body).length;
      if (bytes > MAX_REQUEST_BYTES) {
        this._error = `專案資料是 ${formatBytes(bytes)}，超過 ${formatBytes(MAX_REQUEST_BYTES)} 上限。`;
        this._message = "";
        this._result = null;
        this.render();
        return;
      }
      if (this._request) this._request.abort();
      const controller = new AbortController();
      const revision = ++this._revision;
      this._request = controller;
      this._rawText = source;
      this._busy = true;
      this._result = null;
      this._error = "";
      this._message = "正在做靜態檢查；不會建立任務、呼叫模型或供應商。";
      this.render();
      try {
        const response = await fetch(ENDPOINT, {
          method: "POST",
          credentials: "same-origin",
          cache: "no-store",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
          },
          body: prepared.body,
          signal: controller.signal,
        });
        let data = null;
        try {
          data = await response.json();
        } catch (_error) {
          data = null;
        }
        if (controller.signal.aborted || revision !== this._revision || this._request !== controller) return;
        if (!response.ok) {
          if (response.status === 401) window.dispatchEvent(new CustomEvent("castle-auth-required"));
          const code = normalizeErrorCode(data);
          throw Object.assign(new Error("Project preview failed"), {
            publicMessage: errorCopy(code),
          });
        }
        const normalized = normalizePreview(data);
        if (!normalized || (prepared.projectId && normalized.projectId !== prepared.projectId)) {
          throw Object.assign(new Error("Unsafe project preview response"), {
            publicMessage: "Castle 回傳的預覽資料不完整或安全聲明不一致，已拒絕顯示。",
          });
        }
        this._result = normalized;
        this._productionPack = prepared.pack || null;
        this._message = normalized.staticPlanReady
          ? "靜態規則通過；仍未檢查模型、授權、服務健康或正式報價。"
          : "已找到阻擋項目；沒有建立可執行 DAG。";
        this._error = "";
      } catch (error) {
        if (error && error.name === "AbortError") return;
        if (controller.signal.aborted || revision !== this._revision || this._request !== controller) return;
        this._result = null;
        this._message = "";
        this._error = safeText(error && error.publicMessage)
          || "專案檢查沒有完成；沒有執行任何生成。";
      } finally {
        if (this._request === controller) {
          this._request = null;
          this._busy = false;
          this.render();
        }
      }
    }

    async _createProduction() {
      if (this._productionBusy || !this._productionPack || !this._result?.staticPlanReady) return;
      this._productionBusy = true;
      this._error = "";
      this._message = "正在建立可恢復的製作紀錄；不會開始生成。";
      this.render();
      try {
        const response = await fetch(PRODUCTION_ENDPOINT, {
          method: "POST", credentials: "same-origin", cache: "no-store",
          headers: { Accept: "application/json", "Content-Type": "application/json" },
          body: JSON.stringify({
            schema_version: "castle.production-create.v1",
            production_pack: this._productionPack,
          }),
        });
        const data = await response.json().catch(() => null);
        if (!response.ok) {
          if (response.status === 401) window.dispatchEvent(new CustomEvent("castle-auth-required"));
          const code = safeId(data?.error?.code);
          if (code === "production_conflict") {
            const existing = await this._fetchProduction(this._result.projectId);
            if (!existing || existing.packHash !== this._result.packHash) {
              throw new Error("既有製作和目前檢查的專案版本不同，請勿覆蓋。" );
            }
            this._production = existing;
            this._message = "已載入既有製作進度；沒有重複建立或開始生成。";
            return;
          }
          throw new Error("無法建立製作紀錄，沒有開始生成。");
        }
        const normalized = normalizeProduction(data, this._result.projectId);
        if (!normalized || normalized.packHash !== this._result.packHash) {
          throw new Error("製作紀錄和剛才檢查的專案不一致，已停止。");
        }
        this._production = normalized;
        this._message = "已建立可恢復的製作紀錄；目前仍未排入生成。";
      } catch (error) {
        this._error = safeText(error?.message) || "無法建立製作紀錄，沒有開始生成。";
        this._message = "";
      } finally {
        this._productionBusy = false;
        this.render();
      }
    }

    async _fetchProduction(projectId) {
      const response = await fetch(`${PRODUCTION_ENDPOINT}/${pathId(projectId)}`, {
        method: "GET", credentials: "same-origin", cache: "no-store",
        headers: { Accept: "application/json" },
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        if (response.status === 401) window.dispatchEvent(new CustomEvent("castle-auth-required"));
        return null;
      }
      return normalizeProduction(data, projectId);
    }

    async _resumeProduction(projectId) {
      const exactProjectId = safeId(projectId);
      if (!exactProjectId || this._productionBusy || this._busy) return;
      this._productionBusy = true;
      this._error = "";
      this._message = "正在取回你的 immutable production pack 與最新進度；不會開始生成。";
      this.render();
      try {
        const response = await fetch(
          `${PRODUCTION_ENDPOINT}/${pathId(exactProjectId)}/resume`,
          { method: "GET", credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } },
        );
        const raw = await response.json().catch(() => null);
        if (!response.ok) {
          if (response.status === 401) window.dispatchEvent(new CustomEvent("castle-auth-required"));
          throw new Error(response.status === 404
            ? "找不到屬於目前帳號的製作紀錄。"
            : "無法安全取回製作紀錄。" );
        }
        if (
          raw?.schema_version !== "castle.production-resume.response.v1"
          || raw.ok !== true || !raw.production_pack || containsSensitiveField(raw)
          || raw.authority?.owner_scoped !== true
          || raw.authority?.provider_submission_authorized !== false
          || raw.authority?.gpu_execution_authorized !== false
          || raw.authority?.publishing_authorized !== false
        ) throw new Error("恢復資料沒有通過 owner-scoped 安全契約。" );
        const prepared = preparedRequest(JSON.stringify(raw.production_pack));
        if (prepared.projectId !== exactProjectId || !prepared.pack) {
          throw new Error("恢復的 production pack 身分不一致。" );
        }
        const productionEnvelope = { ...raw, schema_version: "castle.production.response.v1" };
        delete productionEnvelope.production_pack;
        const production = normalizeProduction(productionEnvelope, exactProjectId);
        if (!production) throw new Error("恢復的 production state 不完整。" );
        const previewResponse = await fetch(ENDPOINT, {
          method: "POST", credentials: "same-origin", cache: "no-store",
          headers: { Accept: "application/json", "Content-Type": "application/json" },
          body: prepared.body,
        });
        const previewRaw = await previewResponse.json().catch(() => null);
        const preview = previewResponse.ok ? normalizePreview(previewRaw) : null;
        if (!preview || !preview.staticPlanReady || preview.projectId !== exactProjectId
          || preview.packHash !== production.packHash) {
          throw new Error("既有製作已無法通過目前的安全規則，已停止恢復。" );
        }
        this._rawText = JSON.stringify(raw.production_pack, null, 2);
        this._fileName = "";
        this._productionPack = prepared.pack;
        this._result = preview;
        this._production = production;
        this._shotPreparation.clear();
        await this._loadRecoveredProductionIntents(production, prepared.pack);
        await this._loadProductionOutputMedia(production);
        this._message = `已恢復 ${exactProjectId} 的最新進度；沒有重複建立、生成或扣點。`;
      } catch (error) {
        this._error = safeText(error?.message, "無法恢復製作紀錄。", 300);
        this._message = "";
      } finally {
        this._productionBusy = false;
        this.render();
      }
    }

    async _loadRecoveredProductionIntents(production = this._production, pack = this._productionPack) {
      if (!production || !pack) return;
      const response = await fetch(
        `${PRODUCTION_ENDPOINT}/${pathId(production.projectId)}/intents`,
        { method: "GET", credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } },
      );
      const raw = await response.json().catch(() => null);
      if (!response.ok) throw new Error("無法安全取回這個製作的執行工作。" );
      if (
        raw?.schema_version !== "castle.production-intents.response.v1" || raw.ok !== true
        || raw.project_id !== production.projectId || raw.pack_hash !== production.packHash
        || raw.current_state_hash !== production.stateHash || !Array.isArray(raw.items)
        || raw.items.length > 400 || typeof raw.truncated !== "boolean"
        || raw.authority?.owner_scoped !== true
        || raw.authority?.status_check_transport_performed !== false
        || raw.authority?.status_check_credits_spent !== 0
        || raw.authority?.retry_authorized !== false || containsSensitiveField(raw)
      ) throw new Error("執行工作清單沒有通過 owner-scoped 安全契約。" );
      const routes = new Map((pack.route_variants || []).map((route) => [route.route_variant_id, route]));
      const shots = new Set((pack.shots || []).map((shot) => shot.shot_id));
      const recovered = new Map();
      for (const item of raw.items) {
        const status = item?.status;
        const route = routes.get(item?.route_variant_id);
        if (
          !item || !shots.has(item.shot_id) || !route || !safeHash(item.production_state_hash)
          || !status || !/^intent:[0-9a-f]{32}$/.test(status.intent_id || "")
          || status.provider_id !== route.provider_id || status.capability !== route.capability
          || !Array.isArray(route.visual_classes) || !route.visual_classes.includes(status.visual_class)
          || !INTENT_STATES.includes(status.state)
          || status.terminal !== TERMINAL_INTENT_STATES.includes(status.state)
          || status.retry_available !== false || typeof status.transport_may_have_occurred !== "boolean"
          || typeof status.provider_receipt_available !== "boolean"
          || typeof status.output_verified !== "boolean"
          || (status.output_verified && status.state !== "completed")
          || !Number.isSafeInteger(status.credits_authorized) || status.credits_authorized < 0
          || (status.credits_charged !== null && (!Number.isSafeInteger(status.credits_charged) || status.credits_charged < 0))
          || (status.remaining_credits !== null && (!Number.isSafeInteger(status.remaining_credits) || status.remaining_credits < 0))
          || typeof status.created_at !== "string" || !status.created_at
          || typeof status.updated_at !== "string" || !status.updated_at
        ) throw new Error("某個執行工作與 production route 不一致。" );
        const existing = recovered.get(item.shot_id);
        if (existing) {
          existing.historyCount += 1;
          continue;
        }
        recovered.set(item.shot_id, {
          historyCount: 1, intentId: status.intent_id, credits: status.credits_authorized,
          providerId: status.provider_id, capability: status.capability,
          visualClass: status.visual_class, state: status.state, terminal: status.terminal,
          transportMayHaveOccurred: status.transport_may_have_occurred,
          providerReceiptAvailable: status.provider_receipt_available,
          outputVerified: status.output_verified,
          creditsCharged: status.credits_charged, updatedAt: status.updated_at,
        });
      }
      this._recoveredIntents = recovered;
    }

    async _refreshRecoveredIntents() {
      if (!this._production || this._productionBusy) return;
      this._productionBusy = true;
      this._error = "";
      this._message = "正在讀取 Castle 持久工作狀態；不會呼叫供應商。";
      this.render();
      try {
        await this._reloadProduction();
        await this._loadRecoveredProductionIntents();
        await this._loadProductionOutputMedia();
        this._message = "已更新所有鏡頭工作與輸出；查詢沒有呼叫供應商或花點。";
      } catch (error) {
        this._error = safeText(error?.message, "無法更新工作狀態；請勿重新送出。", 300);
        this._message = "";
      } finally {
        this._productionBusy = false;
        this.render();
      }
    }

    _renderRecoveredIntent(shotId) {
      const intent = this._recoveredIntents.get(shotId);
      if (!intent) return null;
      const staleUncertain = staleSubmissionUncertain(intent);
      if (staleUncertain && recoveredIntentIsHidden(this._preferenceScope, this._production, intent)) {
        const restore = el("button", {
          className: "project-preview__button", type: "button", text: "顯示已隱藏紀錄",
        });
        restore.addEventListener("click", () => {
          if (!setRecoveredIntentHidden(this._preferenceScope, this._production, intent, false)) {
            this._error = "無法更新此瀏覽器的顯示偏好。";
          } else {
            this._error = "";
            this._message = "已重新顯示紀錄；Castle 的持久工作狀態沒有變更。";
          }
          this.render();
        });
        return el("div", { className: "project-preview__intent-hidden" }, [
          el("span", { text: "此瀏覽器已隱藏一筆舊的待核對紀錄；Castle 的持久工作狀態沒有變更。" }),
          restore,
        ]);
      }
      const copy = INTENT_COPY[intent.state] || INTENT_COPY.pending;
      const detail = staleUncertain
        ? "這是超過 48 小時未更新的待核對紀錄，不是目前正在執行的任務。請勿重新送出；先讓 reconciliation 查清楚。"
        : (intent.state === "completed" && intent.outputVerified
          ? "輸出已經 Castle 驗證並納入媒體庫。"
          : copy[1]);
      const refresh = el("button", {
        className: "project-preview__button", type: "button",
        text: this._productionBusy ? "更新中…" : "更新所有鏡頭狀態（不呼叫供應商）",
        attributes: { disabled: this._productionBusy ? "" : null },
      });
      refresh.addEventListener("click", () => this._refreshRecoveredIntents());
      const hide = staleUncertain ? el("button", {
        className: "project-preview__button", type: "button", text: "從畫面隱藏",
      }) : null;
      if (hide) hide.addEventListener("click", () => {
        if (!setRecoveredIntentHidden(this._preferenceScope, this._production, intent, true)) {
          this._error = "無法保存此瀏覽器的顯示偏好。";
        } else {
          this._error = "";
          this._message = "已從這個瀏覽器隱藏舊紀錄；Castle 的持久工作狀態沒有變更。";
        }
        this.render();
      });
      return el("div", {
        className: `project-preview__intent-status ${staleUncertain ? "is-stale-record" : `is-${intent.state}`}`,
      }, [
        el("strong", { text: staleUncertain ? "舊的待核對紀錄／非執行中" : `${copy[0]} · 已恢復` }),
        el("span", { text: detail }),
        el("code", { text: intent.intentId }),
        el("small", { className: "project-preview__intent-updated", text: intentUpdatedAtLabel(intent.updatedAt) }),
        intent.historyCount > 1 ? el("small", { text: `此鏡頭共有 ${intent.historyCount} 次已確認工作；顯示最新一筆。` }) : null,
        intent.creditsCharged === null ? null
          : el("small", { text: `已記錄扣點 ${intent.creditsCharged}／授權 ${intent.credits}` }),
        refresh,
        hide,
      ]);
    }

    async _loadProductionOutputMedia(production = this._production) {
      if (!production) return;
      const response = await fetch(
        `${PRODUCTION_ENDPOINT}/${pathId(production.projectId)}/outputs`,
        { method: "GET", credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } },
      );
      const raw = await response.json().catch(() => null);
      if (!response.ok) throw new Error("無法安全載入已驗證的逐鏡媒體。" );
      if (
        raw?.schema_version !== "castle.production-output-media.response.v1" || raw.ok !== true
        || raw.project_id !== production.projectId || raw.pack_hash !== production.packHash
        || raw.state_hash !== production.stateHash || !Array.isArray(raw.items)
        || raw.items.length > 40000 || containsSensitiveField(raw)
        || raw.authority?.owner_scoped !== true
        || raw.authority?.verified_output_binding_checked !== true
        || raw.authority?.private_source_locator_disclosed !== false
        || raw.authority?.provider_transport_performed !== false
        || raw.authority?.credits_spent !== 0
      ) throw new Error("逐鏡媒體沒有通過 owner-scoped 安全契約。" );
      const versionIndex = new Map();
      for (const shot of production.shots) {
        for (const version of shot.versions) {
          versionIndex.set(`${shot.shotId}:${version.version}`, version.asset_version_id);
        }
      }
      const media = new Map();
      for (const item of raw.items) {
        const key = `${item?.shot_id}:${item?.version}`;
        const expectedUrl = `${PRODUCTION_ENDPOINT}/${pathId(production.projectId)}`
          + `/shots/${pathId(item?.shot_id || "")}/versions/${item?.version}/output`;
        if (
          !safeId(item?.shot_id) || !Number.isSafeInteger(item?.version) || item.version < 1
          || versionIndex.get(key) !== item.asset_version_id
          || !["image", "video", "audio"].includes(item.media_kind)
          || typeof item.mime_type !== "string" || !item.mime_type.startsWith(`${item.media_kind}/`)
          || !Number.isSafeInteger(item.byte_size) || item.byte_size <= 0
          || item.output_url !== expectedUrl || media.has(key)
        ) throw new Error("逐鏡媒體與 production version 不一致。" );
        media.set(key, Object.freeze({
          kind: item.media_kind, mimeType: item.mime_type,
          byteSize: item.byte_size, url: item.output_url,
        }));
      }
      if (media.size !== versionIndex.size) {
        throw new Error("部分 production output 缺少可驗證的審核媒體。" );
      }
      this._productionOutputMedia = media;
    }

    _renderProductionOutputMedia(shotId, version) {
      const media = this._productionOutputMedia.get(`${shotId}:${version}`);
      if (!media) return null;
      if (media.kind === "image") return el("img", {
        className: "project-preview__output-media",
        attributes: { src: media.url, alt: `${shotId} 版本 ${version} 審核圖片`, loading: "lazy" },
      });
      if (media.kind === "video") return el("video", {
        className: "project-preview__output-media",
        attributes: { src: media.url, controls: "", preload: "metadata", playsinline: "" },
      });
      return el("audio", {
        className: "project-preview__output-media",
        attributes: { src: media.url, controls: "", preload: "metadata" },
      });
    }

    _renderProductionResume() {
      const form = el("form", { className: "project-preview__resume" }, [
        el("div", {}, [
          el("strong", { text: "繼續既有製作" }),
          el("small", { text: "直接從你的最近製作續作；也可以輸入專案 ID。載入清單不會執行、排隊或扣點。" }),
        ]),
      ]);
      const list = el("div", { className: "project-preview__production-list" });
      if (this._productionListBusy) {
        list.append(el("p", { text: "正在載入你的製作…" }));
      } else if (this._productionListLoaded && !this._productionList.length) {
        list.append(el("p", { text: "目前還沒有已保存的製作。" }));
      } else {
        const phaseCopy = {
          draft: "製作草稿", planned: "規劃完成", assets_locked: "素材已鎖定",
          generating: "逐鏡生成", story_review: "逐鏡審核", editing: "確定性剪輯",
          delivery_review: "成片審核", ready: "製作完成",
        };
        for (const item of this._productionList) {
          const resume = el("button", {
            className: "project-preview__button", type: "button", text: "繼續製作",
            attributes: { disabled: this._productionBusy ? "" : null },
          });
          resume.addEventListener("click", () => this._resumeProduction(item.projectId));
          list.append(el("article", { className: "project-preview__production-list-item" }, [
            el("div", {}, [
              el("strong", { text: item.title }),
              el("small", { text: `${phaseCopy[item.phase]} · ${item.acceptedCount}/${item.shotCount} 鏡已接受` }),
              el("code", { text: `${item.projectId} · r${item.revision}` }),
            ]),
            resume,
          ]));
        }
      }
      const input = el("input", {
        attributes: {
          name: "project_id", required: "", maxlength: "128", autocomplete: "off",
          pattern: "[a-z0-9][a-z0-9._:-]{0,127}", placeholder: "例如：summer-campaign-01",
          "aria-label": "要繼續的專案 ID",
        },
      });
      const button = el("button", {
        className: "project-preview__button", type: "submit",
        text: this._productionBusy ? "取回中…" : "取回最新進度",
        attributes: { disabled: this._productionBusy ? "" : null },
      });
      form.append(list, input, button);
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        if (!form.reportValidity()) return;
        this._resumeProduction(input.value.trim());
      });
      return form;
    }

    async _loadProductionList() {
      if (!this._authorized || this._productionListBusy) return;
      this._productionListBusy = true;
      this.render();
      try {
        const response = await fetch(PRODUCTION_ENDPOINT, {
          method: "GET", credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" },
        });
        const raw = await response.json().catch(() => null);
        if (!response.ok) {
          if (response.status === 401) window.dispatchEvent(new CustomEvent("castle-auth-required"));
          throw new Error("無法載入你的製作清單。");
        }
        const items = normalizeProductionList(raw);
        if (!items) throw new Error("製作清單沒有通過 owner-scoped 安全契約。");
        this._productionList = [...items];
        this._productionListLoaded = true;
      } catch (error) {
        this._error = safeText(error?.message, "製作清單載入失敗。", 300);
      } finally {
        this._productionListBusy = false;
        this.render();
      }
    }

    async _reviewProductionShot(shotId, version, decision, note) {
      if (!this._production || this._productionBusy) return;
      this._productionBusy = true;
      this._error = "";
      this._message = "正在保存逐鏡審核…";
      this.render();
      try {
        const response = await fetch(
          `${PRODUCTION_ENDPOINT}/${pathId(this._production.projectId)}/mutations`,
          {
            method: "POST", credentials: "same-origin", cache: "no-store",
            headers: { Accept: "application/json", "Content-Type": "application/json" },
            body: JSON.stringify({
              schema_version: "castle.production-mutation.v1",
              expected_state_hash: this._production.stateHash,
              operation: { kind: "review_shot", shot_id: shotId, version, decision, note },
            }),
          },
        );
        const data = await response.json().catch(() => null);
        if (!response.ok) {
          if (response.status === 401) window.dispatchEvent(new CustomEvent("castle-auth-required"));
          throw new Error(response.status === 409
            ? "製作進度已在另一個頁面更新，請重新載入。"
            : "逐鏡審核沒有保存。" );
        }
        const normalized = normalizeProduction(data, this._production.projectId);
        if (!normalized || normalized.packHash !== this._production.packHash) {
          throw new Error("更新後的製作紀錄不一致，已停止顯示。" );
        }
        this._production = normalized;
        this._message = decision === "accepted" ? "已接受這個鏡頭版本。" : "已退回這個鏡頭版本。";
      } catch (error) {
        this._error = safeText(error?.message) || "逐鏡審核沒有保存。";
        this._message = "";
      } finally {
        this._productionBusy = false;
        this.render();
      }
    }

    async _advanceProduction(phase, acknowledgement) {
      if (!this._production || this._productionBusy || !this._productionPack) return;
      this._productionBusy = true;
      this._error = "";
      this._message = "正在保存製作關卡…";
      this.render();
      try {
        const response = await fetch(
          `${PRODUCTION_ENDPOINT}/${pathId(this._production.projectId)}/mutations`,
          {
            method: "POST", credentials: "same-origin", cache: "no-store",
            headers: { Accept: "application/json", "Content-Type": "application/json" },
            body: JSON.stringify({
              schema_version: "castle.production-mutation.v1",
              expected_state_hash: this._production.stateHash,
              operation: {
                kind: "advance", phase,
                confirmation: {
                  schema_version: "castle.production-phase-confirmation.v1",
                  pack_hash: this._production.packHash,
                  acknowledgement,
                },
              },
            }),
          },
        );
        const data = await response.json().catch(() => null);
        if (!response.ok) {
          if (response.status === 401) window.dispatchEvent(new CustomEvent("castle-auth-required"));
          throw new Error(response.status === 409
            ? "製作進度已變更，請重新載入後再確認。"
            : "製作關卡沒有保存；沒有開始任何生成。" );
        }
        const normalized = normalizeProduction(data, this._production.projectId);
        if (!normalized || normalized.packHash !== this._production.packHash || normalized.phase !== phase) {
          throw new Error("更新後的製作關卡不一致，已停止。" );
        }
        this._production = normalized;
        this._message = phase === "generating"
          ? "逐鏡生成窗口已開啟；按下每鏡的生成後會自動綁定當下價格並建立可追蹤工作。"
          : "製作關卡已保存。";
      } catch (error) {
        this._error = safeText(error?.message) || "製作關卡沒有保存。";
        this._message = "";
      } finally {
        this._productionBusy = false;
        this.render();
      }
    }

    async _reloadProduction() {
      const response = await fetch(
        `${PRODUCTION_ENDPOINT}/${pathId(this._production.projectId)}`,
        { method: "GET", credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } },
      );
      const raw = await response.json().catch(() => null);
      const normalized = response.ok ? normalizeProduction(raw, this._production.projectId) : null;
      if (!normalized || normalized.packHash !== this._production.packHash) {
        throw new Error("無法重新載入一致的後製紀錄。" );
      }
      this._production = normalized;
    }

    async _executeProductionEdit() {
      if (!this._production || this._productionBusy) return;
      this._productionBusy = true;
      this._error = "";
      this._message = "正在依已接受版本剪輯並執行 mechanical QA…";
      this.render();
      try {
        const response = await fetch(
          `${PRODUCTION_ENDPOINT}/${pathId(this._production.projectId)}/edit`,
          {
            method: "POST", credentials: "same-origin", cache: "no-store",
            headers: { Accept: "application/json", "Content-Type": "application/json" },
            body: JSON.stringify({
              schema_version: "castle.production-edit-execute.v1",
              expected_state_hash: this._production.stateHash,
              confirmed: true,
            }),
          },
        );
        const raw = await response.json().catch(() => null);
        if (!response.ok) throw new Error(response.status === 409
          ? "Production 或剪輯來源已改變，沒有覆寫任何輸出。"
          : "確定性剪輯或 mechanical QA 沒有完成。" );
        const receipt = raw?.receipt;
        if (
          raw?.schema_version !== "castle.production-edit-execute.response.v1"
          || raw.ok !== true || containsSensitiveField(raw)
          || receipt?.schema_version !== "castle.production-edit-receipt.v5"
          || !safeHash(receipt.receipt_hash) || !safeHash(receipt.output_sha256)
          || receipt.mechanical_qa_passed !== true
          || typeof receipt.subtitle_emitted !== "boolean"
          || typeof receipt.subtitle_burned_in !== "boolean"
          || receipt.subtitle_burned_in !== receipt.subtitle_emitted
          || !receipt.contact_sheet || !safeHash(receipt.contact_sheet.sha256)
          || receipt.contact_sheet.width !== 960
          || receipt.contact_sheet_url !== `/api/productions/${this._production.projectId}/edits/${receipt.receipt_hash}/contact-sheet`
          || typeof receipt.soundtrack_mixed !== "boolean"
          || typeof receipt.soundtrack_ducking_applied !== "boolean"
          || receipt.soundtrack_ducking_applied !== receipt.soundtrack_mixed
          || !Number.isSafeInteger(receipt.sound_effect_count)
          || receipt.sound_effect_count < 0 || receipt.sound_effect_count > 8000
          || raw.authority?.accepted_versions_server_resolved !== true
          || raw.authority?.mechanical_qa_passed !== true
          || raw.authority?.provider_transport_performed !== false
          || raw.authority?.gpu_queue_authorized !== false
          || raw.authority?.credits_spent !== 0
          || raw.authority?.publishing_authorized !== false
        ) throw new Error("剪輯 receipt 未通過公開安全契約。" );
        await this._reloadProduction();
        this._message = "確定性剪輯與 mechanical QA 已完成；尚未核准發布。";
      } catch (error) {
        this._error = safeText(error?.message, "後製剪輯失敗。", 300);
        this._message = "";
      } finally {
        this._productionBusy = false;
        this.render();
      }
    }

    async _reviewProductionDelivery(decision, note) {
      const post = this._production?.postproduction;
      if (!post?.receipt || this._productionBusy) return;
      this._productionBusy = true;
      this._error = "";
      this.render();
      try {
        const response = await fetch(
          `${PRODUCTION_ENDPOINT}/${pathId(this._production.projectId)}/delivery-review`,
          {
            method: "POST", credentials: "same-origin", cache: "no-store",
            headers: { Accept: "application/json", "Content-Type": "application/json" },
            body: JSON.stringify({
              schema_version: "castle.production-delivery-review.v1",
              expected_state_hash: this._production.stateHash,
              receipt_hash: post.receipt.hash, decision, note,
            }),
          },
        );
        const raw = await response.json().catch(() => null);
        if (!response.ok) throw new Error(response.status === 409
          ? "Delivery 狀態已改變，請重新載入。"
          : "Delivery 人工審核沒有保存。" );
        if (
          raw?.schema_version !== "castle.production-delivery-review.response.v1"
          || raw.ok !== true || raw.review?.receipt_hash !== post.receipt.hash
          || raw.review?.decision !== decision || !safeHash(raw.review?.review_hash)
          || !safeHash(raw.production_state_hash)
          || raw.authority?.owner_scoped !== true
          || raw.authority?.publishing_authorized !== false
        ) throw new Error("Delivery review receipt 未通過安全契約。" );
        await this._reloadProduction();
        this._message = decision === "accepted"
          ? "最終成片已人工接受；仍需最後 Ready 關卡才完成。"
          : "最終成片已退回，可用同一批已接受鏡頭重新剪輯。";
      } catch (error) {
        this._error = safeText(error?.message, "Delivery review 失敗。", 300);
        this._message = "";
      } finally {
        this._productionBusy = false;
        this.render();
      }
    }

    _renderProductionProgress() {
      const production = this._production;
      if (!production) return null;
      const phases = [
        ["draft", "製作草稿", "確認範圍、權利與預算"],
        ["planned", "規劃完成", "核對逐鏡路線"],
        ["assets_locked", "素材已鎖定", "選擇 immutable 素材版本"],
        ["generating", "逐鏡生成", "已授權的工作才會執行"],
        ["story_review", "逐鏡審核", "接受或退回每個版本"],
        ["editing", "確定性剪輯", "組裝、聲音與機械 QA"],
        ["delivery_review", "成片審核", "播放最終輸出並決定"],
        ["ready", "製作完成", "仍未授權公開發布"],
      ];
      const current = phases.findIndex(([id]) => id === production.phase);
      return el("ol", {
        className: "project-preview__production-progress",
        attributes: { "aria-label": "製作進度" },
      }, phases.map(([id, label, detail], index) => el("li", {
        className: index < current ? "is-complete" : index === current ? "is-current" : "is-upcoming",
        attributes: index === current ? { "aria-current": "step" } : {},
      }, [
        el("span", { text: index < current ? "✓" : String(index + 1) }),
        el("div", {}, [el("strong", { text: label }), el("small", { text: detail })]),
      ])));
    }

    _renderProductionGate() {
      const production = this._production;
      if (!production) return null;
      const completeOutputs = production.shots.every((shot) => shot.versions.length > 0);
      const acceptedOutputs = production.shots.every((shot) => shot.acceptedVersion !== null);
      const gates = {
        draft: {
          phase: "planned", acknowledgement: "scope_rights_budget_reviewed",
          label: "我已核對用途、權利、角色類型與專案點數上限",
          action: "確認規劃範圍",
          help: "只保存 greenlight；不會呼叫模型或供應商。",
          ready: true,
        },
        planned: {
          phase: "assets_locked", acknowledgement: "immutable_assets_selected",
          label: "我已選定這個 pack 內的角色、服裝、聲音與參考素材版本",
          action: "鎖定素材選擇",
          help: "正式準備時伺服器仍會重新驗證每個 immutable asset version。",
          ready: true,
        },
        assets_locked: {
          phase: "generating", acknowledgement: "open_generation_window_without_spend",
          label: "我了解開啟窗口不等於授權扣點或 GPU；每個正式路由仍需個別確認",
          action: "開啟逐鏡生成窗口",
          help: "開啟後才能把已簽章的輸出安全回填到各 shot。",
          ready: true,
        },
        generating: {
          phase: "story_review", acknowledgement: "all_shot_outputs_received",
          label: "每個鏡頭都已有至少一個可審核版本",
          action: "進入逐鏡審核",
          help: completeOutputs ? "進入後可接受或退回每個版本。" : "還有鏡頭沒有生成結果。",
          ready: completeOutputs,
        },
        story_review: {
          phase: "editing", acknowledgement: "all_shots_accepted",
          label: "每個鏡頭都已有人工作出接受決定",
          action: "鎖定鏡頭並進入剪輯",
          help: acceptedOutputs ? "接受版本將成為 deterministic timeline 的唯一輸入。" : "仍有鏡頭尚未接受。",
          ready: acceptedOutputs,
        },
        editing: {
          phase: "delivery_review", acknowledgement: "mechanical_delivery_qa_passed",
          label: "我已確認 deterministic edit receipt 與 mechanical QA 均已通過",
          action: "進入最終成片審核",
          help: production.postproduction?.receipt ? "進入後可播放並人工接受或退回成片。" : "必須先完成確定性剪輯。",
          ready: Boolean(production.postproduction?.receipt),
        },
        delivery_review: {
          phase: "ready", acknowledgement: "final_delivery_review_accepted",
          label: "我已播放並人工接受這份最終成片",
          action: "標記製作完成",
          help: production.postproduction?.review?.decision === "accepted" ? "此動作仍不代表公開發布。" : "最終成片尚未接受。",
          ready: production.postproduction?.review?.decision === "accepted",
        },
      };
      const gate = gates[production.phase];
      if (!gate) return el("p", { text: "目前沒有可由此頁手動推進的關卡。" });
      const checkbox = el("input", { attributes: { type: "checkbox", disabled: gate.ready ? null : "" } });
      const button = el("button", {
        className: "project-preview__primary", type: "button", text: gate.action,
        attributes: { disabled: "" },
      });
      checkbox.addEventListener("change", () => { button.disabled = !checkbox.checked || this._productionBusy; });
      button.addEventListener("click", () => this._advanceProduction(gate.phase, gate.acknowledgement));
      return el("div", { className: "project-preview__production-gate" }, [
        el("label", {}, [checkbox, el("span", { text: gate.label })]),
        el("small", { text: gate.help }),
        button,
      ]);
    }

    async _loadShotPreparation(shotId) {
      if (!this._production || !["assets_locked", "generating"].includes(this._production.phase)) return;
      const current = this._shotPreparation.get(shotId);
      if (current?.busy) return;
      this._shotPreparation.set(shotId, { busy: true, error: "", candidates: [], assets: [], prepared: null });
      this.render();
      try {
        const optionsResponse = await fetch(
          `${PRODUCTION_ENDPOINT}/${pathId(this._production.projectId)}/shots/${pathId(shotId)}/preparation-options`,
          { method: "GET", credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } },
        );
        const optionsRaw = await optionsResponse.json().catch(() => null);
        if (!optionsResponse.ok) throw new Error("此鏡頭目前無法取得精確生成模組。" );
        const candidates = normalizePreparationOptions(optionsRaw, this._production, shotId);
        if (!candidates) throw new Error("生成模組回應與目前 production state 不一致。" );
        const kinds = [...new Set(candidates.flatMap((item) => item.slots.map((slot) => slot.kind)))];
        const packShot = this._productionPack?.shots?.find((item) => item?.shot_id === shotId);
        const characterIds = Array.isArray(packShot?.entity_ids)
          ? packShot.entity_ids.map(safeId).filter(Boolean)
          : [];
        const scopes = characterIds.length ? characterIds : [""];
        const fetchScopedAssets = async (kind, characterId) => {
          const collected = [];
          let cursor = "";
          for (let page = 0; page < 4; page += 1) {
            const url = new URL("/api/assets", window.location.origin);
            url.searchParams.set("kind", kind);
            if (characterId) url.searchParams.set("characterId", characterId);
            if (cursor) url.searchParams.set("cursor", cursor);
            const response = await fetch(`${url.pathname}${url.search}`, {
              method: "GET", credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" },
            });
            const raw = await response.json().catch(() => null);
            if (!response.ok) throw new Error("無法載入此鏡頭角色的已驗證素材版本。" );
            const assets = normalizePreparationAssets(raw);
            if (!assets) throw new Error("素材庫沒有提供安全的 immutable version 契約。" );
            collected.push(...assets);
            cursor = safeText(raw.next_cursor, "", 5000);
            if (!cursor) break;
          }
          return collected;
        };
        const pages = candidates.every((item) => item.materialResolved)
          ? []
          : await Promise.all(kinds.flatMap((kind) => (
            scopes.map((characterId) => fetchScopedAssets(kind, characterId))
          )));
        const assetsByVersion = new Map();
        for (const asset of candidates.flatMap((item) => item.materialAssets)) {
          assetsByVersion.set(`${asset.projectionId}:${asset.versionId}`, asset);
        }
        for (const asset of pages.flat()) assetsByVersion.set(`${asset.projectionId}:${asset.versionId}`, asset);
        this._shotPreparation.set(shotId, {
          busy: false, error: "", candidates, assets: [...assetsByVersion.values()], prepared: null,
        });
      } catch (error) {
        this._shotPreparation.set(shotId, {
          busy: false, error: safeText(error?.message, "鏡頭準備資料載入失敗。", 300),
          candidates: [], assets: [], prepared: null,
        });
      }
      this.render();
    }

    async _prepareShot(shotId, candidate, card) {
      const state = this._shotPreparation.get(shotId);
      if (!this._production || !state || state.busy || !candidate.qualified) return;
      const selectedOptions = {};
      for (const select of card.querySelectorAll("select[data-preparation-option]")) {
        const key = select.dataset.preparationOption;
        const allowed = candidate.options[key] || [];
        const value = allowed.find((item) => JSON.stringify(item) === select.value);
        if (value === undefined) return;
        selectedOptions[key] = value;
      }
      const packShot = this._productionPack?.shots?.find((item) => item?.shot_id === shotId);
      const resourceIds = Array.isArray(packShot?.input_asset_ids) ? packShot.input_asset_ids : [];
      const shotCharacters = Array.isArray(packShot?.entity_ids)
        ? packShot.entity_ids.map(safeId).filter(Boolean)
        : [];
      const resourceByCharacter = new Map(shotCharacters.map((characterId, index) => (
        [characterId, resourceIds[index]]
      )));
      const assetByVersion = new Map([
        ...state.assets,
        ...candidate.materialAssets,
      ].map((asset) => [`${asset.projectionId}:${asset.versionId}`, asset]));
      const inputCounts = new Map();
      const coveredCharacters = new Map();
      const inputs = [];
      for (const select of card.querySelectorAll("select[data-preparation-slot]")) {
        if (!select.value) continue;
        const slotId = select.dataset.preparationSlot;
        const slot = candidate.slots.find((item) => item.id === slotId);
        if (!slot) return;
        let projectionId;
        let versionId;
        try {
          [projectionId, versionId] = JSON.parse(select.value);
        } catch (_error) {
          return;
        }
        const bindingId = select.dataset.preparationBinding || slotId;
        const resourceSelect = card.querySelector(`select[data-preparation-resource="${CSS.escape(bindingId)}"]`);
        const resourceId = resourceSelect?.value;
        if (!safeId(projectionId) || !safeId(versionId) || !resourceIds.includes(resourceId)) return;
        const characterId = safeId(select.dataset.preparationCharacter);
        if (slot.identityBearing && slot.maximum > 1) {
          const asset = assetByVersion.get(`${projectionId}:${versionId}`);
          if (
            !characterId || !shotCharacters.includes(characterId)
            || resourceByCharacter.get(characterId) !== resourceId
            || !asset || asset.characterIds.length !== 1
            || asset.characterIds[0] !== characterId
          ) return;
          const slotCoverage = coveredCharacters.get(slotId) || new Set();
          if (slotCoverage.has(characterId)) return;
          slotCoverage.add(characterId);
          coveredCharacters.set(slotId, slotCoverage);
        }
        inputCounts.set(slotId, (inputCounts.get(slotId) || 0) + 1);
        inputs.push({
          resource_id: resourceId, projection_id: projectionId,
          asset_version_id: versionId, field_name: slotId,
        });
      }
      for (const slot of candidate.slots) {
        const count = inputCounts.get(slot.id) || 0;
        if (count < slot.minimum || count > slot.maximum) return;
        if (slot.identityBearing && slot.maximum > 1) {
          const coverage = coveredCharacters.get(slot.id) || new Set();
          if (
            shotCharacters.length > slot.maximum
            || shotCharacters.some((characterId) => !coverage.has(characterId))
          ) return;
        }
      }
      state.busy = true;
      state.error = "";
      this.render();
      try {
        const response = await fetch(
          `${PRODUCTION_ENDPOINT}/${pathId(this._production.projectId)}/shots/${pathId(shotId)}/prepare`,
          {
            method: "POST", credentials: "same-origin", cache: "no-store",
            headers: { Accept: "application/json", "Content-Type": "application/json" },
            body: JSON.stringify({
              schema_version: "castle.production-shot-prepare.v1",
              expected_state_hash: this._production.stateHash,
              route_variant_id: candidate.routeId,
              profile_key: candidate.profileKey,
              selected_options: selectedOptions,
              inputs,
            }),
          },
        );
        const raw = await response.json().catch(() => null);
        if (!response.ok) throw new Error(response.status === 409
          ? "Production state 已改變，請重新載入。"
          : "鏡頭 preparation 沒有建立；沒有排隊或扣點。" );
        if (
          raw?.schema_version !== "castle.production-shot-prepare.response.v1"
          || raw.ok !== true || containsSensitiveField(raw)
          || !safeHash(raw.preparation?.preparation_hash)
          || raw.preparation.project_id !== this._production.projectId
          || raw.preparation.shot_id !== shotId
          || raw.preparation.route_variant_id !== candidate.routeId
          || raw.authority?.asset_versions_server_resolved !== true
          || raw.authority?.provider_submission_authorized !== false
          || raw.authority?.gpu_queue_authorized !== false
          || raw.authority?.credits_authorized !== 0
        ) throw new Error("Preparation 回應未通過安全契約。" );
        state.prepared = Object.freeze({
          hash: raw.preparation.preparation_hash,
          providerId: raw.preparation.provider_id,
          variantId: raw.preparation.provider_variant_id,
          routeId: raw.preparation.route_variant_id,
        });
      } catch (error) {
        state.error = safeText(error?.message, "鏡頭 preparation 失敗。", 300);
      } finally {
        state.busy = false;
        this.render();
      }
    }

    async _quotePreparedShot(shotId) {
      const state = this._shotPreparation.get(shotId);
      if (!this._production || !state?.prepared || state.busy || state.quote
        || state.confirmed || state.confirmationUncertainPlanId) return;
      state.busy = true;
      state.error = "";
      state.confirmationRetryMessage = "";
      let confirmAutomatically = false;
      this.render();
      try {
        const response = await fetch(
          `${PRODUCTION_ENDPOINT}/${pathId(this._production.projectId)}/shots/${pathId(shotId)}/quote`,
          {
            method: "POST", credentials: "same-origin", cache: "no-store",
            headers: { Accept: "application/json", "Content-Type": "application/json" },
            body: JSON.stringify({
              schema_version: "castle.production-shot-quote.v1",
              expected_state_hash: this._production.stateHash,
              preparation_hash: state.prepared.hash,
            }),
          },
        );
        const raw = await response.json().catch(() => null);
        const plan = raw?.plan;
        const credits = plan?.quote?.credits;
        if (!response.ok) throw new Error(response.status === 409
          ? "Production state 已改變，請重新準備鏡頭。"
          : "目前無法取得這個精確路由的正式報價。" );
        if (
          raw?.schema_version !== "castle.production-shot-quote.response.v1"
          || raw.ok !== true || raw.project_id !== this._production.projectId
          || raw.shot_id !== shotId || raw.preparation_hash !== state.prepared.hash
          || plan?.schema_version !== 1 || !/^plan:[0-9a-f]{32,64}$/.test(plan.plan_id || "")
          || typeof plan.plan_token !== "string" || plan.plan_token.length < 64
          || !Number.isSafeInteger(credits) || credits < 0
          || plan.confirmation_required !== true
          || plan.provider_write_performed !== false || plan.credits_spent !== 0
          || raw.authority?.provider_write_performed !== false
          || raw.authority?.credits_spent !== 0
          || raw.authority?.explicit_confirmation_required !== true
        ) throw new Error("正式報價回應未通過安全契約。" );
        state.quote = Object.freeze({
          planId: plan.plan_id, token: plan.plan_token, credits,
          providerId: plan.decision?.selected_provider_id || state.prepared.providerId,
        });
        confirmAutomatically = true;
      } catch (error) {
        state.error = safeText(error?.message, "正式報價失敗。", 300);
      } finally {
        state.busy = false;
        this.render();
      }
      if (confirmAutomatically && state.quote) {
        await this._confirmPreparedShot(shotId, null, null, { automatic: true });
      }
    }

    async _confirmPreparedShot(shotId, consent, button, { automatic = false } = {}) {
      const state = this._shotPreparation.get(shotId);
      const quote = state?.quote;
      if (!quote || (!automatic && !consent?.checked) || state.busy) return;
      state.busy = true;
      state.error = "";
      state.quote = null;
      if (button) button.disabled = true;
      if (consent) consent.disabled = true;
      this.render();
      try {
        const response = await fetch(
          `/api/intents/${pathId(quote.planId)}/confirm`,
          {
            method: "POST", credentials: "same-origin", cache: "no-store",
            headers: { Accept: "application/json", "Content-Type": "application/json" },
            body: JSON.stringify({
              schema_version: 1, plan_token: quote.token,
              confirmed: true, accepted_credits: quote.credits,
            }),
          },
        );
        const raw = await response.json().catch(() => null);
        if (!response.ok) {
          const rejection = new Error(response.status === 409
            ? "製作設定剛剛有更新，這次沒有建立工作。"
            : "這次沒有建立工作。" );
          rejection.status = response.status;
          throw rejection;
        }
        if (
          raw?.schema_version !== 1 || raw.plan_id !== quote.planId
          || !/^intent:[0-9a-f]{32}$/.test(raw.intent_id || "")
          || raw.credits_authorized !== quote.credits || raw.credits_spent !== 0
          || raw.transport_dispatched !== false || raw.state !== "pending"
        ) throw new Error("確認回應未證明工作安全進入待處理佇列。" );
        const route = this._productionPack?.route_variants?.find(
          (item) => item?.route_variant_id === state.prepared.routeId,
        );
        const routeVisualClasses = Array.isArray(route?.visual_classes) ? route.visual_classes : [];
        if (!route || route.provider_id !== quote.providerId || routeVisualClasses.length !== 1
          || !["anime", "realistic", "illustration"].includes(routeVisualClasses[0])) {
          throw new Error("確認工作與 production route 不一致。" );
        }
        state.confirmed = Object.freeze({
          intentId: raw.intent_id, credits: quote.credits, providerId: quote.providerId,
          capability: route.capability, visualClass: routeVisualClasses[0],
          state: "pending", terminal: false, transportMayHaveOccurred: false,
          providerReceiptAvailable: false, outputVerified: false,
          creditsCharged: null, updatedAt: "", statusError: "",
        });
        state.confirmationUncertainPlanId = "";
        state.confirmationUncertainMessage = "";
        state.confirmationRetryMessage = "";
      } catch (error) {
        const deterministicRejection = Number.isInteger(error?.status)
          && error.status >= 400 && error.status < 500;
        if (deterministicRejection) {
          state.confirmationUncertainPlanId = "";
          state.confirmationUncertainMessage = "";
          state.confirmationRetryMessage = error.status === 409
            ? "製作設定剛剛有更新，這次沒有建立工作。可以直接再試一次。"
            : "這次沒有建立工作。可以直接再試一次。";
        } else {
          state.confirmationUncertainPlanId = quote.planId;
          state.confirmationUncertainMessage = "Castle 正在核對這次工作是否已建立。你現在不需要再按生成；重新開啟專案時會從已保存進度接續。";
          state.confirmationRetryMessage = "";
        }
        state.error = "";
      } finally {
        state.busy = false;
        this.render();
      }
    }

    async _refreshShotIntent(shotId) {
      const state = this._shotPreparation.get(shotId);
      const expected = state?.confirmed;
      if (!expected || state.busy) return;
      state.busy = true;
      state.error = "";
      this.render();
      try {
        const response = await fetch(`/api/intents/${pathId(expected.intentId)}/status`, {
          method: "GET", credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" },
        });
        const raw = await response.json().catch(() => null);
        const fields = [
          "schema_version", "intent_id", "provider_id", "capability", "visual_class", "state",
          "terminal", "retry_available", "transport_may_have_occurred", "provider_receipt_available",
          "output_verified", "credits_authorized", "credits_charged", "remaining_credits",
          "created_at", "updated_at", "status_check_transport_performed", "status_check_credits_spent",
        ];
        if (!response.ok) throw new Error("無法讀取持久工作狀態；原工作仍保留，請勿重新送出。" );
        if (
          !exactKeys(raw, fields) || containsSensitiveField(raw) || raw.schema_version !== 1
          || raw.intent_id !== expected.intentId || raw.provider_id !== expected.providerId
          || raw.capability !== expected.capability || raw.visual_class !== expected.visualClass
          || !INTENT_STATES.includes(raw.state)
          || raw.terminal !== TERMINAL_INTENT_STATES.includes(raw.state)
          || raw.retry_available !== false || typeof raw.transport_may_have_occurred !== "boolean"
          || typeof raw.provider_receipt_available !== "boolean"
          || typeof raw.output_verified !== "boolean"
          || (raw.output_verified && raw.state !== "completed")
          || raw.credits_authorized !== expected.credits || !Number.isSafeInteger(raw.credits_authorized)
          || (raw.credits_charged !== null && (!Number.isSafeInteger(raw.credits_charged) || raw.credits_charged < 0))
          || (raw.remaining_credits !== null && (!Number.isSafeInteger(raw.remaining_credits) || raw.remaining_credits < 0))
          || typeof raw.created_at !== "string" || !raw.created_at
          || typeof raw.updated_at !== "string" || !raw.updated_at
          || raw.status_check_transport_performed !== false || raw.status_check_credits_spent !== 0
          || (raw.provider_receipt_available && !raw.transport_may_have_occurred)
          || (raw.state === "pending" && (raw.transport_may_have_occurred || raw.provider_receipt_available))
          || (raw.state !== "pending" && raw.state !== "abandoned" && !raw.transport_may_have_occurred)
          || (["submitted", "queued", "running", "completed", "failed", "timeout"].includes(raw.state)
            && !raw.provider_receipt_available)
        ) throw new Error("工作狀態沒有通過 owner／route／credits 安全契約。" );
        state.confirmed = Object.freeze({
          ...expected, state: raw.state, terminal: raw.terminal,
          transportMayHaveOccurred: raw.transport_may_have_occurred,
          providerReceiptAvailable: raw.provider_receipt_available,
          outputVerified: raw.output_verified,
          creditsCharged: raw.credits_charged, updatedAt: raw.updated_at, statusError: "",
        });
        if (raw.state === "completed") {
          await this._reloadProduction();
          await this._loadProductionOutputMedia();
        }
      } catch (error) {
        state.confirmed = Object.freeze({
          ...expected, statusError: safeText(error?.message, "無法確認最新狀態；請勿重新送出。", 300),
        });
      } finally {
        state.busy = false;
        this.render();
      }
    }

    async _prepareRejectedShotRepair(shotId, version) {
      if (!this._production || this._production.phase !== "story_review") return;
      let state = this._shotPreparation.get(shotId);
      if (!state) {
        state = { busy: false, error: "", candidates: [], assets: [], prepared: null };
        this._shotPreparation.set(shotId, state);
      }
      if (state.busy) return;
      state.busy = true;
      state.error = "";
      this.render();
      try {
        const response = await fetch(
          `${PRODUCTION_ENDPOINT}/${pathId(this._production.projectId)}/shots/${pathId(shotId)}/repair`,
          {
            method: "POST", credentials: "same-origin", cache: "no-store",
            headers: { Accept: "application/json", "Content-Type": "application/json" },
            body: JSON.stringify({
              schema_version: "castle.production-shot-repair.v1",
              expected_state_hash: this._production.stateHash,
              rejected_version: version,
            }),
          },
        );
        const raw = await response.json().catch(() => null);
        const preparation = raw?.preparation;
        if (!response.ok) throw new Error(response.status === 409
          ? "Production state 已改變，請重新載入再修復。"
          : "無法從這個被退回版本建立安全的修復工作。" );
        if (
          raw?.schema_version !== "castle.production-shot-repair.response.v1"
          || raw.ok !== true || containsSensitiveField(raw)
          || preparation?.schema_version !== "castle.production-shot-repair-preparation.v1"
          || preparation.project_id !== this._production.projectId
          || preparation.shot_id !== shotId || preparation.rejected_version !== version
          || !safeHash(preparation.preparation_hash)
          || raw.authority?.rejected_output_server_resolved !== true
          || raw.authority?.provider_submission_authorized !== false
          || raw.authority?.gpu_queue_authorized !== false
          || raw.authority?.credits_authorized !== 0
        ) throw new Error("修復 preparation 回應未通過安全契約。" );
        state.prepared = Object.freeze({
          hash: preparation.preparation_hash,
          providerId: "", variantId: "修復上一版", routeId: preparation.rejected_route_variant_id,
          repair: true,
        });
        state.quote = null;
        state.confirmed = null;
      } catch (error) {
        state.error = safeText(error?.message, "鏡頭修復準備失敗。", 300);
      } finally {
        state.busy = false;
        this.render();
      }
    }

    _renderShotPreparation(shot) {
      if (!this._production) return null;
      const state = this._shotPreparation.get(shot.shotId);
      const selecting = ["assets_locked", "generating"].includes(this._production.phase);
      if (!selecting && !(
        this._production.phase === "story_review"
        && (state?.busy || state?.error || state?.prepared?.repair)
      )) return null;
      if (!state) {
        const button = el("button", { className: "project-preview__button", type: "button", text: "選擇模組與素材（不執行）" });
        button.addEventListener("click", () => this._loadShotPreparation(shot.shotId));
        return button;
      }
      if (state.busy) return el("p", { text: "正在核對模組與 immutable 素材版本…" });
      if (state.error) return el("div", { className: "project-preview__preparation-error" }, [
        el("p", { text: state.error }),
        (() => { const retry = el("button", { className: "project-preview__button", type: "button", text: "重新載入" }); retry.addEventListener("click", () => { this._shotPreparation.delete(shot.shotId); this._loadShotPreparation(shot.shotId); }); return retry; })(),
      ]);
      if (state.prepared) return el("div", { className: "project-preview__prepared" }, [
        el("strong", { text: "Preparation 已保存" }),
        el("span", { text: `${providerLabel(state.prepared.providerId)} · ${state.prepared.variantId}` }),
        el("code", { text: `${state.prepared.hash.slice(0, 12)}…` }),
        state.confirmed
          ? (() => {
            const copy = INTENT_COPY[state.confirmed.state] || INTENT_COPY.pending;
            const detail = state.confirmed.state === "completed" && state.confirmed.outputVerified
              ? "輸出已經 Castle 驗證並納入媒體庫。"
              : copy[1];
            const refresh = el("button", {
              className: "project-preview__button", type: "button",
              text: state.busy ? "更新中…" : "更新工作狀態（不呼叫供應商）",
              attributes: { disabled: state.busy ? "" : null },
            });
            refresh.addEventListener("click", () => this._refreshShotIntent(shot.shotId));
            return el("div", { className: `project-preview__intent-status is-${state.confirmed.state}` }, [
              el("strong", { text: copy[0] }), el("span", { text: detail }),
              el("code", { text: state.confirmed.intentId }),
              state.confirmed.creditsCharged === null ? null
                : el("small", { text: `已記錄扣點 ${state.confirmed.creditsCharged}／授權 ${state.confirmed.credits}` }),
              state.confirmed.statusError ? el("p", { text: state.confirmed.statusError }) : null,
              refresh,
            ]);
          })()
          : state.confirmationUncertainPlanId
            ? el("div", {
              className: "project-preview__preparation-error",
              text: state.confirmationUncertainMessage,
              attributes: { role: "alert" },
            })
          : state.quote
            ? (() => {
              const confirm = el("button", {
                className: "project-preview__primary", type: "button",
                text: "繼續生成",
              });
              confirm.addEventListener("click", () => this._confirmPreparedShot(
                shot.shotId, null, confirm, { automatic: true },
              ));
              return el("div", { className: "project-preview__production-confirm" }, [
                el("strong", { text: `當下價格紀錄：${state.quote.credits} 點` }),
                confirm,
              ]);
            })()
            : (() => {
              const quote = el("button", {
                className: "project-preview__primary", type: "button",
                text: state.confirmationRetryMessage ? "再試一次" : "生成",
              });
              quote.addEventListener("click", () => this._quotePreparedShot(shot.shotId));
              return el("div", {}, [
                el("small", {
                  text: state.confirmationRetryMessage
                    || "按下後會取得當下價格並建立同一份可追蹤工作。",
                }),
                quote,
              ]);
            })(),
      ]);
      const packShot = this._productionPack?.shots?.find((item) => item?.shot_id === shot.shotId);
      const resourceIds = Array.isArray(packShot?.input_asset_ids) ? packShot.input_asset_ids : [];
      const shotCharacters = Array.isArray(packShot?.entity_ids)
        ? packShot.entity_ids.map(safeId).filter(Boolean)
        : [];
      const automaticCandidate = state.candidates.find((candidate) => candidate.qualified)
        || state.candidates[0];
      const automaticCandidates = automaticCandidate ? [automaticCandidate] : [];
      return el("div", { className: "project-preview__preparation-candidates" }, automaticCandidates.map((candidate) => {
        const card = el("article", { className: `project-preview__preparation-card${candidate.qualified ? " is-ready" : " is-blocked"}` }, [
          el("strong", { text: "自動路由 · 參考圖影片" }),
          el("small", { text: `${visualLabel(candidate.visualClass)} · 系統依所選圖片自動配對` }),
        ]);
        if (!candidate.qualified) {
          card.append(el("p", { text: candidate.reasons.map(
            (item) => routeReadinessReason(candidate.providerId, item),
          ).join("、") }));
          return card;
        }
        for (const [key, values] of Object.entries(candidate.options)) {
          card.append(el("label", {}, [el("span", { text: key }), el("select", {
            attributes: { "data-preparation-option": key },
          }, values.map((value) => el("option", { text: String(value), attributes: { value: JSON.stringify(value) } })))]));
        }
        let missingRequiredAsset = false;
        for (const slot of candidate.slots) {
          const serverSlot = candidate.materialSlots.find((item) => item.id === slot.id);
          const fallbackAssets = state.assets.filter((asset) => (
            asset.kind === slot.kind
            && (!slot.identityBearing || asset.visualClass === candidate.visualClass)
            && (!slot.identityBearing || asset.characterIds.length === 0
              || asset.characterIds.some((id) => shotCharacters.includes(id)))
          ));
          const eligible = serverSlot
            ? [...serverSlot.assets]
            : rankPreparationAssets(fallbackAssets, candidate, shotCharacters);
          const repeatedIdentity = slot.identityBearing && slot.maximum > 1;
          if (repeatedIdentity) {
            const bindings = shotCharacters.slice(0, slot.maximum).map((characterId, index) => ({
              characterId,
              resourceId: resourceIds[index],
              bindingId: `${slot.id}-${index + 1}`,
            }));
            if (
              shotCharacters.length > slot.maximum
              || bindings.length < slot.minimum
              || bindings.some((binding) => !safeId(binding.resourceId))
            ) missingRequiredAsset = true;
            for (const binding of bindings) {
              const characterAssets = eligible.filter((asset) => (
                asset.characterIds.length === 1
                && asset.characterIds[0] === binding.characterId
              ));
              if (!characterAssets.length) missingRequiredAsset = true;
              card.append(el("div", { className: "project-preview__preparation-input" }, [
                el("label", {}, [el("span", { text: `${binding.characterId} · Production resource` }), el("select", {
                  attributes: { "data-preparation-resource": binding.bindingId, disabled: "" },
                }, [el("option", {
                  text: safeId(binding.resourceId) || "缺少角色對應 resource",
                  attributes: { value: safeId(binding.resourceId) },
                })])]),
                el("label", {}, [el("span", { text: `${binding.characterId} · ${slot.kind} immutable version` }), el("select", {
                  attributes: {
                    "data-preparation-slot": slot.id,
                    "data-preparation-binding": binding.bindingId,
                    "data-preparation-character": binding.characterId,
                  },
                }, [
                  ...(!characterAssets.length ? [el("option", { text: "此角色尚無可用素材", attributes: { value: "" } })] : []),
                  ...characterAssets.map((asset, index) => el("option", {
                    text: `${asset.serverRecommended ? "伺服器建議 · " : (!serverSlot && index === 0 ? "角色匹配 · " : "")}${asset.title} · ${asset.providerId}${asset.catalogRole && asset.catalogRole !== "unclassified" ? ` · ${asset.catalogRole}` : ""}`,
                    attributes: { value: JSON.stringify([asset.projectionId, asset.versionId]) },
                  })),
                ])]),
              ]));
            }
          } else {
            if (slot.required && !eligible.length) missingRequiredAsset = true;
            card.append(el("div", { className: "project-preview__preparation-input" }, [
              el("label", {}, [el("span", { text: `${slot.id} · Production resource` }), el("select", {
                attributes: { "data-preparation-resource": slot.id },
              }, resourceIds.map((id) => el("option", { text: id, attributes: { value: id } })))]),
              el("label", {}, [el("span", { text: `${slot.kind} · Immutable version` }), el("select", {
                attributes: { "data-preparation-slot": slot.id },
              }, [
                ...(slot.required && !eligible.length ? [el("option", { text: "此鏡角色尚無可用素材", attributes: { value: "" } })] : []),
                ...(slot.required ? [] : [el("option", { text: "不使用", attributes: { value: "" } })]),
                ...eligible.map((asset, index) => el("option", {
                  text: `${asset.serverRecommended || (!serverSlot && index === 0) ? "伺服器建議 · " : ""}${asset.title} · ${asset.providerId}${asset.catalogRole && asset.catalogRole !== "unclassified" ? ` · ${asset.catalogRole}` : ""}${asset.characterIds.length ? " · 此鏡角色" : ""}`,
                  attributes: { value: JSON.stringify([asset.projectionId, asset.versionId]) },
                })),
              ])]),
            ]));
          }
        }
        if (missingRequiredAsset) card.append(el("p", {
          className: "project-preview__preparation-error",
          text: "這個模組缺少角色必要素材、超過角色上限，或角色與 Production resource 無法一一對應；請先補齊每位角色的 profile／參考素材。",
        }));
        const prepare = el("button", {
          className: "project-preview__primary", type: "button", text: "使用這些參考圖",
          attributes: { disabled: missingRequiredAsset ? "" : null },
        });
        prepare.addEventListener("click", () => this._prepareShot(shot.shotId, candidate, card));
        card.append(prepare);
        return card;
      }));
    }

    _renderProductionShots() {
      if (!this._production?.shots?.length) return null;
      return el("div", { className: "project-preview__production-shots" }, this._production.shots.map((shot) => {
        const versions = shot.versions.map((version, versionIndex) => {
          const note = el("input", {
            attributes: { maxlength: "1000", placeholder: "退回原因（例如：角色臉變了）", "aria-label": `${shot.shotId} 版本 ${version.version} 退回原因` },
          });
          const reviewOpen = this._production.phase === "story_review" && version.decision === null;
          const row = el("div", { className: "project-preview__production-version" }, [
            el("div", {}, [
              el("strong", { text: `版本 ${version.version}` }),
              el("small", { text: version.decision === "accepted" ? "已接受" : version.decision === "rejected" ? "已退回" : "等待審核" }),
            ]),
            this._renderProductionOutputMedia(shot.shotId, version.version),
            reviewOpen ? el("div", { className: "project-preview__production-review" }, [
              note,
              el("button", { type: "button", className: "project-preview__button", text: "退回重做" }),
              el("button", { type: "button", className: "project-preview__primary", text: "接受此版本" }),
            ]) : (version.review_note ? el("div", {}, [
              el("p", { text: version.review_note }),
              version.decision === "rejected" && versionIndex === shot.versions.length - 1
                ? (() => {
                  const repair = el("button", { className: "project-preview__button", type: "button", text: "依退回原因準備重做（不執行）" });
                  repair.addEventListener("click", () => this._prepareRejectedShotRepair(shot.shotId, version.version));
                  return repair;
                })()
                : null,
            ]) : null),
          ]);
          if (reviewOpen) {
            const buttons = row.querySelectorAll("button");
            buttons[0].addEventListener("click", () => this._reviewProductionShot(
              shot.shotId, version.version, "rejected", note.value.trim() || "需要重做",
            ));
            buttons[1].addEventListener("click", () => this._reviewProductionShot(
              shot.shotId, version.version, "accepted", "",
            ));
          }
          return row;
        });
        return el("article", { className: "project-preview__production-shot" }, [
          el("h3", { text: shot.shotId }),
          this._renderRecoveredIntent(shot.shotId),
          this._renderShotPreparation(shot),
          versions.length ? el("div", {}, versions) : el("p", { text: "尚未產生可審核版本。" }),
        ]);
      }));
    }

    _renderProductionPostproduction() {
      const production = this._production;
      if (!production || !["editing", "delivery_review", "ready"].includes(production.phase)) return null;
      const post = production.postproduction;
      const section = el("section", { className: "project-preview__postproduction" }, [
        el("h3", { text: "後製與最終成片" }),
      ]);
      if (!post?.receipt) {
        const consent = el("input", { attributes: { type: "checkbox" } });
        const execute = el("button", {
          className: "project-preview__primary", type: "button",
          text: "依已接受鏡頭剪輯並做 QA", attributes: { disabled: "" },
        });
        consent.addEventListener("change", () => { execute.disabled = !consent.checked; });
        execute.addEventListener("click", () => this._executeProductionEdit());
        section.append(
          el("p", { text: "只使用逐鏡已接受的 immutable output；CPU FFmpeg，不呼叫供應商、不排 GPU、不扣點。" }),
          el("label", {}, [consent, el("span", { text: "我確認要建立 deterministic edit 與 mechanical QA receipt。" })]),
          execute,
        );
        return section;
      }
      section.append(
        ...(post.receipt.contactSheet ? [el("img", {
          className: "project-preview__contact-sheet",
          attributes: {
            src: post.receipt.contactSheet.url,
            alt: "最終成片六格內容總覽",
            width: String(post.receipt.contactSheet.width),
            height: String(post.receipt.contactSheet.height),
            loading: "lazy",
          },
        })] : []),
        el("a", {
          className: "project-preview__primary", text: "播放／取得已驗證 MP4",
          attributes: { href: post.receipt.outputUrl, target: "_blank", rel: "noopener" },
        }),
        el("small", { text: `${post.receipt.width}×${post.receipt.height} · ${post.receipt.fps} fps · ${post.receipt.duration.toFixed(2)} 秒 · ${(post.receipt.byteSize / 1048576).toFixed(1)} MB${post.receipt.subtitleBurnedIn ? " · 字幕已內嵌" : ""}${post.receipt.soundtrackMixed ? " · 背景音樂已混音" : ""}${post.receipt.soundtrackDuckingApplied ? " · 對白自動避讓" : ""}${post.receipt.soundEffectCount ? ` · 效果音 ${post.receipt.soundEffectCount} 段` : ""}` }),
        el("code", { text: `receipt ${post.receipt.hash.slice(0, 12)}… · library ${post.receipt.outputAssetVersionId}` }),
      );
      if (production.phase === "delivery_review" && !post.review) {
        const note = el("textarea", { attributes: { maxlength: "1000", rows: "3", placeholder: "人工核對備註或退回原因" } });
        const reject = el("button", { className: "project-preview__button", type: "button", text: "退回重新剪輯" });
        const accept = el("button", { className: "project-preview__primary", type: "button", text: "接受最終成片" });
        reject.addEventListener("click", () => this._reviewProductionDelivery("rejected", note.value.trim() || "最終成片需要重新剪輯。"));
        accept.addEventListener("click", () => this._reviewProductionDelivery("accepted", note.value.trim()));
        section.append(note, el("div", {}, [reject, accept]));
      } else if (post.review) {
        section.append(el("p", { text: post.review.decision === "accepted"
          ? `已接受：${post.review.note || "人工核對完成"}`
          : `已退回：${post.review.note}` }));
        if (production.phase === "delivery_review" && post.review.decision === "rejected") {
          const retry = el("button", { className: "project-preview__primary", type: "button", text: "重新剪輯並再次 QA" });
          retry.addEventListener("click", () => this._executeProductionEdit());
          section.append(retry);
        }
        if (production.phase === "ready" && post.review.decision === "accepted") {
          section.append(el("a", {
            className: "project-preview__primary", text: "取得完整交付包（MP4＋SRT＋時間軸＋QA）",
            attributes: {
              href: `${PRODUCTION_ENDPOINT}/${pathId(production.projectId)}/delivery-package`,
              download: `castle-${production.projectId}-delivery.zip`,
            },
          }));
        }
      }
      return section;
    }

    async _compileBrief(form) {
      if (this._briefBusy || this._routeBusy) return;
      const data = new FormData(form);
      const characterIds = data.getAll("character_id").map(String);
      const selected = this._characters.filter((item) => characterIds.includes(item.id));
      const mode = String(data.get("mode") || "single");
      const beatRows = [...form.querySelectorAll(".project-preview__beat")];
      const beats = beatRows.map((row, index) => {
        const beat = {
          beat_id: `beat-${String(index + 1).padStart(3, "0")}`,
          summary: String(row.querySelector('[name="beat_summary"]')?.value || "").trim(),
          action: String(row.querySelector('[name="beat_action"]')?.value || "").trim(),
          camera: String(row.querySelector('[name="beat_camera"]')?.value || "").trim(),
          audio: String(row.querySelector('[name="beat_audio"]')?.value || "").trim(),
          duration_seconds: Number(row.querySelector('[name="beat_duration"]')?.value || 5),
          character_ids: [...row.querySelectorAll("[data-beat-character-id]:checked")]
            .map((input) => String(input.dataset.beatCharacterId || ""))
            .filter((id) => characterIds.includes(id)),
        };
        const effects = [...row.querySelectorAll(".project-preview__effect")].map((effectRow, effectIndex) => ({
          cue_id: `effect-${String(index + 1).padStart(3, "0")}-${String(effectIndex + 1).padStart(2, "0")}`,
          asset_version_id: safeId(String(effectRow.querySelector('[name="effect_asset_version_id"]')?.value || "")),
          start_seconds: Number(effectRow.querySelector('[name="effect_start"]')?.value || 0),
          source_start_seconds: Number(effectRow.querySelector('[name="effect_source_start"]')?.value || 0),
          duration_seconds: Number(effectRow.querySelector('[name="effect_duration"]')?.value || 1),
          gain_db: Number(effectRow.querySelector('[name="effect_gain"]')?.value || -6),
        })).filter((effect) => effect.asset_version_id);
        if (effects.length) beat.sound_effects = effects;
        return beat;
      });
      if (!selected.length) {
        this._error = "請至少選一個已分類、已有大頭貼的角色。";
        this.render();
        return;
      }
      const emptyBeat = beats.findIndex((beat) => beat.character_ids.length === 0);
      if (emptyBeat >= 0) {
        this._error = `請替段落 ${emptyBeat + 1} 選擇至少一位出場角色。`;
        this.render();
        return;
      }
      const mixedBeat = beats.findIndex((beat) => new Set(
        beat.character_ids.map((id) => selected.find((item) => item.id === id)?.visualClass),
      ).size > 1);
      if (mixedBeat >= 0) {
        this._error = `段落 ${mixedBeat + 1} 同時包含動漫與真人角色；請拆成兩個鏡頭，讓 Castle 使用正確模組。`;
        this.render();
        return;
      }
      const invalidEffectBeat = beats.findIndex((beat) => beat.sound_effects?.some((effect) => (
        !this._soundtracks.some((item) => item.versionId === effect.asset_version_id
          && ["unclassified", "sound_effect", "ambience"].includes(item.role))
        || effect.start_seconds < 0 || effect.source_start_seconds < 0
        || effect.duration_seconds < 0.05
        || effect.start_seconds + effect.duration_seconds > beat.duration_seconds
        || (() => {
          const source = this._soundtracks.find((item) => item.versionId === effect.asset_version_id);
          return source?.duration !== null
            && effect.source_start_seconds + effect.duration_seconds > source.duration;
        })()
        || effect.gain_db < -36 || effect.gain_db > 6
      )));
      if (invalidEffectBeat >= 0) {
        this._error = `段落 ${invalidEffectBeat + 1} 的效果音超出鏡頭時間，或素材已不可用。`;
        this.render();
        return;
      }
      const body = {
        schema_version: BRIEF_SCHEMA,
        mode,
        title: String(data.get("title") || "").trim(),
        premise: String(data.get("premise") || "").trim(),
        audience: String(data.get("audience") || "").trim(),
        platform: String(data.get("platform") || "shorts-vertical"),
        language: String(data.get("language") || "zh-TW").trim(),
        aspect_ratio: String(data.get("aspect_ratio") || "9:16"),
        series_id: mode === "single" ? null : String(data.get("series_id") || "").trim(),
        characters: selected.map((item) => ({
          character_id: item.id,
          display_name: item.label,
          visual_class: item.visualClass,
          profile_asset_id: item.profileAssetId,
        })),
        beats,
      };
      const soundtrackVersion = safeId(String(data.get("soundtrack_asset_version_id") || ""));
      if (soundtrackVersion) {
        const selectedSoundtrack = this._soundtracks.find(
          (item) => item.versionId === soundtrackVersion
            && ["unclassified", "music"].includes(item.role),
        );
        if (!selectedSoundtrack) {
          this._error = "請重新選擇目前媒體庫中可用的背景音樂。";
          this.render();
          return;
        }
        body.soundtrack_asset_version_id = selectedSoundtrack.versionId;
        body.soundtrack_gain_db = Number(data.get("soundtrack_gain_db") || -14);
      }
      this._briefBusy = true;
      this._draft = null;
      this._directorPlan = null;
      this._brief = null;
      this._routeReview = null;
      this._packProposal = null;
      this._seriesBible = null;
      this._seriesChecked = false;
      this._error = "";
      this._message = "正在整理故事板；不會排隊、生成或扣點。";
      this.render();
      let briefReady = false;
      try {
        const response = await fetch(BRIEF_ENDPOINT, {
          method: "POST",
          credentials: "same-origin",
          cache: "no-store",
          headers: { Accept: "application/json", "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const result = await response.json().catch(() => null);
        if (!response.ok) {
          if (response.status === 401) window.dispatchEvent(new CustomEvent("castle-auth-required"));
          throw new Error(safeText(result?.error?.message, "故事板資料未通過檢查。", 300));
        }
        if (
          !result || result.schema_version !== "castle.production-brief.response.v1"
          || result.ok !== true || !result.draft
          || result.authority?.execution_authorized !== false
          || result.authority?.credentials_accessed !== false
          || Object.values(result.side_effects || {}).some((value) => value !== 0)
        ) throw new Error("Castle 回傳的故事板安全聲明不完整，已拒絕顯示。");
        if (
          result.director_plan?.schema_version !== "castle.director-plan.v1"
          || result.director_plan.draft_hash !== result.draft.draft_hash
          || result.director_plan.execution_authorized !== false
          || !safeHash(result.director_plan.plan_hash)
          || !Array.isArray(result.director_plan.shots)
        ) throw new Error("Castle 回傳的導演計畫不完整，已拒絕顯示。");
        this._draft = result.draft;
        this._directorPlan = result.director_plan;
        this._brief = body;
        briefReady = true;
        this._message = "故事板已整理；正在準備可用的試做方式。";
      } catch (error) {
        this._error = safeText(error && error.message, "故事板建立失敗。", 300);
        this._message = "";
      } finally {
        this._briefBusy = false;
        this.render();
      }
      if (briefReady) await this._reviewRoutes();
    }

    async _loadAutoIPProvider() {
      const response = await fetch(PROVIDERS_ENDPOINT, {
        method: "GET", credentials: "same-origin", cache: "no-store",
        headers: { Accept: "application/json" },
      });
      const raw = await response.json().catch(() => null);
      if (response.status === 401) {
        window.dispatchEvent(new CustomEvent("castle-auth-required"));
      }
      const provider = response.ok ? normalizeAutoIPProvider(raw) : null;
      if (!provider) throw new Error("目前無法安全讀取 AutoIP 連線狀態。");
      this._autoipProvider = provider;
      if (provider.accountId) this._autoipSetupAccountId = provider.accountId;
      return provider;
    }

    async _probeStoredAutoIP(accountId) {
      const canonicalAccountId = safeId(accountId);
      if (!canonicalAccountId) throw new Error("AutoIP 帳號識別碼不正確。");
      let probeBody = JSON.stringify({ schema_version: 1 });
      try {
        const response = await fetch(
          `/api/providers/autoip/accounts/${pathId(canonicalAccountId)}/probe`,
          {
            method: "POST", credentials: "same-origin", cache: "no-store",
            headers: { Accept: "application/json", "Content-Type": "application/json" },
            body: probeBody,
          },
        );
        probeBody = "";
        if (response.status === 401) {
          window.dispatchEvent(new CustomEvent("castle-auth-required"));
        }
        const raw = await response.json().catch(() => null);
        const activation = response.ok
          ? normalizeAutoIPActivation(raw, canonicalAccountId)
          : null;
        if (!activation) {
          throw new Error("AutoIP 的帳號、API 或價格表目前無法通過零扣點檢查。");
        }
        this._autoipProvider = Object.freeze({
          accountId: canonicalAccountId, configured: true, status: "ready", ready: true,
        });
        return activation;
      } finally {
        probeBody = "";
      }
    }

    async _refreshStoredAutoIP(accountId) {
      if (!this._canConfigureProviders || this._autoipSetupBusy || this._routeBusy) return;
      this._autoipSetupBusy = true;
      this._autoipSetupMessage = "正在使用已保存的 Key 做零扣點連線檢查；不需要重新貼 Key…";
      this.render();
      try {
        const activation = await this._probeStoredAutoIP(accountId);
        this._autoipSetupMessage = `AutoIP 已恢復；目前剩餘 ${activation.availableCredits ?? "未知"} 點。正在重新整理這份專案…`;
        this._autoipSetupBusy = false;
        await this._reviewRoutes({ refreshAutoIP: false });
      } catch (error) {
        this._autoipSetupMessage = safeText(error?.message, "AutoIP 連線檢查沒有完成。", 300);
      } finally {
        this._autoipSetupBusy = false;
        this.render();
      }
    }

    async _reviewRoutes({ refreshAutoIP = true } = {}) {
      if (this._routeBusy || !this._brief || !safeHash(this._draft?.draft_hash)) return;
      this._routeBusy = true;
      this._routeReview = null;
      this._packProposal = null;
      this._seriesBible = null;
      this._seriesChecked = false;
      this._error = "";
      this._message = "正在核對真人／動漫模組、素材欄位與目前連線狀態；不會執行。";
      this.render();
      try {
        if (refreshAutoIP && this._canConfigureProviders) {
          try {
            const provider = await this._loadAutoIPProvider();
            if (provider.configured && provider.accountId && !provider.ready) {
              this._message = "已找到保存好的 AutoIP Key；正在做零扣點連線與價格檢查…";
              this.render();
              const activation = await this._probeStoredAutoIP(provider.accountId);
              this._autoipSetupMessage = `AutoIP 已恢復；目前剩餘 ${activation.availableCredits ?? "未知"} 點。`;
            }
          } catch (error) {
            // A provider-status refresh must not hide otherwise-ready local H3
            // routes.  Keep the actionable AutoIP error beside its route card.
            this._autoipSetupMessage = safeText(
              error?.message, "AutoIP 連線檢查沒有完成。", 300,
            );
          }
        }
        const response = await fetch(ROUTE_REVIEW_ENDPOINT, {
          method: "POST", credentials: "same-origin", cache: "no-store",
          headers: { Accept: "application/json", "Content-Type": "application/json" },
          body: JSON.stringify(this._brief),
        });
        const result = await response.json().catch(() => null);
        if (!response.ok) {
          if (response.status === 401) window.dispatchEvent(new CustomEvent("castle-auth-required"));
          throw new Error("生成模組檢查暫時無法完成。");
        }
        const normalized = normalizeRouteReviewResponse(result, this._draft.draft_hash);
        if (!normalized) throw new Error("Castle 回傳的模組安全聲明不完整，已拒絕顯示。");
        const proposal = normalizePackProposal(
          result.pack_proposal, this._draft.draft_hash, normalized.reviewHash,
        );
        if (!proposal) throw new Error("Castle 回傳的 Production Pack 提案不完整，已拒絕顯示。");
        this._routeReview = normalized;
        this._packProposal = proposal;
        this._message = "已依每個鏡頭的角色類型列出可用與尚未就緒的模組；仍未選擇或執行。";
      } catch (error) {
        this._error = safeText(error?.message, "生成模組檢查失敗。", 300);
        this._message = "";
      } finally {
        this._routeBusy = false;
        this.render();
        if (this._routeReview) {
          queueMicrotask(() => {
            const firstShot = this.querySelector(".project-preview__shot-routes");
            const firstShotButtons = firstShot
              ? [...firstShot.querySelectorAll(".project-preview__trial-button")]
              : [];
            const target = firstShotButtons.length === 1
              ? firstShotButtons[0]
              : this.querySelector(".project-preview__route-review");
            if (!(target instanceof HTMLElement)) return;
            if (target.matches("button,[tabindex]")) target.focus({ preventScroll: true });
            if (typeof target.scrollIntoView === "function") {
              target.scrollIntoView({ behavior: "smooth", block: "center" });
            }
          });
        }
      }
    }

    async _connectAutoIP(event, accountField, credentialField) {
      event.preventDefault();
      if (!this._canConfigureProviders || this._autoipSetupBusy) return;
      const accountId = safeId(accountField?.value);
      let credentialValue = credentialField?.value;
      if (!accountId) {
        this._autoipSetupMessage = "帳號識別碼只能使用小寫英數、點、底線、冒號或連字號。";
        if (credentialField) credentialField.value = "";
        this.render();
        return;
      }
      if (
        typeof credentialValue !== "string"
        || credentialValue.length < 12
        || credentialValue.length > 4096
      ) {
        this._autoipSetupMessage = "請貼上有效的 AutoIP API Key。";
        if (credentialField) credentialField.value = "";
        this.render();
        return;
      }
      this._autoipSetupAccountId = accountId;
      let credentialBody = JSON.stringify({
        schema_version: 1,
        provider_id: "autoip",
        account_id: accountId,
        display_name: "AutoIP",
        api_base: "https://autoip.app/api/v1",
        credential_value: credentialValue,
      });
      credentialField.value = "";
      credentialValue = "";
      this._autoipSetupBusy = true;
      this._autoipSetupMessage = "正在只寫保存 Key，接著會做零扣點連線與即時價格檢查…";
      this.render();
      try {
        const credentialResponse = await fetch(
          `/api/providers/autoip/accounts/${pathId(accountId)}/credential`,
          {
            method: "PUT", credentials: "same-origin", cache: "no-store",
            headers: { Accept: "application/json", "Content-Type": "application/json" },
            body: credentialBody,
          },
        );
        credentialBody = "";
        if (credentialResponse.status === 401) {
          window.dispatchEvent(new CustomEvent("castle-auth-required"));
        }
        if (!credentialResponse.ok) {
          throw new Error(
            credentialResponse.status === 403
              ? "只有管理員可以保存 AutoIP Key。"
              : "AutoIP Key 沒有成功保存。",
          );
        }
        const activation = await this._probeStoredAutoIP(accountId);
        this._autoipSetupMessage = `AutoIP 已啟用；目前剩餘 ${activation.availableCredits ?? "未知"} 點。正在重新整理這份專案的可用路線…`;
        this._autoipSetupBusy = false;
        await this._reviewRoutes({ refreshAutoIP: false });
      } catch (error) {
        this._autoipSetupMessage = safeText(error?.message, "AutoIP 連接沒有完成。", 300);
      } finally {
        credentialBody = "";
        this._autoipSetupBusy = false;
        this.render();
      }
    }

    async _loadSeriesBible() {
      const seriesId = this._packProposal?.seriesId;
      if (!seriesId || this._seriesBusy) return;
      this._seriesBusy = true;
      this._error = "";
      this.render();
      try {
        const response = await fetch(`/api/series/${pathId(seriesId)}`, {
          method: "GET", credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" },
        });
        if (response.status === 404) {
          this._seriesChecked = true;
          this._seriesBible = null;
          return;
        }
        const raw = await response.json().catch(() => null);
        if (!response.ok) throw new Error("系列 Bible 暫時無法載入。");
        const bible = normalizeSeriesResponse(raw, seriesId);
        if (!bible) throw new Error("系列 Bible 安全聲明不完整，已拒絕顯示。");
        this._seriesBible = bible;
        this._seriesChecked = true;
        this._message = `已載入系列 Bible revision ${bible.revision}。`;
      } catch (error) {
        this._error = safeText(error?.message, "系列 Bible 載入失敗。", 300);
      } finally {
        this._seriesBusy = false;
        this.render();
      }
    }

    _canonFromForm(form) {
      const canon = {};
      for (const input of form.querySelectorAll("[data-series-canon-key]")) {
        const key = safeId(input.dataset.seriesCanonKey);
        const value = safeText(input.value, "", 500);
        if (!key || !value) return null;
        canon[key] = value;
      }
      return canon;
    }

    async _createSeriesBible(form) {
      const seriesId = this._packProposal?.seriesId;
      const canon = this._canonFromForm(form);
      if (!seriesId || !canon || this._seriesBusy || !this._draft) return;
      this._seriesBusy = true;
      this.render();
      try {
        const response = await fetch("/api/series", {
          method: "POST", credentials: "same-origin", cache: "no-store",
          headers: { Accept: "application/json", "Content-Type": "application/json" },
          body: JSON.stringify({
            schema_version: "castle.series-create.v1", series_id: seriesId,
            title: this._draft.title, entity_ids: this._draft.characters.map((item) => item.character_id), canon,
          }),
        });
        const raw = await response.json().catch(() => null);
        if (!response.ok) throw new Error("系列 Bible 建立失敗；可能已由另一個裝置建立。");
        const bible = normalizeSeriesResponse(raw, seriesId);
        if (!bible) throw new Error("系列 Bible 建立結果不完整，已拒絕採用。");
        this._seriesBible = bible;
        this._seriesChecked = true;
        this._message = "系列 Bible 已保存；尚未生成任何內容。";
      } catch (error) {
        this._error = safeText(error?.message, "系列 Bible 建立失敗。", 300);
      } finally {
        this._seriesBusy = false;
        this.render();
      }
    }

    async _appendSeriesEpisode(form) {
      const bible = this._seriesBible;
      const after = this._canonFromForm(form);
      const eventSummary = safeText(form.querySelector("[name=event_summary]")?.value, "", 3000);
      if (!bible || !after || !eventSummary || this._seriesBusy || !this._packProposal) return;
      const ordinal = bible.revision;
      this._seriesBusy = true;
      this.render();
      try {
        const response = await fetch(`/api/series/${pathId(bible.seriesId)}/episodes`, {
          method: "POST", credentials: "same-origin", cache: "no-store",
          headers: { Accept: "application/json", "Content-Type": "application/json" },
          body: JSON.stringify({
            schema_version: "castle.series-episode-append.v1",
            episode_id: `${bible.seriesId}.episode-${String(ordinal).padStart(3, "0")}`,
            ordinal, state_before: bible.canon, event_summary: eventSummary, state_after: after,
            director_plan_hash: this._packProposal.directorPlanHash,
            pack_proposal_hash: this._packProposal.proposalHash,
            expected_bible_hash: bible.bibleHash,
          }),
        });
        const raw = await response.json().catch(() => null);
        if (!response.ok) throw new Error(response.status === 409
          ? "系列狀態已被另一個裝置更新，請重新載入後再提交。"
          : "本集連續性保存失敗。");
        const normalized = normalizeSeriesResponse({
          schema_version: "castle.series.response.v1", ok: raw?.ok,
          bible: raw?.episode?.bible, authority: raw?.authority,
        }, bible.seriesId);
        if (!normalized) throw new Error("本集保存結果不完整，已拒絕採用。");
        this._seriesBible = normalized;
        this._message = `第 ${ordinal} 集連續性已保存；尚未生成或扣點。`;
      } catch (error) {
        this._error = safeText(error?.message, "本集連續性保存失敗。", 300);
      } finally {
        this._seriesBusy = false;
        this.render();
      }
    }

    _productionBindingFor(shotId, profile) {
      const production = this._production;
      const pack = this._productionPack;
      if (
        !production || !pack || !this._result
        || !["generating", "story_review"].includes(production.phase)
        || production.projectId !== this._result.projectId
        || production.packHash !== this._result.packHash
        || !production.shots.some((item) => item.shotId === shotId)
      ) return null;
      const packShot = Array.isArray(pack.shots)
        ? pack.shots.find((item) => item?.shot_id === shotId)
        : null;
      const eligible = Array.isArray(packShot?.eligible_visual_variant_ids)
        ? packShot.eligible_visual_variant_ids
        : [];
      const matches = Array.isArray(pack.route_variants) ? pack.route_variants.filter((route) => (
        eligible.includes(route?.route_variant_id)
        && route.provider_id === profile.providerId
        && route.variant_id === profile.variantId
        && route.capability === profile.capability
        && Array.isArray(route.visual_classes)
        && route.visual_classes.includes(profile.visualClass)
      )) : [];
      if (matches.length !== 1 || !safeId(matches[0].route_variant_id)) return null;
      return Object.freeze({
        schema_version: "castle.production-binding.v1",
        project_id: production.projectId,
        pack_hash: production.packHash,
        state_hash: production.stateHash,
        shot_id: shotId,
        route_variant_id: matches[0].route_variant_id,
      });
    }

    _shotBriefSnapshot(shotId) {
      const failed = (message) => Object.freeze({
        draftShot: null, characters: Object.freeze([]), error: `${message}；已停止送出，沒有扣點。`,
      });
      if (!safeId(shotId) || !this._brief || !this._draft) {
        return failed("故事板快照不完整，請重新整理試做方式");
      }
      const scenes = Array.isArray(this._draft.scenes) ? this._draft.scenes : [];
      const matches = scenes.map((scene) => scene?.shot).filter((shot) => shot?.shot_id === shotId);
      if (matches.length !== 1) return failed("找不到唯一對應的故事板鏡頭");
      const draftShot = matches[0];
      const characterIds = Array.isArray(draftShot.character_ids) ? draftShot.character_ids : [];
      if (
        !characterIds.length || characterIds.some((characterId) => !safeId(characterId))
        || new Set(characterIds).size !== characterIds.length
      ) return failed("鏡頭的角色名單為空、重複或已失效");
      const briefCharacters = Array.isArray(this._brief.characters) ? this._brief.characters : [];
      const characters = [];
      for (const characterId of characterIds) {
        const characterMatches = briefCharacters.filter(
          (character) => character?.character_id === characterId,
        );
        if (characterMatches.length !== 1) return failed(`角色 ${characterId} 不在本次故事板快照中`);
        const character = characterMatches[0];
        if (
          !safeText(character.display_name, "", 120)
          || !["anime", "realistic", "illustration"].includes(character.visual_class)
          || character.profile_asset_id !== `castle-character:${characterId}:avatar`
        ) return failed(`角色 ${characterId} 的固定身份圖已失效`);
        characters.push(Object.freeze({
          character_id: characterId,
          display_name: character.display_name,
          visual_class: character.visual_class,
          profile_asset_id: character.profile_asset_id,
        }));
      }
      const expectedAssetIds = characters.map((character) => character.profile_asset_id);
      const inputAssetIds = Array.isArray(draftShot.input_asset_ids)
        ? draftShot.input_asset_ids : [];
      if (
        inputAssetIds.length !== expectedAssetIds.length
        || inputAssetIds.some((assetId, index) => assetId !== expectedAssetIds[index])
      ) return failed("鏡頭的固定身份圖清單與角色快照不一致");
      return Object.freeze({ draftShot, characters: Object.freeze(characters), error: "" });
    }

    _routePlanContext(shot, profile) {
      const snapshot = this._shotBriefSnapshot(shot?.shotId);
      if (snapshot.error) return snapshot;
      const failed = (message) => Object.freeze({
        ...snapshot, error: `${message}；已停止送出，沒有扣點。`,
      });
      if (!profile?.selectable) return failed("這條生成路線目前不可用");
      if (
        (shot.visualClass && shot.visualClass !== profile.visualClass)
        || (shot.capability && shot.capability !== profile.capability)
        || snapshot.characters.some((character) => character.visual_class !== profile.visualClass)
      ) return failed("鏡頭角色與生成模組的類型不一致");
      const expectedCapability = snapshot.characters.length > 1
        ? "multi_subject_video"
        : "image_to_video";
      const characterClasses = new Set(
        snapshot.characters.map((character) => character.visual_class),
      );
      if (
        characterClasses.size !== 1
        || snapshot.draftShot.visual_class !== snapshot.characters[0].visual_class
        || snapshot.draftShot.required_capability !== expectedCapability
      ) return failed("故事板的角色類型或單人／多人設定與本次快照不一致");
      if (profile.capability !== expectedCapability) {
        return failed(snapshot.characters.length > 1
          ? `這個鏡頭有 ${snapshot.characters.length} 位角色，但目前模組不是多人參考影片`
          : "這個鏡頭只有一位角色，但目前模組不是單人圖生影片");
      }
      if (
        profile.providerId === "autoip" && profile.capability === "multi_subject_video"
        && snapshot.characters.length > AUTOIP_MULTI_SUBJECT_LIMIT
      ) return failed(
        `AutoIP 多人參考影片每個鏡頭最多 ${AUTOIP_MULTI_SUBJECT_LIMIT} 位角色；目前有 ${snapshot.characters.length} 位，請拆成兩個鏡頭後再試`,
      );
      return snapshot;
    }

    _openRoutePlan(shot, profile, card) {
      const context = this._routePlanContext(shot, profile);
      if (context.error) {
        this._error = context.error;
        this.render();
        return;
      }
      const { draftShot, characters } = context;
      if (card?.dataset?.routeProfile && card.dataset.routeProfile !== profile.key) {
        this._error = "生成路線卡片與所選模組不一致；已停止送出，沒有扣點。";
        this.render();
        return;
      }
      const options = {};
      const optionSelects = [...card.querySelectorAll("select[data-option-key]")];
      const expectedOptionKeys = Object.keys(profile.options);
      if (
        optionSelects.length !== expectedOptionKeys.length
        || new Set(optionSelects.map((select) => select.dataset.optionKey)).size !== optionSelects.length
      ) {
        this._error = "生成選項與所選模組不一致；已停止送出，沒有扣點。";
        this.render();
        return;
      }
      for (const select of optionSelects) {
        const key = select.dataset.optionKey;
        const allowed = Array.isArray(profile.options[key]) ? profile.options[key] : [];
        const selected = allowed.find((value) => String(value) === select.value);
        if (selected === undefined) {
          this._error = "生成選項已失效，請重新核對模組；已停止送出，沒有扣點。";
          this.render();
          return;
        }
        options[key] = selected;
      }
      const local = profile.providerId === "local_comfy";
      const creativeInstruction = {
        schema_version: "castle.creative-instruction.v1",
        shot_id: draftShot.shot_id,
        frame_intent: draftShot.frame_intent,
        observable_action: draftShot.observable_action,
        camera: draftShot.camera,
        audio_intent: draftShot.audio_intent,
        language: this._brief.language,
      };
      const productionBinding = this._productionBindingFor(draftShot.shot_id, profile);
      if (productionBinding) creativeInstruction.production_binding = productionBinding;
      const request = {
        schema_version: 1,
        capability: profile.capability,
        visual_class: profile.visualClass,
        characters: characters.map((item) => ({
          character_id: item.character_id,
          visual_class: item.visual_class,
          display_name: item.display_name,
          external_refs: [],
        })),
        assets: characters.map((item) => ({
          asset_id: item.profile_asset_id,
          kind: "image",
          uri: `castle://character-assets/${item.character_id}/avatar`,
          visual_class: item.visual_class,
          character_ids: [item.character_id],
          mime_type: null, byte_size: null, sha256: null, duration_seconds: null,
        })),
        options,
        preferred_providers: [profile.providerId],
        provider_id: profile.providerId,
        max_credits: local ? 0 : null,
        action_id: profile.actionId,
        feature_id: profile.featureId,
        profile_key: profile.key,
        creative_instruction: creativeInstruction,
      };
      if (containsSensitiveField(request)) {
        this._error = "規劃內容包含不允許的敏感欄位，已停止。";
        this.render();
        return;
      }
      this.dispatchEvent(new CustomEvent("castle-project-provider-plan", {
        bubbles: true,
        composed: true,
        detail: Object.freeze(request),
      }));
    }

    _beatCastChoices() {
      return this._characters.map((item) => el("label", {
        className: "project-preview__beat-character", hidden: true,
        attributes: { "data-beat-character-choice": item.id },
      }, [
        el("input", { type: "checkbox", attributes: { "data-beat-character-id": item.id } }),
        item.avatarUrl
          ? el("img", { attributes: { src: item.avatarUrl, alt: "", loading: "lazy" } })
          : el("span", { className: "project-preview__beat-avatar-fallback", text: item.label.slice(0, 1) }),
        el("span", { text: item.label }),
        el("small", { text: visualLabel(item.visualClass) }),
      ]));
    }

    _syncBeatCast(form) {
      const selected = new Set([...form.querySelectorAll('[name="character_id"]:checked')]
        .map((input) => String(input.value || "")));
      for (const row of form.querySelectorAll(".project-preview__beat")) {
        const choices = [...row.querySelectorAll("[data-beat-character-choice]")];
        const excluded = new Set(String(row.dataset.castExcludedIds || "")
          .split(",").map((item) => item.trim()).filter(Boolean));
        for (const choice of choices) {
          const characterId = String(choice.dataset.beatCharacterChoice || "");
          const visible = selected.has(characterId);
          choice.hidden = !visible;
          const input = choice.querySelector("input");
          input.checked = visible && !excluded.has(characterId);
        }
        const visibleInputs = choices.filter((choice) => !choice.hidden).map((choice) => choice.querySelector("input"));
        row.querySelector(".project-preview__beat-cast-empty").hidden = visibleInputs.length > 0;
        const included = visibleInputs.filter((input) => input.checked).length;
        const status = row.querySelector(".project-preview__beat-cast-status");
        status.textContent = included === 0
          ? "尚未選擇這一段的出場角色"
          : included === 1
            ? "實際送出 1 位角色 · 使用單人圖生影片"
            : `實際送出 ${included} 位角色 · 使用多人參考影片`;
        status.classList.toggle("is-empty", included === 0);
      }
    }

    _modeBeatTemplate(mode) {
      const templates = {
        single: [
          { summary: "主題開場與核心行動", action: "角色以一個清楚、可看見的動作完成這支影片的重點", camera: "穩定中景，緩慢推近", audio: "環境聲；需要時加入一句核心對白", duration: 6 },
        ],
        series: [
          { summary: "本集鉤子", action: "角色立刻遇到本集問題或目標", camera: "快速建立場景後切到角色", audio: "一句讓觀眾想繼續看的開場", duration: 4 },
          { summary: "本集推進與收束", action: "角色採取行動並留下下一集可延續的狀態", camera: "中景跟拍，結尾停在明確表情或物件", audio: "完成本集重點；保留系列口吻", duration: 8 },
        ],
        drama: [
          { summary: "鉤子：事情突然發生", action: "用一個立刻看得懂的事件打破原本狀態", camera: "近景開場，快速交代人物反應", audio: "短句或聲響建立懸念", duration: 4 },
          { summary: "衝突：角色必須選擇", action: "角色採取具體行動，讓衝突升高", camera: "正反打或穩定跟拍，保持人物方位連續", audio: "一小段對白配一個動作", duration: 8 },
          { summary: "轉折：留下下一步", action: "揭露新資訊或讓角色面臨新的後果", camera: "推近關鍵表情或道具後停住", audio: "用一句話或聲音留下懸念", duration: 5 },
        ],
      };
      return templates[mode] || templates.single;
    }

    _applyModeTemplate(form, beats, mode, hint) {
      const rows = [...beats.querySelectorAll(".project-preview__beat")];
      const hasWriting = beats.dataset.userEdited === "true" || (
        !beats.dataset.templateMode && rows.some((row) => ["beat_summary", "beat_action", "beat_audio"]
          .some((name) => String(row.querySelector(`[name="${name}"]`)?.value || "").trim()))
      );
      const labels = {
        single: "單支影片：一個清楚重點，先做短而完整。",
        series: "系列內容：建立本集鉤子與可延續狀態，方便下一集接續。",
        drama: "短劇：鉤子 → 衝突 → 轉折；每段仍可增刪，角色狀態會逐鏡檢查。",
      };
      hint.textContent = labels[mode] || labels.single;
      if (hasWriting) return;
      beats.replaceChildren(...this._modeBeatTemplate(mode).map((preset, index) => this._beatRow(index + 1, preset)));
      beats.dataset.templateMode = mode;
      beats.dataset.userEdited = "false";
      this._syncBeatCast(form);
    }

    _effectRow(beatIndex, effectIndex) {
      const effectAssets = this._soundtracks.filter((item) => (
        item.role === "unclassified" || item.role === "sound_effect" || item.role === "ambience"
      ));
      const effectSelect = el("select", {
        attributes: { name: "effect_asset_version_id", "aria-label": `段落 ${beatIndex} 效果音 ${effectIndex}` },
      }, [
        el("option", { text: "請選擇音訊", attributes: { value: "" } }),
        ...effectAssets.map((item) => el("option", {
          text: `${item.title}${item.duration ? ` · ${item.duration.toFixed(1)} 秒` : ""}`,
          attributes: { value: item.versionId },
        })),
      ]);
      const search = el("input", { attributes: {
        type: "search", maxlength: "100", placeholder: "搜尋音訊名稱…",
        "aria-label": `搜尋段落 ${beatIndex} 效果音`,
      } });
      const effectPreview = el("audio", { hidden: true, attributes: { controls: "", preload: "metadata" } });
      const effectControls = el("div", { className: "project-preview__three", hidden: true }, [
        el("label", {}, [el("span", { text: "鏡頭第幾秒出現" }), el("input", { type: "number", attributes: { name: "effect_start", min: "0", max: "60", step: "0.05", value: "0" } })]),
        el("label", {}, [el("span", { text: "從音檔第幾秒開始" }), el("input", { type: "number", attributes: { name: "effect_source_start", min: "0", max: "86400", step: "0.05", value: "0" } })]),
        el("label", {}, [el("span", { text: "播放多久" }), el("input", { type: "number", attributes: { name: "effect_duration", min: "0.05", max: "60", step: "0.05", value: "1" } })]),
        el("label", {}, [el("span", { text: "效果音音量" }), el("select", { attributes: { name: "effect_gain" } }, [
          ["-12", "輕（-12 dB）"], ["-6", "平衡（-6 dB）"], ["0", "原音量"], ["6", "加強（+6 dB）"],
        ].map(([value, text]) => el("option", { text, attributes: { value, selected: value === "-6" ? "" : null } })))]),
      ]);
      effectSelect.addEventListener("change", () => {
        const selected = effectAssets.find((item) => item.versionId === effectSelect.value);
        effectControls.hidden = !selected;
        effectPreview.hidden = !selected;
        if (selected) effectPreview.src = selected.sourceUrl;
        else { effectPreview.pause(); effectPreview.removeAttribute("src"); effectPreview.load(); }
        const sourceStart = effectControls.querySelector('[name="effect_source_start"]');
        const duration = effectControls.querySelector('[name="effect_duration"]');
        if (selected?.duration) {
          sourceStart.max = String(Math.max(0, selected.duration - 0.05));
          duration.max = String(selected.duration);
        } else {
          sourceStart.max = "86400";
          duration.max = "60";
        }
      });
      search.addEventListener("input", () => {
        const query = search.value.trim().toLocaleLowerCase("zh-TW");
        for (const option of [...effectSelect.options].slice(1)) {
          option.hidden = Boolean(query) && !option.textContent.toLocaleLowerCase("zh-TW").includes(query);
        }
      });
      const remove = el("button", { className: "project-preview__button project-preview__button--quiet", type: "button", text: "移除此效果音" });
      const row = el("div", { className: "project-preview__effect" }, [
        el("strong", { text: `效果音 ${effectIndex}` }), search, effectSelect,
        effectPreview, effectControls, remove,
      ]);
      remove.addEventListener("click", () => {
        effectPreview.pause();
        const list = row.parentElement;
        row.remove();
        [...(list?.children || [])].forEach((item, position) => {
          item.querySelector("strong").textContent = `效果音 ${position + 1}`;
        });
      });
      return row;
    }

    _beatRow(index, preset = null) {
      const defaults = preset || {};
      const effectList = el("div", { className: "project-preview__effects" });
      const addEffect = el("button", { className: "project-preview__button project-preview__button--quiet", type: "button", text: "＋ 加一段效果音" });
      addEffect.addEventListener("click", () => {
        if (effectList.children.length >= 16) return;
        effectList.append(this._effectRow(index, effectList.children.length + 1));
      });
      const row = el("article", { className: "project-preview__beat" }, [
        el("div", { className: "project-preview__beat-head" }, [
          el("strong", { text: `段落 ${index}` }),
          el("button", { className: "project-preview__beat-remove", type: "button", text: "移除" }),
        ]),
        el("label", {}, [el("span", { text: "這一段發生什麼？" }), el("input", { attributes: { name: "beat_summary", required: "", maxlength: "1000", value: defaults.summary || "", placeholder: "例如：她在雨中撿到一封信" } })]),
        el("label", {}, [el("span", { text: "角色動作" }), el("textarea", { text: defaults.action || "", attributes: { name: "beat_action", required: "", rows: "2", maxlength: "1600", placeholder: "用看得見的動作描述" } })]),
        el("div", { className: "project-preview__two" }, [
          el("label", {}, [el("span", { text: "鏡頭" }), el("input", { attributes: { name: "beat_camera", required: "", maxlength: "500", value: defaults.camera || "穩定中景，緩慢推近" } })]),
          el("label", {}, [el("span", { text: "秒數" }), el("input", { type: "number", attributes: { name: "beat_duration", required: "", min: "1", max: "60", step: "1", value: String(defaults.duration || 5) } })]),
        ]),
        el("label", {}, [el("span", { text: "聲音／對白" }), el("textarea", { text: defaults.audio || "", attributes: { name: "beat_audio", required: "", rows: "2", maxlength: "1000", placeholder: "例如：雨聲；她說：終於找到了" } })]),
        el("fieldset", { className: "project-preview__beat-effects" }, [
          el("legend", { text: "效果音（選用）" }),
          el("p", { text: "可搜尋、預聽並疊加多段自己的音訊；每段可設定不同進場時間。" }),
          effectList, addEffect,
        ]),
        el("fieldset", { className: "project-preview__beat-cast" }, [
          el("legend", { text: "這一段誰出場？" }),
          el("p", { text: "專案角色預設都會加入這一段；若某位不出場，再點一下將他排除。動漫與真人請拆成不同鏡頭。" }),
          el("div", { className: "project-preview__beat-character-grid" }, this._beatCastChoices()),
          el("small", { className: "project-preview__beat-cast-empty", text: "先在上方選擇專案角色。" }),
          el("strong", { className: "project-preview__beat-cast-status is-empty", text: "尚未選擇這一段的出場角色" }),
        ]),
      ]);
      row.querySelector(".project-preview__beat-character-grid").addEventListener("change", (event) => {
        const input = event.target?.closest?.("[data-beat-character-id]");
        if (!input) return;
        const exclusions = new Set(String(row.dataset.castExcludedIds || "")
          .split(",").map((item) => item.trim()).filter(Boolean));
        const characterId = String(input.dataset.beatCharacterId || "");
        if (input.checked) exclusions.delete(characterId);
        else exclusions.add(characterId);
        row.dataset.castExcludedIds = [...exclusions].sort().join(",");
        this._syncBeatCast(row.closest("form"));
      });
      row.querySelector(".project-preview__beat-remove").addEventListener("click", () => {
        const list = row.parentElement;
        if (list && list.children.length > 1) row.remove();
        [...(list?.children || [])].forEach((item, position) => {
          item.querySelector(".project-preview__beat-head strong").textContent = `段落 ${position + 1}`;
        });
      });
      return row;
    }

    _renderBriefWizard() {
      const form = el("form", { className: "project-preview__wizard" });
      const modeFields = el("div", { className: "project-preview__mode-grid" }, [
        ["single", "單支影片", "一次完成一個主題"],
        ["series", "系列內容", "共用角色與風格聖經"],
        ["drama", "短劇", "依場景與角色狀態延續"],
      ].map(([value, title, sub], index) => el("label", { className: "project-preview__mode" }, [
        el("input", { type: "radio", attributes: { name: "mode", value, required: "", checked: index === 0 ? "" : null } }),
        el("strong", { text: title }), el("small", { text: sub }),
      ])));
      const series = el("label", { className: "project-preview__series", hidden: true }, [
        el("span", { text: "系列代號" }),
        el("input", { attributes: { name: "series_id", maxlength: "128", pattern: "[a-z0-9][a-z0-9._:-]{0,127}", placeholder: "my-series" } }),
      ]);
      const modeHint = el("p", { className: "project-preview__mode-hint", text: "單支影片：一個清楚重點，先做短而完整。" });
      modeFields.addEventListener("change", () => {
        const mode = form.querySelector('[name="mode"]:checked')?.value || "single";
        const single = mode === "single";
        series.hidden = single;
        series.querySelector("input").required = !single;
        this._applyModeTemplate(form, beats, mode, modeHint);
      });
      const characterGrid = el("div", { className: "project-preview__character-grid" }, this._characters.map((item) => el("label", { className: "project-preview__character" }, [
        el("input", { type: "checkbox", attributes: { name: "character_id", value: item.id } }),
        item.avatarUrl ? el("img", { attributes: { src: item.avatarUrl, alt: "", loading: "lazy" } }) : el("span", { className: "project-preview__avatar-fallback", text: item.label.slice(0, 2) }),
        el("span", {}, [el("strong", { text: item.label }), el("small", { text: visualLabel(item.visualClass) })]),
      ])));
      const beats = el("div", { className: "project-preview__beats" }, [this._beatRow(1)]);
      beats.addEventListener("input", () => { beats.dataset.userEdited = "true"; });
      const addBeat = el("button", { className: "project-preview__button project-preview__button--quiet", type: "button", text: "＋ 新增段落" });
      addBeat.addEventListener("click", () => {
        beats.append(this._beatRow(beats.children.length + 1));
        beats.dataset.userEdited = "true";
        this._syncBeatCast(form);
      });
      characterGrid.addEventListener("change", () => this._syncBeatCast(form));
      const platformField = el("select", { attributes: { name: "platform" } }, [["shorts-vertical", "直式短片"], ["web-landscape", "橫式影片"], ["social-square", "方形社群"], ["private-review", "私人預覽"]].map(([value, text]) => el("option", { text, attributes: { value } })));
      const aspectField = el("select", { attributes: { name: "aspect_ratio" } }, [["9:16", "直式 9:16"], ["16:9", "橫式 16:9"], ["1:1", "方形 1:1"]].map(([value, text]) => el("option", { text, attributes: { value } })));
      const platformAspects = { "shorts-vertical": "9:16", "web-landscape": "16:9", "social-square": "1:1" };
      const syncPlatformAspect = () => {
        const fixed = platformAspects[platformField.value];
        if (fixed) aspectField.value = fixed;
        aspectField.setAttribute("aria-disabled", fixed ? "true" : "false");
        aspectField.setAttribute("aria-describedby", "project-preview-aspect-help");
      };
      platformField.addEventListener("change", syncPlatformAspect);
      aspectField.addEventListener("change", syncPlatformAspect);
      syncPlatformAspect();
      const musicAssets = this._soundtracks.filter((item) => (
        item.role === "unclassified" || item.role === "music"
      ));
      const soundtrackField = el("select", {
        attributes: { name: "soundtrack_asset_version_id", "aria-label": "背景音樂" },
      }, [
        el("option", { text: "不使用背景音樂", attributes: { value: "" } }),
        ...musicAssets.map((item) => el("option", {
          text: `${item.title}${item.duration ? ` · ${item.duration.toFixed(1)} 秒` : ""}`,
          attributes: { value: item.versionId },
        })),
      ]);
      const soundtrackGain = el("select", {
        attributes: { name: "soundtrack_gain_db", disabled: "", "aria-label": "背景音樂音量" },
      }, [
        ["-18", "很輕（-18 dB）"], ["-14", "平衡（-14 dB）"],
        ["-10", "明顯（-10 dB）"], ["-6", "突出（-6 dB）"],
      ].map(([value, text]) => el("option", {
        text, attributes: { value, selected: value === "-14" ? "" : null },
      })));
      soundtrackField.addEventListener("change", () => {
        soundtrackGain.disabled = !soundtrackField.value;
      });
      form.append(
        el("h2", { text: "用簡單方式開始" }),
        el("p", { className: "project-preview__wizard-intro", text: "填好構想、角色與段落後，按一下準備試做；Castle 會自動整理分鏡與可用方式。" }),
        modeFields,
        modeHint,
        el("div", { className: "project-preview__two" }, [
          el("label", {}, [el("span", { text: "專案名稱" }), el("input", { attributes: { name: "title", required: "", maxlength: "200", placeholder: "例如：雨夜來信" } })]),
          series,
        ]),
        el("label", {}, [el("span", { text: "一句話構想" }), el("textarea", { attributes: { name: "premise", required: "", rows: "2", maxlength: "2000", placeholder: "誰，在什麼情況下，想完成什麼？" } })]),
        el("div", { className: "project-preview__three" }, [
          el("label", {}, [el("span", { text: "觀眾" }), el("input", { attributes: { name: "audience", required: "", maxlength: "500", value: "一般成年觀眾" } })]),
          el("label", {}, [el("span", { text: "用途" }), platformField]),
          el("label", {}, [el("span", { text: "比例" }), aspectField, el("small", { id: "project-preview-aspect-help", text: "公開平台會自動使用正確比例；私人預覽可自由選擇。" })]),
        ]),
        el("input", { type: "hidden", attributes: { name: "language", value: "zh-TW" } }),
        el("h3", { text: "背景音樂（選用）" }),
        el("div", { className: "project-preview__two" }, [
          el("label", {}, [
            el("span", { text: "從統一媒體庫選擇" }), soundtrackField,
            el("small", { text: this._soundtracksBusy ? "正在載入音樂庫…"
              : this._soundtracks.length ? "只顯示已有本機原檔、可鎖定版本的音訊。"
                : "媒體庫目前沒有可用音訊；仍可先製作無背景音樂版本。" }),
          ]),
          el("label", {}, [el("span", { text: "音量" }), soundtrackGain,
            el("small", { text: "會保留角色說話與環境聲，再統一做社群音量標準化。" })]),
        ]),
        el("h3", { text: "選角色" }),
        characterGrid.children.length ? characterGrid : el("div", { className: "project-preview__empty", text: "目前沒有已分類且有 profile 圖的角色。" }),
        el("div", { className: "project-preview__beat-title" }, [el("h3", { text: "故事段落" }), addBeat]),
        beats,
        el("button", {
          className: "project-preview__primary", type: "submit",
          text: this._briefBusy || this._routeBusy ? "正在準備試做…" : "準備試做",
          attributes: { disabled: this._briefBusy || this._routeBusy ? "" : null },
        }),
      );
      this._applyModeTemplate(form, beats, "single", modeHint);
      this._syncBeatCast(form);
      form.addEventListener("submit", (event) => { event.preventDefault(); this._compileBrief(form); });
      return el("section", { className: "project-preview__card project-preview__wizard-card" }, [form]);
    }

    _renderJourney() {
      const current = this._routeReview ? 2 : (this._draft || this._routeBusy ? 1 : 0);
      const steps = [
        ["填構想與角色", "按一次準備試做"],
        ["Castle 自動準備", "整理故事板與可用方式"],
        ["立即試做", "選一個鏡頭直接送出生成"],
      ];
      return el("ol", { className: "project-preview__journey", attributes: { "aria-label": "影片製作進度" } },
        steps.map(([title, detail], index) => el("li", {
          className: `${index < current ? "is-done" : ""}${index === current ? " is-current" : ""}`.trim(),
          attributes: { "aria-current": index === current ? "step" : null },
        }, [
          el("span", { text: index < current ? "✓" : String(index + 1) }),
          el("div", {}, [el("strong", { text: title }), el("small", { text: detail })]),
        ])),
      );
    }

    _renderDraft() {
      if (!this._draft || !Array.isArray(this._draft.scenes)) return null;
      return el("section", { className: "project-preview__card project-preview__draft" }, [
        el("div", { className: "project-preview__section-head" }, [
          el("div", {}, [el("h2", { text: "故事板草稿" }), el("p", { text: `${safeText(this._draft.title, "未命名", 200)} · 約 ${Number(this._draft.target_duration_seconds) || 0} 秒` })]),
          el("span", { className: "project-preview__status is-blocked", text: "尚不可執行" }),
        ]),
        el("div", { className: "project-preview__storyboard" }, this._draft.scenes.slice(0, 120).map((scene, index) => {
          const shot = scene && scene.shot || {};
          const blockers = Array.isArray(shot.blockers) ? shot.blockers : [];
          return el("article", { className: "project-preview__story-card" }, [
            el("span", { text: String(index + 1).padStart(2, "0") }),
            el("div", {}, [el("strong", { text: safeText(scene.summary, "未命名段落", 1000) }), el("p", { text: safeText(shot.observable_action, "", 1600) }), el("small", { text: `${Number(shot.duration_seconds) || 0} 秒 · ${shot.visual_class ? visualLabel(shot.visual_class) : "需拆分角色類型"}` })]),
            blockers.length ? el("em", { text: "需要拆成同類型鏡頭" }) : null,
          ]);
        })),
        el("div", { className: "project-preview__next-gates" }, [
          el("strong", { text: "下一步仍需 Castle 完成" }),
          el("span", { text: "權利／同意" }), el("span", { text: "素材版本" }),
          el("span", { text: "生成路線與技術狀態" }), el("span", { text: "正式報價" }),
        ]),
        this._routeBusy ? el("p", {
          className: "project-preview__route-progress",
          text: "正在準備試做…",
          attributes: { role: "status", "aria-live": "polite" },
        }) : (!this._routeReview ? el("button", {
          className: "project-preview__primary project-preview__route-review-button",
          type: "button",
          text: this._error ? "重試準備試做" : "準備試做",
        }) : null),
      ]);
    }

    _renderDirectorPlan() {
      if (!this._directorPlan) return null;
      const roleCopy = {
        hook: "開場鉤子", development: "推進", payoff: "收束回報",
        recap_hook: "回顧＋鉤子", next_episode_hook: "下集鉤子",
        setup: "建立情境", escalation: "升高衝突", reversal: "轉折",
        cliffhanger: "懸念結尾", hook_and_payoff: "短篇完整弧線",
        episode_micro_arc: "單鏡集數弧線",
      };
      return el("section", { className: "project-preview__card project-preview__director-plan" }, [
        el("div", { className: "project-preview__section-head" }, [
          el("div", {}, [el("h2", { text: "自動導演節奏" }), el("p", {
            text: "依作品模式安排鉤子、推進、轉折、收束與連續性；這仍只是可修改的計畫。",
          })]),
          el("span", { className: "project-preview__status is-blocked", text: "未執行" }),
        ]),
        el("div", { className: "project-preview__storyboard" }, this._directorPlan.shots.map((shot, index) => el("article", { className: "project-preview__story-card" }, [
          el("span", { text: String(index + 1).padStart(2, "0") }),
          el("div", {}, [
            el("strong", { text: roleCopy[shot.narrative_role] || shot.narrative_role }),
            el("p", { text: `${shot.transition_in} · ${shot.pacing} · ${shot.audio_bridge}` }),
            el("small", { text: `${Array.isArray(shot.continuity_locks) ? shot.continuity_locks.length : 0} 個角色連續性鎖` }),
          ]),
        ]))),
      ]);
    }

    _renderRouteReview() {
      if (!this._routeReview) return null;
      const reviewedShots = this._routeReview.shots.map((shot) => {
        const routes = shot.candidates.map((profile) => ({
          profile,
          context: this._routePlanContext(shot, profile),
        }));
        return Object.freeze({
          shot,
          snapshot: this._shotBriefSnapshot(shot.shotId),
          routes: Object.freeze(routes),
          readyChoiceCount: routes.filter(({ profile, context }) => (
            profile.selectable && !context.error
          )).length,
        });
      });
      const readyShotCount = reviewedShots.filter((item) => item.readyChoiceCount > 0).length;
      const section = el("section", {
        className: "project-preview__card project-preview__route-review",
        attributes: { tabindex: "-1" },
      }, [
        el("div", { className: "project-preview__section-head" }, [
          el("div", {}, [el("h2", { text: "下一步：選擇鏡頭的試做方式" }), el("p", {
            text: "每個可用方式都完整列出；有多個時由你選擇，只有一個時直接使用。按下試做才會送出。",
          })]),
          el("span", {
            className: `project-preview__status ${readyShotCount ? "is-pass" : "is-blocked"}`,
            text: readyShotCount ? `${readyShotCount} 個鏡頭可生成` : "等待連線就緒",
          }),
        ]),
      ]);
      for (const reviewed of reviewedShots) {
        const { shot, snapshot, routes, readyChoiceCount } = reviewed;
        const draftShot = snapshot.draftShot;
        const shotCharacters = snapshot.characters.map((character) => {
          const live = this._characters.find((item) => item.id === character.character_id);
          return Object.freeze({
            id: character.character_id,
            label: character.display_name,
            avatarUrl: safeText(live?.avatarUrl, "", 4096),
          });
        });
        const cards = routes.map(({ profile, context }) => {
          const ready = profile.selectable && !context.error;
          const cost = routeCostCopy(profile.providerId);
          const readiness = ready
            ? "可直接試做"
            : profile.selectable
              ? context.error
              : [...new Set(profile.reasons.map((item) => (
                item === "person_rights_scope_missing"
                  ? "缺少符合此用途的真人同意紀錄"
                  : routeReadinessReason(profile.providerId, item)
              )))].join("、") || "這條路線目前尚未準備好；沒有扣點。";
          const card = el("article", {
            className: `project-preview__route-choice ${ready ? "is-ready" : "is-unavailable"}`,
            attributes: {
              "data-route-provider": profile.providerId,
              "data-route-profile": profile.key,
            },
          }, [
            el("div", { className: "project-preview__route-choice-head" }, [
              el("div", {}, [
                el("strong", { text: providerLabel(profile.providerId) }),
                el("small", { text: `模組：${profile.variantId}` }),
              ]),
              el("span", { className: "project-preview__route-cost", text: cost.label }),
            ]),
            el("small", {
              className: "project-preview__route-fit",
              text: `符合鏡頭：${visualLabel(profile.visualClass)} · ${CAPABILITY_COPY[profile.capability] || profile.capability}`,
            }),
            el("p", { className: "project-preview__route-reason", text: cost.detail }),
            el("span", { className: "project-preview__route-state", text: readiness }),
          ]);
          if (ready) {
            card.append(el("div", { className: "project-preview__route-option-fields" }, Object.entries(profile.options).map(([key, rawValues]) => {
              const values = Array.isArray(rawValues) ? rawValues : [];
              const preferred = preferredRouteOption(key, values, draftShot, this._brief);
              return el("label", {}, [
                el("span", { text: routeOptionLabel(key) }),
                el("select", { attributes: { "data-option-key": key } }, values.map((value) => el("option", {
                  text: routeOptionValueLabel(key, value),
                  attributes: {
                    value: String(value),
                    selected: String(value) === String(preferred) ? "" : null,
                  },
                }))),
              ]);
            })));
            const authoredSummary = [
              draftShot?.duration_seconds ? `${draftShot.duration_seconds} 秒` : "",
              this._brief?.aspect_ratio ? routeOptionValueLabel("aspect_ratio", this._brief.aspect_ratio) : "",
            ].filter(Boolean).join("、");
            if (authoredSummary) card.append(el("small", {
              text: `已沿用專案設定：${authoredSummary}。完成後會先出現在媒體庫。`,
            }));
            const generate = el("button", {
              className: "project-preview__primary project-preview__trial-button", type: "button",
              text: routeChoiceButtonCopy(profile, readyChoiceCount),
              attributes: {
                "data-route-provider": profile.providerId,
                "data-route-profile": profile.key,
              },
            });
            generate.addEventListener("click", () => this._openRoutePlan(shot, profile, card));
            card.append(generate);
          } else {
            const autoipNeedsConnection = profile.providerId === "autoip"
              && profile.reasons.includes("technical_compatibility_missing");
            if (autoipNeedsConnection && this._canConfigureProviders) {
            const stored = this._autoipProvider?.configured === true
              && Boolean(this._autoipProvider.accountId);
            if (stored) {
              const retry = el("button", {
                className: "project-preview__primary", type: "button",
                text: this._autoipSetupBusy ? "正在免費檢查…" : "重新檢查 AutoIP（不扣點）",
                attributes: { disabled: this._autoipSetupBusy ? "" : null },
              });
              retry.addEventListener("click", () => this._refreshStoredAutoIP(
                this._autoipProvider.accountId,
              ));
              card.append(el("div", { className: "project-preview__autoip-setup" }, [
                el("p", { text: "已找到保存好的 AutoIP Key，不需要重新輸入。" }),
                retry,
                this._autoipSetupMessage
                  ? el("p", { className: "project-preview__preparation-error", text: this._autoipSetupMessage })
                  : null,
              ]));
            } else {
              const account = el("input", { attributes: {
                type: "text", inputmode: "latin", autocomplete: "off", required: "",
                maxlength: "128", value: this._autoipSetupAccountId,
                "aria-label": "AutoIP 帳號識別碼",
              } });
              const credential = el("input", { attributes: {
                type: "password", autocomplete: "new-password", spellcheck: "false",
                minlength: "12", maxlength: "4096", required: "",
                placeholder: "貼上 AutoIP API Key", "aria-label": "AutoIP API Key",
              } });
              const submit = el("button", {
                className: "project-preview__primary", type: "submit",
                text: this._autoipSetupBusy ? "正在連接…" : "連接 AutoIP",
                attributes: { disabled: this._autoipSetupBusy ? "" : null },
              });
              const form = el("form", { className: "project-preview__autoip-setup" }, [
                el("label", {}, [el("span", { text: "帳號" }), account]),
                el("label", {}, [el("span", { text: "API Key（只寫、不讀回）" }), credential]),
                submit,
                this._autoipSetupMessage
                  ? el("p", { className: "project-preview__preparation-error", text: this._autoipSetupMessage })
                  : null,
              ]);
              form.addEventListener("submit", (event) => this._connectAutoIP(
                event, account, credential,
              ));
              card.append(form);
            }
          }
          }
          return card;
        });
        section.append(el("div", { className: "project-preview__shot-routes" }, [
          el("h3", { text: `${shot.shotId} · ${visualLabel(shot.visualClass)}` }),
          el("strong", { text: safeText(draftShot?.observable_action, "這個鏡頭", 300) }),
          el("div", { className: "project-preview__route-cast" }, [
            el("strong", { text: `實際送出角色（${shotCharacters.length}）` }),
            ...shotCharacters.map((character) => el("span", {}, [
              character.avatarUrl
                ? el("img", { attributes: { src: character.avatarUrl, alt: "", loading: "lazy" } })
                : el("i", { text: character.label.slice(0, 1) }),
              el("b", { text: character.label }),
            ])),
          ]),
          el("small", {
            className: "project-preview__route-capability",
            text: shotCharacters.length > 1
              ? `此鏡頭會把 ${shotCharacters.length} 張角色參考圖送進多人模組。`
              : "此鏡頭會把這位角色的固定大頭貼送進單人圖生影片模組。",
          }),
          snapshot.error ? el("p", {
            className: "project-preview__preparation-error", text: snapshot.error,
          }) : null,
          cards.length
            ? el("div", { className: "project-preview__route-choice-grid" }, cards)
            : el("div", { className: "project-preview__empty", text: shot.blockers.length
              ? "此鏡頭混合不同角色類型，必須先拆鏡；沒有扣點。"
              : "目前沒有可用生成路線；沒有扣點。" }),
        ]));
      }
      return section;
    }

    _renderPackProposal() {
      if (!this._packProposal) return null;
      const blockerCopy = {
        person_rights_missing: "真人角色缺少可驗證的同意範圍",
        qualified_route_missing: "鏡頭尚無同類型且技術條件完整的生成模組",
        mixed_visual_classes_require_separate_shots: "同鏡混合真人與動漫角色，必須先拆鏡",
      };
      const section = el("section", { className: "project-preview__card project-preview__pack-proposal" }, [
        el("div", { className: "project-preview__section-head" }, [
          el("div", {}, [el("h2", { text: "Production Pack 編譯準備" }), el("p", {
            text: `${this._packProposal.entityCount} 個角色 · ${this._packProposal.shotCount} 個鏡頭 · ${this._packProposal.routeCount} 條合格路線`,
          })]),
          el("span", { className: `project-preview__status ${this._packProposal.ready ? "is-pass" : "is-blocked"}`, text: this._packProposal.ready ? "可編譯" : "仍有缺項" }),
        ]),
        this._packProposal.blockers.length
          ? el("div", { className: "project-preview__next-gates" }, this._packProposal.blockers.map((item) => el("span", {
            text: blockerCopy[item.code] || item.code,
          })))
          : el("p", { text: "角色、導演節奏與生成模組已足以進入正式 Pack 編譯；仍未生成或扣點。" }),
      ]);
      if (!this._packProposal.ready) return section;
      if (this._packProposal.mode !== "single" && !this._seriesBible) {
        section.append(el("p", { className: "project-preview__empty", text: "請先在下方建立或載入系列 Bible，Castle 才能鎖定這一集的角色狀態。" }));
        return section;
      }
      const form = el("form", { className: "project-preview__series-form project-preview__formal-pack-form" });
      form.append(el("h3", { text: "確認本次生成的連續性" }), el("p", {
        text: "這些值會鎖進每個鏡頭；請在正式編譯前確認身份、服裝與聲音。",
      }));
      const locks = [...new Set((this._directorPlan?.shots || []).flatMap((shot) => (
        Array.isArray(shot?.continuity_locks) ? shot.continuity_locks : []
      )))].sort();
      const characters = new Map((this._brief?.characters || []).map((item) => [item.character_id, item]));
      for (const key of locks) {
        const split = key.lastIndexOf(".");
        const characterId = split > 0 ? key.slice(0, split) : "";
        const field = split > 0 ? key.slice(split + 1) : "";
        const character = characters.get(characterId);
        const defaults = {
          identity: character?.profile_asset_id || "請確認身份基準",
          wardrobe: "請確認本鏡服裝",
          voice: "請確認本鏡聲音",
        };
        const value = this._seriesBible?.canon?.[key] || defaults[field] || "請確認狀態";
        form.append(el("label", {}, [
          el("span", { text: `${character?.display_name || characterId} · ${field}` }),
          el("input", { attributes: {
            value, required: "", maxlength: "500", "data-formal-continuity-key": key,
          } }),
        ]));
      }
      const submit = el("button", {
        className: "project-preview__primary", type: "submit",
        text: this._formalBusy ? "編譯與檢查中…" : "編譯正式 Production Pack（不生成）",
        attributes: { disabled: this._formalBusy || !locks.length ? "" : null },
      });
      form.append(submit, el("small", { text: "完成後會自動做 strict dry-run；正式製作時只需逐鏡按下生成。" }));
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        this._compileFormalPack(form);
      });
      section.append(form);
      return section;
    }

    async _compileFormalPack(form) {
      if (this._formalBusy || !this._brief || !this._packProposal?.ready) return;
      const continuityState = {};
      for (const input of form.querySelectorAll("[data-formal-continuity-key]")) {
        const key = safeText(input.dataset.formalContinuityKey, "", 260);
        const value = safeText(input.value, "", 500);
        if (!key || !value) {
          this._error = "請完整確認每個角色的身份、服裝與聲音狀態。";
          this.render();
          return;
        }
        continuityState[key] = value;
      }
      this._formalBusy = true;
      this._error = "";
      this._message = "正在由伺服器重新核對權利、技術狀態、帳號與連續性；不會執行。";
      this.render();
      try {
        const response = await fetch(FORMAL_PACK_ENDPOINT, {
          method: "POST", credentials: "same-origin", cache: "no-store",
          headers: { Accept: "application/json", "Content-Type": "application/json" },
          body: JSON.stringify({ production_brief: this._brief, continuity_state: continuityState }),
        });
        const raw = await response.json().catch(() => null);
        if (!response.ok) {
          if (response.status === 401) window.dispatchEvent(new CustomEvent("castle-auth-required"));
          throw new Error(response.status === 422
            ? "正式 Pack 仍缺目前有效的權利、技術狀態、路線或連續性。"
            : "正式 Pack 編譯暫時無法完成。");
        }
        const compilation = raw && raw.production_pack;
        if (
          raw?.schema_version !== "castle.formal-pack.response.v1" || raw.ok !== true
          || raw.execution_authorized !== false || !safeHash(raw.pack_hash)
          || compilation?.version !== "castle.production-pack.v1"
          || raw.authority?.computed_by !== "castle_server"
          || raw.authority?.evidence_resolved_server_side !== true
          || raw.authority?.execution_authorized !== false
          || raw.authority?.provider_submission_authorized !== false
          || raw.authority?.gpu_execution_authorized !== false
          || raw.authority?.credits_authorized !== 0
          || raw.authority?.publishing_authorized !== false
        ) throw new Error("Castle 回傳的正式 Pack 安全聲明不完整，已拒絕採用。");
        this._rawText = JSON.stringify(compilation);
        this._fileName = "server-formal-production-pack.json";
        this._productionPack = null;
        this._production = null;
        this._result = null;
        this._message = "正式 Pack 已編譯；正在進行 strict dry-run。";
        this._formalBusy = false;
        this.render();
        await this._preview();
        return;
      } catch (error) {
        this._error = safeText(error?.message, "正式 Pack 編譯失敗。", 300);
        this._message = "";
      } finally {
        this._formalBusy = false;
        this.render();
      }
    }

    _renderSeriesContinuity() {
      if (!this._packProposal?.seriesId || this._packProposal.mode === "single") return null;
      const section = el("section", { className: "project-preview__card project-preview__series" }, [
        el("div", { className: "project-preview__section-head" }, [
          el("div", {}, [el("h2", { text: "系列連續性" }), el("p", {
            text: `${this._packProposal.seriesId} · 身份、服裝、聲音與關係會從上一集接到下一集`,
          })]),
          el("span", { className: `project-preview__status ${this._seriesBible ? "is-pass" : "is-blocked"}`, text: this._seriesBible ? `revision ${this._seriesBible.revision}` : "尚未載入" }),
        ]),
      ]);
      if (!this._seriesChecked) {
        const load = el("button", { className: "project-preview__primary", type: "button", text: this._seriesBusy ? "載入中…" : "載入系列 Bible" });
        load.disabled = this._seriesBusy;
        load.addEventListener("click", () => this._loadSeriesBible());
        section.append(el("p", { text: "先讀取伺服器上的最新 canon；這一步不生成、不排 GPU。" }), load);
        return section;
      }
      if (!this._seriesBible) {
        const form = el("form", { className: "project-preview__series-form" });
        form.append(el("p", { text: "第一次建立時，請確認每個角色的初始狀態。之後每集只能從上一集結束狀態接續。" }));
        for (const character of this._draft?.characters || []) {
          for (const [field, label, value] of (
            [["identity", "身份基準", character.profile_asset_id], ["wardrobe", "服裝", "待設定"], ["voice", "聲音", "待設定"]]
          )) {
            const key = `${character.character_id}.${field}`;
            form.append(el("label", {}, [el("span", { text: `${character.display_name} · ${label}` }), el("input", {
              attributes: { value, maxlength: "500", required: "", "data-series-canon-key": key },
            })]));
          }
        }
        const create = el("button", { className: "project-preview__primary", type: "submit", text: this._seriesBusy ? "保存中…" : "建立系列 Bible（不生成）" });
        create.disabled = this._seriesBusy;
        form.append(create);
        form.addEventListener("submit", (event) => { event.preventDefault(); this._createSeriesBible(form); });
        section.append(form);
        return section;
      }
      const form = el("form", { className: "project-preview__series-form" });
      form.append(el("label", {}, [el("span", { text: `第 ${this._seriesBible.revision} 集發生什麼事` }), el("textarea", {
        attributes: { name: "event_summary", rows: "3", maxlength: "3000", required: "", placeholder: "描述本集造成的角色、服裝、關係或場景改變" },
      })]));
      for (const [key, value] of Object.entries(this._seriesBible.canon)) {
        form.append(el("label", {}, [el("span", { text: key }), el("input", {
          attributes: { value, maxlength: "500", required: "", "data-series-canon-key": key },
        })]));
      }
      const save = el("button", { className: "project-preview__primary", type: "submit", text: this._seriesBusy ? "保存中…" : `保存第 ${this._seriesBible.revision} 集連續性（不生成）` });
      save.disabled = this._seriesBusy;
      form.append(save);
      form.addEventListener("submit", (event) => { event.preventDefault(); this._appendSeriesEpisode(form); });
      section.append(form);
      return section;
    }

    _renderInput() {
      const textarea = el("textarea", {
        id: "project-preview-json",
        attributes: {
          rows: "13",
          spellcheck: "false",
          autocomplete: "off",
          placeholder: "貼上 castle.production-pack.v1，或完整的 castle.project-dry-run.request.v1 JSON",
          "aria-label": "Production pack JSON",
          "aria-describedby": "project-preview-input-help",
        },
      });
      textarea.value = this._rawText;
      textarea.addEventListener("input", () => {
        this._rawText = textarea.value;
        this._fileName = "";
        this._invalidateOutcome();
      });
      this._textarea = textarea;

      const fileInput = el("input", {
        id: "project-preview-file",
        type: "file",
        attributes: { accept: ".json,application/json" },
      });
      fileInput.addEventListener("change", () => this._selectFile(fileInput.files && fileInput.files[0]));
      const submit = el("button", {
        className: "project-preview__primary",
        type: "button",
        text: this._readingFile ? "讀檔中…" : (this._busy ? "檢查中…" : "檢查規劃（不執行）"),
        attributes: { disabled: this._readingFile || this._busy || !this._rawText.trim() ? "" : null },
      });
      submit.addEventListener("click", () => this._preview());
      this._submitButton = submit;

      const clear = el("button", {
        className: "project-preview__button project-preview__button--quiet",
        type: "button",
        text: "清除",
      });
      clear.addEventListener("click", () => {
        this._revision += 1;
        if (this._request) this._request.abort();
        this._request = null;
        this._busy = false;
        this._readingFile = false;
        this._rawText = "";
        this._fileName = "";
        this._result = null;
        this._message = "";
        this._error = "";
        this.render();
      });

      return el("section", {
        className: "project-preview__card project-preview__input-card",
        attributes: { "aria-labelledby": "project-preview-input-title" },
      }, [
        el("div", { className: "project-preview__section-head" }, [
          el("div", {}, [
            el("h2", { id: "project-preview-input-title", text: "1. 放入專案規格" }),
            el("p", {
              id: "project-preview-input-help",
              text: "可直接放 production pack；介面只替你包上 dry-run 外層，原始內容仍由伺服器嚴格檢查。",
            }),
          ]),
          el("span", { className: "project-preview__limit", text: `上限 ${formatBytes(MAX_REQUEST_BYTES)}` }),
        ]),
        el("label", { className: "project-preview__file-button", text: "選擇 JSON 檔案" }, [fileInput]),
        this._fileName ? el("p", { className: "project-preview__file-name", text: this._fileName }) : null,
        el("details", { className: "project-preview__paste", attributes: { open: this._rawText ? "" : null } }, [
          el("summary", { text: this._rawText ? "查看或修改 JSON" : "也可以直接貼上 JSON" }),
          textarea,
        ]),
        el("div", { className: "project-preview__actions" }, [submit, clear]),
      ]);
    }

    _renderFinding(item, warning = false) {
      return el("li", { className: `project-preview__finding${warning ? " is-warning" : ""}` }, [
        el("strong", { text: FINDING_COPY[item.code] || `需要處理：${item.code}` }),
        el("code", { text: item.path }),
      ]);
    }

    _renderRoutes(result) {
      const section = el("section", {
        className: "project-preview__card",
        attributes: { "aria-labelledby": "project-preview-routes-title" },
      }, [
        el("div", { className: "project-preview__section-head" }, [
          el("div", {}, [
            el("h2", { id: "project-preview-routes-title", text: "候選生成資源" }),
            el("p", { text: "這些只來自 pack 宣告；尚未驗證安裝、授權、連線、執行環境或報價。" }),
          ]),
        ]),
      ]);
      const grid = el("div", { className: "project-preview__route-grid" });
      for (const route of result.routes) {
        const usage = route.usedBy.length
          ? `${route.usedBy.length} 個鏡頭使用`
          : "目前沒有鏡頭使用";
        grid.append(el("article", { className: "project-preview__route" }, [
          el("div", { className: "project-preview__route-head" }, [
            el("strong", { text: providerLabel(route.providerId) }),
            el("span", {
              className: `project-preview__declared${route.releaseReadyDeclared ? "" : " is-blocked"}`,
              text: route.releaseReadyDeclared ? "Pack 宣告已釋出" : "Pack 宣告未釋出",
            }),
          ]),
          el("p", { text: CAPABILITY_COPY[route.capability] || route.capability }),
          el("div", { className: "project-preview__chips" }, route.visualClasses.map((value) => (
            el("span", { text: visualLabel(value) })
          ))),
          el("small", { text: `${route.domain === "local" ? "本機候選" : "外部候選"} · ${usage} · runtime 未驗證 · quote 未驗證` }),
        ]));
      }
      if (!result.routes.length) {
        grid.append(el("div", { className: "project-preview__empty", text: "Production pack 沒有候選生成資源。" }));
      }
      section.append(grid);
      return section;
    }

    _renderDag(result) {
      const taskCounts = new Map();
      if (result.dag) {
        for (const item of result.dag.nodes) {
          taskCounts.set(item.taskType, (taskCounts.get(item.taskType) || 0) + 1);
        }
      }
      const content = [];
      if (result.dag) {
        content.push(
          el("p", {
            className: "project-preview__dag-summary",
            text: `共 ${result.dag.nodeCount} 個靜態步驟；真正執行仍需另一份明確授權。`,
          }),
          el("div", { className: "project-preview__task-list" }, [...taskCounts.entries()].map(([type, count]) => (
            el("div", { className: "project-preview__task" }, [
              el("span", { text: TASK_COPY[type] || type }),
              el("strong", { text: count }),
            ])
          ))),
        );
      } else {
        content.push(el("div", {
          className: "project-preview__empty",
          text: "因為有阻擋項目，Castle 沒有編出 DAG，也沒有建立任何任務。",
        }));
      }
      return el("section", {
        className: "project-preview__card",
        attributes: { "aria-labelledby": "project-preview-dag-title" },
      }, [
        el("div", { className: "project-preview__section-head" }, [
          el("div", {}, [
            el("h2", { id: "project-preview-dag-title", text: "預計流程" }),
            el("p", { text: "只顯示依賴結構，不含 prompt、私人證據、帳號或資源鎖。" }),
          ]),
        ]),
        ...content,
      ]);
    }

    _renderOutcome(result) {
      const summary = result.summary;
      const findings = el("section", {
        className: "project-preview__card",
        attributes: { "aria-labelledby": "project-preview-findings-title" },
      }, [
        el("div", { className: "project-preview__section-head" }, [
          el("div", {}, [
            el("h2", {
              id: "project-preview-findings-title",
              text: result.staticPlanReady ? "靜態規則通過" : `需要處理 ${result.failCount} 項`,
            }),
            el("p", {
              text: result.staticPlanReady
                ? "這只代表資料結構與 pre-spend 規則通過，不代表可以執行。"
                : "先修正這些項目；Castle 沒有排入生成。",
            }),
          ]),
          el("span", {
            className: `project-preview__status ${result.staticPlanReady ? "is-pass" : "is-blocked"}`,
            text: result.staticPlanReady ? "靜態通過" : "已阻擋",
            }),
          ]),
        ]);
      if (result.fails.length) {
        findings.append(el("ul", { className: "project-preview__findings" }, result.fails.map((item) => (
          this._renderFinding(item)
        ))));
      }
      if (result.warns.length) {
        findings.append(
          el("h3", { text: `提醒 ${result.warnCount} 項` }),
          el("ul", { className: "project-preview__findings" }, result.warns.map((item) => (
            this._renderFinding(item, true)
          ))),
        );
      }
      if (!result.failCount && !result.warnCount) {
        findings.append(el("div", { className: "project-preview__empty is-pass", text: "沒有靜態阻擋或提醒。" }));
      }
      if (result.findingsTruncated) {
        findings.append(el("p", { className: "project-preview__truncated", text: "項目很多；畫面只顯示前幾項，總數仍保留。" }));
      }

      const productionAction = result.staticPlanReady && this._productionPack
        ? el("section", { className: "project-preview__card project-preview__production-start" }, [
          el("div", {}, [
            el("h2", { text: this._production ? "製作紀錄已建立" : "建立可恢復的製作" }),
            el("p", { text: this._production
              ? `目前階段：${this._production.phase} · revision ${this._production.revision}`
              : "建立後可以跨裝置恢復與逐鏡審核；這一步不會生成、不會排 GPU、也不會扣點。" }),
          ]),
          this._production ? el("span", { className: "project-preview__status is-pass", text: "已保存" })
            : el("button", {
              className: "project-preview__primary project-preview__create-button", type: "button",
              text: this._productionBusy ? "建立中…" : "建立製作紀錄（不生成）",
              attributes: { disabled: this._productionBusy ? "" : null },
            }),
          this._production ? this._renderProductionProgress() : null,
          this._production ? this._renderProductionGate() : null,
          this._production ? this._renderProductionShots() : null,
          this._production ? this._renderProductionPostproduction() : null,
        ]) : null;
      productionAction?.querySelector(".project-preview__create-button")
        ?.addEventListener("click", () => this._createProduction());
      return el("div", { className: "project-preview__outcome" }, [
        el("section", { className: "project-preview__result-hero" }, [
          el("div", {}, [
            el("p", { className: "project-preview__eyebrow", text: "Dry-run 結果" }),
            el("h2", { text: result.projectId }),
            el("p", {
              text: result.staticPlanReady
                ? "可以進入後續 runtime／授權／報價檢查；現在仍不能執行。"
                : "Production pack 已被安全擋下；現在不能執行。",
            }),
          ]),
          el("code", { text: `pack ${result.packHash.slice(0, 12)}…` }),
        ]),
        el("div", { className: "project-preview__stats" }, [
          ["角色／主體", summary.entity_count],
          ["場景", summary.scene_count],
          ["鏡頭", summary.shot_count],
          ["候選路線", summary.route_variant_count],
          ["昂貴步驟", summary.expected_expensive_node_count],
          ["最低宣告點數", summary.minimum_declared_estimated_credits === null ? "未知" : summary.minimum_declared_estimated_credits],
        ].map(([label, value]) => el("div", {}, [
          el("strong", { text: value }),
          el("span", { text: label }),
        ]))),
        findings,
        this._renderRoutes(result),
        this._renderDag(result),
        productionAction,
        el("section", {
          className: "project-preview__proof",
          attributes: { "aria-label": "零副作用證明" },
        }, [
          el("strong", { text: "本次確定沒有執行" }),
          el("span", { text: "0 專案寫入" }),
          el("span", { text: "0 任務排程" }),
          el("span", { text: "0 ComfyUI prompt" }),
          el("span", { text: "0 供應商呼叫" }),
          el("span", { text: "0 點數授權／花費" }),
        ]),
      ]);
    }

    render() {
      if (!this.isConnected && !this.parentNode) return;
      const root = el("div", { className: "project-preview__inner" }, [
        el("header", { className: "project-preview__hero" }, [
          el("div", {}, [
            el("p", { className: "project-preview__eyebrow", text: "Castle 自動影片工作室" }),
            el("h1", {
              id: "project-preview-heading",
              text: "從一個構想做到完整影片",
              attributes: { tabindex: "-1" },
            }),
            el("p", { text: "選角色、說故事；Castle 自動整理分鏡並選好可用資源，你只要決定何時開始試做。" }),
          ]),
          el("div", {
            className: "project-preview__safety",
            attributes: { role: "note" },
          }, [
            el("strong", { text: "最短只要兩次點擊" }),
            el("span", { text: "準備試做 → 立即試做；中間檢查全部自動完成" }),
          ]),
        ]),
      ]);
      this._status = el("div", {
        className: `project-preview__live${this._error ? " is-error" : ""}`,
        text: this._error || this._message,
        attributes: {
          role: this._error ? "alert" : "status",
          "aria-live": this._error ? "assertive" : "polite",
          "aria-atomic": "true",
        },
      });
      root.append(this._status);
      if (!this._authorized) {
        root.append(el("section", { className: "project-preview__card" }, [
          el("div", { className: "project-preview__empty", text: "需要管理員或指定操作員權限。" }),
        ]));
      } else {
        root.append(this._renderJourney());
        if (!this._draft) {
          root.append(this._renderBriefWizard());
        } else if (!this._routeReview) {
          const draft = this._renderDraft();
          draft.querySelector(".project-preview__route-review-button")?.addEventListener("click", () => this._reviewRoutes());
          root.append(draft);
        }
        if (this._routeReview) root.append(this._renderRouteReview());
        if (this._draft && this._routeReview) {
          const authoredPlan = el("details", { className: "project-preview__advanced" }, [
            el("summary", { text: "查看故事板與導演安排" }),
          ]);
          const draft = this._renderDraft();
          if (draft) authoredPlan.append(draft);
          if (this._directorPlan) authoredPlan.append(this._renderDirectorPlan());
          root.append(authoredPlan);
          root.append(el("details", { className: "project-preview__advanced" }, [
            el("summary", { text: "重新建立另一個試做" }),
            this._renderBriefWizard(),
          ]));
        }
        const formalProduction = el("details", {
          className: "project-preview__advanced",
          attributes: { open: this._production || this._result ? "" : null },
        }, [
          el("summary", { text: "進階：完整專案製作、逐鏡審核與剪輯" }),
        ]);
        if (this._packProposal) formalProduction.append(this._renderPackProposal());
        if (this._packProposal?.seriesId) formalProduction.append(this._renderSeriesContinuity());
        formalProduction.append(this._renderProductionResume());
        formalProduction.append(el("details", { className: "project-preview__advanced" }, [
          el("summary", { text: "匯入完整 Production Pack JSON" }),
          this._renderInput(),
        ]));
        if (this._result) formalProduction.append(this._renderOutcome(this._result));
        root.append(formalProduction);
      }
      this.replaceChildren(root);
    }
  }

  if (!customElements.get("castle-project-preview")) {
    customElements.define("castle-project-preview", CastleProjectPreview);
  }

  window.CastleProjectPreview = Object.freeze({
    mount(host, options = {}) {
      if (!(host instanceof Element)) throw new TypeError("A host element is required");
      const component = document.createElement("castle-project-preview");
      component.configure(options);
      host.replaceChildren(component);
      return component;
    },
  });
})();

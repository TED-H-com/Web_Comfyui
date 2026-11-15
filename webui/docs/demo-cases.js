/* Demo cases strip, input matching, button gate, timing, .assist-btn and rich result cards. */
(() => {
  'use strict';

  const EXEMPT_DOMAIN_SLOTS = new Set([
    'dance.video',
    'h3-reference-video.video1',
    'h3-reference-video.audio1',
    'lipsync.voice',
  ]);
  const PROMPT_KEYS = ['positive', 'instruction', 'quick', 'detailed_description', 'summary', 'text', 'speech'];
  const ASPECT_MAP = {
    portrait: ['直', 'portrait'],
    landscape: ['橫', 'landscape'],
    square: ['方', 'square'],
  };

  let app = null;
  let installed = false;
  let casesByFeature = new Map();
  let caseByIdMap = new Map();
  let subjectMap = new Map();
  let subjectByCharId = new Map();
  let materialMap = new Map();
  const addedResultOutputs = new Set();
  const sessionVoiceIds = new Set();
  const formStates = new WeakMap();
  const observedButtons = new WeakSet();
  const activeForms = new Set();

  const drive = () => window.WebComfyDemoDrive;
  const isEn = () => window.WebComfyI18n?.language === 'en' || document.documentElement.lang.startsWith('en');
  const pickLang = (obj, zhKey, enKey, fallback = '') => {
    if (!obj) return fallback;
    const zh = obj[zhKey] ?? fallback;
    const en = obj[enKey] ?? zh;
    return isEn() ? en : zh;
  };
  const assetUrl = (file) => drive()?.assetUrl?.(file) || (file ? new URL('../assets/examples/' + String(file).replace(/^\/+/, ''), location.href).href : '');

  function setI18n(node, zh, en) {
    const z = String(zh ?? '');
    const e = String(en ?? z);
    if (node.dataset.i18nZh !== z) node.dataset.i18nZh = z;
    if (node.dataset.i18nEn !== e) node.dataset.i18nEn = e;
    const target = isEn() ? e : z;
    if (node.textContent !== target) node.textContent = target;
    return node;
  }

  function keyPair(key, varsZh, varsEn) {
    const d = drive();
    if (!d?.i18nPair) return [key, key];
    const [z] = d.i18nPair(key, varsZh);
    const [, e] = d.i18nPair(key, varsEn || varsZh);
    return [z, e];
  }

  function setKeyI18n(node, key, varsZh, varsEn) {
    const [z, e] = keyPair(key, varsZh, varsEn);
    return setI18n(node, z, e);
  }

  function el(tag, cls = '', text = '') {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text) node.textContent = text;
    return node;
  }

  function resolveMaterial(id) {
    if (!id) return null;
    return drive()?.materialById?.(id) || materialMap.get(id) || null;
  }

  function resolveSubject(idOrCharId) {
    if (!idOrCharId) return null;
    return subjectMap.get(idOrCharId) || subjectByCharId.get(idOrCharId) || null;
  }

  function resolveFromSubject(fromId) {
    if (!fromId) return null;
    const list = window.WebComfyCharacterSession?.list?.() || [];
    const char = list.find((item) => item.id === fromId);
    if (char) {
      const boundSub = char.subjectId ? resolveSubject(char.subjectId) : null;
      return {
        charId: char.id,
        nameZh: char.name || boundSub?.name_zh || char.id,
        nameEn: char.name || boundSub?.name_en || char.id,
      };
    }
    const sub = resolveSubject(fromId);
    if (sub) {
      return {
        charId: sub.character_id || ('docs-character-' + sub.id),
        nameZh: sub.name_zh || sub.id,
        nameEn: sub.name_en || sub.name_zh || sub.id,
      };
    }
    return null;
  }

  function install(registry) {
    app = registry;
    const ds = registry?.demo_set || {};
    casesByFeature = new Map();
    caseByIdMap = new Map();
    subjectMap = new Map();
    subjectByCharId = new Map();
    materialMap = new Map();

    for (const s of ds.subjects || []) {
      subjectMap.set(s.id, s);
      if (s.character_id) subjectByCharId.set(s.character_id, s);
    }
    for (const m of ds.materials || []) materialMap.set(m.id, m);

    const indexed = (ds.cases || []).map((c, idx) => ({ c, idx }));
    indexed.sort((a, b) => (a.c.order ?? 0) - (b.c.order ?? 0) || a.idx - b.idx);
    for (const { c } of indexed) {
      caseByIdMap.set(c.id, c);
      if (!casesByFeature.has(c.feature_id)) casesByFeature.set(c.feature_id, []);
      casesByFeature.get(c.feature_id).push(c);
    }

    if (!installed) {
      installed = true;
      document.addEventListener('click', handleAssistCapture, true);
      window.addEventListener('hashchange', handleHashChange);
    }
  }

  function displayedCases(featureId) {
    return (casesByFeature.get(featureId) || []).filter(
      (c) => c.display && (c.kind === 'recorded' || c.kind === 'prepared'),
    );
  }

  function featureSpec(featureId) {
    return (app?.features || []).find((f) => f.id === featureId) || null;
  }

  function isCaseForm(form) {
    const id = form?.dataset?.demoId;
    return Boolean(id && displayedCases(id).length > 0);
  }

  function hashQueryParams() {
    const idx = location.hash.indexOf('?');
    return new URLSearchParams(idx >= 0 ? location.hash.slice(idx + 1) : '');
  }

  function readRouteParams(featureId) {
    const consumed = drive()?.consumePendingRoute?.('#/studio/' + featureId) || {};
    const hq = hashQueryParams();
    return {
      caseId: hq.get('case') || consumed.caseId || consumed.case || '',
      fromId: hq.get('from') || consumed.from || '',
      voiceId: hq.get('voice') || consumed.voice || '',
    };
  }

  function handleHashChange() {
    const hash = location.hash || '';
    const match = /^#\/studio\/([^/?]+)/.exec(hash);
    if (!match) return;
    const featureId = decodeURIComponent(match[1]);
    for (const form of [...activeForms]) {
      if (!form.isConnected) { activeForms.delete(form); continue; }
      if (form.dataset.demoId !== featureId) continue;
      const st = formStates.get(form);
      if (!st) continue;
      const params = readRouteParams(featureId);
      if (params.fromId) renderBackLink(form, st, params.fromId);
      if (params.caseId) select(form, params.caseId, { viaUrl: true });
      else if (params.voiceId && featureId === 'voice-speak') applyVoiceParam(form, st, params.voiceId);
    }
  }

  function handleAssistCapture(event) {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    const assist = target.closest('.assist-btn');
    if (!assist) return;
    const form = assist.closest('form[data-demo-cases="true"]');
    if (!form) return;
    event.preventDefault();
    event.stopImmediatePropagation();

    const st = formStates.get(form);
    const c = st ? (caseByIdMap.get(st.selectedCaseId) || st.cases[0]) : null;
    if (!c) return;
    const fieldScope = assist.closest('.field, label') || form;
    const fieldInput = fieldScope.querySelector('textarea, input[type="text"], input:not([type])');
    if (!fieldInput) return;

    const fieldId = fieldInput.dataset.fieldId || fieldInput.name || '';
    const settings = c.settings || {};
    let val = fieldId && settings[fieldId] !== undefined ? settings[fieldId] : undefined;
    if (val === undefined) {
      for (const k of PROMPT_KEYS) {
        if (settings[k] !== undefined) { val = settings[k]; break; }
      }
    }
    if (val === undefined) return;

    st.applying = true;
    try {
      fieldInput.value = String(val);
      fieldInput.dispatchEvent(new Event('input', { bubbles: true }));
      fieldInput.dispatchEvent(new Event('change', { bubbles: true }));
    } finally {
      st.applying = false;
    }
    drive()?.toast?.('assist.filled');
    applyGate(form);
  }

  function cleanupRetries(st) {
    for (const t of st.retryTimers) clearTimeout(t);
    st.retryTimers.clear();
  }

  function findSubmitButton(form) {
    return form.querySelector('[data-demo-submit], button[type="submit"].generate-btn, button[type="submit"]');
  }

  function observeSubmitButton(form, btn) {
    if (!btn || observedButtons.has(btn)) return;
    observedButtons.add(btn);
    let ticking = false;
    const mo = new MutationObserver(() => {
      if (ticking || !form.isConnected) return;
      ticking = true;
      queueMicrotask(() => {
        ticking = false;
        const st = formStates.get(form);
        if (st && !st.applyingGate && form.isConnected) applyGate(form);
      });
    });
    mo.observe(btn, { attributes: true, attributeFilter: ['disabled'], childList: true, characterData: true, subtree: true });
  }

  function ensureVoiceCloneUploadTab(form) {
    const segs = [...form.querySelectorAll('.seg-btn')];
    const upBtn = segs.find((b) => /上傳|upload/i.test(b.textContent || '')) || segs[1];
    if (upBtn && !upBtn.classList.contains('active')) upBtn.click();
  }

  function isVoiceCloneLibraryTab(form) {
    const st = formStates.get(form);
    if (st?.featureId !== 'voice-clone') return false;
    const libBtn = form.querySelector('.seg-btn');
    return Boolean(libBtn && libBtn.classList.contains('active') && !/上傳|upload/i.test(libBtn.textContent || ''));
  }

  function slotIdOfElement(slotEl) {
    return slotEl?.dataset?.uploadId || slotEl?.dataset?.uploadSlot || '';
  }

  function readFilledSlots(form) {
    const st = formStates.get(form);
    const slots = {};
    for (const slot of form.querySelectorAll('[data-upload-id], [data-upload-slot]')) {
      const slotId = slotIdOfElement(slot);
      if (!slotId) continue;
      if (st?.featureId === 'voice-clone' && slotId === 'voice' && isVoiceCloneLibraryTab(form)) {
        const libSel = findVoiceCloneLibSelect(form);
        if (libSel?.value) slots[slotId] = libSel.value;
        continue;
      }
      const input = slot.querySelector('input[type="file"]');
      if (input?.dataset?.demoMaterial) slots[slotId] = input.dataset.demoMaterial;
    }
    return slots;
  }

  function findVoiceCloneLibSelect(form) {
    return [...form.querySelectorAll('select')].find((sel) => {
      const box = sel.closest('.post-mode-panel, .field, label');
      return box && !box.hidden && !box.classList.contains('hidden');
    }) || form.querySelector('select');
  }

  function findVoiceSpeakClonePanel(form) {
    const panels = [...form.querySelectorAll('.post-mode-panel')];
    return panels.find((p) => p.querySelector('select') && p.querySelector('textarea'))
      || panels.find((p) => !p.classList.contains('hidden') && !p.hidden)
      || panels[0]
      || null;
  }

  function isVoiceSpeakClonePanelSelect(form, sel) {
    const panel = sel.closest('.post-mode-panel');
    if (!panel || panel !== findVoiceSpeakClonePanel(form)) return false;
    const panelSelects = [...panel.querySelectorAll('select')];
    return panelSelects[0] === sel;
  }

  function isCloneEngineSelect(sel) {
    return [...sel.options].some((o) => ['taiwan', 'breezy', 'f5'].includes(o.value));
  }

  function selectContainsValue(sel, value) {
    return [...sel.options].some((o) => o.value === String(value));
  }

  function findControlsForSetting(form, st, key, val) {
    const featureId = st.featureId;
    const valStr = String(val);

    if (key === 'aspect') {
      const wrap = form.querySelector('[data-field-id="aspect"] .aspect-group, .aspect-group');
      if (wrap) return { kind: 'aspect', container: wrap };
    }

    if (featureId === 'post-generator') {
      if (key === 'mode') {
        const segs = [...form.querySelectorAll('.seg-btn')];
        if (segs.length >= 2) return { kind: 'seg', buttons: segs };
      }
      if (key === 'quick') {
        const ta = form.querySelector('.post-mode-panel textarea, textarea[data-field-id="quick"]');
        if (ta) return { kind: 'value', el: ta };
      }
      if (['sister', 'platform', 'tone', 'length'].includes(key)) {
        const direct = form.querySelector(`select[data-field-id="${CSS.escape(key)}"], select[name="${CSS.escape(key)}"]`);
        if (direct) return { kind: 'select', el: direct };
        const matched = [...form.querySelectorAll('select')].find((s) => selectContainsValue(s, valStr));
        if (matched) return { kind: 'select', el: matched };
      }
    }

    if (featureId === 'voice-speak') {
      if (key === 'voiceSource') {
        const mapped = valStr === 'library' ? 'clone' : valStr;
        const card = form.querySelector(`.mode-card[data-value="${CSS.escape(valStr)}"]`)
          || form.querySelector(`.mode-card[data-value="${CSS.escape(mapped)}"]`);
        if (card) return { kind: 'card', el: card };
        const boundSummary = form.querySelector('.role-bound-summary .voice-path-label');
        if (boundSummary && (valStr === 'clone' || valStr === 'library')) return { kind: 'implicit-bound', el: boundSummary };
      }
      if (key === 'voiceId') {
        const sel = form.querySelector('select[data-field-id="voiceId"]')
          || findVoiceSpeakClonePanel(form)?.querySelector('select');
        if (sel) return { kind: 'select', el: sel, asyncWait: true };
      }
      if (key === 'sampleText') {
        const ta = form.querySelector('textarea[data-field-id="sampleText"]')
          || findVoiceSpeakClonePanel(form)?.querySelector('textarea');
        if (ta) return { kind: 'value', el: ta };
      }
      if (key === 'cloneEngine') {
        const sel = form.querySelector('select[data-field-id="cloneEngine"]')
          || [...form.querySelectorAll('select')].find(isCloneEngineSelect);
        if (sel) return { kind: 'select', el: sel };
      }
      if (key === 'text') {
        const tas = [...form.querySelectorAll('textarea')];
        const speechTa = form.querySelector('textarea[data-field-id="text"]')
          || tas.find((t) => !t.closest('.post-mode-panel'))
          || tas[tas.length - 1];
        if (speechTa) return { kind: 'value', el: speechTa };
      }
    }

    const esc = CSS.escape(key);
    const directInput = form.querySelector(
      `input[data-field-id="${esc}"], select[data-field-id="${esc}"], textarea[data-field-id="${esc}"], ` +
      `input[name="${esc}"], select[name="${esc}"], textarea[name="${esc}"]`,
    );
    if (directInput) {
      if (directInput.type === 'checkbox') return { kind: 'checkbox', el: directInput };
      if (directInput.tagName === 'SELECT') return { kind: 'select', el: directInput };
      return { kind: 'value', el: directInput };
    }

    const fieldNode = form.querySelector(`[data-field-id="${esc}"]`);
    if (fieldNode) {
      const card = fieldNode.querySelector(`.mode-card[data-value="${CSS.escape(valStr)}"]`);
      if (card) return { kind: 'card', el: card };
    }

    // Card option matching value directly inside form
    const card = form.querySelector(`.mode-card[data-value="${CSS.escape(valStr)}"]`);
    if (card) return { kind: 'card', el: card };

    // Fallback select containing option value (only if unambiguous by option value)
    const matchingSelects = [...form.querySelectorAll('select')].filter(
      (s) => !s.dataset.fieldId && selectContainsValue(s, valStr),
    );
    if (matchingSelects.length === 1) return { kind: 'select', el: matchingSelects[0] };

    return null;
  }

  function setSingleSetting(form, st, key, val, allowAsyncRetry = true) {
    const ctl = findControlsForSetting(form, st, key, val);
    if (!ctl) return false;

    st.applying = true;
    try {
      if (ctl.kind === 'implicit-bound') return true;
      if (ctl.kind === 'card') {
        if (!ctl.el.classList.contains('active')) ctl.el.click();
        return ctl.el.classList.contains('active');
      }
      if (ctl.kind === 'seg') {
        const idx = val === 'advanced' ? 1 : 0;
        const target = ctl.buttons[idx];
        if (target && !target.classList.contains('active')) target.click();
        return Boolean(target && target.classList.contains('active'));
      }
      if (ctl.kind === 'aspect') {
        const v = String(val).toLowerCase();
        const tokens = ASPECT_MAP[v] || [v];
        const btn = [...ctl.container.querySelectorAll('.aspect-btn, button')].find((b) => {
          if (b.dataset.value && b.dataset.value.toLowerCase() === v) return true;
          const txt = (b.textContent || '').trim().toLowerCase();
          return tokens.some((t) => txt.startsWith(t));
        });
        if (!btn) return false;
        if (!btn.classList.contains('active')) btn.click();
        return btn.classList.contains('active');
      }
      if (ctl.kind === 'checkbox') {
        const want = Boolean(val);
        if (ctl.el.checked !== want) {
          ctl.el.checked = want;
          ctl.el.dispatchEvent(new Event('change', { bubbles: true }));
        }
        return ctl.el.checked === want;
      }
      if (ctl.kind === 'select') {
        const want = String(val);
        if (selectContainsValue(ctl.el, want)) {
          if (ctl.el.value !== want) {
            ctl.el.value = want;
            ctl.el.dispatchEvent(new Event('change', { bubbles: true }));
          }
          return ctl.el.value === want;
        }
        if (allowAsyncRetry && (ctl.asyncWait || ctl.el.options.length <= 1)) {
          scheduleSelectRetry(form, st, key, val);
          return 'pending';
        }
        return false;
      }
      if (ctl.kind === 'value') {
        const want = String(val ?? '');
        if (ctl.el.value !== want) {
          ctl.el.value = want;
          ctl.el.dispatchEvent(new Event('input', { bubbles: true }));
          ctl.el.dispatchEvent(new Event('change', { bubbles: true }));
        }
        return ctl.el.value === want;
      }
    } finally {
      st.applying = false;
    }
    return false;
  }

  function updateUnfilledDataset(form, st) {
    const keys = [...st.unfilled];
    form.dataset.demoUnfilled = keys.join(',');
  }

  function scheduleSelectRetry(form, st, key, val) {
    const deadline = Date.now() + 3000;
    const poll = () => {
      if (!form.isConnected || st.selectedCaseId !== st.pendingCaseId) return;
      const res = setSingleSetting(form, st, key, val, false);
      if (res === true) {
        st.unfilled.delete(key);
        updateUnfilledDataset(form, st);
        applyGate(form);
        return;
      }
      if (Date.now() < deadline) {
        const timer = setTimeout(poll, 80);
        st.retryTimers.add(timer);
      } else {
        st.unfilled.add(key);
        updateUnfilledDataset(form, st);
        applyGate(form);
      }
    };
    const timer = setTimeout(poll, 60);
    st.retryTimers.add(timer);
  }

  function orderedSettingsEntries(settings) {
    const priority = ['voiceSource', 'mode', 'voiceId', 'cloneEngine', 'voiceEngine'];
    const entries = Object.entries(settings || {});
    entries.sort(([a], [b]) => {
      const ia = priority.indexOf(a);
      const ib = priority.indexOf(b);
      if (ia !== -1 || ib !== -1) return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
      return 0;
    });
    return entries;
  }

  function resetDemoBox(form) {
    const demoBox = form.querySelector('.app-demo');
    if (!demoBox || demoBox.dataset.state === 'idle') return;
    const resetBtn = demoBox.querySelector('.explorer-toolbar button:last-child');
    if (resetBtn) resetBtn.click();
  }

  function select(form, caseId, opts = {}) {
    const st = formStates.get(form);
    if (!st || !st.cases.length) return null;

    cleanupRetries(st);
    resetDemoBox(form);
    st.selecting = true;
    try {
      const exact = st.cases.find((c) => c.id === caseId);
    const chosen = exact || st.cases[0];

    st.selectedCaseId = chosen.id;
    st.pendingCaseId = chosen.id;
    st.matchReason = '';
    st.edited = false;
    st.unfilled = new Set();
    form.dataset.demoCase = chosen.id;
    form.dataset.demoEdited = 'false';

    // Reset case buttons active state and keep active chip visible inside strip (scrollLeft only)
    let activeBtn = null;
    for (const btn of form.querySelectorAll('.demo-case-btn')) {
      const active = btn.dataset.caseId === chosen.id;
      btn.classList.toggle('is-active', active);
      btn.setAttribute('aria-pressed', active ? 'true' : 'false');
      if (active) activeBtn = btn;
    }
    if (activeBtn) {
      const centerChip = () => {
        const stripRow = activeBtn.closest('.demo-case-strip-buttons');
        if (!stripRow || stripRow.scrollWidth <= stripRow.clientWidth) return;
        const rowRect = stripRow.getBoundingClientRect();
        const btnRect = activeBtn.getBoundingClientRect();
        const targetLeft = stripRow.scrollLeft + (btnRect.left - rowRect.left) - (stripRow.clientWidth - btnRect.width) / 2;
        stripRow.scrollLeft = Math.max(0, Math.min(stripRow.scrollWidth - stripRow.clientWidth, targetLeft));
      };
      centerChip();
      if (typeof requestAnimationFrame === 'function') requestAnimationFrame(centerChip);
    }

    // Update switch note
    const switchNote = form.querySelector('.demo-case-switch-note');
    if (switchNote) {
      if (opts.viaUrl && caseId && !exact) {
        setKeyI18n(switchNote, 'case.unknown');
        switchNote.hidden = false;
      } else if (opts.switchedFromMaterial) {
        setKeyI18n(
          switchNote,
          'case.switched',
          { title: chosen.title_zh || chosen.id },
          { title: chosen.title_en || chosen.title_zh || chosen.id },
        );
        switchNote.hidden = false;
      } else {
        switchNote.hidden = true;
      }
    }

    // Populate upload slots first
    const wantInputs = chosen.inputs || {};
    st.applying = true;
    try {
      for (const slotEl of form.querySelectorAll('[data-upload-id], [data-upload-slot]')) {
        const slotId = slotIdOfElement(slotEl);
        if (slotId && !(slotId in wantInputs)) {
          drive()?.clearSlot?.(form, slotId);
        }
      }
      for (const [slotId, matId] of Object.entries(wantInputs)) {
        if (st.featureId === 'voice-clone' && slotId === 'voice') ensureVoiceCloneUploadTab(form);
        drive()?.fillSlot?.(form, slotId, matId, 'case');
      }
    } finally {
      st.applying = false;
    }

    // Populate settings
    for (const [key, val] of orderedSettingsEntries(chosen.settings)) {
      const placed = setSingleSetting(form, st, key, val, true);
      if (placed === false) st.unfilled.add(key);
    }
    updateUnfilledDataset(form, st);

    // Sync tour preview state without re-overwriting form inputs
    if (st.tourState) {
      st.tourState.selectedCaseId = chosen.id;
    }
    applyGate(form);
    return chosen;
    } finally {
      st.selecting = false;
    }
  }

  function computeDomainMismatch(st, filledSlots) {
    const entries = Object.entries(filledSlots);
    const nonExemptDomains = [];
    for (const [slotId, matId] of entries) {
      if (EXEMPT_DOMAIN_SLOTS.has(`${st.featureId}.${slotId}`)) continue;
      const mat = resolveMaterial(matId);
      if (mat?.domain) nonExemptDomains.push(mat.domain);
    }
    if (new Set(nonExemptDomains).size > 1) return true;
    if (nonExemptDomains.length > 0) {
      const d = nonExemptDomains[0];
      const caseDomains = st.cases.map((c) => c.domain).filter(Boolean);
      if (caseDomains.length > 0 && !caseDomains.includes(d)) return true;
    }
    return false;
  }

  function resolvePickedSlot(filled, slot, materialId) {
    if (slot && filled[slot] === materialId) return slot;
    for (const [key, value] of Object.entries(filled)) {
      if (value === materialId) return key;
    }
    return '';
  }

  // Spec 5.9 rule 3 (amendment 09:12): cases that use the picked material in the picked slot; the most
  // other filled slots equal to the case inputs wins, ties go to the lowest order (st.cases is sorted).
  function bestCaseForPick(st, filled, pickedSlot, materialId) {
    let best = null;
    let bestScore = -1;
    for (const c of st.cases) {
      const ci = c.inputs || {};
      if (ci[pickedSlot] !== materialId) continue;
      let score = 0;
      for (const [slot, matId] of Object.entries(filled)) {
        if (slot !== pickedSlot && ci[slot] === matId) score += 1;
      }
      if (score > bestScore) {
        best = c;
        bestScore = score;
      }
    }
    return best;
  }

  function handleMaterialChange(form, st, opts = {}) {
    if (st.selecting) return;
    const clearedSlot = opts.clearedSlot || null;
    const filled = readFilledSlots(form);
    const filledCount = Object.keys(filled).length;

    if (opts.switchedFromMaterial && !clearedSlot) {
      const pickedSlot = resolvePickedSlot(filled, opts.pickedSlot || '', opts.switchedFromMaterial);
      if (pickedSlot) {
        const target = bestCaseForPick(st, filled, pickedSlot, opts.switchedFromMaterial);
        if (!target) {
          clearSelectionState(form, st, computeDomainMismatch(st, filled) ? 'nomatch.domain' : 'nomatch.other');
        } else if (target.id === st.selectedCaseId) {
          st.matchReason = '';
          applyGate(form);
        } else {
          select(form, target.id, { switchedFromMaterial: opts.switchedFromMaterial });
        }
        return;
      }
    }

    if (clearedSlot && filledCount === 0) {
      clearSelectionState(form, st, '');
      return;
    }

    const candidates = st.cases.filter((c) => {
      if (clearedSlot && c.inputs?.[clearedSlot]) return false;
      const ci = c.inputs || {};
      for (const [slot, matId] of Object.entries(filled)) {
        if (ci[slot] !== matId) return false;
      }
      return true;
    });

    if (candidates.length > 0) {
      const stay = !clearedSlot && !opts.switchedFromMaterial
        ? candidates.find((c) => c.id === st.selectedCaseId)
        : null;
      const target = stay || candidates[0];
      if (target.id !== st.selectedCaseId || clearedSlot || opts.switchedFromMaterial) {
        select(form, target.id, { switchedFromMaterial: opts.switchedFromMaterial });
      } else {
        st.matchReason = '';
        applyGate(form);
      }
      return;
    }

    if (clearedSlot) {
      clearSelectionState(form, st, '');
      return;
    }

    const reason = computeDomainMismatch(st, filled) ? 'nomatch.domain' : 'nomatch.other';
    clearSelectionState(form, st, reason);
  }

  function clearSelectionState(form, st, reason) {
    cleanupRetries(st);
    resetDemoBox(form);
    st.selectedCaseId = '';
    st.pendingCaseId = '';
    st.matchReason = reason;
    form.dataset.demoCase = '';
    for (const btn of form.querySelectorAll('.demo-case-btn')) {
      btn.classList.remove('is-active');
      btn.setAttribute('aria-pressed', 'false');
    }
    const switchNote = form.querySelector('.demo-case-switch-note');
    if (switchNote) switchNote.hidden = true;
    applyGate(form);
  }

  function distinguishingSelectKeys(cases) {
    const keys = new Set();
    const valuesByKey = new Map();
    for (const c of cases) {
      for (const [k, v] of Object.entries(c.settings || {})) {
        if (!valuesByKey.has(k)) valuesByKey.set(k, new Set());
        valuesByKey.get(k).add(String(v));
      }
    }
    for (const [k, set] of valuesByKey) {
      if (set.size > 0 && !['mode', 'quick', 'positive', 'negative', 'instruction', 'sampleText', 'text', 'speech', 'style'].includes(k)) {
        keys.add(k);
      }
    }
    return keys;
  }

  // Spec 5.9 rule 7 (amendment 09:12): a select key "differs" when the displayed cases hold at least two
  // distinct values for it.
  function differingSelectKeys(cases) {
    const out = new Set();
    for (const k of distinguishingSelectKeys(cases)) {
      const values = new Set(cases.map((c) => String(c.settings?.[k] ?? '')));
      if (values.size > 1) out.add(k);
    }
    return out;
  }

  function inferSelectKey(form, st, sel) {
    if (sel.dataset.fieldId) return sel.dataset.fieldId;
    if (sel.name) return sel.name;
    if (st.featureId === 'voice-speak') {
      if (isVoiceSpeakClonePanelSelect(form, sel)) return 'voiceId';
      if (isCloneEngineSelect(sel)) return 'cloneEngine';
    }
    const vals = new Set([...sel.options].map((o) => o.value).filter(Boolean));
    for (const k of distinguishingSelectKeys(st.cases)) {
      for (const c of st.cases) {
        if (c.settings?.[k] !== undefined && vals.has(String(c.settings[k]))) return k;
      }
    }
    return '';
  }

  function readSelectValueByKey(form, st, key) {
    const ctl = findControlsForSetting(form, st, key, '');
    if (ctl?.kind === 'select' && ctl.el) return ctl.el.value;
    if (st.featureId === 'voice-speak' && key === 'voiceId') {
      return findVoiceSpeakClonePanel(form)?.querySelector('select')?.value || '';
    }
    if (st.featureId === 'post-generator') {
      for (const s of form.querySelectorAll('select')) {
        if (inferSelectKey(form, st, s) === key) return s.value;
      }
    }
    return '';
  }

  function applyVoiceParam(form, st, voiceId) {
    const matchingCase = st.cases.find((c) => c.settings?.voiceId === voiceId);
    if (matchingCase) {
      select(form, matchingCase.id);
      return;
    }
    const sel = form.querySelector('select[data-field-id="voiceId"]')
      || findVoiceSpeakClonePanel(form)?.querySelector('select');
    const deadline = Date.now() + 3000;
    const trySet = () => {
      if (!form.isConnected) return;
      if (sel && selectContainsValue(sel, voiceId)) {
        st.applying = true;
        sel.value = voiceId;
        sel.dispatchEvent(new Event('change', { bubbles: true }));
        st.applying = false;
        clearSelectionState(form, st, 'nomatch.other');
        return;
      }
      if (Date.now() < deadline) st.retryTimers.add(setTimeout(trySet, 80));
    };
    trySet();
  }

  function handleSelectMatch(form, st, sel) {
    if (st.featureId === 'voice-clone' && isVoiceCloneLibraryTab(form) && sel === findVoiceCloneLibSelect(form)) {
      const matId = sel.value || '';
      if (!matId) {
        clearSelectionState(form, st, '');
        return;
      }
      const candidates = st.cases.filter((c) => c.inputs?.voice === matId);
      if (candidates.length > 0) select(form, candidates[0].id, { switchedFromMaterial: matId });
      else clearSelectionState(form, st, 'nomatch.other');
      return;
    }

    const changedKey = inferSelectKey(form, st, sel);
    if (!changedKey) return;

    const val = sel.value;
    if (st.featureId === 'voice-speak') {
      if (changedKey === 'voiceId') {
        const hit = st.cases.find((c) => String(c.settings?.voiceId || '') === val);
        if (hit) select(form, hit.id, { switchedFromMaterial: val });
        else clearSelectionState(form, st, val ? 'nomatch.other' : '');
      } else {
        st.edited = true;
        form.dataset.demoEdited = 'true';
      }
      return;
    }

    const dKeys = differingSelectKeys(st.cases);
    // Rule 5: a key that does not differ is an edit; the select's input event already marked the form edited.
    if (!dKeys.has(changedKey)) return;

    const filled = readFilledSlots(form);
    let best = null;
    let bestScore = -1;
    for (const cand of st.cases) {
      if (String(cand.settings?.[changedKey] ?? '') !== val) continue;
      const ci = cand.inputs || {};
      let score = 0;
      for (const [slot, matId] of Object.entries(filled)) {
        if (ci[slot] === matId) score += 1;
      }
      for (const k of dKeys) {
        if (k === changedKey) continue;
        if (String(cand.settings?.[k] ?? '') === readSelectValueByKey(form, st, k)) score += 1;
      }
      if (score > bestScore) {
        bestScore = score;
        best = cand;
      }
    }
    if (!best) {
      clearSelectionState(form, st, computeDomainMismatch(st, filled) ? 'nomatch.domain' : 'nomatch.other');
      return;
    }
    if (best.id === st.selectedCaseId) {
      st.matchReason = '';
      applyGate(form);
      return;
    }
    select(form, best.id, { switchedFromMaterial: best.id });
  }

  function missingRequiredSlot(form, st) {
    const spec = featureSpec(st.featureId);
    const filled = readFilledSlots(form);
    if (spec?.uploads?.length) {
      for (const up of spec.uploads) {
        if (up.required && !filled[up.id]) return up.id;
      }
    }
    if (spec?.minimumUploads && Object.keys(filled).length < spec.minimumUploads) {
      return 'minimum';
    }
    return '';
  }

  function applyGate(form) {
    const st = formStates.get(form);
    if (!st) return;
    const btn = findSubmitButton(form);
    if (!btn) return;
    if (btn.dataset.demoSubmit !== 'true') btn.dataset.demoSubmit = 'true';
    observeSubmitButton(form, btn);

    const demoBox = form.querySelector('.app-demo');
    if (demoBox?.dataset?.state === 'running') return;

    st.applyingGate = true;
    try {
      const hwAllowed = window.WebComfyHardware ? window.WebComfyHardware.canUse(st.featureId) !== false : true;
      const reasonNode = form.querySelector('.demo-gate-reason');

      if (!hwAllowed) {
        if (!btn.disabled) btn.disabled = true;
        form.dataset.demoGate = 'hardware-blocked';
        if (reasonNode && !reasonNode.hidden) reasonNode.hidden = true;
        setButtonDemoLabel(btn);
        return;
      }

      let gateKey = '';
      if (st.matchReason) {
        gateKey = st.matchReason;
      } else if (!st.selectedCaseId || missingRequiredSlot(form, st)) {
        gateKey = 'empty.slot';
      }

      const shouldDisable = Boolean(gateKey);
      if (btn.disabled !== shouldDisable) btn.disabled = shouldDisable;
      form.dataset.demoGate = gateKey || 'ready';

      if (reasonNode) {
        if (gateKey) {
          setKeyI18n(reasonNode, gateKey);
          reasonNode.dataset.gateKey = gateKey;
          if (reasonNode.hidden) reasonNode.hidden = false;
        } else {
          delete reasonNode.dataset.gateKey;
          if (!reasonNode.hidden) reasonNode.hidden = true;
        }
      }

      setButtonDemoLabel(btn);
    } finally {
      st.applyingGate = false;
    }
  }

  function setButtonDemoLabel(btn) {
    const zh = '開始生成（示範）';
    const en = 'Generate (demo)';
    if (btn.dataset.i18nZh !== zh) btn.dataset.i18nZh = zh;
    if (btn.dataset.i18nEn !== en) btn.dataset.i18nEn = en;
    const target = isEn() ? en : zh;
    if (btn.textContent !== target) btn.textContent = target;
  }

  function durationMs(form) {
    if (drive()?.reducedMotion?.()) return 0;
    const st = formStates.get(form);
    const c = st ? caseByIdMap.get(st.selectedCaseId) : null;
    if (!c) return 1600;
    const run = c.run_id ? app?.demo_runs?.[c.run_id] : null;
    if (run && typeof run.duration_seconds === 'number' && run.duration_seconds > 0) {
      const sec = Math.min(4.0, Math.max(1.5, Math.sqrt(run.duration_seconds) * 0.45));
      return Math.round(sec * 1000);
    }
    return 1600;
  }

  function renderBackLink(form, st, fromId) {
    const existing = form.querySelector(':scope > .demo-char-back');
    const info = resolveFromSubject(fromId);
    if (!info) {
      if (existing) existing.remove();
      return;
    }
    st.fromInfo = info;
    const link = existing || el('a', 'ghost small demo-char-back');
    link.href = `#/c/${encodeURIComponent(info.charId)}`;
    setKeyI18n(link, 'char.back', { name: info.nameZh }, { name: info.nameEn });
    if (!existing) form.prepend(link);
  }

  function buildStrip(form, st) {
    const strip = el('section', 'demo-case-strip');
    const label = el('p', 'demo-case-strip-label');
    const allRecorded = st.cases.every((c) => c.kind === 'recorded');
    const allPrepared = st.cases.every((c) => c.kind === 'prepared');
    if (allRecorded) setKeyI18n(label, 'strip.recorded');
    else if (allPrepared) setKeyI18n(label, 'strip.prepared');
    else setI18n(label, '示範案例（含本機實跑與預製範例）', 'Demo cases (recorded on local GPU & prepared examples)');

    const row = el('div', 'demo-case-strip-buttons');
    for (const c of st.cases) {
      const btn = el('button', 'ghost small demo-case-btn');
      btn.type = 'button';
      btn.dataset.caseId = c.id;
      btn.dataset.caseKind = c.kind;
      btn.setAttribute('aria-pressed', 'false');

      const titleSpan = el('span', 'demo-case-btn-title');
      setI18n(titleSpan, c.title_zh || c.id, c.title_en || c.title_zh || c.id);

      const badge = el('span', `demo-case-btn-kind is-${c.kind}`);
      if (c.kind === 'recorded') {
        setI18n(badge, '本機實跑', 'Local GPU');
      } else if (c.stand_in_for) {
        setI18n(badge, '預製（暫用替代）', 'Prepared (stand-in)');
      } else {
        setI18n(badge, '預製範例', 'Prepared');
      }

      btn.append(titleSpan, badge);
      btn.addEventListener('click', () => select(form, c.id, { userInitiated: true }));
      row.append(btn);
    }

    const switchNote = el('p', 'demo-case-switch-note');
    switchNote.hidden = true;
    strip.append(label, row, switchNote);
    return strip;
  }

  function attach(form, state) {
    if (!form || !isCaseForm(form)) return null;
    if (formStates.has(form)) return formStates.get(form);

    const featureId = form.dataset.demoId;
    const cases = displayedCases(featureId);
    const st = {
      form,
      featureId,
      tourState: state || null,
      cases,
      selectedCaseId: '',
      pendingCaseId: '',
      edited: false,
      matchReason: '',
      applying: false,
      applyingGate: false,
      unfilled: new Set(),
      retryTimers: new Set(),
      fromInfo: null,
    };
    formStates.set(form, st);
    activeForms.add(form);
    form.dataset.demoCases = 'true';
    form.dataset.demoEdited = 'false';

    const params = readRouteParams(featureId);
    if (params.fromId) renderBackLink(form, st, params.fromId);

    const strip = buildStrip(form, st);
    const anchor = form.querySelector(':scope > .demo-prefill-bar') || form.querySelector(':scope > .demo-description');
    if (anchor) anchor.before(strip);
    else {
      const back = form.querySelector(':scope > .demo-char-back');
      if (back) back.after(strip);
      else form.prepend(strip);
    }

    const gateReason = el('p', 'demo-gate-reason');
    gateReason.setAttribute('role', 'status');
    gateReason.hidden = true;
    const submitBtn = findSubmitButton(form);
    if (submitBtn) {
      submitBtn.dataset.demoSubmit = 'true';
      submitBtn.before(gateReason);
      observeSubmitButton(form, submitBtn);
    } else {
      form.append(gateReason);
    }

    // Edit tracking + Rule 7 (<select> matching)
    const onControlMutation = (event) => {
      if (st.applying) return;
      const target = event.target instanceof Element ? event.target : null;
      if (!target || target.closest('.demo-case-strip, .app-demo, .compute-placement')) return;
      if (!target.matches('input, textarea, select')) return;

      if (target.tagName === 'SELECT' && event.type === 'change') {
        handleSelectMatch(form, st, target);
      } else {
        st.edited = true;
        form.dataset.demoEdited = 'true';
      }
      queueMicrotask(() => applyGate(form));
    };
    form.addEventListener('input', onControlMutation);
    form.addEventListener('change', onControlMutation);

    // Matching rules 3-6 from demo-drive slot fills/removals
    form.addEventListener('demo:material-picked', (event) => {
      if (event.detail?.source === 'case') return;
      handleMaterialChange(form, st, { switchedFromMaterial: event.detail?.materialId, pickedSlot: event.detail?.slot || '' });
    });
    form.addEventListener('demo:material-cleared', (event) => {
      if (event.detail?.source === 'case') return;
      handleMaterialChange(form, st, { clearedSlot: event.detail?.slot ?? event.detail?.slotId });
    });

    // Click on seg-btn / mode-card / aspect-btn marks edited (unless programmatic or voice-clone upload/lib tab)
    form.addEventListener('click', (event) => {
      if (st.applying) return;
      const btn = event.target instanceof Element ? event.target.closest('button') : null;
      if (!btn || btn.closest('.demo-case-strip, .app-demo, .assist-btn, [data-upload-slot], .compute-placement')) return;
      if (btn.matches('.aspect-btn, .mode-card, .seg-btn')) {
        if (st.featureId === 'voice-clone' && btn.classList.contains('seg-btn')) {
          queueMicrotask(() => handleVoiceCloneTabSwitch(form, st));
        } else {
          st.edited = true;
          form.dataset.demoEdited = 'true';
        }
        queueMicrotask(() => applyGate(form));
      }
    });

    if (params.caseId) {
      select(form, params.caseId, { viaUrl: true });
    } else if (params.voiceId && featureId === 'voice-speak') {
      applyVoiceParam(form, st, params.voiceId);
    } else {
      select(form, cases[0].id);
    }

    return st;
  }

  function handleVoiceCloneTabSwitch(form, st) {
    if (isVoiceCloneLibraryTab(form)) {
      const libSel = findVoiceCloneLibSelect(form);
      if (libSel?.value) {
        const hit = st.cases.find((c) => c.inputs?.voice === libSel.value);
        if (hit) select(form, hit.id, { switchedFromMaterial: libSel.value });
        else clearSelectionState(form, st, 'nomatch.other');
      } else {
        clearSelectionState(form, st, '');
      }
    } else {
      handleMaterialChange(form, st, {});
    }
  }

  function deriveNextLinks(c) {
    const links = [];
    const seen = new Set();
    const pushLink = (href, zh, en) => {
      if (!href || seen.has(href) || links.length >= 4) return;
      seen.add(href);
      links.push({ href, zh, en });
    };

    if (Array.isArray(c.what_next)) {
      for (const item of c.what_next) {
        if (item.href) pushLink(item.href, item.label_zh || item.label_en, item.label_en || item.label_zh);
      }
      return links;
    }

    const outMats = new Set((c.outputs || []).map((o) => o.material).filter(Boolean));
    for (const matId of outMats) {
      for (const cand of app?.demo_set?.cases || []) {
        if (cand.id === c.id || cand.feature_id === c.feature_id || !cand.display) continue;
        const usesMat = Object.values(cand.inputs || {}).includes(matId) || cand.settings?.voiceId === matId;
        if (usesMat) {
          const feat = featureSpec(cand.feature_id);
          const fTitle = feat?.title || cand.feature_id;
          pushLink(
            `#/studio/${encodeURIComponent(cand.feature_id)}?case=${encodeURIComponent(cand.id)}`,
            `${fTitle} · ${cand.title_zh || cand.id}`,
            `${fTitle}: ${cand.title_en || cand.title_zh || cand.id}`,
          );
        }
      }
    }

    for (const subId of c.subjects || []) {
      const sub = resolveSubject(subId);
      if (sub) {
        const cid = sub.character_id || ('docs-character-' + sub.id);
        pushLink(
          `#/c/${encodeURIComponent(cid)}`,
          `回到角色頁：${sub.name_zh || sub.id}`,
          `Character page: ${sub.name_en || sub.name_zh || sub.id}`,
        );
      }
    }

    if ((c.outputs || []).some((o) => o.kind !== 'text')) {
      pushLink('#/manage', '查看素材庫中的這份成果', 'View this output in Library');
    }

    return links;
  }

  function recordCaseOutputsToSession(c) {
    const d = drive();
    if (!d?.addToSession) return;
    for (const out of c.outputs || []) {
      if (!out.file) continue;
      const dedupeKey = `${c.id}|${out.kind}|${out.file}`;
      if (addedResultOutputs.has(dedupeKey)) continue;
      addedResultOutputs.add(dedupeKey);
      const refMat = out.material ? resolveMaterial(out.material) : null;
      d.addToSession({
        id: refMat?.id || ('result-' + c.id + '-' + out.file),
        file: out.file,
        kind: out.kind,
        poster: out.poster || refMat?.poster || null,
        thumb: out.thumb || refMat?.thumb || null,
        domain: c.domain ?? refMat?.domain ?? null,
        label_zh: `${c.title_zh || c.feature_id}`,
        label_en: `${c.title_en || c.title_zh || c.feature_id}`,
        transcript: refMat?.transcript || null,
        source: 'result',
      });
    }
  }

  function buildImageFigure(file, altZh, altEn, captionZh, captionEn) {
    const fig = el('figure');
    const img = el('img');
    img.src = assetUrl(file);
    img.alt = isEn() ? (altEn || altZh || '') : (altZh || '');
    img.loading = 'lazy';
    img.decoding = 'async';
    fig.append(img);
    if (captionZh || captionEn) {
      const cap = el('figcaption');
      setI18n(cap, captionZh || captionEn, captionEn || captionZh);
      fig.append(cap);
    }
    return fig;
  }

  function renderRunRecordDetails(c, run) {
    const details = el('details', 'demo-run-record');
    const summary = el('summary');
    setKeyI18n(summary, 'result.runrecord');
    details.append(summary);

    const payload = {
      case_id: c.id,
      feature_id: c.feature_id,
      kind: c.kind,
      engine: c.engine,
      inputs: {},
      settings: c.settings || {},
    };
    for (const [slot, matId] of Object.entries(c.inputs || {})) {
      const mat = resolveMaterial(matId);
      payload.inputs[slot] = mat ? `${mat.file} (${matId})` : matId;
    }
    if (run) {
      payload.run_id = c.run_id;
      payload.executed_at = run.executed_at;
      payload.duration_seconds = run.duration_seconds;
      payload.comfyui_version = run.comfyui_version;
      payload.workflow_sha256 = run.workflow_sha256;
      if (run.adjustments) payload.adjustments = run.adjustments;
      if (run.inputs) payload.recorded_inputs = run.inputs;
      if (run.settings) payload.recorded_settings = run.settings;
    }
    const pre = el('pre', 'graph-parameters');
    pre.dataset.i18nIgnore = 'true';
    pre.textContent = JSON.stringify(payload, null, 2);
    details.append(pre);
    return details;
  }

  function renderResult(form, container) {
    const st = formStates.get(form);
    if (!st || !container) return;
    const c = caseByIdMap.get(st.selectedCaseId) || st.cases[0];
    if (!c) return;

    container.dataset.demoCaseResult = c.id;
    container.replaceChildren();

    const run = c.kind === 'recorded' && c.run_id ? app?.demo_runs?.[c.run_id] : null;
    recordCaseOutputsToSession(c);

    // Header
    const head = el('div', 'demo-result-head');
    const heading = el('h4');
    setI18n(heading, c.title_zh || c.id, c.title_en || c.title_zh || c.id);
    head.append(heading);
    container.append(head);

    // Provenance / Disclosure line
    if (c.kind === 'recorded') {
      const prov = el('p', 'demo-disclosure demo-result-provenance');
      const duration = typeof run?.duration_seconds === 'number' ? run.duration_seconds.toFixed(1) : '—';
      const version = run?.comfyui_version || '—';
      const date = run?.executed_at ? String(run.executed_at).slice(0, 10) : '—';
      setKeyI18n(prov, 'result.provenance', { duration, version, date });
      container.append(prov);
    } else {
      const disc = el('p', 'demo-disclosure demo-result-disclosure');
      const dz = c.disclosure_zh || '這是預先準備好的範例成果，展示介面效果；實際使用需在本機安裝執行。';
      const de = c.disclosure_en || c.disclosure_zh || 'Prepared example output demonstrating the interface. Install locally to generate your own.';
      setI18n(disc, dz, de);
      container.append(disc);
    }

    if (c.note_zh || c.note_en) {
      const note = el('p', 'demo-result-note');
      setI18n(note, c.note_zh || c.note_en, c.note_en || c.note_zh);
      container.append(note);
    }

    // Comparison (Before / After)
    let startOutputIndex = 0;
    if (c.comparison?.before && c.outputs?.[0]?.kind === 'image') {
      const beforeMat = resolveMaterial(c.inputs?.[c.comparison.before]);
      if (beforeMat?.file) {
        const comp = el('div', 'demo-gallery demo-comparison');
        const beforeFig = buildImageFigure(beforeMat.file, beforeMat.label_zh, beforeMat.label_en, beforeMat.label_zh, beforeMat.label_en);
        beforeFig.dataset.comparisonBefore = 'true';
        const beforeStrong = el('strong');
        setI18n(beforeStrong, '修改前', 'Before');
        beforeFig.prepend(beforeStrong);

        const afterOut = c.outputs[0];
        const afterFig = buildImageFigure(afterOut.file, c.title_zh, c.title_en, c.title_zh, c.title_en);
        afterFig.dataset.comparisonAfter = 'true';
        const afterStrong = el('strong');
        setI18n(afterStrong, '修改後', 'After');
        afterFig.prepend(afterStrong);

        comp.append(beforeFig, afterFig);
        container.append(comp);
        startOutputIndex = 1;
      }
    }

    // Remaining media outputs
    const rest = (c.outputs || []).slice(startOutputIndex);
    const images = rest.filter((o) => o.kind === 'image');
    const videos = rest.filter((o) => o.kind === 'video');
    const audios = rest.filter((o) => o.kind === 'audio');

    if (images.length) {
      const galClass = c.feature_id === 'character-angles' ? 'demo-gallery demo-angles-grid' : 'demo-gallery';
      const gallery = el('div', galClass);
      for (const imgOut of images) {
        gallery.append(buildImageFigure(imgOut.file, c.title_zh, c.title_en));
      }
      container.append(gallery);
    }

    for (const vidOut of videos) {
      const wrap = el('div', 'demo-video-result');
      const video = el('video');
      video.controls = true;
      video.playsInline = true;
      video.preload = 'metadata';
      video.src = assetUrl(vidOut.file);
      if (vidOut.poster) video.poster = assetUrl(vidOut.poster);
      wrap.append(video);
      container.append(wrap);
    }

    if (audios.length) {
      const audioList = el('div', 'demo-audio-list');
      for (const audOut of audios) {
        const mat = audOut.material ? resolveMaterial(audOut.material) : null;
        const item = el('div', 'demo-audio-track');
        if (mat) {
          const title = el('h5');
          setI18n(title, mat.label_zh || mat.file, mat.label_en || mat.label_zh || mat.file);
          item.append(title);
        }
        const audio = el('audio');
        audio.controls = true;
        audio.preload = 'metadata';
        audio.src = assetUrl(audOut.file);
        item.append(audio);
        if (mat?.transcript) {
          const tDet = el('details', 'demo-audio-transcript');
          const tSum = el('summary');
          setI18n(tSum, '逐字稿', 'Transcript');
          const tBody = el('p', 'demo-prompt-echo', mat.transcript);
          tBody.dataset.i18nIgnore = 'true';
          tDet.append(tSum, tBody);
          item.append(tDet);
        }
        audioList.append(item);
      }
      container.append(audioList);
    }

    if (c.post && (c.post.text_zh || c.post.text_en)) {
      const postBox = el('div', 'demo-result-text demo-post-output');
      setI18n(postBox, c.post.text_zh || c.post.text_en, c.post.text_en || c.post.text_zh);
      container.append(postBox);
    }

    // Run record disclosure
    container.append(renderRunRecordDetails(c, run));

    // What next navigation chips
    const nextLinks = deriveNextLinks(c);
    if (nextLinks.length) {
      const nextSec = el('section', 'demo-what-next');
      const nextTitle = el('h4');
      setKeyI18n(nextTitle, 'result.next');
      const linkRow = el('div', 'demo-what-next-links');
      for (const item of nextLinks) {
        const a = el('a', 'ghost small');
        a.href = item.href;
        setI18n(a, item.zh, item.en);
        linkRow.append(a);
      }
      nextSec.append(nextTitle, linkRow);
      container.append(nextSec);
    }
  }

  function voiceLibraryMaterials() {
    const result = [];
    const seen = new Set();
    for (const mat of materialMap.values()) {
      if (mat.kind !== 'audio') continue;
      if (mat.library || sessionVoiceIds.has(mat.id)) {
        seen.add(mat.id);
        result.push(mat);
      }
    }
    return result;
  }

  function renderVoiceLibraryGroups(host) {
    host.replaceChildren();
    const mats = voiceLibraryMaterials();
    const characterVoices = [];
    const otherVoices = [];
    for (const mat of mats) {
      if (mat.subject && subjectMap.has(mat.subject)) characterVoices.push(mat);
      else otherVoices.push(mat);
    }

    const speakCases = casesByFeature.get('voice-speak') || [];
    const cloneCases = casesByFeature.get('voice-clone') || [];

    const renderGroup = (groupKey, items) => {
      if (!items.length) return;
      const sec = el('section', 'demo-voice-group');
      const h = el('h3', 'demo-voice-group-title');
      setKeyI18n(h, groupKey);
      const list = el('div', 'demo-audio-list');

      for (const mat of items) {
        const card = el('article', 'demo-voice-card');
        const head = el('div', 'demo-voice-card-head');
        const title = el('h4');
        setI18n(title, mat.label_zh || mat.file, mat.label_en || mat.label_zh || mat.file);
        head.append(title);

        if (mat.gender === 'female' || mat.gender === 'male') {
          const gBadge = el('span', 'demo-badge');
          setI18n(gBadge, mat.gender === 'female' ? '女聲' : '男聲', mat.gender === 'female' ? "Women's voice" : "Men's voice");
          head.append(gBadge);
        }
        const sub = mat.subject ? resolveSubject(mat.subject) : null;
        if (sub) {
          const sBadge = el('span', 'demo-badge');
          setI18n(sBadge, sub.name_zh || sub.id, sub.name_en || sub.name_zh || sub.id);
          head.append(sBadge);
        }

        const audio = el('audio');
        audio.controls = true;
        audio.preload = 'metadata';
        audio.src = assetUrl(mat.file);

        card.append(head, audio);

        if (mat.transcript) {
          const t = el('p', 'demo-voice-transcript voice-transcript', mat.transcript);
          t.dataset.i18nIgnore = 'true';
          card.append(t);
        }

        const actions = el('div', 'demo-voice-card-actions');
        const speakCase = speakCases.find((c) => c.settings?.voiceId === mat.id && c.display);
        const speakLink = el('a', 'ghost small');
        speakLink.href = speakCase
          ? `#/studio/voice-speak?case=${encodeURIComponent(speakCase.id)}`
          : `#/studio/voice-speak?voice=${encodeURIComponent(mat.id)}`;
        setKeyI18n(speakLink, 'voice.speak');

        actions.append(speakLink);
        const cloneCase = cloneCases.find((c) => c.inputs?.voice === mat.id && c.display);
        if (cloneCase) {
          const refLink = el('a', 'ghost small');
          refLink.href = `#/studio/voice-clone?case=${encodeURIComponent(cloneCase.id)}`;
          setKeyI18n(refLink, 'voice.reference');
          actions.append(refLink);
        }
        card.append(actions);
        list.append(card);
      }

      sec.append(h, list);
      host.append(sec);
    };

    renderGroup('voice.group.character', characterVoices);
    renderGroup('voice.group.other', otherVoices);
  }

  function attachVoiceLibrary(form, panel) {
    if (!form || !panel) return;
    panel.querySelector('[data-voice-actions]')?.remove();
    panel.querySelector('.demo-voice-library')?.remove();

    const host = el('div', 'demo-voice-library');
    renderVoiceLibraryGroups(host);
    form.after(host);

    form.addEventListener('demo:material-picked', (event) => {
      const matId = event.detail?.materialId;
      if (!matId) return;
      sessionVoiceIds.add(matId);
      renderVoiceLibraryGroups(host);
      drive()?.toast?.('voice.added');
    });
  }

  window.WebComfyDemoCases = {
    install,
    attach,
    select,
    applyGate,
    durationMs,
    renderResult,
    isCaseForm,
    attachVoiceLibrary,
    voiceLibraryMaterials,
    addedVoiceIds: () => [...sessionVoiceIds],
  };
})();

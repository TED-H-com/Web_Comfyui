/* Closed-loop demo disk, library picker, URL alias flow, stubs and #/manage view. */
(() => {
  'use strict';

  const base = new URL('../', location.href);
  const STRINGS = {
    'common.close': ['關閉', 'Close'],
    'drive.title': ['示範硬碟（模擬上傳）', 'Demo disk (simulated upload)'],
    'drive.subtitle': ['示範站不會讀取你的檔案；這裡是預先放好的示範素材。', 'The demo never reads your files; these are prepared demo files.'],
    'folder.people': ['人物照片', 'People'],
    'folder.scenes': ['場景', 'Scenes'],
    'folder.videos': ['影片', 'Videos'],
    'folder.voices': ['聲音', 'Voices'],
    'badge.case': ['本案例使用的素材', 'Used by this case'],
    'badge.prerun': ['有預跑結果', 'Pre-run'],
    'disabled.image': ['這個欄位只接受圖片', 'This slot accepts images only'],
    'disabled.video': ['這個欄位只接受影片', 'This slot accepts videos only'],
    'disabled.audio': ['這個欄位只接受聲音', 'This slot accepts audio only'],
    'upload.progress': ['上傳中…', 'Uploading…'],
    'toast.uploaded': ['已上傳到素材庫', 'Uploaded to the library'],
    'library.title': ['素材庫', 'Library'],
    'library.session': ['你剛剛加入的', 'Added just now'],
    'library.results': ['預跑成果', 'Pre-run results'],
    'refuse.drop': ['示範站不接受拖放檔案；請按『上傳』從示範硬碟選。', 'The demo does not accept dropped files; press Upload and pick from the demo disk.'],
    'url.progress': ['下載中…', 'Downloading…'],
    'url.toast': ['已從示範網址下載（模擬）', 'Downloaded from a demo URL (simulated)'],
    'url.refuse': ['示範站不連線下載；請選一個示範網址：', 'The demo does not download from the internet; pick a demo URL:'],
    'crawl.note': ['示範站沒有 fb_crawl 工作區；安裝後這裡會列出你抓下來的影片。', 'The demo has no fb_crawl workspace; after installing, your crawled videos are listed here.'],
    'gdrive.note': ['示範站不連線 Google Drive；安裝後可以登入你自己的帳號匯入影片。', 'The demo does not connect to Google Drive; after installing you can sign in with your own account and import videos.'],
    'gdrive.toast': ['示範站不連線 Google；安裝後才能登入你自己的帳號。', 'The demo does not connect to Google; sign in with your own account after installing.'],
    'manage.title': ['素材庫', 'Library'],
    'manage.notice': ['示範素材庫：這裡是預先跑好的成果與示範素材；重新整理會清除你這次加入的項目。', 'Demo library: pre-run results and demo materials. Reloading clears what you added this visit.'],
    'manage.filter.all': ['全部', 'All'],
    'manage.filter.image': ['圖片', 'Images'],
    'manage.filter.video': ['影片', 'Videos'],
    'manage.filter.audio': ['聲音', 'Audio'],
    'manage.session': ['這次加入的', 'Added this visit'],
    'manage.session.empty': ['還沒有加入任何素材。按「上傳」或「從素材庫選擇」挑一個試試。', 'Nothing added yet. Use Upload or Pick from library to add one.'],
    'manage.results': ['預跑成果', 'Pre-run results'],
    'strip.recorded': ['示範案例（都已在本機 GPU 預先跑好）', 'Demo cases (all pre-run on a local GPU)'],
    'strip.prepared': ['示範案例（都已預先準備）', 'Demo cases (all prepared in advance)'],
    'case.unknown': ['找不到指定的示範案例，已改用第一個。', 'The requested demo case was not found; showing the first one.'],
    'case.switched': ['已切換到使用這個素材的示範案例：{title}', 'Switched to the demo case that uses this material: {title}'],
    'nomatch.domain': ['真人要配真實場景、動漫角色要配動漫場景；這個組合沒有預跑結果。', 'Realistic people go with real scenes and anime characters with anime scenes; this combination has no pre-run result.'],
    'nomatch.other': ['這個素材組合沒有預跑結果。示範站只展示真的跑過的組合：請改選標示「有預跑結果」的素材，或點一個示範案例。', 'This combination has no pre-run result. The demo only shows combinations that were actually run: pick materials marked "Pre-run" or click a demo case.'],
    'edited': ['示範結果使用這個案例實際跑過的設定；你修改的內容不會重新生成。', 'The demo result uses the settings this case actually ran with; your edits are not regenerated.'],
    'empty.slot': ['請先放入素材，或點一個示範案例。', 'Add a material first, or click a demo case.'],
    'result.provenance': ['本機 RTX 5090 實際執行 · {duration} 秒 · ComfyUI {version} · {date}', 'Run on a local RTX 5090 · {duration} s · ComfyUI {version} · {date}'],
    'result.runrecord': ['執行紀錄', 'Run record'],
    'result.next': ['接著可以做', 'What you can do next'],
    'assist.filled': ['示範站：已填入這個案例實際使用的提示詞', 'Demo: filled in the prompt this case actually used'],
    'char.badge': ['示範角色', 'Demo character'],
    'char.notice': ['示範角色：所有成果都是預先跑好的。', 'Demo character: every result was pre-run.'],
    'char.grid': ['這個角色可以做什麼', 'What this character can do'],
    'char.back': ['← 回到 {name}', '← Back to {name}'],
    'char.voice': ['{name}的聲音', "{name}'s voice"],
    'cc.only-demo': ['示範站只能用示範人物照片建立角色', 'The demo can only create characters from demo people photos'],
    'cc.bound': ['這個暫存角色使用「{name}」的示範照片，所以列出{name}的預跑案例。', "This temporary character uses {name}'s demo photo, so {name}'s pre-run cases are listed."],
    'cc.nophoto': ['挑一張示範人物照片，就能看到這個角色可以做什麼。', 'Pick a demo people photo to see what this character can do.'],
    'voice.group.character': ['角色聲音', 'Character voices'],
    'voice.group.other': ['其他示範聲音', 'Other demo voices'],
    'voice.added': ['已加入聲音庫（示範）', 'Added to the voice library (demo)'],
    'group.image': ['圖片', 'Images'],
    'group.video': ['影片', 'Videos'],
    'group.voice': ['聲音', 'Voice'],
    'group.text': ['文字', 'Text'],
    'slot.remove': ['移除', 'Remove'],
    'run.idle': ['按「開始生成（示範）」播放所選的示範案例。', 'Press Generate (demo) to play the selected demo case.'],
    'run.done': ['已顯示所選示範案例的成果。', "The selected demo case's result is shown."],
    'voice.slot': ['加入聲音（從示範硬碟選）', 'Add a voice (from the demo disk)'],
    'voice.note': ['示範聲音庫：試聽預製聲音，用「用於說話」或「用作參考音」打開對應工具；你加入的聲音只保留到重新整理。', 'Demo voice library: audition prepared voices and open them with Use for speech or Use as reference; voices you add stay until you reload.'],
    'voice.speak': ['用於說話', 'Use for speech'],
    'voice.reference': ['用作參考音', 'Use as reference'],
  };

  const FOLDERS = ['people', 'scenes', 'videos', 'voices'];
  const DEFAULT_FOLDER_FOR_KIND = { image: 'people', video: 'videos', audio: 'voices' };

  let appRegistry = null;
  let sessionItems = [];
  let toastRegion = null;
  let toastTimer = null;
  let datalistSerial = 0;
  let uploadTimer = null;
  let pendingRouteQuery = null;
  let manageContext = null;

  function captureHashQuery() {
    const raw = String(location.hash || '');
    const qIdx = raw.indexOf('?');
    if (qIdx === -1) return false;
    const cleanHash = raw.slice(0, qIdx) || '#/studio/text2img';
    const rawQuery = raw.slice(qIdx + 1);
    pendingRouteQuery = {
      path: cleanHash,
      rawQuery,
      params: new URLSearchParams(rawQuery),
    };
    history.replaceState(history.state, '', location.pathname + location.search + cleanHash);
    return true;
  }

  captureHashQuery();
  window.addEventListener('hashchange', captureHashQuery, true);

  function lang() {
    return window.WebComfyI18n?.language === 'en' ? 'en' : 'zh';
  }

  function formatPair(pair, vars = {}) {
    if (!pair) return ['', ''];
    let [zh, en] = pair;
    for (const [k, v] of Object.entries(vars)) {
      const token = '{' + k + '}';
      zh = zh.replaceAll(token, String(v ?? ''));
      en = en.replaceAll(token, String(v ?? ''));
    }
    return [zh, en];
  }

  function str(key, vars) {
    const [zh, en] = formatPair(STRINGS[key] || [key, key], vars);
    return lang() === 'en' ? en : zh;
  }

  function i18nNode(tag, zh, en, className = '') {
    const el = document.createElement(tag);
    if (className) el.className = className;
    el.dataset.i18nZh = zh;
    el.dataset.i18nEn = en;
    el.textContent = lang() === 'en' ? en : zh;
    return el;
  }

  function i18nPair(key, vars) {
    return formatPair(STRINGS[key] || [key, key], vars);
  }

  function consumePendingRoute(path) {
    const norm = p => String(p || '').replace(/^#/, '');
    if (!pendingRouteQuery || (path && norm(pendingRouteQuery.path) !== norm(path))) return null;
    const caseVal = pendingRouteQuery.params.get('case');
    const fromVal = pendingRouteQuery.params.get('from');
    const voiceVal = pendingRouteQuery.params.get('voice');
    const result = {
      case: caseVal,
      caseId: caseVal,
      from: fromVal,
      voice: voiceVal,
    };
    pendingRouteQuery = null;
    return result;
  }

  function i18nKeyNode(tag, key, className = '', vars) {
    const [zh, en] = formatPair(STRINGS[key] || [key, key], vars);
    return i18nNode(tag, zh, en, className);
  }

  function demoSet() {
    return appRegistry?.demo_set || null;
  }

  function materials() {
    return demoSet()?.materials || [];
  }

  function materialById(id) {
    return materials().find(m => m.id === id) || null;
  }

  function caseById(id) {
    return (demoSet()?.cases || []).find(c => c.id === id) || null;
  }

  function assetUrl(file) {
    if (typeof file !== 'string' || !file.trim() || file.includes('..')) return null;
    try {
      const url = new URL('assets/examples/' + file.replace(/^\/+/, ''), base);
      return url.origin === location.origin && /^https?:$/.test(url.protocol) ? url.href : null;
    } catch (_) {
      return null;
    }
  }

  function acceptToKind(accept) {
    const val = String(accept || '').trim().toLowerCase();
    if (!val || val === '*/*') return null;
    if (/^image\//.test(val)) return 'image';
    if (/^video\//.test(val)) return 'video';
    if (/^audio\//.test(val)) return 'audio';
    return null;
  }

  function reducedMotion() {
    return Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches);
  }

  function formatBytes(bytes) {
    const b = Number(bytes || 0);
    if (b >= 1024 * 1024) return (b / (1024 * 1024)).toFixed(1) + ' MB';
    return Math.round(b / 1024) + ' KB';
  }

  function session() {
    return sessionItems.map(item => ({ ...item }));
  }

  function addToSession(materialId, source) {
    if (!materialById(materialId)) return session();
    sessionItems = [
      { materialId, source: source || 'library', addedAt: Date.now() },
      ...sessionItems.filter(item => !(item.materialId === materialId && item.source === (source || 'library'))),
    ];
    if (manageContext?.root?.isConnected) manageContext.render();
    return session();
  }

  function toast(key, vars) {
    if (!document.body) return;
    if (!toastRegion || !toastRegion.isConnected) {
      toastRegion = document.createElement('div');
      toastRegion.className = 'demo-toast-region';
      toastRegion.setAttribute('aria-live', 'polite');
      toastRegion.setAttribute('role', 'status');
      document.body.appendChild(toastRegion);
    }
    clearTimeout(toastTimer);
    const pill = i18nKeyNode('div', key, 'app-toast demo-toast', vars);
    toastRegion.replaceChildren(pill);
    toastTimer = setTimeout(() => {
      if (toastRegion) toastRegion.replaceChildren();
    }, 2500);
  }

  function caseFor(form) {
    return form?.dataset?.demoCase || null;
  }

  function slotKeyForInput(input) {
    if (!input) return '';
    return (
      input.dataset.fieldId ||
      input.closest('[data-field-id]')?.dataset.fieldId ||
      input.closest('[data-upload-id]')?.dataset.uploadId ||
      input.closest('[data-upload-slot]')?.dataset.uploadSlot ||
      input.name ||
      'input'
    );
  }

  function findSlotInput(form, slot) {
    if (!form) return null;
    const inputs = [...form.querySelectorAll('input[type=file]')];
    for (const input of inputs) {
      if (slotKeyForInput(input) === slot) return input;
    }
    if (slot && inputs.length === 1) return inputs[0];
    return null;
  }

  function renderSlotPreview(slotContainer, dropPreview, slot, mat, form = null) {
    const ownerForm = form || slotContainer?.closest?.('[data-demo-id]') || null;
    const box = document.createElement('div');
    box.className = 'demo-slot-preview';
    box.dataset.slot = slot;
    box.dataset.materialId = mat.id;

    if (mat.kind === 'audio') {
      const audio = document.createElement('audio');
      audio.controls = true;
      audio.preload = 'metadata';
      const src = assetUrl(mat.file);
      if (src) audio.src = src;
      box.appendChild(audio);
    } else {
      const thumbFile = mat.thumb || mat.poster || (mat.kind === 'image' ? mat.file : null);
      const src = assetUrl(thumbFile);
      if (src) {
        const img = document.createElement('img');
        img.src = src;
        img.alt = lang() === 'en' ? mat.label_en : mat.label_zh;
        img.dataset.demoAltZh = mat.label_zh;
        img.dataset.demoAltEn = mat.label_en;
        box.appendChild(img);
      }
    }

    const meta = document.createElement('div');
    meta.className = 'demo-slot-preview-meta';
    meta.append(
      i18nNode('strong', mat.label_zh, mat.label_en, 'demo-slot-preview-label'),
      Object.assign(document.createElement('span'), { className: 'file-name demo-slot-preview-file', textContent: mat.file })
    );
    box.appendChild(meta);

    const escSlot = CSS.escape(slot);
    (ownerForm || slotContainer)?.querySelectorAll?.(`.demo-slot-remove[data-slot="${escSlot}"]`).forEach(node => node.remove());

    if (dropPreview) {
      if (!dropPreview._demoOriginal) dropPreview._demoOriginal = [...dropPreview.childNodes];
      dropPreview.replaceChildren(box);
    } else {
      slotContainer.querySelector(`.demo-slot-preview[data-slot="${escSlot}"]`)?.remove();
      slotContainer.appendChild(box);
    }

    if (ownerForm?.dataset?.demoCases === 'true') {
      box.dataset.docsInteractive = 'true';
      const removeBtn = i18nKeyNode('button', 'slot.remove', 'ghost small demo-slot-remove');
      removeBtn.type = 'button';
      removeBtn.dataset.slot = slot;
      removeBtn.dataset.docsInteractive = 'true';
      removeBtn.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        clearSlot(ownerForm, slot);
        ownerForm.dispatchEvent(new CustomEvent('demo:material-cleared', {
          bubbles: true,
          detail: { slot },
        }));
      });
      const hostLabel = dropPreview?.closest('label') || slotContainer?.closest('label') || (slotContainer?.matches?.('label') ? slotContainer : null);
      if (hostLabel) hostLabel.after(removeBtn);
      else box.appendChild(removeBtn);
    }
  }

  function clearSlot(form, slot) {
    if (!form) return;
    const input = findSlotInput(form, slot);
    const slotContainer =
      input?.closest('.upload-slot') ||
      input?.closest('label.dropzone') ||
      input?.closest('label') ||
      input?.parentElement ||
      form;
    const dropzone = input?.closest('.dropzone') || slotContainer?.querySelector('.dropzone');
    const dropPreview = dropzone?.querySelector('.drop-preview') || null;
    if (input) {
      input.value = '';
      delete input.dataset.demoMaterial;
    }
    form.querySelectorAll(`.demo-slot-remove[data-slot="${CSS.escape(slot)}"]`).forEach(node => node.remove());
    if (dropPreview?._demoOriginal) {
      dropPreview.replaceChildren(...dropPreview._demoOriginal);
    } else if (slotContainer) {
      slotContainer.querySelectorAll('.demo-slot-preview').forEach(node => node.remove());
    }
  }

  function fillSlot(form, slot, materialId, source = 'library') {
    const mat = materialById(materialId);
    if (!form || !mat) return false;
    const input = findSlotInput(form, slot);
    const slotContainer =
      input?.closest('.upload-slot') ||
      input?.closest('label.dropzone') ||
      input?.closest('label') ||
      input?.parentElement ||
      form;
    const dropzone = input?.closest('.dropzone') || slotContainer?.querySelector('.dropzone');
    const dropPreview = dropzone?.querySelector('.drop-preview') || null;
    if (input) {
      input.value = '';
      input.dataset.demoMaterial = mat.id;
    }
    if (dropzone) dropzone.classList.remove('required-empty');
    renderSlotPreview(slotContainer, dropPreview, slot || 'input', mat, form);
    slotContainer?.querySelector('.demo-url-refuse')?.remove();
    if (source !== 'case') addToSession(mat.id, source);
    form.dispatchEvent(new CustomEvent('demo:material-picked', {
      bubbles: true,
      detail: { slot: slot || slotKeyForInput(input), materialId: mat.id, source },
    }));
    return true;
  }

  function tileAriaLabel(mat, metaFallback) {
    const labelText = lang() === 'en' ? mat.label_en : mat.label_zh;
    const sizeText = formatBytes(mat.bytes ?? metaFallback?.bytes ?? 0);
    return `${labelText}, ${mat.file}, ${sizeText}`;
  }

  function createTileElement(tag, entry, options = {}) {
    const {
      selectedCase = null,
      displayedFeatureCases = [],
      slot = '',
      acceptedKind = null,
      filter = null,
      href = null,
      onPick = null,
    } = options;
    const mat = entry.material;
    const meta = entry.meta || mat || {};
    const kind = mat?.kind || entry.kind;
    const file = mat?.file || entry.file;
    const labelZh = mat?.label_zh || entry.labelZh || file;
    const labelEn = mat?.label_en || entry.labelEn || file;
    const kindAllowed = !acceptedKind || kind === acceptedKind;
    const filterReason = typeof filter === 'function' && mat ? filter(mat) : null;
    const allowed = kindAllowed && !filterReason;

    const tile = document.createElement(tag);
    tile.className = 'demo-tile';
    if (tag === 'button') tile.type = 'button';
    if (tag === 'a' && href) tile.href = href;
    if (mat?.id) tile.dataset.materialId = mat.id;
    tile.dataset.tileKind = kind;
    const sizeStr = formatBytes(meta.bytes ?? 0);
    const setAria = () => {
      const currentLabel = lang() === 'en' ? labelEn : labelZh;
      tile.setAttribute('aria-label', `${currentLabel}, ${file}, ${sizeStr}`);
    };
    setAria();
    tile._updateAria = setAria;

    const mediaBox = document.createElement('div');
    mediaBox.className = 'demo-tile-media';
    if (kind === 'audio') {
      const icon = document.createElement('span');
      icon.className = 'demo-audio-icon';
      icon.setAttribute('aria-hidden', 'true');
      icon.textContent = '♪';
      mediaBox.appendChild(icon);
    } else {
      const thumbFile = mat?.thumb || mat?.poster || entry.thumb || entry.poster || (kind === 'image' ? file : null);
      const src = assetUrl(thumbFile);
      if (src) {
        const img = document.createElement('img');
        img.src = src;
        img.alt = '';
        mediaBox.appendChild(img);
      }
    }
    tile.appendChild(mediaBox);

    const body = document.createElement('div');
    body.className = 'demo-tile-body';
    const titleEl = i18nNode('strong', labelZh, labelEn, 'demo-tile-label');
    const fileEl = document.createElement('span');
    fileEl.className = 'file-name demo-tile-file';
    fileEl.textContent = file;

    const facts = document.createElement('span');
    facts.className = 'demo-tile-facts';
    const parts = [document.createTextNode(sizeStr)];
    if (meta.width && meta.height) {
      parts.push(document.createTextNode(` · ${meta.width}×${meta.height}`));
    } else if (meta.duration_seconds != null) {
      const secs = Number(meta.duration_seconds).toFixed(1);
      parts.push(document.createTextNode(' · '), i18nNode('span', `${secs} 秒`, `${secs} s`, 'demo-tile-duration'));
    }
    facts.append(...parts);
    body.append(titleEl, fileEl, facts);

    const badges = document.createElement('div');
    badges.className = 'demo-tile-badges';
    if (mat && selectedCase?.inputs?.[slot] === mat.id) {
      badges.appendChild(i18nKeyNode('span', 'badge.case', 'demo-badge demo-badge-case'));
    }
    if (mat && displayedFeatureCases.some(c => c.inputs?.[slot] === mat.id)) {
      badges.appendChild(i18nKeyNode('span', 'badge.prerun', 'demo-badge demo-badge-prerun'));
    }
    if (badges.childElementCount) body.appendChild(badges);

    if (!allowed) {
      tile.setAttribute('aria-disabled', 'true');
      const reasonKey = !kindAllowed && acceptedKind ? `disabled.${acceptedKind}` : filterReason;
      if (reasonKey) body.appendChild(i18nKeyNode('span', reasonKey, 'demo-tile-disabled-reason'));
    }

    tile.appendChild(body);
    if (tag === 'button' && onPick) {
      tile.addEventListener('click', () => {
        if (tile.getAttribute('aria-disabled') === 'true') return;
        onPick(mat);
      });
    }
    return tile;
  }

  function syncSheetChromeI18n(root, dialog, titleKey) {
    const [titleZh, titleEn] = formatPair(STRINGS[titleKey]);
    const [closeZh, closeEn] = formatPair(STRINGS['common.close']);
    const heading = dialog.querySelector('.sheet-head h2');
    if (heading) {
      heading.dataset.i18nZh = titleZh;
      heading.dataset.i18nEn = titleEn;
      heading.textContent = lang() === 'en' ? titleEn : titleZh;
    }
    const closeBtn = dialog.querySelector('.sheet-close');
    if (closeBtn) {
      closeBtn.dataset.i18nZh = closeZh;
      closeBtn.dataset.i18nEn = closeEn;
      closeBtn.textContent = lang() === 'en' ? closeEn : closeZh;
    }
    root.querySelectorAll('.sheet-close,.sheet-scrim').forEach(node => {
      node.dataset.demoCloseLabel = titleKey;
      node.setAttribute('aria-label', `${lang() === 'en' ? closeEn : closeZh}: ${lang() === 'en' ? titleEn : titleZh}`);
    });
    window.WebComfyI18n?.translate(root);
  }

  function open({ form, slot, accept, mode = 'upload', opener, filter = null, folder = null } = {}) {
    if (!demoSet()) return false;
    const isLibrary = mode === 'library';
    const titleKey = isLibrary ? 'library.title' : 'drive.title';
    const acceptedKind = acceptToKind(accept);
    const featureId = form?.dataset?.demoId || null;
    const selectedCaseId = caseFor(form);
    const selectedCase = selectedCaseId ? caseById(selectedCaseId) : null;
    const displayedFeatureCases = (demoSet()?.cases || []).filter(c => c.display && c.feature_id === featureId);
    const allMaterials = materials();

    const container = document.createElement('div');
    container.className = isLibrary ? 'demo-modal demo-library-modal' : 'demo-modal demo-disk-modal';
    container.dataset.demoModalMode = isLibrary ? 'library' : 'upload';

    let activeDialog = null;
    let uploading = false;
    clearTimeout(uploadTimer);

    const closeModal = () => {
      const btn = activeDialog?.querySelector('.sheet-close');
      if (btn) btn.click();
    };

    const handlePick = (mat, sourceKind, progressHost) => {
      if (uploading) return;
      if (isLibrary) {
        closeModal();
        fillSlot(form, slot, mat.id, 'library');
        return;
      }
      uploading = true;
      container.querySelectorAll('.demo-tile').forEach(t => { t.disabled = true; });
      const barWrap = document.createElement('div');
      barWrap.className = 'demo-upload-progress';
      barWrap.setAttribute('role', 'progressbar');
      barWrap.setAttribute('aria-valuemin', '0');
      barWrap.setAttribute('aria-valuemax', '100');
      barWrap.setAttribute('aria-valuenow', '0');
      const barTrack = document.createElement('div');
      barTrack.className = 'demo-upload-track';
      const barFill = document.createElement('div');
      barFill.className = 'demo-upload-fill';
      barTrack.appendChild(barFill);
      barWrap.append(i18nKeyNode('span', 'upload.progress', 'demo-upload-label'), barTrack);
      progressHost.prepend(barWrap);

      const duration = reducedMotion() ? 0 : 800;
      requestAnimationFrame(() => {
        barWrap.setAttribute('aria-valuenow', '100');
        barFill.style.width = '100%';
      });
      uploadTimer = setTimeout(() => {
        uploadTimer = null;
        if (!activeDialog?.isConnected) return;
        closeModal();
        toast('toast.uploaded');
        fillSlot(form, slot, mat.id, sourceKind);
      }, duration);
    };

    if (!isLibrary) {
      container.appendChild(i18nKeyNode('p', 'drive.subtitle', 'demo-drive-subtitle'));
      const progressHost = document.createElement('div');
      progressHost.className = 'demo-upload-host';
      container.appendChild(progressHost);

      const layout = document.createElement('div');
      layout.className = 'demo-drive-layout';
      const folderNav = document.createElement('div');
      folderNav.className = 'demo-drive-folders';
      folderNav.setAttribute('role', 'tablist');

      const tileGrid = document.createElement('div');
      tileGrid.className = 'demo-drive-tiles demo-tiles';
      tileGrid.setAttribute('role', 'tabpanel');

      const caseMatId = selectedCase?.inputs?.[slot];
      const caseFolder = caseMatId ? materialById(caseMatId)?.folder : null;
      let activeFolder =
        (folder && FOLDERS.includes(folder) && folder) ||
        (caseFolder && FOLDERS.includes(caseFolder) && caseFolder) ||
        DEFAULT_FOLDER_FOR_KIND[acceptedKind] ||
        'people';

      const byFolder = new Map(FOLDERS.map(f => [f, allMaterials.filter(m => m.folder === f)]));
      const folderButtons = new Map();

      function renderFolderTiles() {
        const list = byFolder.get(activeFolder) || [];
        tileGrid.replaceChildren(
          ...list.map(mat =>
            createTileElement('button', { material: mat }, {
              selectedCase,
              displayedFeatureCases,
              slot,
              acceptedKind,
              filter,
              onPick: picked => handlePick(picked, 'upload', progressHost),
            })
          )
        );
        for (const [f, btn] of folderButtons) {
          btn.setAttribute('aria-selected', f === activeFolder ? 'true' : 'false');
          btn.classList.toggle('is-active', f === activeFolder);
        }
      }

      for (const folder of FOLDERS) {
        const list = byFolder.get(folder) || [];
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'ghost small demo-folder-btn';
        btn.dataset.folder = folder;
        btn.setAttribute('role', 'tab');
        btn.append(
          i18nKeyNode('span', `folder.${folder}`, 'demo-folder-name'),
          Object.assign(document.createElement('span'), { className: 'demo-folder-count', textContent: ` (${list.length})` })
        );
        btn.addEventListener('click', () => {
          if (uploading) return;
          activeFolder = folder;
          renderFolderTiles();
        });
        folderButtons.set(folder, btn);
        folderNav.appendChild(btn);
      }

      layout.append(folderNav, tileGrid);
      container.appendChild(layout);
      renderFolderTiles();
    } else {
      const acceptFilter = m => !acceptedKind || m.kind === acceptedKind;
      const groups = [];

      const seenSession = new Set();
      const sessionMats = [];
      for (const item of sessionItems) {
        if (seenSession.has(item.materialId)) continue;
        const m = materialById(item.materialId);
        if (m && acceptFilter(m)) {
          seenSession.add(m.id);
          sessionMats.push(m);
        }
      }
      if (sessionMats.length) {
        groups.push({ key: 'library.session', items: sessionMats });
      }

      const libMats = allMaterials.filter(m => m.library && acceptFilter(m));
      for (const folder of FOLDERS) {
        const items = libMats.filter(m => m.folder === folder);
        if (items.length) groups.push({ key: `folder.${folder}`, items });
      }
      const unfoldered = libMats.filter(m => m.folder == null);
      if (unfoldered.length) {
        groups.push({ key: 'library.results', items: unfoldered });
      }

      for (const group of groups) {
        const sec = document.createElement('section');
        sec.className = 'demo-library-group';
        sec.appendChild(i18nKeyNode('h3', group.key, 'demo-library-group-title'));
        const grid = document.createElement('div');
        grid.className = 'demo-tiles';
        for (const mat of group.items) {
          grid.appendChild(
            createTileElement('button', { material: mat }, {
              selectedCase,
              displayedFeatureCases,
              slot,
              acceptedKind,
              filter,
              onPick: picked => handlePick(picked, 'library', container),
            })
          );
        }
        sec.appendChild(grid);
        container.appendChild(sec);
      }
    }

    const fallbackOpener =
      opener ||
      findSlotInput(form, slot)?.closest('.dropzone') ||
      findSlotInput(form, slot) ||
      document.activeElement;

    window.openSheet({
      id: isLibrary ? 'demo-library-picker' : 'demo-disk-picker',
      title: str(titleKey),
      opener: fallbackOpener,
      content: container,
      className: 'demo-drive-sheet',
      onOpen({ root, dialog }) {
        activeDialog = dialog;
        syncSheetChromeI18n(root, dialog, titleKey);
      },
    });
    return true;
  }

  function openFromFileInput(input) {
    const form = input?.closest?.('[data-demo-id]');
    if (!form) return false;
    const dropzone = input.closest('.dropzone');
    const opener = dropzone || input;
    const options = {
      form,
      slot: slotKeyForInput(input),
      accept: input.accept || '',
      mode: 'upload',
      opener,
    };
    if (form.dataset.demoId === 'character-create') {
      const allowedIds = new Set();
      for (const s of demoSet()?.subjects || []) {
        if (!s.listed) continue;
        if (s.base) allowedIds.add(s.base);
        if (s.portrait) allowedIds.add(s.portrait);
      }
      options.folder = 'people';
      options.filter = mat => (allowedIds.has(mat.id) ? null : 'cc.only-demo');
    }
    return open(options);
  }

  function featureRankMap() {
    const map = new Map();
    const features = appRegistry?.features || [];
    features.forEach((f, idx) => map.set(f.id, idx));
    if (!map.has('character-create')) map.set('character-create', features.length + 1);
    return map;
  }

  function caseExecutedTimestamp(caseObj) {
    const dates = appRegistry?.demo_run_dates || {};
    let iso = null;
    if (caseObj.run_id) {
      iso = dates[caseObj.run_id] || null;
      if (!iso && appRegistry?.demos?.[caseObj.feature_id]?.evidence?.run_id === caseObj.run_id) {
        iso = appRegistry.demos[caseObj.feature_id].executed_at || null;
      }
    } else if (caseObj.stand_in_for) {
      const target = caseById(caseObj.stand_in_for);
      if (target?.run_id) iso = dates[target.run_id] || null;
    } else if (caseObj.audio?.length) {
      const audioMat = materialById(caseObj.audio[0]);
      if (audioMat?.produced_by) iso = dates[audioMat.produced_by] || null;
    }
    if (!iso) return null;
    const ms = Date.parse(iso);
    return Number.isFinite(ms) ? ms : null;
  }

  function sessionItemTargetHref(mat) {
    const ds = demoSet();
    const cases = ds?.cases || [];
    const ranks = featureRankMap();
    const displayed = cases
      .filter(c => c.display)
      .sort((a, b) => (ranks.get(a.feature_id) ?? 999) - (ranks.get(b.feature_id) ?? 999) || (a.order ?? 9999) - (b.order ?? 9999));

    if (mat.produced_by) {
      const producer = displayed.find(c => c.id === mat.produced_by);
      if (producer) return `#/studio/${encodeURIComponent(producer.feature_id)}?case=${encodeURIComponent(producer.id)}`;
    }
    const consumer = displayed.find(
      c => Object.values(c.inputs || {}).includes(mat.id) || (c.audio || []).includes(mat.id)
    );
    if (consumer) return `#/studio/${encodeURIComponent(consumer.feature_id)}?case=${encodeURIComponent(consumer.id)}`;
    const fallbackFeature = mat.kind === 'video' ? 'img2video' : mat.kind === 'audio' ? 'lipsync' : 'image-blend';
    return `#/studio/${fallbackFeature}`;
  }

  function renderManage(route, hooks) {
    if (!route || route.view !== 'library') return false;
    const ds = demoSet();
    if (!ds) return false;

    const ws = hooks.begin('workspace-route-root demo-manage-route');
    const matchedSubject = route.characterId
      ? (ds.subjects || []).find(s => s.id === route.characterId || (s.character_id && s.character_id === route.characterId)) || null
      : null;
    const subjectFilter = route.characterId ? (matchedSubject ? matchedSubject.id : route.characterId) : null;
    let kindFilter = ['image', 'video', 'audio'].includes(route.kind) ? route.kind : 'all';

    const panel = document.createElement('section');
    panel.className = 'panel demo-manage-panel';
    panel.append(
      i18nKeyNode('h1', 'manage.title', 'demo-manage-title'),
      i18nKeyNode('p', 'manage.notice', 'console-notice demo-manage-notice')
    );

    const filtersBar = document.createElement('div');
    filtersBar.className = 'demo-manage-filters';
    filtersBar.setAttribute('role', 'group');
    const filterKinds = ['all', 'image', 'video', 'audio'];
    const filterButtons = new Map();

    const sessionHost = document.createElement('section');
    sessionHost.className = 'demo-manage-section';
    const resultsHost = document.createElement('section');
    resultsHost.className = 'demo-manage-section';

    function renderSections() {
      for (const [k, btn] of filterButtons) {
        btn.setAttribute('aria-pressed', k === kindFilter ? 'true' : 'false');
        btn.classList.toggle('is-active', k === kindFilter);
      }

      // 1. Session section
      const sessionGrid = document.createElement('div');
      sessionGrid.className = 'demo-tiles';
      const filteredSession = sessionItems
        .map(item => ({ item, mat: materialById(item.materialId) }))
        .filter(({ mat }) => {
          if (!mat) return false;
          if (kindFilter !== 'all' && mat.kind !== kindFilter) return false;
          if (!subjectFilter) return true;
          if (mat.subject === subjectFilter) return true;
          const prodCase = mat.produced_by ? caseById(mat.produced_by) : null;
          return Boolean(prodCase?.subjects?.includes(subjectFilter));
        });

      sessionHost.replaceChildren(i18nKeyNode('h2', 'manage.session', 'demo-manage-section-title'));
      if (!filteredSession.length) {
        sessionHost.appendChild(i18nKeyNode('p', 'manage.session.empty', 'demo-manage-empty'));
      } else {
        for (const { mat } of filteredSession) {
          sessionGrid.appendChild(
            createTileElement('a', { material: mat }, {
              href: sessionItemTargetHref(mat),
            })
          );
        }
        sessionHost.appendChild(sessionGrid);
      }

      // 2. Results section: outputs of all displayed cases, deduplicated by file
      const ranks = featureRankMap();
      const displayedSorted = (ds.cases || [])
        .filter(c => c.display && (!subjectFilter || (c.subjects || []).includes(subjectFilter)))
        .sort(
          (a, b) =>
            (ranks.get(a.feature_id) ?? 999) - (ranks.get(b.feature_id) ?? 999) ||
            (a.order ?? 9999) - (b.order ?? 9999)
        );

      const seenFiles = new Map();
      for (const c of displayedSorted) {
        const ts = caseExecutedTimestamp(c);
        for (const out of c.outputs || []) {
          if (!out.file || seenFiles.has(out.file)) continue;
          seenFiles.set(out.file, {
            out,
            caseObj: c,
            material: out.material ? materialById(out.material) : null,
            timestamp: ts,
            caseOrder: c.order ?? 9999,
            featureRank: ranks.get(c.feature_id) ?? 999,
          });
        }
      }

      const entries = [...seenFiles.values()]
        .filter(entry => kindFilter === 'all' || entry.out.kind === kindFilter)
        .sort((a, b) => {
          const aKnown = a.timestamp !== null;
          const bKnown = b.timestamp !== null;
          if (aKnown !== bKnown) return aKnown ? -1 : 1;
          if (aKnown && b.timestamp !== a.timestamp) return b.timestamp - a.timestamp;
          if (a.caseOrder !== b.caseOrder) return a.caseOrder - b.caseOrder;
          return a.featureRank - b.featureRank;
        });

      const resultsGrid = document.createElement('div');
      resultsGrid.className = 'demo-tiles';
      const assetMetaMap = appRegistry?.demo_asset_meta || {};
      for (const entry of entries) {
        const href = `#/studio/${encodeURIComponent(entry.caseObj.feature_id)}?case=${encodeURIComponent(entry.caseObj.id)}`;
        resultsGrid.appendChild(
          createTileElement(
            'a',
            {
              material: entry.material,
              file: entry.out.file,
              kind: entry.out.kind,
              thumb: entry.out.thumb,
              poster: entry.out.poster,
              meta: entry.material || assetMetaMap[entry.out.file] || null,
              labelZh: entry.material?.label_zh || entry.caseObj.title_zh,
              labelEn: entry.material?.label_en || entry.caseObj.title_en,
            },
            { href }
          )
        );
      }
      resultsHost.replaceChildren(i18nKeyNode('h2', 'manage.results', 'demo-manage-section-title'), resultsGrid);
      window.WebComfyI18n?.translate(ws);
    }

    for (const k of filterKinds) {
      const btn = i18nKeyNode('button', `manage.filter.${k}`, 'ghost small demo-filter-btn');
      btn.type = 'button';
      btn.dataset.filterKind = k;
      btn.addEventListener('click', () => {
        kindFilter = k;
        renderSections();
      });
      filterButtons.set(k, btn);
      filtersBar.appendChild(btn);
    }

    panel.append(filtersBar, sessionHost, resultsHost);
    ws.appendChild(panel);
    manageContext = { root: ws, render: renderSections };
    renderSections();
    return true;
  }

  function decorateUrlFetch(urlBtn) {
    const box = urlBtn.closest('.url-fetch');
    const urlInput = box?.querySelector('.url-input');
    if (!box || !urlInput || urlInput.dataset.demoUrlEnhanced) return;
    urlInput.dataset.demoUrlEnhanced = 'true';
    const slot = urlBtn.dataset.urlFetch || '';
    const slotContainer = urlBtn.closest('.upload-slot');
    const accept = slotContainer?.querySelector('input[type=file]')?.accept || '';
    const acceptedKind = acceptToKind(accept);
    const aliases = materials().filter(m => m.url_alias && (!acceptedKind || m.kind === acceptedKind));
    if (aliases.length) {
      const listId = 'demo-url-aliases-' + (++datalistSerial);
      const datalist = document.createElement('datalist');
      datalist.id = listId;
      for (const m of aliases) {
        const option = document.createElement('option');
        option.value = m.url_alias;
        datalist.appendChild(option);
      }
      box.appendChild(datalist);
      urlInput.setAttribute('list', listId);
    }
  }

  function enhanceDom() {
    if (!demoSet()) return;
    for (const dropzone of document.querySelectorAll('[data-demo-id] label.dropzone')) {
      if (!dropzone.hasAttribute('tabindex')) dropzone.setAttribute('tabindex', '0');
      if (!dropzone.dataset.demoKeyboardBound) {
        dropzone.dataset.demoKeyboardBound = 'true';
        dropzone.addEventListener('keydown', event => {
          if (event.target?.closest?.('button,a,audio,video')) return;
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            const input = dropzone.querySelector('input[type=file]');
            if (input) input.click();
          }
        });
      }
    }
    for (const btn of document.querySelectorAll('[data-url-fetch]')) decorateUrlFetch(btn);
    for (const crawl of document.querySelectorAll('[data-crawl-picker]')) {
      if (!crawl.querySelector('.demo-picker-note')) {
        crawl.appendChild(i18nKeyNode('p', 'crawl.note', 'demo-picker-note'));
      }
    }
    for (const drive of document.querySelectorAll('[data-drive-picker]')) {
      if (!drive.querySelector('.demo-picker-note')) {
        drive.appendChild(i18nKeyNode('p', 'gdrive.note', 'demo-picker-note'));
      }
    }
  }

  function runUrlAliasFlow(box, urlBtn, urlInput, slot, mat) {
    const form = box.closest('[data-demo-id]');
    const status = box.querySelector('.url-status');
    box.querySelector('.demo-url-refuse')?.remove();
    urlInput.value = mat.url_alias;
    urlBtn.disabled = true;
    const [progZh, progEn] = formatPair(STRINGS['url.progress']);
    if (status) {
      status.className = 'url-status';
      status.dataset.i18nZh = progZh;
      status.dataset.i18nEn = progEn;
      status.textContent = lang() === 'en' ? progEn : progZh;
    }
    const delay = reducedMotion() ? 0 : 800;
    setTimeout(() => {
      if (!urlBtn.isConnected || !form?.isConnected) return;
      urlBtn.disabled = false;
      if (status) {
        const [doneZh, doneEn] = formatPair(STRINGS['url.toast']);
        status.className = 'url-status ok';
        status.dataset.i18nZh = doneZh;
        status.dataset.i18nEn = doneEn;
        status.textContent = lang() === 'en' ? doneEn : doneZh;
      }
      fillSlot(form, slot, mat.id, 'url');
      toast('url.toast');
    }, delay);
  }

  function handleUrlFetchClick(btn) {
    const box = btn.closest('.url-fetch');
    const urlInput = box?.querySelector('.url-input');
    const slot = btn.dataset.urlFetch || '';
    const slotContainer = btn.closest('.upload-slot');
    const accept = slotContainer?.querySelector('input[type=file]')?.accept || '';
    const acceptedKind = acceptToKind(accept);
    const aliases = materials().filter(m => m.url_alias && (!acceptedKind || m.kind === acceptedKind));
    const value = (urlInput?.value || '').trim();
    const match = aliases.find(m => m.url_alias === value);
    if (match) {
      runUrlAliasFlow(box, btn, urlInput, slot, match);
      return;
    }
    box?.querySelector('.demo-url-refuse')?.remove();
    const refuse = document.createElement('div');
    refuse.className = 'demo-url-refuse';
    refuse.setAttribute('role', 'alert');
    refuse.appendChild(i18nKeyNode('p', 'url.refuse', 'url-status err demo-url-refuse-msg'));
    const btnList = document.createElement('div');
    btnList.className = 'demo-url-aliases';
    for (const m of aliases) {
      const aliasBtn = document.createElement('button');
      aliasBtn.type = 'button';
      aliasBtn.className = 'ghost small demo-url-alias-btn';
      aliasBtn.textContent = m.url_alias;
      aliasBtn.addEventListener('click', () => runUrlAliasFlow(box, btn, urlInput, slot, m));
      btnList.appendChild(aliasBtn);
    }
    if (btnList.childElementCount) refuse.appendChild(btnList);
    box?.appendChild(refuse);
  }

  document.addEventListener('click', event => {
    if (!demoSet()) return;

    const pickerBtn = event.target.closest?.('[data-material-picker]');
    if (pickerBtn) {
      event.preventDefault();
      event.stopImmediatePropagation();
      const form = pickerBtn.closest('[data-demo-id]');
      const slot = pickerBtn.dataset.materialPicker || '';
      const accept = pickerBtn.closest('.upload-slot')?.querySelector('input[type=file]')?.accept || '';
      open({ form, slot, accept, mode: 'library', opener: pickerBtn });
      return;
    }

    const urlBtn = event.target.closest?.('[data-url-fetch]');
    if (urlBtn) {
      event.preventDefault();
      event.stopImmediatePropagation();
      handleUrlFetchClick(urlBtn);
      return;
    }

    const driveBox = event.target.closest?.('[data-drive-picker]');
    const clickedBtn = event.target.closest?.('button');
    if (driveBox && clickedBtn) {
      const labelText = clickedBtn.textContent.trim();
      const origZh = clickedBtn.dataset.i18nOrig || '';
      if (
        ['登入 Google Drive', 'Sign in to Google Drive'].includes(labelText) ||
        origZh === '登入 Google Drive'
      ) {
        event.preventDefault();
        event.stopImmediatePropagation();
        toast('gdrive.toast');
      }
    }
  }, true);

  window.addEventListener('webcomfy:language', () => {
    document.querySelectorAll('.demo-tile').forEach(tile => tile._updateAria?.());
    document.querySelectorAll('[data-demo-alt-zh]').forEach(img => {
      img.alt = lang() === 'en' ? img.dataset.demoAltEn : img.dataset.demoAltZh;
    });
    document.querySelectorAll('[data-demo-close-label]').forEach(node => {
      const key = node.dataset.demoCloseLabel;
      const [titleZh, titleEn] = formatPair(STRINGS[key]);
      const [closeZh, closeEn] = formatPair(STRINGS['common.close']);
      node.setAttribute('aria-label', `${lang() === 'en' ? closeEn : closeZh}: ${lang() === 'en' ? titleEn : titleZh}`);
    });
  });

  function install(registry) {
    appRegistry = registry;
    enhanceDom();
    const observer = new MutationObserver(() => enhanceDom());
    for (const id of ['workspace', 'sheetLayer']) {
      const host = document.getElementById(id);
      if (host) observer.observe(host, { childList: true, subtree: true });
    }
  }

  window.WebComfyDemoDrive = {
    install,
    open,
    openFromFileInput,
    renderManage,
    materials,
    materialById,
    assetUrl,
    session,
    addToSession,
    toast,
    fillSlot,
    clearSlot,
    caseFor,
    caseById,
    demoSet,
    str,
    i18nNode,
    i18nKeyNode,
    i18nPair,
    reducedMotion,
    consumePendingRoute,
    pendingRoute: () =>
      pendingRouteQuery
        ? {
            path: pendingRouteQuery.path,
            caseId: pendingRouteQuery.params.get('case'),
            from: pendingRouteQuery.params.get('from'),
          }
        : null,
  };
})();

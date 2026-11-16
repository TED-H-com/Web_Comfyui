/* A local, finite walkthrough of the real forms. Never uploads or queues work. */
(() => {
  'use strict';
  let app;
  const mounted = new Map();
  const language = () => window.WebComfyI18n?.language === 'en' ? 'en' : 'zh';
  const pick = (value, key) => language() === 'en' && value[key + '_en'] !== undefined ? value[key + '_en'] : value[key];
  function text(tag, zh, en, cls = '') {
    const node = document.createElement(tag); node.className = cls;
    node.dataset.i18nZh = zh; node.dataset.i18nEn = en;
    node.textContent = language() === 'en' ? en : zh; return node;
  }
  function button(zh, en, action) {
    const node = text('button', zh, en, 'ghost small'); node.type = 'button';
    node.addEventListener('click', action); return node;
  }
  function demoFor(id) { return app?.demos?.[id]; }
  function readPrompt(state) {
    const values = [...state.form.querySelectorAll('textarea,input[type=text],input:not([type])')]
      .filter(input => !/negative|url|seed/i.test(input.dataset.fieldId || input.name || '') && !input.closest('[data-docs-interactive]'))
      .map(input => input.value.trim()).filter(Boolean);
    return values.join(' · ') || pick(state.demo, 'prompt');
  }
  function fill(state, force = false) {
    const prompt = pick(state.demo, 'prompt');
    for (const input of state.form.querySelectorAll('textarea,input[type=text],input:not([type])')) {
      if (input.closest('[data-docs-interactive]')) continue;
      const key = input.dataset.fieldId || input.name || '';
      if (force || !state.filled.has(input) || input.value === state.filled.get(input)) {
        const field = state.feature?.fields?.find(item => item.id === key);
        let value = state.demo.prefill?.[key];
        if (language() === 'en') value = state.demo.prefill_en?.[key] ?? value;
        if (value === undefined && /negative/i.test(key)) value = field?.default || '';
        if (value === undefined && /seed/i.test(key)) value = field?.default ?? '42';
        if (value === undefined && /soundscape/i.test(key)) value = language() === 'en' ? 'Gentle wind and natural ambience.' : '輕柔的風聲與自然環境音。';
        if (value === undefined && /music/i.test(key)) value = language() === 'en' ? 'Soft instrumental music.' : '柔和的器樂配樂。';
        if (value === undefined) value = prompt;
        input.value = String(value); state.filled.set(input, input.value); input.dispatchEvent(new Event('input', {bubbles: true}));
      }
    }
    for (const field of state.feature?.fields || []) {
      let value = state.demo.prefill?.[field.id];
      if (language() === 'en') value = state.demo.prefill_en?.[field.id] ?? value;
      value ??= field.default;
      if (value === undefined) continue;
      for (const control of state.form.querySelectorAll('[data-field-id="' + CSS.escape(field.id) + '"]')) {
        if (control.matches('input[type=checkbox],select,input[type=range],input[type=number]')) {
          const checkbox = control.matches('input[type=checkbox]');
          const current = checkbox ? control.checked : control.value;
          if (!force && state.filled.has(control) && current !== state.filled.get(control)) continue;
          if (checkbox) control.checked = Boolean(value); else control.value = String(value);
          state.filled.set(control, checkbox ? control.checked : control.value);
          control.dispatchEvent(new Event('input', {bubbles: true}));
          control.dispatchEvent(new Event('change', {bubbles: true}));
        } else if (control.querySelector('button[data-value]')) {
          const current = control.querySelector('button[data-value].active')?.dataset.value;
          if (!force && state.filled.has(control) && current !== state.filled.get(control)) continue;
          const option = [...control.querySelectorAll('button[data-value]')].find(button => button.dataset.value === String(value));
          if (option) {option.click(); state.filled.set(control, String(value));}
        }
      }
    }
    for (const select of state.form.querySelectorAll('select')) {
      if (select.closest('[data-docs-interactive]')) continue;
      if (window.WebComfyDemoCases?.isCaseForm?.(state.form)) continue;
      if (!select.value && ![...select.options].some(option => option.value)) {
        const option = text('option', '示範案例', 'Demo example'); option.value = 'documentation-example'; select.append(option); select.value = option.value;
      }
    }
    for (const control of state.form.querySelectorAll('.generate-btn,button[type=submit]')) {
      control.disabled = state.running; control.dataset.demoSubmit = 'true';
      const zh = state.id === 'character-create' ? '建立角色（示範）' : '開始生成（示範）';
      const en = state.id === 'character-create' ? 'Create character (demo)' : 'Generate (demo)';
      control.dataset.i18nZh = zh; control.dataset.i18nEn = en; control.textContent = language() === 'en' ? en : zh;
    }
    window.WebComfyHardware?.decorate(state.id, state.form);
  }
  function mediaPreview(host, item) {
    host.replaceChildren();
    if (!item) {host.append(text('span', '尚未提供實測參考素材。', 'No tested reference input is available.')); return;}
    const media = document.createElement(item.kind === 'video' ? 'video' : item.kind === 'audio' ? 'audio' : 'img'); media.src = item.src;
    if (['video', 'audio'].includes(item.kind)) {
      media.controls = true; media.playsInline = true; media.preload = 'metadata';
      if (item.poster) media.poster = item.poster;
      media.setAttribute('aria-label', pick(item, 'alt') || 'Reference media');
    } else {media.alt = pick(item, 'alt') || 'Reference input'; media.loading = 'lazy'; media.decoding = 'async';}
    host.append(media);
  }
  function prepareUploads(state) {
    for (const input of state.form.querySelectorAll('input[type=file]')) {
      if (input.dataset.demoUpload) continue;
      input.dataset.demoUpload = 'true'; input.required = false;
      const slot = input.closest('.upload-slot') || input.closest('label') || input.parentElement;
      const box = document.createElement('div'); box.className = 'demo-input-example'; box.dataset.docsInteractive = 'true';
      const title = text('span', '參考輸入 · 實測使用的原圖', 'Reference input · original used in the recorded run', 'demo-input-label');
      const preview = document.createElement('div'); preview.className = 'demo-input-preview';
      const inputs = state.demo.inputs || [];
      const key = input.dataset.fieldId || input.closest('[data-field-id]')?.dataset.fieldId || input.closest('[data-upload-id]')?.dataset.uploadId || input.name;
      const item = inputs.find(item => item.slot && item.slot === key) || inputs.filter(item => !item.slot)[state.uploads.filter(slot => !slot.item?.slot).length];
      const slotState = {key, input, preview, title, item, assetUrl: null};
      slotState.reset = () => {
        state.result.demo.reset();
        slotState.assetUrl = null;
        window.WebComfyDemoDrive?.clearSlot(state.form, key);
        input.value = ''; preview.replaceChildren();
        title.dataset.i18nZh = item?.label || '參考輸入'; title.dataset.i18nEn = item?.label_en || 'Reference input';
        title.textContent = language() === 'en' ? title.dataset.i18nEn : title.dataset.i18nZh;
        mediaPreview(preview, item);
      };
      slotState.reset();
      box.append(title, preview, button('還原參考輸入', 'Restore reference input', slotState.reset));
      box.append(state.id === 'character-create'
        ? text('small', '選擇的照片會顯示在示範角色卡上，只在此頁預覽，不會上傳。', 'Your selected photo appears on the demo character card, stays on this page, and is not uploaded.')
        : state.demo.evidence?.kind !== 'local-comfyui' ? text('small', '這是預製示範的參考素材；自選檔案只供本機預覽，不會改變預製成果。', 'Prepared reference material; your own files are previewed locally and do not change the prepared result.')
        : text('small', '這是修改前的參考素材。按「開始生成（示範）」才會顯示固定實測成果；自選檔案只供本機預覽。', 'This is a reference input. Select Generate (demo) to reveal the recorded result; your own files are previewed locally only.'));
      slot.append(box); state.uploads.push(slotState);
    }
  }
  function mount(form, id, feature) {
    if (mounted.has(form) || !demoFor(id)) return;
    const demo = demoFor(id);
    // These native custom forms omit registry IDs on their hand-built textareas.
    const textKeys = id === 'voice-speak' ? ['sampleText','text'] : id === 'voice-clone' ? ['speech'] : [];
    [...form.querySelectorAll('textarea:not([data-field-id])')].forEach((input,index) => {if(textKeys[index]) input.dataset.fieldId = textKeys[index];});
    const state = {id, form, feature, demo, uploads: [], filled: new WeakMap(), running: false}; mounted.set(form, state);
    form.addEventListener('demo:material-picked', event => {
      const slotKey = event.detail?.slot;
      const mat = event.detail?.materialId ? window.WebComfyDemoDrive?.materialById(event.detail.materialId) : null;
      const matched = state.uploads.find(s => s.key === slotKey);
      if (matched) {
        matched.assetUrl = mat ? window.WebComfyDemoDrive?.assetUrl(mat.file) || null : null;
        matched.materialId = event.detail?.materialId || null;
        matched.subjectId = mat?.subject || null;
      }
    });
    form.addEventListener('demo:material-cleared', event => {
      const slotKey = event.detail?.slot ?? event.detail?.slotId;
      const matched = state.uploads.find(s => s.key === slotKey);
      if (matched) {
        matched.assetUrl = null;
        matched.materialId = null;
        matched.subjectId = null;
      }
    });
    form.dataset.demoId = id; form.noValidate = true;
    const caseMode = Boolean(window.WebComfyDemoCases?.isCaseForm?.(form));
    const bar = document.createElement('div'); bar.className = 'demo-prefill-bar'; bar.dataset.docsInteractive = 'true';
    bar.append(text('strong', id === 'character-create' ? '範例資料・尚未建立' : '示範案例', id === 'character-create' ? 'Example data · not created yet' : 'Demo example'), id === 'character-create'
      ? text('span', '填入名字、描述與參考照片，建立後進入暫存角色工作室。', 'Enter a name, description and reference photo, then open your session character workspace.')
      : demo.evidence?.kind !== 'local-comfyui' ? text('span', '先查看預填資料，再按示範按鈕體驗流程。結果為預製範例，不會依輸入即時生成，也不代表此工具的模型實測。', 'Review the example inputs, then try the demo action. Results are prepared examples, not generated from your input or benchmarks of this tool’s models.')
      : text('span', '先查看參考素材與預填指令，再按「開始生成（示範）」查看成品。此頁重播已完成的實測，不會即時生成。', 'Review the reference and prefilled instruction, then select Generate (demo) to reveal the result. This page replays a completed run, not live generation.'));
    bar.append(button('重新填入示範', 'Reset example inputs', () => {
      if (caseMode) {
        window.WebComfyDemoCases?.select?.(form, form.dataset.demoCase || undefined);
      } else {
        fill(state, true);
        state.uploads.forEach(slot => slot.reset());
        state.result.demo.reset();
      }
    }));
    form.prepend(bar);
    if (id === 'character-create' && !form.querySelector('[name=description]')) {
      const label = document.createElement('label'); label.className = 'field demo-description';
      const input = document.createElement('textarea'); input.name = 'description'; input.rows = 3; input.maxLength = 2000;
      label.append(text('span', '角色描述（示範）', 'Character description (demo)'), input);
      form.querySelector('.experimental-create-actions').before(label);
    }
    if (!form.querySelector('textarea,input[type=text],input:not([type])') && id !== 'voice-library') {
      const label = document.createElement('label'); label.className = 'field demo-description';
      label.append(text('span', '示範描述', 'Example description'));
      const input = document.createElement('textarea'); input.dataset.fieldId = 'demo-description'; input.rows = 2;
      label.append(input); bar.after(label);
    }
    if (!form.querySelector('.generate-btn,button[type=submit]') && id !== 'voice-library') {
      const submit = text('button', '開始生成（示範）', 'Generate (demo)', 'primary generate-btn'); submit.type = 'submit'; form.append(submit);
    }
    const resultOptions = caseMode
      ? {
          inline: true,
          hidePrompt: true,
          durationMs: () => window.WebComfyDemoCases.durationMs(form),
          renderResult: (container) => window.WebComfyDemoCases.renderResult(form, container),
        }
      : {inline: true, hidePrompt: true, suppressOutput: id === 'character-create' || id === 'voice-library'};
    const result = window.WebComfyExplorers.demoContent(demo, resultOptions);
    state.result = result; form.append(result);
    if (caseMode) {
      for (const input of form.querySelectorAll('input[type=file]')) { input.dataset.demoUpload = 'true'; input.required = false; }
      window.WebComfyDemoCases.attach(form, state);
    } else if (id === 'voice-library') {
      for (const input of form.querySelectorAll('input[type=file]')) { input.dataset.demoUpload = 'true'; input.required = false; }
      const panel = form.closest('.feature-panel');
      if (panel) window.WebComfyDemoCases?.attachVoiceLibrary?.(form, panel);
    } else {
      prepareUploads(state); fill(state, true);
    }
    window.WebComfyI18n?.translate(form);
  }
  function voiceDemoForm(panel, id) {
    const existing = panel.querySelector('form[data-docs-voice-form]');
    if (existing) return existing;
    const form = document.createElement('form'); form.className = 'gen-form'; form.dataset.docsVoiceForm = id;
    const field = (key, zh, en, multiline = false) => {
      const label = document.createElement('label'); label.className = 'field';
      const input = document.createElement(multiline ? 'textarea' : 'input');
      if (multiline) input.rows = 4; else input.type = 'text';
      input.name = key; input.dataset.fieldId = key;
      label.append(text('span', zh, en), input); form.append(label);
    };
    if (id === 'voice-batch') {
      field('text', '旁白段落（空行分段）', 'Narration segments (separate with blank lines)', true);
      form.append(text('p', '此處預覽已準備的分段旁白；不解析 PPTX、不建立實際批次。', 'Preview prepared narration segments here. No PPTX parsing or real batch is performed.', 'panel-note'));
    } else {
      const upload = document.createElement('label');upload.className = 'field upload-slot dropzone';upload.dataset.uploadId = 'voice';upload.dataset.uploadSlot = 'voice';
      const input = document.createElement('input');input.type = 'file';input.accept = 'audio/*';input.name = 'voice';input.dataset.fieldId = 'voice';
      const spanPair = window.WebComfyDemoDrive?.i18nPair?.('voice.slot') || ['加入聲音（從示範硬碟選）', 'Add a voice (from the demo disk)'];
      const pickPair = window.WebComfyDemoDrive?.i18nPair?.('voice.pick') || ['點一下，從示範硬碟選聲音', 'Click to pick a voice from the demo disk'];
      const notePair = window.WebComfyDemoDrive?.i18nPair?.('voice.note') || ['示範聲音庫：試聽預製聲音，用「用於說話」或「用作參考音」打開對應工具；你加入的聲音只保留到重新整理。', 'Demo voice library: audition prepared voices and open them with Use for speech or Use as reference; voices you add stay until you reload.'];
      const preview = document.createElement('div');preview.className = 'drop-preview';
      const hint = document.createElement('div');hint.className = 'drop-hint';
      const ico = document.createElement('span');ico.className = 'drop-ico';ico.textContent = '⬆️';ico.setAttribute('aria-hidden', 'true');
      hint.append(ico, text('span', pickPair[0], pickPair[1], 'drop-text'));
      preview.append(hint);
      upload.append(text('span', spanPair[0], spanPair[1], 'drop-label'), preview, input);form.append(upload);
      form.append(text('p', notePair[0], notePair[1], 'panel-note'));
    }
    panel.querySelector('.voice-batch-page,.voice-lib,.missing-box')?.remove();
    panel.append(form);return form;
  }
  function scan() {
    for (const [form, state] of mounted) {
      if (!form.isConnected) {state.result.demo.dispose(); mounted.delete(form);}
      else state.syncVoiceSelection?.();
    }
    if (!app || !window.WebComfyExplorers) return;
    for (const panel of document.querySelectorAll('.feature-panel')) {
      const id = panel.querySelector('[data-guide-feature]')?.dataset.guideFeature;
      if (!id) continue;
      const feature = app.features.find(item => item.id === id);
      let form = ['voice-batch','voice-library'].includes(id) ? voiceDemoForm(panel, id) : panel.querySelector('form');
      if (!form && !panel.querySelector('[data-demo-fallback]')) {
        form = document.createElement('form'); form.className = 'gen-form'; form.dataset.demoFallback = 'true'; panel.append(form);
      }
      if (form) mount(form, id, feature);
    }
    for (const form of document.querySelectorAll('.experimental-create-form')) {
      if (!mounted.has(form)) {
        form.before(window.WebComfyDocs.featureButton('character-create')); mount(form, 'character-create');
      }
    }
  }
  async function start(form) {
    const state = mounted.get(form); if (!state || state.running) return false;
    if (window.WebComfyHardware && !window.WebComfyHardware.canUse(state.id)) return false;
    state.running = true;
    const buttons = [...form.querySelectorAll('[data-demo-submit]')]; buttons.forEach(button => {button.disabled = true;});
    const pickedUpload = state.uploads.find(slot => slot.assetUrl);
    const characterValues = state.id === 'character-create' ? {
      name: form.elements.namedItem('name')?.value.trim(),
      description: form.elements.namedItem('description')?.value.trim(),
      photoUrl: pickedUpload?.assetUrl || null,
      materialId: pickedUpload?.materialId || null,
      subjectId: pickedUpload?.subjectId || null,
    } : {};
    try {
      const completed = state.result.demo.start(readPrompt(state), characterValues);
      state.result.scrollIntoView({behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start'});
      const finished = await completed;
      if (finished && state.id === 'character-create' && form.isConnected) {
        const character = window.WebComfyCharacterSession.create(characterValues);
        await window.WebComfyDocs.completeCharacter(character);
      }
    }
    finally {
      state.running = false;
      if (window.WebComfyDemoCases?.isCaseForm?.(form)) window.WebComfyDemoCases.applyGate(form);
      else {
        buttons.forEach(button => {button.disabled = false;});
        window.WebComfyHardware?.decorate(state.id, form);
      }
    }
    return true;
  }
  window.addEventListener('webcomfy:language', () => {for (const state of mounted.values()) if (!window.WebComfyDemoCases?.isCaseForm?.(state.form)) fill(state);});
  window.addEventListener('webcomfy:hardware', () => {
    for (const state of mounted.values()) {
      if (!window.WebComfyHardware.canUse(state.id)) state.result.demo.reset();
      window.WebComfyHardware.decorate(state.id, state.form);
    }
  });
  window.WebComfyTour = {
    install(catalog) {
      app = catalog; scan(); let queued = false;
      const observer = new MutationObserver(() => {if(!queued) {queued = true; queueMicrotask(() => {queued = false; scan();});}});
      for (const id of ['workspace', 'sheetLayer']) observer.observe(document.getElementById(id), {childList: true, subtree: true});
    },
    start, demoFor,
  };
})();

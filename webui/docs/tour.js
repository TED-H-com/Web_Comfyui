/* A local, finite walkthrough of the real forms. Never uploads or queues work. */
(() => {
  'use strict';
  let app, selectedVoice = null;
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
  const voiceTracks = () => app?.demos?.['voice-library']?.output?.audio_tracks || [];
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
        if (selectedVoice !== null && ['voice-speak','voice-clone','voxcpm-tts'].includes(state.id) && ['sampleText','promptText'].includes(key)) value = pick(voiceTracks()[selectedVoice], 'transcript');
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
      const slotState = {input, preview, title, item, url: null};
      slotState.reset = () => {
        state.result.demo.reset();
        releaseSlot(state, slotState);
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
    const state = {id, form, feature, demo, uploads: [], urls: new Set(), filled: new WeakMap(), running: false}; mounted.set(form, state);
    form.dataset.demoId = id; form.noValidate = true;
    const bar = document.createElement('div'); bar.className = 'demo-prefill-bar'; bar.dataset.docsInteractive = 'true';
    bar.append(text('strong', id === 'character-create' ? '範例資料・尚未建立' : '示範案例', id === 'character-create' ? 'Example data · not created yet' : 'Demo example'), id === 'character-create'
      ? text('span', '填入名字、描述與參考照片，建立後進入暫存角色工作室。', 'Enter a name, description and reference photo, then open your session character workspace.')
      : demo.evidence?.kind !== 'local-comfyui' ? text('span', '先查看預填資料，再按示範按鈕體驗流程。結果為預製範例，不會依輸入即時生成，也不代表此工具的模型實測。', 'Review the example inputs, then try the demo action. Results are prepared examples, not generated from your input or benchmarks of this tool’s models.')
      : text('span', '先查看參考素材與預填指令，再按「開始生成（示範）」查看成品。此頁重播已完成的實測，不會即時生成。', 'Review the reference and prefilled instruction, then select Generate (demo) to reveal the result. This page replays a completed run, not live generation.'));
    bar.append(button('重新填入示範', 'Reset example inputs', () => {fill(state, true); state.uploads.forEach(slot => slot.reset()); state.result.demo.reset();}));
    form.prepend(bar);
    if (id === 'character-create' && !form.querySelector('[name=description]')) {
      const label = document.createElement('label'); label.className = 'field demo-description';
      const input = document.createElement('textarea'); input.name = 'description'; input.rows = 3; input.maxLength = 2000;
      label.append(text('span', '角色描述（示範）', 'Character description (demo)'), input);
      form.querySelector('.experimental-create-actions').before(label);
    }
    if (!form.querySelector('textarea,input[type=text],input:not([type])')) {
      const label = document.createElement('label'); label.className = 'field demo-description';
      label.append(text('span', '示範描述', 'Example description'));
      const input = document.createElement('textarea'); input.dataset.fieldId = 'demo-description'; input.rows = 2;
      label.append(input); bar.after(label);
    }
    if (!form.querySelector('.generate-btn,button[type=submit]')) {
      const submit = text('button', '開始生成（示範）', 'Generate (demo)', 'primary generate-btn'); submit.type = 'submit'; form.append(submit);
    }
    const result = window.WebComfyExplorers.demoContent(demo, {inline: true, hidePrompt: true, suppressOutput: id === 'character-create'});
    state.result = result; form.append(result); prepareUploads(state); fill(state, true); voiceSelector(state);
    window.WebComfyI18n?.translate(form);
  }
  function addVoiceActions(state) {
    state.result.querySelectorAll('[data-voice-actions]').forEach(node => node.remove());
    const rows = state.result.querySelectorAll('.demo-audio-track');
    voiceTracks().forEach((track, index) => {
      const actions = document.createElement('div');actions.dataset.voiceActions = 'true';actions.className = 'workflow-actions';
      const choose = destination => {selectedVoice = index;location.hash = '#/studio/' + destination;};
      const speak = button('用於說話', 'Use for speech', () => choose('voice-speak'));
      const reference = button('用作參考音', 'Use as reference', () => choose('voice-clone'));
      speak.setAttribute('aria-label',pick(track,'title') + ' · ' + speak.textContent);
      reference.setAttribute('aria-label',pick(track,'title') + ' · ' + reference.textContent);
      actions.append(speak,reference);
      (rows[index] || state.result.querySelector('.demo-output') || state.result).append(actions);
    });
  }
  function voiceSelector(state) {
    if (!['voice-speak','voice-clone','voxcpm-tts'].includes(state.id) || !voiceTracks().length) return;
    const label = document.createElement('label');label.className = 'field demo-voice-selection';label.dataset.docsInteractive = 'true';
    label.append(text('span','預製聲音範例（只影響參考資料）','Prepared voice sample (reference data only)'));
    const select = document.createElement('select');select.dataset.demoVoiceSelector = 'true';
    voiceTracks().forEach((track,index) => {const option = text('option',track.title,track.title_en || track.title);option.value = String(index);select.append(option);});
    select.value = String(selectedVoice ?? 0);
    const preview = document.createElement('audio');preview.controls = true;preview.preload = 'metadata';
    const transcript = document.createElement('p');
    const apply = () => {
      selectedVoice = Number(select.value);const track = voiceTracks()[selectedVoice];
      preview.src = track.src;transcript.textContent = pick(track,'transcript');transcript.dataset.i18nZh = track.transcript;transcript.dataset.i18nEn = track.transcript_en || track.transcript;
      for (const key of ['sampleText','promptText']) {const input = state.form.querySelector('textarea[data-field-id="' + key + '"],input[data-field-id="' + key + '"]');if(input) {input.value = pick(track,'transcript');input.dispatchEvent(new Event('input',{bubbles:true}));state.filled.set(input,input.value);}}
      state.pendingVoiceSelection = 'docs-voice-' + selectedVoice;
      state.syncVoiceSelection = () => {
        if (!state.pendingVoiceSelection) return;
        for (const control of state.form.querySelectorAll('select:not([data-demo-voice-selector])')) if ([...control.options].some(option => option.value === state.pendingVoiceSelection)) {
          control.value = state.pendingVoiceSelection;state.pendingVoiceSelection = null;
          control.dispatchEvent(new Event('change',{bubbles:true}));break;
        }
      };
      state.syncVoiceSelection();
    };
    select.onchange = apply;label.append(select,preview,transcript,text('small','只帶入預製參考音與逐字稿；不會把自選音訊克隆成結果。','Only the prepared reference and transcript are selected; your own audio is not cloned into the result.'));
    state.form.querySelector('.demo-prefill-bar').after(label);apply();
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
      field('name', '聲音名稱', 'Voice name');field('source', '來源／備註', 'Source / notes');field('transcript', '參考音訊逐字稿', 'Reference transcript', true);
      const upload = document.createElement('label');upload.className = 'field';upload.dataset.uploadId = 'voice';
      const input = document.createElement('input');input.type = 'file';input.accept = 'audio/*';input.name = 'voice';input.dataset.fieldId = 'voice';
      upload.append(text('span', '本機音訊預覽（不會上傳）', 'Local audio preview (not uploaded)'),input);form.append(upload);
      form.append(text('p', '試聽預製聲音範例與逐字稿；此頁不會新增到實際聲音庫。', 'Audition prepared voice examples and transcripts. This page does not add to the installed voice library.', 'panel-note'));
    }
    panel.querySelector('.voice-batch-page,.voice-lib,.missing-box')?.remove();
    panel.append(form);return form;
  }
  function scan() {
    for (const [form, state] of mounted) {
      if (!form.isConnected) {state.result.demo.dispose(); state.urls.forEach(url => URL.revokeObjectURL(url)); mounted.delete(form);}
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
    const characterValues = state.id === 'character-create' ? {
      name: form.elements.namedItem('name')?.value.trim(), description: form.elements.namedItem('description')?.value.trim(),
      photo: state.uploads.find(slot => slot.file)?.file,
    } : {};
    try {
      const completed = state.result.demo.start(readPrompt(state), characterValues);
      state.result.scrollIntoView({behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start'});
      const finished = await completed;
      if (finished && state.id === 'voice-library' && form.isConnected) addVoiceActions(state);
      if (finished && state.id === 'character-create' && form.isConnected) {
        const character = window.WebComfyCharacterSession.create(characterValues);
        await window.WebComfyDocs.completeCharacter(character);
      }
    }
    finally {state.running = false; buttons.forEach(button => {button.disabled = false;}); window.WebComfyHardware?.decorate(state.id, form);}
    return true;
  }
  function localFile(input, file) {
    const state = mounted.get(input.closest('form'));
    const slot = state?.uploads.find(slot => slot.input === input); if (!slot || !file) return;
    state.result.demo.reset();
    releaseSlot(state, slot);
    if (!/^(image\/(png|jpeg|webp)|video\/(mp4|webm)|audio\/(mpeg|wav|x-wav|ogg|mp4|webm))$/.test(file.type) || (input.accept.startsWith('audio/') && !file.type.startsWith('audio/')) || (state.id === 'character-create' && !file.type.startsWith('image/')) || file.size > 25 * 1024 * 1024) {
      slot.preview.replaceChildren(text('span', '請選擇 25 MB 以內的 PNG、JPG、WebP、影片或音訊。', 'Choose a PNG, JPG, WebP, video or audio file under 25 MB.')); input.value = ''; return;
    }
    const url = URL.createObjectURL(file); state.urls.add(url); slot.url = url; slot.file = file;
    const media = document.createElement(file.type.startsWith('image/') ? 'img' : file.type.startsWith('video/') ? 'video' : 'audio');
    media.src = url; if (media.tagName !== 'IMG') {media.controls = true; media.preload = 'metadata';} else media.alt = file.name;
    slot.preview.replaceChildren(media); slot.title.removeAttribute('data-i18n-zh'); slot.title.removeAttribute('data-i18n-en'); slot.title.textContent = file.name;
  }
  function releaseSlot(state, slot) {
    slot.file = null;
    if (slot.url) {URL.revokeObjectURL(slot.url); state.urls.delete(slot.url); slot.url = null;}
  }
  window.addEventListener('webcomfy:language', () => {for (const state of mounted.values()) fill(state);});
  window.addEventListener('webcomfy:hardware', () => {
    for (const state of mounted.values()) {
      if (!window.WebComfyHardware.canUse(state.id)) state.result.demo.reset();
      window.WebComfyHardware.decorate(state.id, state.form);
    }
  });
  document.addEventListener('change', event => {
    if (event.target.matches('input[type=file][data-demo-upload]')) {event.stopImmediatePropagation(); localFile(event.target, event.target.files?.[0]);}
  }, true);
  document.addEventListener('dragover', event => {if(event.target.closest('[data-demo-id]')) event.preventDefault();}, true);
  window.WebComfyTour = {
    install(catalog) {
      app = catalog; scan(); let queued = false;
      const observer = new MutationObserver(() => {if(!queued) {queued = true; queueMicrotask(() => {queued = false; scan();});}});
      for (const id of ['workspace', 'sheetLayer']) observer.observe(document.getElementById(id), {childList: true, subtree: true});
    },
    start, demoFor,
    handleDrop(event) {
      const form = event.target.closest('[data-demo-id]'); if (!form) return false;
      const input = event.target.closest('.upload-slot,label')?.querySelector('input[type=file]') || form.querySelector('input[type=file]');
      if (input) localFile(input, event.dataTransfer?.files?.[0]); return true;
    },
  };
})();

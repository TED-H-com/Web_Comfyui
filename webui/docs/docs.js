/* Documentation adapter for the actual app shell. No server is connected. */
(() => {
  'use strict';
  const nativeFetch = window.fetch.bind(window);
  const base = new URL('./', location.href);
  const reply = (data, status = 200) => new Response(JSON.stringify(data), {status, headers: {'Content-Type': 'application/json'}});
  let registry = null, workflows = null;
  const ready = Promise.all(['app-guide.json', 'catalog.json'].map(async path => {
    const response = await nativeFetch(new URL(path, base), {credentials: 'omit', cache: 'no-cache'});
    if (!response.ok) throw Error('說明資料載入失敗，請重新整理頁面。');
    return response.json();
  })).then(([app, library]) => {registry = app; workflows = library; return app;});
  // Keep the application's draft/preferences APIs in memory on the docs origin.
  for (const name of ['localStorage', 'sessionStorage']) {
    const values = new Map();
    Object.defineProperty(window, name, {value: {
      getItem: key => values.get(String(key)) ?? null,
      setItem: (key, value) => values.set(String(key), String(value)),
      removeItem: key => values.delete(String(key)), clear: () => values.clear(),
      key: index => [...values.keys()][index] ?? null, get length() {return values.size;},
    }});
  }
  window.EventSource = class { close() {} addEventListener() {} removeEventListener() {} };
  window.WebSocket = class { constructor() {throw Error('說明站不建立即時服務連線');} };
  window.XMLHttpRequest = class { open() {throw Error('說明站不建立 API 連線');} };
  if (navigator.sendBeacon) navigator.sendBeacon = () => false;
  const emptyProvider = {ok: true, provider_transport_performed: false, credits_spent: 0, next_cursor: null};
  window.fetch = async (input, options = {}) => {
    const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url, location.href);
    const method = String(options.method || input?.method || 'GET').toUpperCase();
    await ready;
    if (url.origin !== location.origin || method !== 'GET') return reply({ok: false, error: '這是功能說明站；請在自己的 WebUI 執行此操作。'}, 405);
    // No fallthrough to native fetch: unknown calls fail closed in memory.
    switch (url.pathname) {
      case '/api/auth/status': return reply({configured: true, authenticated: true, lane: 'local', user: {email: 'documentation@example.invalid', displayName: '說明模式', role: 'admin', operator: true}});
      case '/api/features': return reply({studios: registry.studios, features: registry.features.map(feature => ({...feature, ready: true, runnable: !['storage', 'gated'].includes(feature.kind), missingModels: [], missingNodes: [], documentationOnly: true}))});
      case '/api/characters': return reply({characters: [], actions: []});
      case '/api/provider-characters': return reply({...emptyProvider, characters: []});
      case '/api/providers': return reply({...emptyProvider, providers: []});
      case '/api/assets': return reply({...emptyProvider, assets: [], items: []});
      case '/api/queue': return reply({jobs: [], terminal: [], recent_errors: [], summary: {running: 0, pending: 0, total: 0, external: 0}});
      case '/api/history': return reply({items: [], has_more: false});
      case '/api/posts': return reply({posts: [], items: []});
      case '/api/voices': return reply({voices: [], items: []});
      case '/api/personas': return reply({personas: []});
      case '/api/voice-batches': return reply({batches: [], items: []});
      case '/api/workflow-library': return reply(workflows);
    }
    if (url.href === new URL('catalog.json', base).href) return reply(workflows);
    if (url.href === new URL('app-guide.json', base).href) return reply(registry);
    if (workflows.items.some(item => item.download_url && url.href === new URL(item.download_url, base).href)) return nativeFetch(url, {credentials: 'omit'});
    return reply({ok: false, error: '此資料由安裝後的本機服務提供；說明站沒有個人資料。'}, 404);
  };
  const el = (tag, text = '', className = '') => {
    const node = document.createElement(tag); node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const link = (text, href) => {
    const node = el('a', text, 'ghost small workflow-link');
    node.href = href; node.target = '_blank'; node.rel = 'noopener noreferrer'; return node;
  };
  function rows(host, title, values, numbered = false) {
    if (!values?.length) return;
    const section = el('section', '', 'workflow-guide-section');
    const list = el(numbered ? 'ol' : 'ul');
    for (const value of values) list.append(el('li', typeof value === 'string' ? value : JSON.stringify(value)));
    section.append(el('h3', title), list); host.append(section);
  }
  const sourceLink = path => 'https://github.com/TED-H-com/Web_Comfyui/blob/main/' + path.split('/').map(encodeURIComponent).join('/');
  async function openFeature(id, opener = document.activeElement) {
    await ready;
    const guide = registry.guides.find(item => item.id === id);
    const feature = registry.features.find(item => item.id === id);
    if (!guide || !feature) return openHelp(opener);
    const content = el('div', '', 'workflow-details app-feature-guide');
    content.dataset.featureId = id;
    content.append(el('p', guide.summary, 'workflow-summary'));
    content.append(el('p', '以下對照目前 WebUI 的實際功能定義與組圖程式。這裡只展示流程，不代表本機模型已安裝或通過 GPU 驗證。', 'workflow-evidence'));
    const models = el('section', '', 'workflow-guide-section'); models.append(el('h3', '實際使用的 checkpoint 與模型'));
    if (guide.models?.length) {
      const table = el('table', '', 'app-guide-models');
      const head = el('thead'); const tr = el('tr'); ['用途', '模型／權重', '安裝位置'].forEach(value => tr.append(el('th', value))); head.append(tr);table.append(head);
      const body = el('tbody');
      for (const model of guide.models) {
        const row = el('tr');row.append(el('td', model.role || '模型'), el('td', model.name), el('td', model.directory_hint ? 'models/' + model.directory_hint : '依功能設定'));
        if (model.condition) row.children[1].append(el('small', model.condition));
        body.append(row);
      }
      table.append(body);models.append(table);
    } else models.append(el('p', '這項功能沒有固定的 checkpoint；請參考下方的組圖方式與外部依賴。'));
    content.append(models);
    rows(content, '對應哪個 template', (guide.templates || []).map(item => typeof item === 'string' ? item : [item.path, item.role].filter(Boolean).join(' — ')));
    rows(content, '會怎麼跑', guide.steps, true);
    rows(content, '使用什麼技巧', (guide.techniques || []).map(item => typeof item === 'string' ? item : [item.name, item.explanation].filter(Boolean).join('：')));
    rows(content, '執行前準備', guide.requirements);
    const settings = (feature.fields || []).filter(field => ['slider','number','checkbox','select','aspect'].includes(field.type) && !/seed/i.test(field.id));
    rows(content, '介面預設參數', settings.map(field => `${field.label}：${String(field.default ?? '依選擇')}`));
    const sources = el('div', '', 'workflow-actions');
    for (const file of guide.source_files || []) sources.append(link('查看 ' + file, sourceLink(file)));
    sources.append(link('安裝自己的 WebUI', sourceLink('README.zh-TW.md')));
    const share = link('分享這個功能的說明', new URL('./#/studio/' + encodeURIComponent(id) + '?guide=1', base).href); sources.append(share);
    content.append(sources);
    window.openSheet({id: 'app-feature-guide', title: `${feature.title} · 流程與模型`, opener, content, className: 'catalogue-detail-sheet'});
  }
  function openHelp(opener = document.activeElement) {
    const content = el('div', '', 'workflow-details');
    content.append(el('p', '這是 Web ComfyUI 的互動說明版，使用與本機版相同的頁面、按鈕和表單。', 'workflow-summary'));
    rows(content, '怎麼看這個介面', [
      '從「創作」切換文生圖、改圖、影片或聲音工具；點工具名稱或「查看流程與模型」會說明真正使用的模型與 template。',
      '表單可以切換設定以了解操作位置；生成、上傳、連接 API 與儲存按鈕只開啟說明。',
      '「工作庫」整理其他可下載工作流與來源方法；這些社群流程與創作表單使用的內建組圖程式分別標示。',
      '角色、素材、任務與供應商清單從空白開始。個人內容由安裝後的本機 WebUI 管理。',
    ]);
    rows(content, '開始實際使用', ['先安裝並啟動 ComfyUI。', '下載 Web_Comfyui、安裝 Python 依賴並設定 .env。', '執行 run.py --check，確認連線後啟動 run.py；首次登入設定自己的密碼。'], true);
    const actions = el('div', '', 'workflow-actions');
    actions.append(link('完整安裝步驟', sourceLink('README.zh-TW.md')), link('來源與授權', sourceLink('docs/workflow-coverage.md')), link('公開 Repo', 'https://github.com/TED-H-com/Web_Comfyui'));
    content.append(actions);
    window.openSheet({id: 'app-docs-help', title: '互動說明與安裝', opener, content, className: 'catalogue-detail-sheet'});
  }
  function selectedFeature() {
    return registry?.features.find(item => location.hash.split('?')[0] === '#/studio/' + encodeURIComponent(item.id));
  }
  function explainAction(opener) {
    const selected = selectedFeature(); return selected ? openFeature(selected.id, opener) : openHelp(opener);
  }
  window.WebComfyDocs = {
    ready, openFeature, openHelp,
    featureButton(id) {
      const wrap = el('div', '', 'app-guide-button'); const button = el('button', '查看流程與模型', 'ghost small'); button.type = 'button';
      button.onclick = () => openFeature(id, button); wrap.append(button); return wrap;
    },
    libraryOptions(openSheet) {return {openSheet, docsMode: true, catalogUrl: new URL('catalog.json', base).href, onSelect(id) {history.replaceState(history.state, '', '#/workflow/' + encodeURIComponent(id));}};},
  };
  document.addEventListener('submit', event => {event.preventDefault();event.stopImmediatePropagation();explainAction(event.submitter);}, true);
  document.addEventListener('click', event => {
    const target = event.target.closest('button,input,a');
    if (!target || target.closest('.sheet-layer .workflow-details')) return;
    if (target.matches('a[href="/admin"]') || (target.matches('button') && target.textContent.trim() === '登出')) {
      event.preventDefault();event.stopImmediatePropagation();openHelp(target);return;
    }
    if (target.matches('input[type=file],input[type=password],button[type=submit],.generate-btn,.assist-btn')) {
      event.preventDefault();event.stopImmediatePropagation();explainAction(target);
    }
  }, true);
  document.addEventListener('drop', event => {event.preventDefault();event.stopImmediatePropagation();explainAction(event.target);}, true);
  document.addEventListener('beforeinput', event => {if(event.target.matches('input[type=password]')) {event.preventDefault();event.stopImmediatePropagation();}}, true);
  const disableSecrets = () => document.querySelectorAll('input[type=password]').forEach(input => {input.disabled = true;input.value = '';input.placeholder = '請在本機 WebUI 設定';});
  new MutationObserver(disableSecrets).observe(document.documentElement, {childList: true, subtree: true});
  disableSecrets();
  if (!location.hash || location.hash === '#/') history.replaceState(null, '', '#/studio/text2img');
  // Keep former static help links useful; workflow deep links remain supported.
  if (['#/start', '#/about'].includes(location.hash)) {history.replaceState(null, '', '#/studio/text2img');ready.then(() => setTimeout(openHelp, 100));}
  const guideMatch = /^#\/studio\/([\w-]+)\?guide=1$/.exec(location.hash);
  if (guideMatch) {
    history.replaceState(null, '', '#/studio/' + guideMatch[1]);
    ready.then(() => setTimeout(() => openFeature(guideMatch[1]), 200));
  }
  ready.catch(() => {const message = document.getElementById('docsBanner'); if(message) message.textContent = '說明資料載入失敗，請重新整理頁面。';});
  document.getElementById('docsHelpButton')?.addEventListener('click', event => openHelp(event.currentTarget));
})();

/* Documentation adapter for the actual app shell. No server is connected. */
(() => {
  'use strict';
  const nativeFetch = window.fetch.bind(window);
  const base = new URL('../', location.href);
  const demoBase = new URL('./', location.href);
  const reply = (data, status = 200) => new Response(JSON.stringify(data), {status, headers: {'Content-Type': 'application/json'}});
  let registry = null, workflows = null;
  const ready = Promise.all(['app-guide.json', 'catalog.json'].map(async path => {
    const response = await nativeFetch(new URL(path, base), {credentials: 'omit', cache: 'no-cache'});
    if (!response.ok) throw Error('說明資料載入失敗，請重新整理頁面。');
    return response.json();
  })).then(([app, library]) => {
    registry = app; workflows = library;
    for (const item of workflows.items) if (item.download_url) item.download_url = new URL(item.download_url, base).href;
    for (const demo of Object.values(app.demos || {})) {
      for (const item of [...(demo.inputs || []), ...(demo.output?.images || [])]) {
        item.src = new URL(item.src, base).href;
        if (item.poster) item.poster = new URL(item.poster, base).href;
      }
      for (const key of ['src', 'poster']) if (demo.output?.video?.[key]) demo.output.video[key] = new URL(demo.output.video[key], base).href;
    }
    // Recorded runs may use dimensions outside the native form presets.
    // Add those choices only to this in-memory documentation catalogue.
    for (const feature of app.features || []) {
      const demo = app.demos?.[feature.id];
      if (demo?.evidence?.kind !== 'local-comfyui') continue;
      for (const field of feature.fields || []) {
        if (field.type !== 'aspect') continue;
        const value = String(demo.prefill?.[field.id] ?? '');
        const match = /^(\d+)x(\d+)$/.exec(value);
        if (!match || field.options?.some(option => `${option.width}x${option.height}` === value)) continue;
        const width = Number(match[1]), height = Number(match[2]);
        if (width < 1 || height < 1) continue;
        const label = `實測尺寸 ${width}×${height}`;
        (field.options ||= []).push({label, width, height});
        (app.translations ||= {})[label] = `Recorded size ${width}×${height}`;
      }
    }
    return app;
  });
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
      case '/api/characters': return reply({characters: window.WebComfyCharacterSession?.list() || [], actions: []});
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
  const english = value => registry?.translations?.[value] || window.WebComfyEnglish?.[value] || value;
  const label = (zh, en = english(zh)) => {
    const node = el('span'); node.dataset.i18nZh = zh; node.dataset.i18nEn = en;
    node.textContent = window.WebComfyI18n?.t(zh, en) || zh; return node;
  };
  function titledSheet(options, zh, en) {
    window.openSheet({...options, title: window.WebComfyI18n?.t(zh, en) || zh, onOpen({root, dialog}) {
      const heading = dialog.querySelector('.sheet-head h2');
      heading.dataset.i18nZh = zh; heading.dataset.i18nEn = en;
      root.querySelectorAll('.sheet-close,.sheet-scrim').forEach(node => {
        node.dataset.docsCloseZh = '關閉' + zh; node.dataset.docsCloseEn = 'Close ' + en;
        node.setAttribute('aria-label', window.WebComfyI18n?.t(node.dataset.docsCloseZh, node.dataset.docsCloseEn) || node.dataset.docsCloseZh);
      });
      window.WebComfyI18n?.translate(root);
    }});
  }
  window.addEventListener('webcomfy:language', () => {
    document.querySelectorAll('[data-docs-close-zh]').forEach(node => node.setAttribute('aria-label', window.WebComfyI18n?.t(node.dataset.docsCloseZh, node.dataset.docsCloseEn) || node.dataset.docsCloseZh));
    updateDocsIdentity();
  });
  function updateDocsIdentity() {
    const name = window.WebComfyI18n?.t('說明模式', 'Guide mode') || '說明模式';
    document.querySelectorAll('.identity-name').forEach(node => {
      if (['說明模式', 'Guide mode'].includes(node.textContent) && node.textContent !== name) node.textContent = name;
    });
  }
  const link = (text, href) => {
    const node = el('a', text, 'ghost small workflow-link');
    node.href = href; node.target = '_blank'; node.rel = 'noopener noreferrer'; return node;
  };
  function rows(host, title, values, numbered = false) {
    if (!values?.length) return;
    const section = el('section', '', 'workflow-guide-section');
    const list = el(numbered ? 'ol' : 'ul');
    for (const value of values) {const item = el('li'); if (value instanceof Node) item.append(value); else item.textContent = typeof value === 'string' ? value : JSON.stringify(value); list.append(item);}
    section.append(el('h3', title), list); host.append(section);
  }
  const sourceLink = path => 'https://github.com/TED-H-com/Web_Comfyui/blob/main/' + path.split('/').map(encodeURIComponent).join('/');
  const characterGuide = {
    id: 'character-create', title: '建立角色', summary: '輸入角色名稱並選擇照片，先建立角色資料與素材，再選擇適合的生成工作流。建立資料本身不會訓練模型。',
    models: [], templates: [{path: 'webui/static/app.js#createExperimentalCharacter', role: '角色建立與素材管理流程'}],
    steps: ['輸入角色名稱，選取一張清楚的參考照片。', '本機版會儲存角色設定與照片；此說明站只在瀏覽器預覽。', '進入角色工作室，依條件選擇寫真、影片或其他創作功能。'],
    techniques: ['角色資料與素材分開管理', '使用角色參考圖維持視覺一致性'], requirements: ['照片須為自己有權使用的素材。'], source_files: ['webui/static/app.js'],
  };
  const featureRecord = id => registry.features.find(item => item.id === id) || (id === 'character-create' ? characterGuide : null);
  const guideRecord = id => registry.guides.find(item => item.id === id) || (id === 'character-create' ? characterGuide : null);
  async function openFeature(id, opener = document.activeElement) {
    await ready;
    const guide = guideRecord(id);
    const feature = featureRecord(id);
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
        const row = el('tr'), name = el('td'), directory = el('td');name.append(el('code', model.name));directory.append(model.directory_hint ? el('code', 'models/' + model.directory_hint) : label('依功能設定'));row.append(el('td', model.role || '模型'), name, directory);
        if (model.condition) row.children[1].append(el('small', model.condition));
        body.append(row);
      }
      table.append(body);models.append(table);
    } else models.append(el('p', '這項功能沒有固定的 checkpoint；請參考下方的組圖方式與外部依賴。'));
    content.append(models);
    rows(content, '對應哪個 template', (guide.templates || []).map(item => {if (typeof item === 'string') return el('code', item);const row = el('span');row.append(el('code', item.path));if (item.role) row.append(document.createTextNode(' — '), label(item.role));return row;}));
    rows(content, '會怎麼跑', guide.steps, true);
    rows(content, '使用什麼技巧', (guide.techniques || []).map(item => {if (typeof item === 'string') return item;const row = el('span');row.append(label(item.name));if (item.explanation) row.append(document.createTextNode(': '), label(item.explanation));return row;}));
    rows(content, '執行前準備', guide.requirements);
    const settings = (feature.fields || []).filter(field => ['slider','number','checkbox','select','aspect'].includes(field.type) && !/seed/i.test(field.id));
    rows(content, '介面預設參數', settings.map(field => {const row = el('span');row.append(label(field.label), document.createTextNode(': '), field.default == null ? label('依選擇') : el('code', String(field.default)));return row;}));
    const sources = el('div', '', 'workflow-actions');
    for (const file of guide.source_files || []) {const source = link('', sourceLink(file));source.append(label('查看 ', 'View '), el('code', file));sources.append(source);}
    sources.append(link('安裝自己的 WebUI', sourceLink('README.zh-TW.md')));
    const share = link('分享這個功能的說明', new URL('./#/studio/' + encodeURIComponent(id) + '?guide=1', demoBase).href); sources.append(share);
    content.append(sources);
    titledSheet({id: 'app-feature-guide', opener, content, className: 'catalogue-detail-sheet'}, `${feature.title} · 流程與模型`, `${english(feature.title)} · Workflow and models`);
  }
  async function openGraph(id, opener = document.activeElement) {
    await ready;
    const graph = registry.graphs?.[id];
    if (!graph) return openFeature(id, opener);
    const title = featureRecord(id)?.title || id;
    titledSheet({id: 'app-node-graph', opener, content: window.WebComfyExplorers.graphContent(graph), className: 'catalogue-detail-sheet'}, `${title} · ComfyUI 範本與節點`, `${english(title)} · ComfyUI templates and nodes`);
  }
  async function openLibraryGraph(item, opener) {
    let graph;
    if (item.download_url) {
      const response = await fetch(new URL(item.download_url, base)); const raw = await response.json();
      if (!response.ok) return openHelp(opener);
      if (Array.isArray(raw.nodes)) {
        graph = {nodes: raw.nodes.map(node => ({id: node.id, type: node.type, title: node.title,
          inputs: {widgets: node.widgets_values || []}})),
          links: (raw.links || []).map(link => Array.isArray(link) ? {from: link[1], from_slot: link[2], to: link[3], to_slot: link[4]} : {from: link.origin_id, from_slot: link.origin_slot, to: link.target_id, to_slot: link.target_slot})};
      } else {
        graph = {nodes: [], links: []};
        for (const [id, node] of Object.entries(raw)) if (node?.class_type) {
          graph.nodes.push({id, type: node.class_type, inputs: node.inputs || {}});
          for (const [key, value] of Object.entries(node.inputs || {})) if (Array.isArray(value) && value.length === 2 && raw[value[0]]?.class_type) graph.links.push({from: value[0], from_slot: value[1], to: id, to_slot: key});
        }
      }
      Object.assign(graph, {kind: 'comfyui', source: item.upstream_url,
        evidence: '從這份可下載 JSON 的外層節點與連線繪製；群組內部請下載 JSON 查看。此處不執行工作流。',
        evidence_en: 'Drawn from the outer nodes and connections in this downloadable JSON. Download it to inspect nested groups. No workflow is executed here.'});
    } else {
      const steps = item.guide?.steps || [item.guide?.summary || item.title];
      graph = {kind: 'process', source: item.upstream_url, evidence: '依教學步驟整理的流程示意；沒有附帶可驗證的 ComfyUI 節點圖。',
        evidence_en: 'Process outline from the guide steps. No verified ComfyUI graph is included.',
        nodes: steps.map((step, index) => ({id: String(index), type: 'Guide step', title: step, inputs: {}})),
        links: steps.slice(1).map((_, index) => ({from: String(index), to: String(index + 1)}))};
    }
    window.openSheet({id: 'library-node-graph', title: item.title, opener, content: window.WebComfyExplorers.graphContent(graph), className: 'catalogue-detail-sheet'});
  }
  function openHelp(opener = document.activeElement) {
    const content = el('div', '', 'workflow-details');
    content.append(el('p', '這是 Web ComfyUI 的互動說明版，使用與本機版相同的頁面、按鈕和表單。', 'workflow-summary'));
    rows(content, '怎麼看這個介面', [
      '從「創作」切換文生圖、改圖、影片或聲音工具；點工具名稱或「查看流程與模型」會說明真正使用的模型與 template。',
      label('表單已填入示範案例；有 GPU 實測記錄的工具會分開展示原始輸入、實測設定與成果，未實測工具明示尚無成果。修改文字或預覽檔案不會觸發生成或上傳。', 'Forms are prefilled with examples. Tools with recorded GPU runs show original inputs, tested settings and results separately; untested tools explicitly show no result. Editing text or previewing files does not generate or upload anything.'),
      '查看 ComfyUI 範本與節點可探索節點連線與參數；API 或獨立工具則顯示已標明的處理流程。',
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
    ready, openFeature, openGraph, openHelp,
    renderCharacterRoute: (route, hooks) => window.WebComfyCharacterSession?.render(route, hooks),
    completeCharacter: character => window.completeDocumentationCharacter(character),
    createCharacterDemo() { location.hash = '#/characters'; openFeature('character-create'); },
    featureButton(id) {
      const wrap = el('div', '', 'app-guide-button'); wrap.dataset.guideFeature = id;
      const button = el('button', '查看流程與模型', 'ghost small'); button.type = 'button';
      const graph = el('button', '查看 ComfyUI 範本與節點', 'ghost small'); graph.type = 'button'; graph.dataset.action = 'view-graph';
      button.onclick = () => openFeature(id, button); graph.onclick = () => openGraph(id, graph); wrap.append(button, graph); return wrap;
    },
    libraryOptions(openSheet) {return {openSheet, docsMode: true, catalogUrl: new URL('catalog.json', base).href, onGraph: openLibraryGraph, onSelect(id) {history.replaceState(history.state, '', '#/workflow/' + encodeURIComponent(id));}};},
  };
  document.addEventListener('submit', event => {
    if (event.target.closest('[data-docs-interactive="true"]')) return;
    event.preventDefault();event.stopImmediatePropagation();
    if (event.target.dataset.demoId) window.WebComfyTour.start(event.target); else explainAction(event.submitter);
  }, true);
  document.addEventListener('click', event => {
    const target = event.target.closest('button,input,a');
    if (!target || target.closest('[data-docs-interactive="true"],.sheet-layer .workflow-details')) return;
    if (target.matches('a[href="/admin"]') || (target.matches('button') && ['登出', 'Sign out', 'Log out'].includes(target.textContent.trim()))) {
      event.preventDefault();event.stopImmediatePropagation();openHelp(target);return;
    }
    if (target.matches('input[type=file]') && target.closest('[data-demo-id]')) return;
    if (target.matches('input[type=file],input[type=password],button[type=submit],.generate-btn,.assist-btn')) {
      event.preventDefault();event.stopImmediatePropagation();
      const form = target.closest('[data-demo-id]');
      if (form && target.matches('button[type=submit],.generate-btn')) window.WebComfyTour.start(form); else explainAction(target);
    }
  }, true);
  document.addEventListener('drop', event => {event.preventDefault();event.stopImmediatePropagation();if (!window.WebComfyTour?.handleDrop(event)) explainAction(event.target);}, true);
  document.addEventListener('beforeinput', event => {if(event.target.matches('input[type=password]')) {event.preventDefault();event.stopImmediatePropagation();}}, true);
  const disableSecrets = () => {
    document.querySelectorAll('input[type=password]').forEach(input => {input.disabled = true;input.value = '';input.placeholder = '請在本機 WebUI 設定';});
    updateDocsIdentity();
  };
  new MutationObserver(disableSecrets).observe(document.documentElement, {childList: true, subtree: true});
  disableSecrets();
  // Wait for the real app script, including slow first visits; a fixed delay
  // can fire before its shared sheet controller has been defined.
  const appLoaded = new Promise(resolve => {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', resolve, {once: true});
    else resolve();
  });
  const showLoadError = () => {const message = document.getElementById('docsBanner'); if(message) message.textContent = '說明資料載入失敗，請重新整理頁面。';};
  const initialGuide = callback => Promise.all([ready, appLoaded]).then(() => callback()).catch(showLoadError);
  initialGuide(() => {
    window.WebComfyI18n?.register({...registry.translations, '示範角色': 'Demo characters', '只保留在本頁記憶體，重新整理後清除。': 'Kept in page memory only; cleared on refresh.'});
    const requestedLanguage = new URLSearchParams(location.search).get('lang');
    if (['en', 'zh-Hant'].includes(requestedLanguage)) window.WebComfyI18n?.setLanguage(requestedLanguage);
    window.WebComfyHardware?.install(registry);
    window.WebComfyCharacterSession?.install(registry);
    window.WebComfyTour?.install(registry);
  });
  if (!location.hash || location.hash === '#/') history.replaceState(null, '', '#/studio/text2img');
  // Keep former static help links useful; workflow deep links remain supported.
  if (['#/start', '#/about'].includes(location.hash)) {history.replaceState(null, '', '#/studio/text2img');initialGuide(openHelp);}
  const guideMatch = /^#\/studio\/([\w-]+)\?guide=1$/.exec(location.hash);
  if (guideMatch) {
    history.replaceState(null, '', '#/studio/' + guideMatch[1]);
    initialGuide(() => openFeature(guideMatch[1]));
  }
  ready.catch(showLoadError);
  document.getElementById('docsHelpButton')?.addEventListener('click', event => openHelp(event.currentTarget));
})();

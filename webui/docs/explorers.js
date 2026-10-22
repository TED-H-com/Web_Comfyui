/* Offline documentation explorers. Data becomes text, never executable markup. */
(() => {
  'use strict';
  const COPY = {
    '服務處理流程 · 這不是 ComfyUI 可執行節點圖。': 'Service process · This is not an executable ComfyUI graph.',
    'ComfyUI 節點與連線 · 點選節點查看來源參數。': 'ComfyUI nodes and connections · Select a node to inspect source parameters.',
    '此項目尚未提供可展示的節點。': 'No displayable nodes are provided for this item.',
    '選取一個節點': 'Select a node', '來源連線': 'Source connections',
    '來源未列出可展示的參數；沒有自行補上預設值。': 'The source lists no displayable parameters. No defaults have been invented.',
    '適合畫面': 'Fit view', '還原實際大小': 'Reset to actual size', '縮小節點圖': 'Zoom out', '放大節點圖': 'Zoom in',
    '操作範例': 'Example walkthrough', '範例提示詞': 'Example prompt',
    '模擬展示・預先準備範例，並非依輸入即時生成': 'Simulated demonstration · Prepared examples, not generated from your input',
    '播放模擬展示': 'Play simulation', '重設展示': 'Reset demo', '重播展示': 'Replay demo',
    '等待播放預製範例。': 'Ready to show a prepared example.',
    '讀取展示輸入': 'Read demonstration input', '開啟預製範例': 'Open prepared example', '展示處理階段': 'Show processing stages',
    '預製範例結果': 'Prepared example result',
    '預製動畫範例；播放的是已準備的影片。': 'Prepared animation example; this plays an existing video.',
    '預製分鏡圖；這組圖片不是即時影片生成。': 'Prepared storyboard; these images are not live video generation.',
    '音訊流程示意：以下為預製文字內容，本展示不合成或播放人聲。': 'Audio process demonstration: the transcript below is prepared. No speech is synthesized or played.',
    '此範例尚未附上可展示的素材。': 'No displayable media is attached to this example.',
    '你的輸入僅用來體驗操作，不會改變這份預製結果。': 'Your input is only for trying the interface; it does not change this prepared result.',
    '展示完成 · 這是預先準備的範例。': 'Demonstration complete · This is a prepared example.',
  };
  const translated = (zh, en = COPY[zh] || zh) => window.WebComfyI18n?.t?.(zh, en === zh ? undefined : en) ?? (document.documentElement.lang.startsWith('en') ? en : zh);
  function copy(node, zh, en = COPY[zh] || zh) {
    node.dataset.i18nZh = zh; node.dataset.i18nEn = en; node.dataset.explorerCopy = 'true'; node.textContent = translated(zh, en); return node;
  }
  function attribute(node, name, zh, en = COPY[zh] || zh) {
    const values = JSON.parse(node.dataset.explorerAttributes || '{}');
    values[name] = [zh, en]; node.dataset.explorerAttributes = JSON.stringify(values);
    node.setAttribute(name, translated(zh, en)); return node;
  }
  const el = (tag, text = '', cls = '') => {
    const node = document.createElement(tag); node.textContent = text;
    if (COPY[text]) copy(node, text);
    if (cls) node.className = cls;
    if (cls === 'graph-node-type' || cls === 'graph-parameters') node.dataset.i18nIgnore = 'true';
    return node;
  };
  const button = (text, label, fn, primary = false) => {
    const node = el('button', text, `${primary ? 'primary' : 'ghost'} small`);
    node.type = 'button'; attribute(node, 'aria-label', label || text); node.addEventListener('click', fn); return node;
  };
  const text = value => typeof value === 'string' ? value : value == null ? '' : String(value);
  function scalar(value) {
    try { return typeof value === 'string' ? value : JSON.stringify(value, null, 2) ?? '—'; }
    catch (_) { return '無法顯示此值'; }
  }
  function localAsset(value) {
    if (typeof value !== 'string' || !value.trim()) return null;
    try {
      const url = new URL(value, document.baseURI);
      return url.origin === location.origin && /^https?:$/.test(url.protocol) && !url.username && !url.password ? url.href : null;
    } catch (_) { return null; }
  }
  function removalGuard(root, cleanup) {
    let mounted = root.isConnected, ended = false;
    const observer = new MutationObserver(() => {
      if (root.isConnected) mounted = true;
      else if (mounted) dispose();
    });
    function dispose() { if (ended) return; ended = true; observer.disconnect(); cleanup(); }
    observer.observe(document.documentElement, {childList: true, subtree: true});
    return dispose;
  }

  function graphContent(data = {}) {
    const root = el('section', '', 'app-node-graph'); root.dataset.docsInteractive = 'true';
    const description = el('p', data.kind === 'process' ? '服務處理流程 · 這不是 ComfyUI 可執行節點圖。' : 'ComfyUI 節點與連線 · 點選節點查看來源參數。', 'explorer-lead');
    root.append(description);
    if (data.evidence) root.append(copy(el('p', '', 'explorer-evidence'), text(data.evidence), text(data.evidence_en || data.evidence)));
    const raw = Array.isArray(data.nodes) ? data.nodes : [];
    const nodes = [], byId = new Map();
    for (const item of raw) {
      if (!item || item.id == null || byId.has(String(item.id))) continue;
      const node = {id: String(item.id), type: text(item.type) || '未命名節點', title: text(item.title), title_en: text(item.title_en || item.title), inputs: item.inputs && typeof item.inputs === 'object' ? item.inputs : {}, ins: [], outs: [], level: 0};
      nodes.push(node); byId.set(node.id, node);
    }
    if (!nodes.length) { root.append(el('p', '此項目尚未提供可展示的節點。', 'explorer-empty')); return root; }
    const links = (Array.isArray(data.links) ? data.links : []).filter(link => link && byId.has(String(link.from)) && byId.has(String(link.to))).map(link => ({from: String(link.from), to: String(link.to), from_slot: text(link.from_slot ?? 0), to_slot: text(link.to_slot ?? 0)}));
    for (const link of links) {
      const source = byId.get(link.from), target = byId.get(link.to);
      if (!source.outs.includes(link.from_slot)) source.outs.push(link.from_slot);
      if (!target.ins.includes(link.to_slot)) target.ins.push(link.to_slot);
    }
    // Layer DAG nodes; disconnected or cyclic nodes stay visible in a separate layer.
    const degree = new Map(nodes.map(n => [n.id, links.filter(l => l.to === n.id).length]));
    const queue = nodes.filter(n => !degree.get(n.id)); const visited = new Set();
    for (let i = 0; i < queue.length; i++) {
      const node = queue[i]; visited.add(node.id);
      for (const link of links.filter(l => l.from === node.id)) {
        const next = byId.get(link.to); next.level = Math.max(next.level, node.level + 1);
        degree.set(next.id, degree.get(next.id) - 1); if (!degree.get(next.id)) queue.push(next);
      }
    }
    const lastLevel = Math.max(0, ...nodes.filter(n => visited.has(n.id)).map(n => n.level));
    for (const node of nodes) if (!visited.has(node.id)) node.level = lastLevel + 1;
    const columnY = new Map(), width = 244, gap = 88, margin = 28;
    for (const node of nodes) {
      node.height = 100 + Math.max(1, node.ins.length, node.outs.length) * 24;
      node.x = margin + node.level * (width + gap); node.y = columnY.get(node.level) || margin;
      columnY.set(node.level, node.y + node.height + 28);
    }
    const canvasWidth = Math.max(...nodes.map(n => n.x + width)) + margin;
    const canvasHeight = Math.max(...nodes.map(n => n.y + n.height)) + margin;
    const tools = el('div', '', 'explorer-toolbar'); attribute(tools, 'aria-label', '節點圖檢視工具', 'Node graph view controls');
    const zoomLabel = el('output', '100%', 'graph-zoom-label'); zoomLabel.setAttribute('aria-live', 'polite');
    const viewport = el('div', '', 'graph-viewport'); viewport.tabIndex = 0; attribute(viewport, 'aria-label', '節點畫布，可捲動；方向鍵平移，加減鍵縮放', 'Scrollable node canvas; arrow keys pan, plus and minus zoom');
    const surface = el('div', '', 'graph-surface'), canvas = el('div', '', 'graph-canvas');
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.classList.add('graph-wires'); svg.setAttribute('width', canvasWidth); svg.setAttribute('height', canvasHeight); svg.setAttribute('aria-hidden', 'true');
    canvas.style.width = `${canvasWidth}px`; canvas.style.height = `${canvasHeight}px`; canvas.append(svg); surface.append(canvas); viewport.append(surface);
    const inspector = el('section', '', 'graph-inspector'); attribute(inspector, 'aria-label', '選取節點參數', 'Selected node parameters');
    const inspectorTitle = el('h3', '選取一個節點'), inspectorBody = el('div'); inspector.append(inspectorTitle, inspectorBody);
    const controls = new Map(), paths = [];
    function select(node) {
      for (const [id, control] of controls) control.setAttribute('aria-pressed', String(id === node.id));
      paths.forEach(({path, link}) => path.classList.toggle('selected', link.from === node.id || link.to === node.id));
      copy(inspectorTitle, `${node.title || node.type} · #${node.id}`, `${node.title_en || node.type} · #${node.id}`); inspectorBody.replaceChildren(el('p', node.type, 'graph-node-type'));
      const values = Object.entries(node.inputs);
      if (!values.length) inspectorBody.append(el('p', '來源未列出可展示的參數；沒有自行補上預設值。', 'explorer-evidence'));
      else {
        const list = el('dl', '', 'graph-parameters');
        values.forEach(([key, value]) => {list.append(el('dt', key), el('dd', scalar(value)));}); inspectorBody.append(list);
      }
      const connections = links.filter(link => link.to === node.id || link.from === node.id);
      if (connections.length) {
        const list = el('ul', '', 'graph-connections'); connections.forEach(link => list.append(el('li', `#${link.from} [${link.from_slot}] → #${link.to} [${link.to_slot}]`)));
        inspectorBody.append(el('h4', '來源連線'), list);
      }
    }
    for (const node of nodes) {
      const card = el('button', '', 'graph-node'); card.type = 'button'; card.style.left = `${node.x}px`; card.style.top = `${node.y}px`; card.style.width = `${width}px`; card.style.height = `${node.height}px`;
      card.dataset.nodeId = node.id; attribute(card, 'aria-label', `節點 ${node.id}：${node.title || node.type}，查看參數`, `Node ${node.id}: ${node.title_en || node.type}, inspect parameters`); card.setAttribute('aria-pressed', 'false');
      const heading = el('span', '', 'graph-node-heading'); heading.append(el('small', `#${node.id}`), copy(el('strong'), node.title || node.type, node.title_en || node.type), el('span', node.type, 'graph-node-type')); card.append(heading);
      const ports = el('span', '', 'graph-ports');
      for (let i = 0; i < Math.max(1, node.ins.length, node.outs.length); i++) {
        const row = el('span', '', 'graph-port-row');
        for (const [slots, side] of [[node.ins, 'in'], [node.outs, 'out']]) {
          const port = el('span', slots[i] ?? '', `graph-port ${side}`);
          if (slots[i] !== undefined) {port.classList.add('connected'); attribute(port, 'title', `${side === 'in' ? '輸入' : '輸出'}插槽 ${slots[i]}`, `${side === 'in' ? 'Input' : 'Output'} slot ${slots[i]}`);}
          row.append(port);
        }
        ports.append(row);
      }
      card.append(ports, copy(el('span', '', 'graph-node-footer'), `查看參數 · ${Object.keys(node.inputs).length}`, `Parameters · ${Object.keys(node.inputs).length}`)); card.addEventListener('click', () => select(node)); canvas.append(card); controls.set(node.id, card);
    }
    for (const link of links) {
      const a = byId.get(link.from), b = byId.get(link.to);
      const x1 = a.x + width, y1 = a.y + 75 + a.outs.indexOf(link.from_slot) * 24, x2 = b.x, y2 = b.y + 75 + b.ins.indexOf(link.to_slot) * 24;
      const bend = Math.max(44, Math.abs(x2 - x1) * .45), path = document.createElementNS(svg.namespaceURI, 'path');
      const lane = 8 + paths.length % 3 * 6;
      const curve = Math.abs(a.level - b.level) > 1 || b.level <= a.level
        ? `M ${x1} ${y1} C ${x1 + 24} ${y1}, ${x1 + 24} ${lane}, ${x1 + 44} ${lane} L ${x2 - 44} ${lane} C ${x2 - 24} ${lane}, ${x2 - 24} ${y2}, ${x2} ${y2}`
        : `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`;
      path.setAttribute('d', curve); svg.append(path); paths.push({path, link});
    }
    let zoom = 1;
    function setZoom(value, center = true) {
      const prior = zoom, cx = (viewport.scrollLeft + viewport.clientWidth / 2) / prior, cy = (viewport.scrollTop + viewport.clientHeight / 2) / prior;
      zoom = Math.max(.2, Math.min(2, value)); canvas.style.transform = `scale(${zoom})`; surface.style.width = `${canvasWidth * zoom}px`; surface.style.height = `${canvasHeight * zoom}px`; zoomLabel.textContent = `${Math.round(zoom * 100)}%`;
      if (center) {viewport.scrollLeft = cx * zoom - viewport.clientWidth / 2; viewport.scrollTop = cy * zoom - viewport.clientHeight / 2;}
    }
    const fit = () => {setZoom(Math.min(1, (viewport.clientWidth - 24) / canvasWidth, (viewport.clientHeight - 24) / canvasHeight), false); viewport.scrollLeft = 0; viewport.scrollTop = 0;};
    tools.append(button('−', '縮小節點圖', () => setZoom(zoom / 1.2)), zoomLabel, button('+', '放大節點圖', () => setZoom(zoom * 1.2)), button('適合畫面', '', fit), button('100%', '還原實際大小', () => setZoom(1)));
    const help = copy(el('p', '', 'explorer-help'), `${nodes.length} 個節點 · ${links.length} 條連線。拖曳空白處或捲動畫布；Tab 選節點，Enter 查看參數。插槽標籤依來源提供。`, `${nodes.length} nodes · ${links.length} connections. Drag empty space or scroll; Tab selects nodes, Enter inspects parameters. Port labels come from the source.`);
    root.append(tools, viewport, help, inspector);
    if (data.source) {const source = el('p', '', 'explorer-evidence graph-source'); source.append(copy(el('span'), '來源：', 'Source: '), el('code', scalar(data.source))); root.append(source);}
    if (Array.isArray(data.notes)) {const list = el('ul', '', 'explorer-notes'); data.notes.forEach((note, index) => list.append(copy(el('li'), text(note), text(data.notes_en?.[index] || note)))); root.append(list);}
    viewport.addEventListener('keydown', event => {
      if (event.target !== viewport) return;
      if (event.key === '+' || event.key === '=') {event.preventDefault(); setZoom(zoom * 1.2);}
      else if (event.key === '-') {event.preventDefault(); setZoom(zoom / 1.2);}
      else if (event.key === '0') {event.preventDefault(); fit();}
      else if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) {event.preventDefault(); viewport.scrollLeft += event.key === 'ArrowRight' ? 80 : event.key === 'ArrowLeft' ? -80 : 0; viewport.scrollTop += event.key === 'ArrowDown' ? 80 : event.key === 'ArrowUp' ? -80 : 0;}
    });
    let drag = null;
    viewport.addEventListener('pointerdown', event => {if (event.pointerType !== 'mouse' || event.button || event.target.closest('button')) return; drag = {x: event.clientX, y: event.clientY, left: viewport.scrollLeft, top: viewport.scrollTop}; viewport.setPointerCapture(event.pointerId); viewport.classList.add('dragging');});
    viewport.addEventListener('pointermove', event => {if (!drag) return; viewport.scrollLeft = drag.left + drag.x - event.clientX; viewport.scrollTop = drag.top + drag.y - event.clientY;});
    const release = () => {drag = null; viewport.classList.remove('dragging');}; viewport.addEventListener('pointerup', release); viewport.addEventListener('pointercancel', release);
    setZoom(1, false); select(nodes[0]);
    // Keep node labels readable initially; overview remains a deliberate control.
    const frame = requestAnimationFrame(() => {if (root.isConnected) setZoom(1, false);});
    root.dispose = removalGuard(root, () => cancelAnimationFrame(frame));
    return root;
  }

  function demoContent(demo = {}, options = {}) {
    const root = el('section', '', `app-demo${options.inline ? ' app-demo-inline' : ''}`); root.dataset.docsInteractive = 'true'; root.dataset.demoId = text(demo.id);
    const evidence = demo.evidence || {}, verified = evidence.kind === 'local-comfyui' && Boolean(evidence.run_id);
    const prepared = evidence.kind === 'prepared-example';
    const unavailable = demo.output?.kind === 'unavailable';
    const character = demo.id === 'character-create' && demo.output?.kind === 'character' && evidence.kind === 'interface-simulation';
    const disclosure = verified
      ? copy(el('p', '', 'demo-disclosure'), 'GPU 實測記錄 · 此頁播放已完成的成果，不會即時生成', 'Recorded GPU result · This page replays a completed run; it does not generate live')
      : prepared ? copy(el('p', '', 'demo-disclosure'), '預製介面範例 · 非此工作流實測', 'Prepared interface example · not a verified run of this workflow')
      : character ? copy(el('p', '', 'demo-disclosure'), '角色卡模擬 · 使用表單內容在此頁預覽，不會保存或訓練模型', 'Character card simulation · Preview your form on this page; nothing is saved or trained')
      : unavailable ? copy(el('p', '', 'demo-disclosure'), '尚無此工作流的 GPU 實測成果', 'No recorded GPU result for this workflow yet')
      : el('p', '模擬展示・預先準備範例，並非依輸入即時生成', 'demo-disclosure');
    root.append(el('h3', text(demo.title) || '操作範例'), disclosure);
    if (demo.note) root.append(copy(el('p', '', 'explorer-evidence'), text(demo.note), text(demo.note_en || demo.note)));
    if (!options.inline && Array.isArray(demo.inputs) && demo.inputs.length) {
      const references = el('section', '', 'demo-references');
      references.append(copy(el('h4'), '參考輸入 · 生成前', 'Reference inputs · before generation'));
      const gallery = el('div', '', 'demo-gallery');
      demo.inputs.forEach(item => {const figure = imageFigure({...item, alt: item.label || item.alt, alt_en: item.label_en || item.alt_en}); if (figure) gallery.append(figure);});
      references.append(gallery); root.append(references);
    }
    if (verified) {
      const record = el('details', '', 'demo-run-record');
      record.append(copy(el('summary'), '實測提示詞與設定', 'Tested prompt and settings'));
      const values = el('dl', '', 'graph-parameters');
      for (const [key, zh, en] of [['prompt','實際提示詞','Exact prompt'], ['negative_prompt','反向提示詞','Negative prompt'], ['workflow','工作流','Workflow'], ['device','顯卡','GPU'], ['models','模型','Models'], ['seed','種子','Seed'], ['steps','步數','Steps'], ['width','寬度','Width'], ['height','高度','Height'], ['cfg','CFG','CFG'], ['guidance','引導強度','Guidance'], ['weight','參考強度','Reference weight'], ['sampler','取樣器','Sampler'], ['scheduler','排程器','Scheduler'], ['length','影格數','Frames'], ['fps','每秒影格','FPS'], ['duration_seconds','執行秒數（不含排隊）','Execution seconds (excluding queue)'], ['run_id','執行記錄','Run ID']]) {
        if (evidence[key] === undefined) continue;
        values.append(copy(el('dt'), zh, en), el('dd', Array.isArray(evidence[key]) ? evidence[key].join(' · ') : scalar(evidence[key])));
      }
      record.append(values); root.append(record);
    }
    const form = el('form', '', 'demo-form'); form.dataset.docsInteractive = 'true';
    const label = el('label'), input = el('textarea'); input.rows = 3; input.maxLength = 2000; input.value = text(options.initialPrompt ?? translated(text(demo.prompt), text(demo.prompt_en || demo.prompt))); input.setAttribute('aria-label', '範例提示詞'); label.append(el('span', '範例提示詞'), input);
    const titleNode = root.querySelector('h3'); if (demo.title_en) copy(titleNode, text(demo.title), text(demo.title_en));
    const run = button('播放模擬展示', '', () => start(input.value), true), resetButton = button('重設展示', '', reset);
    if (!options.hidePrompt) form.append(label, run);
    form.addEventListener('submit', event => {event.preventDefault(); start(input.value);});
    if (!options.hidePrompt) root.append(form);
    const echo = el('p', '', 'demo-prompt-echo'); echo.hidden = true;
    const state = el('p', '等待播放預製範例。', 'demo-state'); state.setAttribute('role', 'status'); state.setAttribute('aria-live', 'polite');
    const meter = el('div', '', 'demo-progress'); meter.setAttribute('role', 'progressbar'); attribute(meter, 'aria-label', '模擬進度，非即時 GPU 進度', 'Simulated progress, not live GPU progress'); meter.setAttribute('aria-valuemin', '0'); meter.setAttribute('aria-valuemax', '100'); meter.setAttribute('aria-valuenow', '0'); meter.append(el('span'));
    const progressLabel = copy(el('p', '', 'demo-progress-label'), '模擬進度 · 非即時 GPU 進度', 'Simulated progress · not live GPU progress');
    let steps = (Array.isArray(demo.steps) ? demo.steps : []).filter(s => typeof s === 'string').slice(0, 5);
    if (steps.length < 3) steps = ['讀取展示輸入', ...steps, '開啟預製範例'].slice(0, 5);
    if (steps.length < 3) steps.splice(1, 0, '展示處理階段');
    const list = el('ol', '', 'demo-stages'), stageNodes = steps.map(step => el('li', step)); stageNodes.forEach((node, i) => {if (demo.steps_en?.[i]) copy(node, steps[i], demo.steps_en[i]); list.append(node);});
    const output = el('div', '', 'demo-output'); output.hidden = true;
    const actions = el('div', '', 'explorer-toolbar'), replay = button('重播展示', '', () => start(lastPrompt, lastCharacter)); replay.hidden = true;
    actions.append(replay, resetButton); root.append(state, progressLabel, meter, output, echo, copy(el('p', '', 'demo-workflow-label'), '工作流步驟說明 · 此頁不執行', 'Workflow reference · not executed on this page'), list, actions);
    let timer = null, resolveRun = null, pendingRun = null, disposed = false, lastPrompt = input.value, sequence = 0, lastCharacter = {}, characterURL = null;
    function releaseCharacter() {if (characterURL) URL.revokeObjectURL(characterURL); characterURL = null;}
    function cancel() {sequence++; clearTimeout(timer); timer = null; pendingRun = null; run.disabled = false; replay.disabled = false; if (resolveRun) {resolveRun(false); resolveRun = null;} root.querySelectorAll('video,audio').forEach(media => media.pause());}
    function setProgress(percent) {
      meter.setAttribute('aria-valuenow', String(percent)); meter.firstElementChild.style.width = `${percent}%`;
      copy(progressLabel, `模擬進度 ${percent}% · 非即時 GPU 進度`, `Simulated progress ${percent}% · not live GPU progress`);
      stageNodes.forEach((node, i) => attribute(node, 'aria-label', `工作流步驟：${steps[i]}`, `Workflow reference: ${demo.steps_en?.[i] || steps[i]}`));
    }
    function imageFigure(image) {const src = localAsset(image?.src); if (!src) return null; const figure = el('figure');
      if (image.kind === 'video') {
        const video = el('video'); video.src = src; video.controls = true; video.playsInline = true; video.preload = 'metadata';
        const poster = localAsset(image.poster); if (poster) video.poster = poster;
        attribute(video, 'aria-label', text(image.alt) || '參考影片', text(image.alt_en) || 'Reference video');
        figure.append(video, copy(el('figcaption'), text(image.alt), text(image.alt_en || image.alt))); return figure;
      }
      const picture = el('img'); picture.src = src; picture.alt = translated(text(image.alt) || '預先準備的展示圖片', text(image.alt_en) || 'Prepared demonstration image'); picture.dataset.explorerAltZh = text(image.alt) || '預先準備的展示圖片'; picture.dataset.explorerAltEn = text(image.alt_en) || 'Prepared demonstration image'; picture.loading = 'lazy'; picture.decoding = 'async'; figure.append(picture); if (image.alt) figure.append(copy(el('figcaption'), text(image.alt), text(image.alt_en || image.alt))); return figure;}
    function renderOutput() {
      output.replaceChildren(verified ? copy(el('h4'), 'GPU 實測成果 · 生成後', 'Recorded GPU result · after generation') : prepared ? copy(el('h4'), '預製介面範例 · 非此工作流實測', 'Prepared interface example · not a verified run of this workflow') : character ? copy(el('h4'), '示範角色建立完成', 'Demo character created') : unavailable ? copy(el('h4'), '尚無實測成果', 'Result unavailable') : el('h4', '預製範例結果'));
      if (character) {
        const card = el('article', '', 'demo-character-card demo-character'), picture = el('img');
        const photo = lastCharacter.photo;
        if (photo instanceof File && /^image\/(png|jpeg|webp)$/.test(photo.type) && photo.size <= 25 * 1024 * 1024) characterURL = URL.createObjectURL(photo);
        const source = characterURL || localAsset(demo.inputs?.[0]?.src);
        if (source) {picture.src = source; attribute(picture, 'alt', '示範角色的參考照片', 'Demo character reference photo'); card.append(picture);}
        const details = el('div'), name = el('h5', text(lastCharacter.name) || translated(demo.prefill?.name, demo.prefill_en?.name)), description = el('p', text(lastCharacter.description) || translated(demo.prefill?.description, demo.prefill_en?.description), 'demo-result-text');
        name.dataset.i18nIgnore = 'true'; description.dataset.i18nIgnore = 'true';
        details.append(copy(el('span', '', 'demo-character-badge'), '示範角色', 'Demo character'), name, description,
          copy(el('p', '', 'explorer-evidence'), '這張角色卡只存在目前頁面，照片沒有上傳。重新整理或離開此表單後即清除。', 'This card exists only on this page. Your photo is not uploaded; refreshing or leaving this form clears it.'));
        const next = el('nav', '', 'explorer-toolbar'); attribute(next, 'aria-label', '接著體驗其他示範', 'Explore more demonstrations');
        for (const [id, zh, en] of [['character-angles', '查看肖像示範', 'Explore portraits'], ['img2video', '查看圖生影片示範', 'Explore image to video'], ['dance', '查看動作示範', 'Explore motion']]) {
          const link = copy(el('a', '', 'ghost small'), zh, en); link.href = '#/studio/' + id; next.append(link);
        }
        details.append(copy(el('p'), '下一步可查看以下工作流的固定案例：', 'Next, explore the recorded examples for these workflows:'), next); card.append(details); output.append(card); output.hidden = false; return;
      }
      if (unavailable) {
        output.append(copy(el('p', '', 'explorer-evidence'), text(demo.output.reason || '尚未完成此工作流的 GPU 實測。'), text(demo.output.reason_en || 'This workflow has not completed a verified GPU run.')));
        output.hidden = false; return;
      }
      const media = demo.output || {}, images = Array.isArray(media.images) ? media.images : [];
      if (media.kind === 'video' && localAsset(media.video?.src || media.src)) {
        const video = el('video'); video.controls = true; video.playsInline = true; video.preload = 'none'; video.src = localAsset(media.video?.src || media.src); const poster = localAsset(media.video?.poster); if (poster) video.poster = poster; attribute(video, 'aria-label', verified ? 'GPU 實測影片' : '預製動畫範例，非本次模型生成', verified ? 'Recorded GPU video' : 'Prepared animation, not generated by this interaction'); output.append(video);
      } else if (images.length) {
        const gallery = el('div', '', `demo-gallery${media.kind === 'character' ? ' demo-character' : ''}`);
        if (verified && media.comparison && demo.inputs?.[0]) {
          gallery.classList.add('demo-comparison');
          const before = imageFigure(demo.inputs[0]);
          if (before) {before.dataset.comparisonBefore = 'true'; before.prepend(copy(el('strong'), demo.id === 'img2img' ? '修改前 · 青綠白隊服' : '修改前 · 原始人物與背景', demo.id === 'img2img' ? 'Before · teal and white uniform' : 'Before · original subject and background')); gallery.append(before);}
        }
        images.forEach(image => {const figure = imageFigure(image); if (figure) {if (media.comparison) figure.prepend(copy(el('strong'), demo.id === 'img2img' ? '修改後 · 紅色上衣、深藍短褲' : '修改後 · 保留人物、更換背景', demo.id === 'img2img' ? 'After · red jersey and navy shorts' : 'After · original subject, new background')); gallery.append(figure);}}); output.append(gallery);
        if (media.kind === 'video') output.append(el('p', '預製分鏡圖；這組圖片不是即時影片生成。', 'explorer-evidence'));
      }
      const tracks = Array.isArray(media.audio_tracks) ? media.audio_tracks : media.audio ? [typeof media.audio === 'string' ? {src: media.audio} : media.audio] : [];
      const audioList = el('div', '', 'demo-audio-list');
      tracks.forEach((track, index) => {
        const src = localAsset(track?.src); if (!src) return;
        const item = el('section', '', 'demo-audio-track');
        const title = text(track.title) || `聲音範例 ${index + 1}`, titleEn = text(track.title_en) || text(track.title) || `Voice example ${index + 1}`;
        item.append(copy(el('h5'), title, titleEn));
        if (track.style) item.append(copy(el('p', '', 'demo-audio-style'), text(track.style), text(track.style_en || track.style)));
        const audio = el('audio'); audio.controls = true; audio.preload = 'metadata'; audio.src = src;
        attribute(audio, 'aria-label', title, titleEn);
        audio.addEventListener('play', () => root.querySelectorAll('audio,video').forEach(other => {if (other !== audio) other.pause();}));
        item.append(audio);
        if (track.transcript) {
          const transcript = el('details', '', 'demo-audio-transcript');
          transcript.append(copy(el('summary'), '逐字稿', 'Transcript'), copy(el('p', '', 'demo-result-text'), text(track.transcript), text(track.transcript_en || track.transcript)));
          item.append(transcript);
        }
        audioList.append(item);
      });
      if (audioList.childElementCount) output.append(audioList);
      if (media.text) output.append(copy(el('p', '', 'demo-result-text'), text(media.text), text(media.text_en || media.text)));
      if (!images.length && !media.text && !audioList.childElementCount && !localAsset(media.video?.src || media.src)) output.append(el('p', '此範例尚未附上可展示的素材。'));
      output.append(prepared ? copy(el('p', '', 'explorer-evidence'), '修改輸入或選擇自己的檔案不會改變這份預製範例；播放不會執行模型。', 'Editing inputs or selecting your files does not change this prepared example; playback does not run a model.') : copy(el('p', '', 'explorer-evidence'), '修改文字或預覽自己的檔案不會改變這份記錄的成果。', 'Editing text or previewing your files does not change this recorded result.')); output.hidden = false;
    }
    function reset() {cancel(); releaseCharacter(); lastCharacter = {}; root.hidden = Boolean(options.inline); run.disabled = false; replay.hidden = true; output.hidden = true; output.replaceChildren(); echo.hidden = true; verified ? copy(state, '可開啟已完成的 GPU 實測記錄。', 'Ready to open the recorded GPU result.') : character ? copy(state, '填妥表單後即可建立示範角色卡。', 'Complete the form to create a demo character card.') : unavailable ? copy(state, '此工作流尚無實測成果。', 'No recorded result for this workflow yet.') : copy(state, '等待播放預製範例。'); setProgress(0); stageNodes.forEach(node => node.classList.remove('active')); root.dataset.state = 'idle';}
    function start(prompt = input.value, characterValues = {}) {
      if (disposed) return Promise.resolve(false);
      if (pendingRun) return pendingRun;
      cancel(); releaseCharacter(); lastCharacter = characterValues; root.hidden = false; const current = sequence; lastPrompt = text(prompt).slice(0, 2000); input.value = lastPrompt;
      copy(echo, `這次體驗輸入：${lastPrompt || '（未填寫）'}`, `Your demonstration input: ${lastPrompt || '(empty)'}`); echo.hidden = false; output.hidden = true; output.replaceChildren(); replay.hidden = true; run.disabled = true; replay.disabled = true; root.dataset.state = 'running';
      const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      root.classList.toggle('demo-reduced-motion', reduced);
      pendingRun = new Promise(resolve => {
        resolveRun = resolve;
        function advance(tick) {
          if (disposed || current !== sequence || !root.isConnected) {cancel(); return;}
          timer = null;
          setProgress(Math.round(tick / 24 * 100));
          if (tick === 24) {if (!options.suppressOutput) renderOutput(); verified ? copy(state, '已開啟 GPU 實測記錄。', 'Recorded GPU result opened.') : character ? copy(state, '示範角色卡已完成，可繼續探索工作流。', 'Demo character card complete. You can now explore the workflows.') : unavailable ? copy(state, '此工作流尚無實測成果。', 'No recorded result for this workflow yet.') : copy(state, '展示完成 · 這是預先準備的範例。'); root.dataset.state = 'complete'; run.disabled = false; replay.disabled = false; replay.hidden = false; resolveRun = null; pendingRun = null; resolve(true); root.dispatchEvent(new CustomEvent('demo-complete', {bubbles: true, detail: {id: text(demo.id)}})); return;}
          copy(state, '正在播放模擬流程，完成後顯示範例成果。', 'Playing the simulated flow. The example result appears when complete.');
          timer = setTimeout(() => advance(reduced || unavailable ? 24 : tick + 1), reduced || unavailable ? 0 : 100);
        }
        advance(0);
      });
      return pendingRun;
    }
    root.demo = {start, reset, dispose: removalGuard(root, () => {disposed = true; cancel(); releaseCharacter(); lastCharacter = {};})};
    if (unavailable) {meter.hidden = true; progressLabel.hidden = true;}
    reset(); return root;
  }
  window.addEventListener('webcomfy:language', () => {
    document.querySelectorAll('[data-explorer-attributes]').forEach(node => {for (const [name, [zh, en]] of Object.entries(JSON.parse(node.dataset.explorerAttributes))) node.setAttribute(name, translated(zh, en));});
    document.querySelectorAll('[data-explorer-alt-zh]').forEach(node => {node.alt = translated(node.dataset.explorerAltZh, node.dataset.explorerAltEn);});
    document.querySelectorAll('[data-explorer-copy]').forEach(node => {node.textContent = translated(node.dataset.i18nZh, node.dataset.i18nEn);});
  });
  window.WebComfyExplorers = Object.freeze({graphContent, demoContent});
})();

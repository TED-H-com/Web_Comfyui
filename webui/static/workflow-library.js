/* One workflow library for the local app and the public documentation site. */
window.WorkflowLibrary = (() => {
  const el = (tag, text = '', cls = '') => {
    const node = document.createElement(tag); node.textContent = text;
    if (cls) node.className = cls;
    return node;
  };
  const link = (text, url) => {
    const node = el('a', text, 'ghost small workflow-link');
    try {
      const target = new URL(url, location.href);
      if (!['http:', 'https:'].includes(target.protocol) || target.username || target.password) return null;
      node.href = target.href;
    } catch (_) { return null; }
    node.target = '_blank'; node.rel = 'noopener noreferrer';
    return node;
  };
  const list = (host, title, values, ordered = false) => {
    if (!values?.length) return;
    const section = el('section', '', 'workflow-guide-section');
    const rows = el(ordered ? 'ol' : 'ul');
    values.forEach(value => rows.append(el('li', value)));
    section.append(el('h3', title), rows); host.append(section);
  };
  async function request(path, options) {
    const response = await fetch(path, {credentials: 'same-origin', ...options});
    const data = await response.json();
    if (!response.ok) throw Error(data.error || '讀取失敗');
    return data;
  }
  function mount(host, {openSheet, canLoad = false, docsMode = false, catalogUrl = './catalog.json', onSelect = () => {}} = {}) {
    let items = [], page = 0, comfyUrl = 'http://127.0.0.1:8188';
    const panel = el('section', '', 'panel workflow-library');
    const heading = el('div', '', 'workflow-heading');
    const title = el('div');
    title.append(el('h1', '工作庫'), el('p', docsMode ? '先了解用途、執行步驟與技巧，再選擇適合自己的工作流。' : '依用途選擇工作流，檢查模型與節點，再送到自己的 ComfyUI。', 'sub'));
    const check = el('button', docsMode ? '重新載入' : '檢查 ComfyUI 節點', 'ghost small'); check.type = 'button';
    heading.append(title, check);
    const toolbar = el('div', '', 'catalogue-toolbar');
    const search = el('input'); search.type = 'search'; search.placeholder = '搜尋用途、技巧、模型或節點'; search.setAttribute('aria-label', '搜尋工作流');
    const category = el('select'); category.setAttribute('aria-label', '工作流分類');
    const source = el('select'); source.setAttribute('aria-label', '工作流來源');
    const mode = el('select'); mode.setAttribute('aria-label', '收錄狀態');
    for (const [value, text] of [['all', '全部內容'], ['local', '可下載工作流'], ['recipe', '方法與參數'], ['reference', '上游參考']]) {
      const option = el('option', text); option.value = value; mode.append(option);
    }
    toolbar.append(search, category, source, mode);
    const status = el('p', '載入中…', 'catalogue-status'); status.setAttribute('role', 'status');
    const grid = el('div', '', 'workflow-grid');
    const pager = el('div', '', 'catalogue-pagination');
    panel.append(heading, toolbar, status, grid, pager); host.append(panel);
    const details = (item, opener) => {
      const content = el('div', '', 'workflow-details');
      const guide = item.guide || {};
      content.append(el('p', guide.summary || item.description || '從下方來源查看工作流的用途與設定。', 'workflow-summary'));
      content.append(el('p', item.available ? '已收錄 JSON；請在自己的 ComfyUI 檢查模型與輸入。尚未完成 GPU 生成驗證。' : item.kind === 'recipe' ? '這是方法與參數說明，需要依自己的環境建立流程；未附可直接執行的 JSON。' : '此項目提供上游來源；未附 JSON。請先閱讀原作者說明與授權。', 'workflow-evidence'));
      list(content, '先準備什麼', guide.inputs);
      list(content, '會怎麼跑', guide.steps, true);
      if (guide.output) content.append(el('p', '預期結果：' + guide.output, 'sub'));
      if (guide.techniques?.length) {
        const section = el('section', '', 'workflow-guide-section'); section.append(el('h3', '使用什麼技巧'));
        const rows = el('dl', '', 'workflow-techniques');
        for (const technique of guide.techniques) rows.append(el('dt', technique.name), el('dd', technique.explanation));
        section.append(rows); content.append(section);
      }
      list(content, '調整與排錯', guide.tips);
      if (guide.parameters?.length) {
        const section = el('section', '', 'workflow-guide-section'); section.append(el('h3', '圖內參數'));
        const rows = el('dl', '', 'workflow-techniques');
        for (const parameter of guide.parameters) rows.append(el('dt', parameter.name + '：' + String(parameter.value)), el('dd', parameter.note || '以匯入後的實際節點設定為準。'));
        section.append(rows); content.append(section);
      }
      if (guide.evidence) content.append(el('p', guide.evidence, 'workflow-evidence'));
      if (item.models?.length) list(content, '模型需求', item.models.map(model => typeof model === 'string' ? model : `${model.name}${model.directory_hint ? ' → models/' + model.directory_hint : ''}${model.node_type ? '（' + model.node_type + '）' : ''}`));
      if (item.missing_nodes) list(content, '目前缺少的節點', item.missing_nodes);
      const technical = el('details', '', 'workflow-technical'); technical.append(el('summary', '節點、參數與來源資訊'));
      for (const [label, value] of [['來源', item.source || item.source_urls], ['授權', item.license], ['節點需求', item.node_types], ['環境需求', item.requirements], ['方案設定', item.settings], ['驗證紀錄', item.validation]]) {
        if (!value || (Array.isArray(value) && !value.length)) continue;
        technical.append(el('h3', label), el('pre', typeof value === 'string' ? value : JSON.stringify(value, null, 2)));
      }
      content.append(technical);
      const actions = el('div', '', 'workflow-actions');
      const result = el('p', '', 'catalogue-status'); result.setAttribute('role', 'status');
      if (item.available) {
        const download = link('下載 JSON', docsMode ? item.download_url : '/api/workflow-library/file?id=' + encodeURIComponent(item.id));
        if (download) {download.download = item.id + '.json'; actions.append(download);}
        if (!docsMode && canLoad && item.format === 'visual') {
          const load = el('button', '載入 ComfyUI', 'primary small'); load.type = 'button';
          load.onclick = async () => {
            load.disabled = true; result.textContent = '正在加入 ComfyUI…';
            try {
              const data = await request('/api/workflow-library/open', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id: item.id})});
              result.textContent = data.message + (data.missing_nodes?.length ? ` 缺少 ${data.missing_nodes.length} 個節點，請先安裝。` : '');
            } catch (error) { result.textContent = error.message; }
            finally { load.disabled = false; }
          };
          actions.append(load);
        }
        if (!docsMode) {const editor = link('開啟 ComfyUI', comfyUrl); if (editor) actions.append(editor);}
      }
      if (docsMode) {
        const permalink = link('此工作流的說明連結', '#/workflow/' + encodeURIComponent(item.id));
        permalink.target = '_self'; actions.append(permalink);
      }
      for (const [index, url] of [item.upstream_url, ...(item.source_urls || [])].filter(Boolean).entries()) {
        const sourceLink = link(index ? `閱讀來源 ${index + 1}` : '閱讀來源', url); if (sourceLink) actions.append(sourceLink);
      }
      content.append(actions, result);
      onSelect(item.id);
      openSheet({id: 'workflow-detail', title: item.title, opener, content, className: 'catalogue-detail-sheet'});
    };
    const paint = () => {
      const query = search.value.trim().toLocaleLowerCase();
      const filtered = items.filter(item => (!category.value || item.category === category.value)
        && (!source.value || (item.source || 'recipe') === source.value)
        && (mode.value === 'all' || (mode.value === 'local' && item.available) || (mode.value === 'recipe' && item.kind === 'recipe') || (mode.value === 'reference' && !item.available && item.kind !== 'recipe'))
        && (!query || JSON.stringify([item.title, item.category, item.tags, item.node_types, item.models, item.guide]).toLocaleLowerCase().includes(query)));
      const pages = Math.max(1, Math.ceil(filtered.length / 36)); page = Math.min(page, pages - 1);
      status.textContent = `${filtered.length} 個項目 · ${items.filter(i => i.available).length} 個已收錄工作流`;
      grid.replaceChildren();
      for (const item of filtered.slice(page * 36, (page + 1) * 36)) {
        const card = el('button', '', 'workflow-card'); card.type = 'button';
        card.append(el('span', item.category || 'workflow', 'workflow-category'), el('strong', item.title));
        if (item.guide?.summary) card.append(el('span', item.guide.summary, 'workflow-card-summary'));
        card.append(el('span', item.available ? '已收錄 JSON' : item.kind === 'recipe' ? '方法與參數' : '上游參考', 'workflow-state'), el('small', item.source || '教學整理'));
        if (item.missing_nodes) card.append(el('small', item.missing_nodes.length ? `缺少 ${item.missing_nodes.length} 個節點` : '節點已具備，模型待確認'));
        card.onclick = () => details(item, card); grid.append(card);
      }
      if (!filtered.length) grid.append(el('p', '沒有符合條件的工作流。請調整搜尋或分類。'));
      pager.replaceChildren();
      if (pages > 1) {
        const previous = el('button', '上一頁', 'ghost small'); previous.disabled = page === 0; previous.onclick = () => {page--; paint();};
        const next = el('button', '下一頁', 'ghost small'); next.disabled = page === pages - 1; next.onclick = () => {page++; paint();};
        pager.append(previous, el('span', `${page + 1} / ${pages}`), next);
      }
    };
    const openItem = id => {
      const item = items.find(row => row.id === id);
      if (item) details(item, search);
      else if (items.length) status.textContent = '找不到這個工作流，請從工作庫搜尋。';
    };
    const load = async (verify = false) => {
      check.disabled = true; status.textContent = verify ? '檢查節點中…' : '載入中…';
      try {
        const data = await request(docsMode ? catalogUrl : '/api/workflow-library' + (verify ? '?check=1' : ''));
        if (!host.isConnected) return;
        items = [...(data.items || [])].sort((a, b) => {
          const rank = item => item.available ? 0 : item.kind === 'recipe' ? 1 : 2;
          return rank(a) - rank(b) || String(a.title).localeCompare(String(b.title));
        }); comfyUrl = data.comfy_url || comfyUrl;
        for (const [select, field, label] of [[category, 'category', '全部分類'], [source, 'source', '全部來源']]) {
          const old = select.value; select.replaceChildren(); const all = el('option', label); all.value = ''; select.append(all);
          for (const value of [...new Set(items.map(item => item[field] || (field === 'source' ? 'recipe' : '')).filter(Boolean))].sort()) {
            const option = el('option', value); option.value = value; select.append(option);
          }
          select.value = old;
        }
        paint();
        if (data.connection?.error) status.textContent += ' · ' + data.connection.error;
        if (docsMode && location.hash.startsWith('#/workflow/')) openItem(decodeURIComponent(location.hash.slice(11)));
      } catch (error) { status.textContent = error.message + '，可按上方按鈕重試。'; }
      finally { check.disabled = false; }
    };
    for (const control of [search, category, source, mode]) control.addEventListener(control === search ? 'input' : 'change', () => {page = 0; paint();});
    check.onclick = () => load(!docsMode); load();
    return {openItem};
  }
  return {mount};
})();

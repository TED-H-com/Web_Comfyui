/* Shared by the private workspace and the standalone public distribution. */
window.WorkflowLibrary = (() => {
  const el = (tag, text = '', cls = '') => {
    const node = document.createElement(tag);
    node.textContent = text;
    if (cls) node.className = cls;
    return node;
  };
  const link = (text, url) => {
    const node = el('a', text, 'ghost small workflow-link');
    try {
      const target = new URL(url, location.origin);
      if (!['http:', 'https:'].includes(target.protocol) || target.username || target.password) return null;
      node.href = target.href;
    } catch (_) { return null; }
    node.target = '_blank'; node.rel = 'noopener noreferrer';
    return node;
  };
  async function request(path, options) {
    const response = await fetch(path, {credentials: 'same-origin', ...options});
    const data = await response.json();
    if (!response.ok) throw Error(data.error || '讀取失敗');
    return data;
  }
  function mount(host, {openSheet, canLoad = false} = {}) {
    let items = [], page = 0, comfyUrl = 'http://127.0.0.1:8188';
    const panel = el('section', '', 'panel workflow-library');
    const heading = el('div', '', 'workflow-heading');
    const title = el('div');
    title.append(el('h1', '工作庫'), el('p', '依用途選擇工作流，檢查模型與節點，再送到自己的 ComfyUI。', 'sub'));
    const check = el('button', '檢查 ComfyUI 節點', 'ghost small'); check.type = 'button';
    heading.append(title, check);
    const toolbar = el('div', '', 'catalogue-toolbar');
    const search = el('input'); search.type = 'search'; search.placeholder = '搜尋工作流、模型或節點'; search.setAttribute('aria-label', '搜尋工作流');
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
      content.append(el('p', item.description || (item.notes || []).join?.(' ') || String(item.notes || '')));
      content.append(el('p', item.available ? '已收錄 JSON。節點檢查不代表模型已安裝或 GPU 生成已驗證。' : '此項目提供方法、參數或上游來源；未附可直接執行的 JSON。', 'sub'));
      for (const [label, value] of [['來源', item.source || item.source_urls], ['狀態', item.status], ['授權', item.license], ['模型需求', item.models], ['節點需求', item.node_types], ['需求', item.requirements], ['建議參數', item.settings], ['驗證', item.validation], ['缺少節點', item.missing_nodes]]) {
        if (!value || (Array.isArray(value) && !value.length)) continue;
        content.append(el('h3', label), el('pre', typeof value === 'string' ? value : JSON.stringify(value, null, 2)));
      }
      const actions = el('div', '', 'workflow-actions');
      const result = el('p', '', 'catalogue-status'); result.setAttribute('role', 'status');
      if (item.available) {
        const download = link('下載 JSON', '/api/workflow-library/file?id=' + encodeURIComponent(item.id));
        download.download = item.id + '.json'; actions.append(download);
        if (canLoad && item.format === 'visual') {
          const load = el('button', '載入 ComfyUI', 'primary small');
          load.type = 'button';
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
        const editor = link('開啟 ComfyUI', comfyUrl); if (editor) actions.append(editor);
      }
      for (const url of [item.upstream_url, ...(item.source_urls || [])].filter(Boolean)) {
        const sourceLink = link('閱讀來源', url); if (sourceLink) actions.append(sourceLink);
      }
      content.append(actions, result);
      openSheet({id: 'workflow-detail', title: item.title, opener, content, className: 'catalogue-detail-sheet'});
    };
    const paint = () => {
      const query = search.value.trim().toLocaleLowerCase();
      const filtered = items.filter(item => (!category.value || item.category === category.value)
        && (!source.value || (item.source || 'recipe') === source.value)
        && (mode.value === 'all' || (mode.value === 'local' && item.available) || (mode.value === 'recipe' && item.kind === 'recipe') || (mode.value === 'reference' && !item.available && item.kind !== 'recipe'))
        && (!query || JSON.stringify([item.title, item.category, item.tags, item.node_types, item.models]).toLocaleLowerCase().includes(query)));
      const pages = Math.max(1, Math.ceil(filtered.length / 36)); page = Math.min(page, pages - 1);
      status.textContent = `${filtered.length} 個項目 · ${items.filter(i => i.available).length} 個已收錄工作流`;
      grid.replaceChildren();
      for (const item of filtered.slice(page * 36, (page + 1) * 36)) {
        const card = el('button', '', 'workflow-card'); card.type = 'button';
        card.append(el('span', item.category || 'workflow', 'workflow-category'), el('strong', item.title),
          el('span', item.available ? '已收錄 JSON' : item.kind === 'recipe' ? '方法與參數' : '上游參考', 'workflow-state'),
          el('small', item.source || '教學整理'));
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
    const load = async (verify = false) => {
      check.disabled = true; status.textContent = verify ? '檢查節點中…' : '載入中…';
      try {
        const data = await request('/api/workflow-library' + (verify ? '?check=1' : ''));
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
      } catch (error) { status.textContent = error.message + '，可按檢查按鈕重試。'; }
      finally { check.disabled = false; }
    };
    for (const control of [search, category, source, mode]) control.addEventListener(control === search ? 'input' : 'change', () => {page = 0; paint();});
    check.onclick = () => load(true); load();
  }
  return {mount};
})();

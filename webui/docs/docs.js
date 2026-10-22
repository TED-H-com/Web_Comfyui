/* Static shell: uses the same library and sheet classes as the local WebUI. */
(() => {
  const libraryHost = document.getElementById('workflowPage');
  let dialog = null, selectedId = null;
  function dismiss({updateRoute = false, restoreFocus = true} = {}) {
    if (!dialog) return;
    const old = dialog; dialog = null; selectedId = null;
    old.close(); old.remove(); document.body.classList.remove('sheet-open');
    if (restoreFocus && old.opener?.isConnected) old.opener.focus();
    if (updateRoute && location.hash.startsWith('#/workflow/')) history.replaceState(null, '', '#/workflows');
  }
  function openSheet({title, opener, content, className}) {
    dismiss({restoreFocus: false});
    const item = document.createElement('dialog'); dialog = item;
    item.className = 'app-sheet ' + className; item.opener = opener;
    const head = document.createElement('div'); head.className = 'sheet-head';
    const label = document.createElement('h2'); label.id = 'workflowTitle'; label.textContent = title;
    item.setAttribute('aria-labelledby', label.id);
    const close = document.createElement('button'); close.className = 'sheet-close'; close.type = 'button'; close.textContent = '關閉';
    close.onclick = () => dismiss({updateRoute: true});
    head.append(label, close);
    const body = document.createElement('div'); body.className = 'sheet-body'; body.append(content);
    item.append(head, body); document.getElementById('sheetLayer').append(item);
    item.addEventListener('cancel', event => {event.preventDefault(); dismiss({updateRoute: true});});
    item.addEventListener('click', event => {if (event.target === item) {const r = item.getBoundingClientRect(); if(event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dismiss({updateRoute: true});}});
    document.body.classList.add('sheet-open'); item.showModal(); close.focus();
    selectedId = location.hash.startsWith('#/workflow/') ? decodeURIComponent(location.hash.slice(11)) : null;
  }
  const library = WorkflowLibrary.mount(libraryHost, {openSheet, docsMode: true, catalogUrl: './catalog.json', onSelect(id) {
    if (location.hash !== '#/workflow/' + encodeURIComponent(id)) history.pushState(null, '', '#/workflow/' + encodeURIComponent(id));
  }});
  function route() {
    const hash = location.hash;
    const page = hash === '#/start' ? 'start' : hash === '#/about' ? 'about' : 'workflows';
    libraryHost.hidden = page !== 'workflows';
    document.getElementById('startPage').hidden = page !== 'start';
    document.getElementById('aboutPage').hidden = page !== 'about';
    document.querySelectorAll('[data-page]').forEach(link => {const active = link.dataset.page === page; link.classList.toggle('active', active); if(active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');});
    document.title = (page === 'start' ? '開始使用' : page === 'about' ? '來源與授權' : '工作流說明') + ' · Web ComfyUI';
    if (hash.startsWith('#/workflow/')) {
      let id; try {id = decodeURIComponent(hash.slice(11));} catch (_) {return;}
      if (id !== selectedId) {dismiss({restoreFocus: false}); library.openItem(id);}
    } else dismiss();
  }
  addEventListener('hashchange', route); route();
})();

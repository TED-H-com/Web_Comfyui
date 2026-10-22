/* Introduction-only interactions. No app runtime, API requests or telemetry. */
(() => {
  'use strict';
  const requestedLanguage = new URLSearchParams(location.search).get('lang') === 'en' ? 'en' : 'zh-Hant';
  // Share the app translator while keeping this static site's preference in memory.
  const values = new Map([['webcomfy.language', requestedLanguage]]);
  Object.defineProperty(window, 'localStorage', {value: {
    getItem: key => values.get(String(key)) ?? null,
    setItem: (key, value) => values.set(String(key), String(value)),
    removeItem: key => values.delete(String(key)), clear: () => values.clear(),
    key: index => [...values.keys()][index] ?? null, get length() {return values.size;},
  }});
  // Old app-route bookmarks keep their route; normal introduction anchors stay here.
  if (location.hash.startsWith('#/')) {
    const destination = new URL('./demo/', location.href);
    destination.searchParams.set('lang', requestedLanguage);
    destination.hash = location.hash;
    location.replace(destination.href);
    return;
  }
  const language = () => window.WebComfyI18n?.language || requestedLanguage;
  const t = (zh, en) => language() === 'en' ? en : zh;
  let clearStatus;
  function updateLanguage() {
    document.title = t('Web ComfyUI — 角色、素材與工作流', 'Web ComfyUI — Characters, media and workflows');
    document.querySelector('meta[name="description"]')?.setAttribute('content', t(
      'Web ComfyUI：整合角色、素材與工作流的本機創作介面。先試用互動示範，再安裝到自己的 ComfyUI 環境。',
      'A local creative interface for characters, media and workflows. Explore the interactive demo, then install beside your own ComfyUI.'
    ));
    document.querySelectorAll('[data-label-zh][data-label-en]').forEach(node => {node.setAttribute('aria-label', language() === 'en' ? node.dataset.labelEn : node.dataset.labelZh);});
    document.querySelectorAll('[data-alt-zh][data-alt-en]').forEach(node => {node.alt = language() === 'en' ? node.dataset.altEn : node.dataset.altZh;});
    document.querySelectorAll('a[href]').forEach(link => {
      const target = new URL(link.getAttribute('href'), location.href);
      if (target.origin === location.origin && target.pathname === new URL('./demo/', location.href).pathname) {
        target.searchParams.set('lang', language());
        link.setAttribute('href', './demo/?' + target.searchParams.toString() + target.hash);
      }
    });
    const status = document.getElementById('landing-copy-status');
    if (status) status.textContent = '';
  }
  document.querySelectorAll('[data-copy]').forEach(button => {
    button.addEventListener('click', async () => {
      const code = document.getElementById(button.dataset.copy);
      const status = document.getElementById('landing-copy-status');
      if (!code || !status) return;
      try {
        await navigator.clipboard.writeText(code.textContent);
        status.textContent = t('指令已複製。請在自己的終端機執行。', 'Commands copied. Run them in your own terminal.');
      } catch (_) {
        status.textContent = t('無法自動複製，請選取上方指令手動複製。', 'Automatic copy is unavailable. Select and copy the commands above.');
      }
      clearTimeout(clearStatus);
      clearStatus = setTimeout(() => {status.textContent = '';}, 5000);
    });
  });
  window.addEventListener('webcomfy:language', updateLanguage);
  document.addEventListener('DOMContentLoaded', updateLanguage, {once: true});
  updateLanguage();
})();

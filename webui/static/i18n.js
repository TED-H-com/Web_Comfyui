/* Offline UI language layer. User values, technical data and unknown prose stay intact. */
(() => {
  'use strict';
  const KEY = 'webcomfy.language';
  const dictionary = new Map(Object.entries(window.WebComfyEnglish || {}));
  const originals = new WeakMap();
  const elementHistory = new WeakMap();
  const attributeOriginals = new WeakMap();
  const boundSelectors = new WeakSet();
  const ignored = 'script,style,code,pre,[contenteditable="true"],[data-i18n-ignore],.identity-name,.auth-identity,.file-name,.fname,.voice-name,.voice-transcript,.post-text,.post-meta,.result-detail-text,.bound-value,.wall-card-owner,.catalogue-lightbox-caption,.catalogue-card strong,.catalogue-detail-sheet .result-detail-meta,.orch-run-title,.orch-task-title,.node-input-name,.generation-prompt,.prompt-text,.output-caption,.input-echo,.demo-prompt-echo,.graph-parameters,.graph-node-type';
  let language = 'zh-Hant';
  try { if (localStorage.getItem(KEY) === 'en') language = 'en'; } catch (_) { /* Storage may be disabled. */ }
  let scheduled = false;
  const pending = new Set();

  function excluded(element) {
    if (!element || element.closest(ignored)) return true;
    if (element.closest('.wall-card-name') && !element.closest('.new-character-card')) return true;
    if (element.closest('.wall-card-tagline') && !element.closest('.provider-character-card,.new-character-card')) return true;
    if (element.closest('.demo-input-label') && !element.closest('[data-i18n-zh][data-i18n-en]')) return true;
    if (element.closest('select[name="voiceId"],select[name="persona"],select[name="characterId"],select[data-user-options]')) return true;
    return false;
  }

  // Anchored UI-only number patterns; never replace arbitrary words in prose.
  function pattern(text) {
    const rules = [
      [/^(\d+) 個項目 · (\d+) 個已收錄工作流$/, (_,n,m) => `${n} items · ${m} included workflows`],
      [/^預期結果：(.*)$/, (_,output) => `Expected result: ${english(output)}`],
      [/^(\d+) 幀 · ([\d.]+) 秒$/, (_,a,b) => `${a} frames · ${b} s`],
      [/^參考圖片 (\d+)$/, (_,n) => `Reference image ${n}`],
      [/^參考影片 (\d+)$/, (_,n) => `Reference video ${n}`],
      [/^參考音訊 (\d+)$/, (_,n) => `Reference audio ${n}`],
      [/^參考圖 (\d+)(（可選）)?$/, (_,n,opt) => `Reference ${n}${opt ? ' (optional)' : ''}`],
      [/^圖片 (\d+) · (<image\d+>)（(原圖|可選參考)）$/, (_,n,tag,kind) => `Image ${n} · ${tag} (${kind === '原圖' ? 'original' : 'optional reference'})`],
      [/^(直|橫|方) ([\d:×]+)$/, (_,kind,size) => `${{直:'Portrait',橫:'Landscape',方:'Square'}[kind]} ${size}`],
      [/^參考音訊逐字稿（最多 (\d+) 字）$/, (_,n) => `Reference transcript (up to ${n} characters)`],
      [/^(要生成的文字|要說的話)（最多 (\d+) 字，自動分段合併）$/, (_,label,n) => `Speech text (up to ${n} characters; split and merge automatically)`],
      [/^開啟任務，(\d+) 個進行中$/, (_,n) => `Open tasks, ${n} active`],
      [/^段落 (\d+)$/, (_,n) => `Beat ${n}`],
      [/^第 (\d+) 段$/, (_,n) => `Segment ${n}`],
      [/^(\d+) 媒體$/, (_,n) => `${n} media items`],
      [/^(\d+) 位$/, (_,n) => `${n} characters`],
      [/^(\d+) 字$/, (_,n) => `${n} characters`],
      [/^缺少 (\d+) 個節點、(\d+) 個模型$/, (_,n,m) => `Missing ${n} nodes and ${m} models`],
      [/^顯示 (\d+)[–-](\d+) ／ (\d+)$/, (_,a,b,total) => `Showing ${a}–${b} / ${total}`],
    ];
    for (const [regex, render] of rules) if (regex.test(text)) return text.replace(regex, render);
    return null;
  }

  function english(text) {
    if (dictionary.has(text)) return dictionary.get(text);
    // Required-field decoration and empty-result messages are composed by the
    // app. Translate their label only when it is already a known UI phrase.
    if (text.endsWith(' *')) {
      const source = text.slice(0, -2), result = english(source);
      if (result !== source) return `${result} *`;
    }
    const empty = /^還沒有「([^「」]+)」的輸出。$/.exec(text);
    if (empty && dictionary.has(empty[1])) return `No output from “${dictionary.get(empty[1])}” yet.`;
    const direct = pattern(text); if (direct !== null) return direct;
    // UI headings often join a known label and a known tool. Translate only
    // when EVERY nontechnical segment has an exact registered translation.
    if (text.includes(' · ')) {
      const parts = text.split(' · ');
      const rendered = parts.map(part => dictionary.get(part) || pattern(part) || (/^[\x20-\x7e]+$/.test(part) ? part : null));
      if (rendered.every(part => part !== null)) return rendered.join(' · ');
    }
    return text;
  }

  function t(zh, en) {
    const text = String(zh ?? '');
    if (language !== 'en') return text;
    if (typeof en === 'string') return en;
    const core = text.trim();
    const translated = english(core);
    return translated === core ? text : text.replace(core, translated);
  }

  function textNode(node) {
    const parent = node.parentElement;
    if (excluded(parent) || parent.closest('textarea,option:not([value])')) return;
    let record = originals.get(node);
    if (!record || node.data !== record.last) {
      const history = record?.history || (parent.childNodes.length === 1 && elementHistory.get(parent)) || new Map();
      if (parent.childNodes.length === 1) elementHistory.set(parent, history);
      record = { original: history.get(node.data) || node.data, last: node.data, history };
    }
    const explicit = parent.hasAttribute('data-i18n-zh') && parent.hasAttribute('data-i18n-en') && parent.childNodes.length === 1;
    const next = explicit ? (language === 'en' ? parent.dataset.i18nEn : parent.dataset.i18nZh) : t(record.original);
    if (next !== record.original) record.history.set(next, record.original);
    record.last = next; originals.set(node, record);
    if (node.data !== next) node.data = next;
  }

  function attributes(element) {
    if (excluded(element) || element.matches('textarea[data-i18n-ignore],input[data-i18n-ignore]')) return;
    let records = attributeOriginals.get(element);
    if (!records) {records = new Map();attributeOriginals.set(element, records);}
    for (const name of ['placeholder', 'title', 'aria-label']) {
      if (!element.hasAttribute(name)) {records.delete(name);continue;}
      const value = element.getAttribute(name);
      let record = records.get(name);
      if (!record || value !== record.last) record = { original: value, last: value };
      const next = t(record.original);record.last = next;records.set(name, record);
      if (value !== next) element.setAttribute(name, next);
    }
  }

  function bindSelector(element) {
    if (!['languageSelect', 'authLanguageSelect'].includes(element.id)) return;
    if (!boundSelectors.has(element)) {
      element.addEventListener('change', () => setLanguage(element.value));
      boundSelectors.add(element);
    }
    if (element.value !== language) element.value = language;
  }

  function translate(root = document.body) {
    if (!root) return;
    if (root.nodeType === Node.TEXT_NODE) {textNode(root);return;}
    if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_NODE && root.nodeType !== Node.DOCUMENT_FRAGMENT_NODE) return;
    if (root.nodeType === Node.ELEMENT_NODE) {bindSelector(root);attributes(root);}
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      if (node.nodeType === Node.ELEMENT_NODE) {bindSelector(node);attributes(node);}
      else textNode(node);
    }
  }

  function enqueue(root) {
    pending.add(root);
    if (scheduled) return;
    scheduled = true;
    queueMicrotask(() => {
      scheduled = false;
      const roots = [...pending];pending.clear();
      for (const node of roots) if (node.isConnected) translate(node);
    });
  }

  function setLanguage(code) {
    const next = code === 'en' ? 'en' : 'zh-Hant';
    const changed = language !== next;
    language = next;document.documentElement.lang = language;
    try {localStorage.setItem(KEY, language);} catch (_) { /* No remote fallback. */ }
    translate(document.body);
    if (changed) window.dispatchEvent(new CustomEvent('webcomfy:language', {detail: {language}}));
    return language;
  }

  function register(mapping) {
    for (const [zh,en] of Object.entries(mapping || {})) if (typeof en === 'string') dictionary.set(zh,en);
    translate(document.body);
  }

  window.WebComfyI18n = {get language() {return language;}, setLanguage, t, translate, register};
  document.documentElement.lang = language;
  function start() {
    translate(document.body);
    new MutationObserver(records => {
      for (const record of records) {
        if (record.type === 'childList') for (const node of record.addedNodes) enqueue(node);
        else enqueue(record.target);
      }
    }).observe(document.body, {subtree: true,childList: true,characterData: true,attributes: true,
      attributeFilter: ['placeholder','title','aria-label','data-i18n-zh','data-i18n-en','data-i18n-ignore']});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();

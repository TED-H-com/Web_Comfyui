/* Static demo planning only: never detects hardware, executes a job or calls an API. */
(() => {
  'use strict';
  let data = null, selected = 'vram-32', observer = null, scheduled = false;
  const controls = new WeakMap();
  const en = () => window.WebComfyI18n?.language === 'en' || document.documentElement.lang.startsWith('en');
  const choose = (zh, english) => en() ? english : zh;
  function label(node, zh, english) {
    if (node.dataset.i18nZh !== zh) node.dataset.i18nZh = zh;
    if (node.dataset.i18nEn !== english) node.dataset.i18nEn = english;
    const value = choose(zh, english); if (node.textContent !== value) node.textContent = value;
    return node;
  }
  const profile = () => data?.profiles.find(item => item.id === selected);
  function status(id) {
    const requirement = data?.features[id], current = profile();
    if (!requirement || !current) return {allowed: false, reason: '尚未提供硬體規劃。', reason_en: 'No hardware planning data is available.'};
    const capacity = Math.max(0, ...(current.cards_gb || []));
    const allowed = Boolean(current.cloud_simulation || capacity >= requirement.estimated_vram_gb);
    let reason, reason_en;
    if (current.cloud_simulation) {
      reason = 'AutoIP 雲端示範：所有預製案例開放，不用本機顯存。本頁不呼叫 API、不扣點；實際支援模型、帳戶與點數價格須另確認。';
      reason_en = 'AutoIP cloud simulation: every prepared example is available without local VRAM. This page makes no API calls or charges; check actual supported models, account setup and credit pricing separately.';
    } else if (requirement.estimated_vram_gb === 0) {
      reason = '此流程不要求本機擴散 GPU；仍需對應素材、軟體或自行設定的外部服務。此處只播放預製示範。';
      reason_en = 'This flow does not require a local diffusion GPU; assets, software or a separately configured external service may still be needed. This page only plays prepared examples.';
    } else {
      reason = `保守規劃：單卡 ${requirement.estimated_vram_gb} GB；目前單卡 ${capacity} GB。${allowed ? '可查看生成示範，仍需驗證實際環境。' : '此設定低於規劃值，生成示範停用；仍可查看流程與節點。'}`;
      reason_en = `Conservative plan: ${requirement.estimated_vram_gb} GB on one GPU; selected capacity ${capacity} GB per GPU. ${allowed ? 'The generation demo is available; validate your actual environment separately.' : 'Below the planning allowance: generation demo disabled; guides and nodes remain available.'}`;
    }
    if ((current.cards_gb || []).length > 1) {
      reason += ' 兩張 24 GB 不視為 48 GB；此組圖未實作模型分片。';
      reason_en += ' Two 24 GB cards do not count as 48 GB; these builders do not implement model sharding.';
    }
    return {allowed, reason, reason_en, requirement, capacity, cloud: Boolean(current.cloud_simulation)};
  }
  function canUse(id) { return status(id).allowed; }
  function note(id, form, result) {
    let host = form.querySelector(':scope > .hardware-feature-status');
    if (!host) {host = document.createElement('aside');host.className = 'hardware-feature-status';host.setAttribute('role','status');host.setAttribute('aria-live','polite');form.prepend(host);}
    host.classList.toggle('hardware-blocked', !result.allowed);
    let message = host.querySelector('.hardware-reason');
    if (!message) {message = document.createElement('p');message.className = 'hardware-reason';host.append(message);}
    label(message, result.reason, result.reason_en);
    let cloudLink = host.querySelector('.hardware-cloud-option');
    if (!cloudLink) {
      cloudLink = document.createElement('p');cloudLink.className='hardware-cloud-option';
      const link = document.createElement('a');link.href='https://autoip.app/register?ref=TEDH2FND&utm_source=affiliate&utm_medium=referral&utm_campaign=TEDH2FND';link.target='_blank';link.rel='sponsored noopener noreferrer';
      label(link, '考慮 AutoIP 雲端服務（推薦連結）', 'Consider AutoIP cloud services (referral)');
      const code = document.createElement('code');code.textContent='TEDH2FND';
      cloudLink.append(link,document.createTextNode(' · '),code);host.append(cloudLink);
    }
    cloudLink.hidden = result.allowed;
    let details = host.querySelector('details');
    if (!details) {
      details = document.createElement('details'); const summary = document.createElement('summary');
      label(summary, '估計依據與限制', 'Estimate basis and limits');
      const basis = document.createElement('p');basis.className = 'hardware-basis';
      const limits = document.createElement('p');limits.className = 'hardware-limits';
      label(limits, '這是估計，不是最低需求或效能測試。假設一次一項工作、分階段 CPU 卸載與足夠 RAM；解析度、影格、精度、節點及參考數會改變需求。較小量化版本可能低於此值，大型工作可能超過。', 'These are estimates, not minimum requirements or benchmarks. They assume one job at a time, staged CPU offload and enough RAM. Resolution, frames, precision, nodes and references change demand; smaller quantized variants may use less and large jobs may need more.');
      const sources = document.createElement('p');sources.className = 'hardware-sources';
      for (const item of data.profiles) for (const url of item.source_urls || []) {
        if ([...sources.querySelectorAll('a')].some(a => a.href === url)) continue;
        const link = document.createElement('a'); link.href = url;link.target='_blank';link.rel='noopener noreferrer';link.textContent = 'NVIDIA ' + new URL(url).pathname.split('/').filter(Boolean).at(-1).replaceAll('-', ' ');sources.append(link);
      }
      details.append(summary,basis,limits,sources);host.append(details);
    }
    label(details.querySelector('.hardware-basis'), result.requirement?.basis || '', result.requirement?.basis_en || '');
    if (result.requirement?.native_api_disabled) {
      let disabled = host.querySelector('.hardware-native-disabled');
      if (!disabled) {disabled = document.createElement('p');disabled.className='hardware-native-disabled';host.append(disabled);}
      label(disabled, '原生生成入口目前停用；硬體選項與雲端示範不會解除實際功能限制。', 'The native generation endpoint is disabled. Hardware choices and cloud simulations do not enable it.');
    }
    for (const button of form.querySelectorAll('.generate-btn,button[type=submit]')) {
      if (!result.allowed) {
        if (!controls.has(button)) controls.set(button, {disabled: button.disabled && !button.dataset.demoSubmit});
        button.disabled = true;button.dataset.hardwareBlocked = 'true';
      } else if (button.dataset.hardwareBlocked && form.querySelector('.app-demo')?.dataset.state !== 'running') {
        button.disabled = controls.get(button)?.disabled || false;
        button.removeAttribute('data-hardware-blocked');controls.delete(button);
      }
    }
  }
  function decorate(id, root = document) {
    if (!data) return;
    if (typeof id === 'object' && id?.nodeType) {root = id;id = null;}
    if (id && typeof id === 'object') id = id.id;
    const forms = root.matches?.('form[data-demo-id]') ? [root] : [...root.querySelectorAll('form[data-demo-id]')];
    for (const form of forms) {
      const feature = id || form.dataset.demoId;
      note(feature, form, status(feature));
    }
    const cards = root.matches?.('[data-hardware-feature]') ? [root] : [...root.querySelectorAll('[data-hardware-feature]')];
    for (const card of cards) {
      const result = status(card.dataset.hardwareFeature);
      card.classList.toggle('hardware-unavailable', !result.allowed);
      if (!result.allowed) card.setAttribute('aria-disabled','true');else card.removeAttribute('aria-disabled');
      let badge = card.querySelector(':scope > .hardware-availability');
      if (!badge) {badge = document.createElement('small');badge.className='hardware-availability';card.append(badge);}
      badge.hidden = result.allowed;
      label(badge, '估計不足 · 生成停用', 'Below estimate · generation off');
    }
    const select = document.getElementById('hardwareSelect');
    if (select) {
      select.value = selected;select.setAttribute('aria-label', choose('示範硬體設定','Demo hardware profile'));
      for (const option of select.options) {
        const item = data.profiles.find(p => p.id === option.value);if(item) label(option,item.label,item.label_en);
      }
    }
  }
  function setProfile(id) {
    if (!data?.profiles.some(item => item.id === id)) return false;
    selected = id;
    try {sessionStorage.setItem('webcomfy.demo.hardware',id);} catch (_) { /* In-memory UI still works. */ }
    decorate();window.dispatchEvent(new CustomEvent('webcomfy:hardware',{detail:{profile:selected}}));return true;
  }
  function install(registry) {
    data = registry.hardware;
    if (data?.scope !== 'static-demo-planning-only' || !data?.profiles?.length || !data?.features) {data=null;return false;}
    selected = data.default_profile || 'vram-32';
    try {const saved = sessionStorage.getItem('webcomfy.demo.hardware');if(data.profiles.some(item => item.id === saved)) selected = saved;} catch (_) {}
    const actions = document.querySelector('#appTopbar .top-actions');
    if (actions && !document.getElementById('hardwareSelect')) {
      const wrapper = document.createElement('label');wrapper.className='hardware-control';
      const title = label(document.createElement('span'),'硬體','Hardware');title.className='hardware-control-label';
      const select = document.createElement('select');select.id='hardwareSelect';
      for (const item of data.profiles) {const option=document.createElement('option');option.value=item.id;label(option,item.label,item.label_en);select.append(option);}
      select.addEventListener('change',()=>setProfile(select.value));wrapper.append(title,select);actions.prepend(wrapper);
      document.documentElement.classList.add('has-hardware-selector');
    }
    decorate();
    if (!observer) {
      observer = new MutationObserver(records => {
        if (!records.some(record => [...record.addedNodes].some(node => node.nodeType === Node.ELEMENT_NODE && !node.closest?.('.hardware-feature-status,.hardware-availability,.hardware-control')))) return;
        if (!scheduled) {scheduled=true;queueMicrotask(()=>{scheduled=false;decorate();});}
      });
      observer.observe(document.body,{childList:true,subtree:true});
    }
    return true;
  }
  window.addEventListener('webcomfy:language',()=>decorate());
  window.WebComfyHardware = {install,canUse,decorate,setProfile,status,get profile(){return selected;}};
})();

/* Documentation-only characters: listed demo subjects + session-created characters. */
(() => {
  'use strict';

  const characters = new Map();
  let catalog = null;
  let serial = 0;

  const isEn = () => window.WebComfyI18n?.language === 'en' || document.documentElement.lang.startsWith('en');
  const drive = () => window.WebComfyDemoDrive;
  const assetUrl = (file) => drive()?.assetUrl?.(file) || (file ? new URL('../assets/examples/' + String(file).replace(/^\/+/, ''), location.href).href : '');

  function setI18n(el, zh, en) {
    const z = String(zh ?? '');
    const e = String(en ?? z);
    if (el.dataset.i18nZh !== z) el.dataset.i18nZh = z;
    if (el.dataset.i18nEn !== e) el.dataset.i18nEn = e;
    const target = isEn() ? e : z;
    if (el.textContent !== target) el.textContent = target;
    return el;
  }

  function keyPair(key, varsZh, varsEn) {
    const d = drive();
    if (!d?.i18nPair) return [key, key];
    const [z] = d.i18nPair(key, varsZh);
    const [, e] = d.i18nPair(key, varsEn || varsZh);
    return [z, e];
  }

  function setKeyI18n(el, key, varsZh, varsEn) {
    const [z, e] = keyPair(key, varsZh, varsEn);
    return setI18n(el, z, e);
  }

  function node(tag, text = '', cls = '') {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text) el.textContent = text;
    return el;
  }

  function label(tag, zh, en, cls = '') {
    return setI18n(node(tag, '', cls), zh, en);
  }

  function action(zh, en, fn) {
    const btn = label('button', zh, en, 'ghost small');
    btn.type = 'button';
    btn.addEventListener('click', fn);
    return btn;
  }

  function materials() {
    return catalog?.demo_set?.materials || [];
  }

  function subjects() {
    return catalog?.demo_set?.subjects || [];
  }

  function materialById(id) {
    return materials().find((m) => m.id === id) || null;
  }

  function subjectCharId(sub) {
    return sub.character_id || ('docs-character-' + sub.id);
  }

  function subjectPhotoUrl(sub, preferThumb = false) {
    const mat = materialById(sub.portrait || sub.base) || materialById(sub.base);
    const file = preferThumb ? (mat?.thumb || mat?.file) : mat?.file;
    return file ? assetUrl(file) : (catalog?.demos?.['character-create']?.inputs?.[0]?.src || '');
  }

  function listedSubjects() {
    return subjects().filter((s) => s.listed);
  }

  function listedCharacters() {
    return listedSubjects().map((sub) => {
      const avatar = subjectPhotoUrl(sub, true);
      const zhName = sub.name_zh || sub.id;
      const enName = sub.name_en || zhName;
      const zhRole = sub.role_zh || '';
      const enRole = sub.role_en || zhRole;
      const zhDesc = sub.description_zh || '';
      const enDesc = sub.description_en || zhDesc;
      return {
        id: subjectCharId(sub),
        name: isEn() ? enName : zhName,
        name_zh: zhName,
        name_en: enName,
        tagline: isEn() ? enRole : zhRole,
        role_zh: zhRole,
        role_en: enRole,
        profile: isEn() ? enDesc : zhDesc,
        type: sub.domain === 'anime' ? 'anime' : 'realistic',
        visualClass: sub.domain === 'anime' ? 'anime' : 'realistic',
        listedDemoSubject: true,
        subjectId: sub.id,
        avatarUrl: avatar,
        assetUrls: { avatar },
        canDelete: false,
      };
    });
  }

  function decorateWall() {
    const listed = listedSubjects();
    if (!listed.length) return;
    const byId = new Map(listed.map((s) => [subjectCharId(s), s]));
    for (const card of document.querySelectorAll('.wall-card[data-character-id]')) {
      const sub = byId.get(card.dataset.characterId);
      if (!sub) continue;
      card.querySelector('.works-badge')?.remove();
      const body = card.querySelector('.wall-card-body');
      if (!body) continue;

      const nameEl = body.querySelector('.wall-card-name');
      if (nameEl) {
        if (nameEl.hasAttribute('data-i18n-ignore')) nameEl.removeAttribute('data-i18n-ignore');
        setI18n(nameEl, sub.name_zh || sub.id, sub.name_en || sub.name_zh || sub.id);
      }
      const tagEl = body.querySelector('.wall-card-tagline');
      if (tagEl && (sub.role_zh || sub.role_en)) {
        if (tagEl.hasAttribute('data-i18n-ignore')) tagEl.removeAttribute('data-i18n-ignore');
        setI18n(tagEl, sub.role_zh || sub.role_en, sub.role_en || sub.role_zh);
      }
      if (!body.querySelector('.demo-character-wall-badge')) {
        const badge = setKeyI18n(node('span', '', 'demo-badge demo-character-wall-badge'), 'char.badge');
        const enter = body.querySelector('.wall-card-enter');
        if (enter) enter.before(badge);
        else body.append(badge);
      }
    }
  }

  function remove(id) {
    characters.delete(id);
  }

  function create(values) {
    const fallback = catalog?.demos?.['character-create']?.inputs?.[0]?.src || '';
    const photo = values.photoUrl || fallback;
    const name = values.name || (isEn() ? 'Qingqing' : '晴晴');
    const character = {
      id: 'docs-character-' + (++serial),
      name,
      profile: values.description || '',
      type: 'experimental',
      visualClass: 'unknown',
      avatarUrl: photo,
      assetUrls: { avatar: photo },
      subjectId: values.subjectId || null,
    };
    characters.set(character.id, character);
    return character;
  }

  function renderSubjectVoice(host, sub) {
    const cases = catalog?.demo_set?.cases || [];
    const voiceCase = cases.find(
      (c) => c.feature_id === 'character-voice' && c.display && (c.subjects || []).includes(sub.id),
    );
    const matId = voiceCase?.audio?.[0] || voiceCase?.outputs?.[0]?.material;
    const voiceMat = (matId ? materialById(matId) : null)
      || materials().find((m) => m.kind === 'audio' && m.subject === sub.id);
    const audioFile = voiceCase?.outputs?.[0]?.file || voiceMat?.file;
    if (!audioFile) return;

    const sec = node('section', '', 'panel docs-character-voice');
    const h = setKeyI18n(
      node('h2'),
      'char.voice',
      { name: sub.name_zh || sub.id },
      { name: sub.name_en || sub.name_zh || sub.id },
    );
    sec.append(h);

    if (voiceCase?.disclosure_zh || voiceCase?.disclosure_en) {
      sec.append(label('p', voiceCase.disclosure_zh || voiceCase.disclosure_en, voiceCase.disclosure_en || voiceCase.disclosure_zh, 'demo-disclosure'));
    }
    const audio = node('audio');
    audio.controls = true;
    audio.preload = 'metadata';
    audio.src = assetUrl(audioFile);
    sec.append(audio);

    const transcript = voiceMat?.transcript;
    if (transcript) {
      const tp = node('p', transcript, 'demo-prompt-echo');
      tp.dataset.i18nIgnore = 'true';
      sec.append(tp);
    }
    host.append(sec);
  }

  function caseGroupForFeature(featureId) {
    const feat = (catalog?.features || []).find((f) => f.id === featureId);
    const group = feat?.studio || feat?.kind || '';
    if (group === 'image') return 'group.image';
    if (group === 'video') return 'group.video';
    if (group === 'voice' || group === 'audio') return 'group.voice';
    if (group === 'text') return 'group.text';
    return 'group.image';
  }

  function renderSubjectCases(host, sub, backCharId) {
    renderSubjectVoice(host, sub);

    const features = catalog?.features || [];
    const featMap = new Map(features.map((f, idx) => [f.id, { feat: f, idx }]));
    const cases = (catalog?.demo_set?.cases || [])
      .filter((c) => c.display && (c.kind === 'recorded' || c.kind === 'prepared') && c.feature_id !== 'character-voice' && (c.subjects || []).includes(sub.id))
      .slice()
      .sort((a, b) => {
        const fa = featMap.get(a.feature_id)?.idx ?? 999;
        const fb = featMap.get(b.feature_id)?.idx ?? 999;
        return fa - fb || (a.order ?? 0) - (b.order ?? 0);
      });

    if (!cases.length) return;

    const sectionTitle = setKeyI18n(node('h2'), 'char.grid');
    host.append(sectionTitle);

    const groups = new Map([
      ['group.image', []],
      ['group.video', []],
      ['group.voice', []],
      ['group.text', []],
    ]);
    for (const c of cases) {
      const gKey = caseGroupForFeature(c.feature_id);
      if (!groups.has(gKey)) groups.set(gKey, []);
      groups.get(gKey).push(c);
    }

    const wrap = node('div', '', 'docs-character-groups');
    for (const [groupKey, groupCases] of groups) {
      if (!groupCases.length) continue;
      const groupSec = node('section', '', 'docs-character-group');
      const gh = setKeyI18n(node('h3', '', 'docs-character-group-title'), groupKey);
      const grid = node('div', '', 'docs-character-grid');

      for (const c of groupCases) {
        const feat = featMap.get(c.feature_id)?.feat;
        const card = node('article', '', 'panel docs-character-case-card');
        card.dataset.caseId = c.id;
        card.dataset.featureId = c.feature_id;

        const out = (c.outputs || [])[0];
        if (out?.kind === 'image' && out.file) {
          const img = node('img');
          img.src = assetUrl(out.file);
          img.alt = isEn() ? (c.title_en || c.title_zh || c.id) : (c.title_zh || c.id);
          img.loading = 'lazy';
          img.decoding = 'async';
          card.append(img);
        } else if (out?.kind === 'video' && out.file) {
          if (out.poster) {
            const img = node('img');
            img.src = assetUrl(out.poster);
            img.alt = isEn() ? (c.title_en || c.title_zh || c.id) : (c.title_zh || c.id);
            img.loading = 'lazy';
            img.decoding = 'async';
            card.append(img);
          } else {
            const vid = node('video');
            vid.controls = true;
            vid.muted = true;
            vid.playsInline = true;
            vid.preload = 'metadata';
            vid.src = assetUrl(out.file);
            card.append(vid);
          }
        } else if (out?.kind === 'audio' && out.file) {
          const aud = node('audio');
          aud.controls = true;
          aud.preload = 'metadata';
          aud.src = assetUrl(out.file);
          card.append(aud);
        }

        const badge = c.kind === 'recorded'
          ? label('span', '本機實跑', 'Local GPU', 'demo-badge')
          : label('span', c.stand_in_for ? '預製（暫用替代）' : '預製範例', c.stand_in_for ? 'Prepared (stand-in)' : 'Prepared', 'demo-badge');

        const fTitle = feat?.title ? node('small', feat.title, 'docs-character-case-tool') : null;
        const cTitle = label('h4', c.title_zh || c.id, c.title_en || c.title_zh || c.id);

        const openLink = label('a', '打開這個工具試試', 'Open in studio', 'ghost small');
        openLink.href = `#/studio/${encodeURIComponent(c.feature_id)}?case=${encodeURIComponent(c.id)}&from=${encodeURIComponent(backCharId)}`;

        if (fTitle) card.append(badge, fTitle, cTitle, openLink);
        else card.append(badge, cTitle, openLink);
        grid.append(card);
      }

      groupSec.append(gh, grid);
      wrap.append(groupSec);
    }
    host.append(wrap);
  }

  function render(route, hooks) {
    if (!route.view.startsWith('character-') || !route.characterId?.startsWith('docs-character-')) return false;
    const host = hooks.begin();
    host.classList.add('docs-character-workspace');

    const back = action('返回角色列表', 'Back to characters', () => hooks.navigate({ view: 'characters' }));
    const sessionChar = characters.get(route.characterId);
    const listedSub = !sessionChar
      ? listedSubjects().find((s) => subjectCharId(s) === route.characterId)
      : null;

    if (!sessionChar && !listedSub) {
      host.append(
        label('p', '此暫存角色已清除。重新整理會清除示範角色。', 'This session character was cleared. Refreshing clears demo characters.'),
        back,
      );
      return true;
    }

    if (listedSub) {
      host.dataset.demoSubject = listedSub.id;
      const header = node('section', '', 'panel docs-character-header');
      const avatar = node('img');
      avatar.src = subjectPhotoUrl(listedSub);
      avatar.alt = isEn() ? (listedSub.name_en || listedSub.name_zh || listedSub.id) : (listedSub.name_zh || listedSub.id);
      avatar.className = 'docs-character-reference';

      const details = node('div');
      const badge = setKeyI18n(node('span', '', 'demo-badge'), 'char.badge');
      const notice = setKeyI18n(node('p', '', 'panel-note'), 'char.notice');
      const heading = label('h1', listedSub.name_zh || listedSub.id, listedSub.name_en || listedSub.name_zh || listedSub.id);
      details.append(badge, notice, heading);
      if (listedSub.role_zh || listedSub.role_en) {
        details.append(label('p', listedSub.role_zh || listedSub.role_en, listedSub.role_en || listedSub.role_zh, 'docs-character-role'));
      }
      if (listedSub.description_zh || listedSub.description_en) {
        details.append(label('p', listedSub.description_zh || listedSub.description_en, listedSub.description_en || listedSub.description_zh, 'docs-character-description'));
      }
      if (Array.isArray(listedSub.tags_zh) && listedSub.tags_zh.length) {
        const tagRow = node('div', '', 'demo-tile-badges');
        listedSub.tags_zh.forEach((tz, i) => {
          tagRow.append(label('span', tz, listedSub.tags_en?.[i] || tz, 'demo-badge'));
        });
        details.append(tagRow);
      }

      header.append(avatar, details);
      const controls = node('div', '', 'workflow-actions');
      controls.append(back);
      host.append(header, controls);

      renderSubjectCases(host, listedSub, subjectCharId(listedSub));
      window.WebComfyI18n?.translate(host);
      return true;
    }

    // Session-created character
    host.dataset.sessionCharacter = sessionChar.id;
    const header = node('section', '', 'panel docs-character-header');
    const avatar = node('img');
    avatar.src = sessionChar.assetUrls.avatar;
    avatar.alt = sessionChar.name;
    avatar.className = 'docs-character-reference';

    const details = node('div');
    details.append(
      label('p', '示範角色已建立 · 本頁暫存', 'Demo character created · this page only', 'panel-note'),
      node('h1', sessionChar.name),
      node('p', sessionChar.profile, 'docs-character-description'),
      label('p', '參考照片與角色資料保留至重新整理。沒有上傳、訓練或即時生成。', 'Your reference photo and character details stay until refresh. No upload, training or live generation occurs.', 'panel-note'),
    );
    details.querySelector('h1').dataset.i18nIgnore = '';
    details.querySelector('.docs-character-description').dataset.i18nIgnore = '';
    header.append(avatar, details);

    const controls = node('div', '', 'workflow-actions');
    controls.append(
      back,
      action('刪除此角色', 'Delete character', async () => {
        remove(sessionChar.id);
        await hooks.refresh();
        hooks.navigate({ view: 'characters' });
      }),
      action('清除所有暫存角色', 'Clear all session characters', async () => {
        for (const id of [...characters.keys()]) remove(id);
        await hooks.refresh();
        hooks.navigate({ view: 'characters' });
      }),
    );
    host.append(header, controls);

    const boundSub = sessionChar.subjectId
      ? subjects().find((s) => s.id === sessionChar.subjectId)
      : null;

    if (boundSub) {
      host.dataset.boundSubject = boundSub.id;
      const boundNote = setKeyI18n(
        node('p', '', 'panel-note demo-cc-bound'),
        'cc.bound',
        { name: boundSub.name_zh || boundSub.id },
        { name: boundSub.name_en || boundSub.name_zh || boundSub.id },
      );
      host.append(boundNote);
      renderSubjectCases(host, boundSub, sessionChar.id);
    } else {
      const noPhoto = setKeyI18n(node('p', '', 'panel-note demo-cc-nophoto'), 'cc.nophoto');
      host.append(noPhoto);
    }

    window.WebComfyI18n?.translate(host);
    return true;
  }

  window.WebComfyCharacterSession = {
    install(app) { catalog = app; },
    list: () => [...characters.values()],
    listedCharacters,
    decorateWall,
    create,
    render,
  };
})();

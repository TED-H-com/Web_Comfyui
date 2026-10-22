/* Documentation-only characters. No persistence, uploads or API mutations. */
(() => {
  'use strict';
  const characters = new Map(); let catalog; let serial = 0;
  const node = (tag, value = '', cls = '') => {const el = document.createElement(tag); el.textContent = value; el.className = cls; return el;};
  const label = (tag, zh, en, cls = '') => {const el = node(tag, window.WebComfyI18n?.language === 'en' ? en : zh, cls); el.dataset.i18nZh = zh; el.dataset.i18nEn = en; return el;};
  const action = (zh, en, fn) => {const el = label('button', zh, en, 'ghost small');el.type = 'button';el.onclick = fn;return el;};
  function remove(id) {const character = characters.get(id);if(character?.ownedPhoto) URL.revokeObjectURL(character.assetUrls.avatar);characters.delete(id);}
  function create(values) {
    if (values.photo && (!(values.photo instanceof File) || !['image/png','image/jpeg','image/webp'].includes(values.photo.type) || values.photo.size > 25 * 1024 * 1024)) throw new Error('Choose a PNG, JPG or WebP under 25 MB.');
    const photo = values.photo ? URL.createObjectURL(values.photo) : catalog.demos['character-create'].inputs[0].src;
    const character = {id: 'docs-character-' + (++serial), name: values.name || (window.WebComfyI18n?.language === 'en' ? 'Qingqing' : '晴晴'), profile: values.description || '', type: 'experimental', visualClass: 'unknown', assetUrls: {avatar: photo}, ownedPhoto: Boolean(values.photo)};
    characters.set(character.id, character);return character;
  }
  function render(route, hooks) {
    if (!route.view.startsWith('character-') || !route.characterId?.startsWith('docs-character-')) return false;
    const host = hooks.begin();host.classList.add('docs-character-workspace');
    const character = characters.get(route.characterId);
    const back = action('返回角色列表', 'Back to characters', () => hooks.navigate({view:'characters'}));
    if (!character) {host.append(label('p','此暫存角色已清除。重新整理會清除示範角色。','This session character was cleared. Refreshing clears demo characters.'),back);return true;}
    host.dataset.sessionCharacter = character.id;
    const header = node('section', '', 'panel docs-character-header');
    const avatar = node('img');avatar.src = character.assetUrls.avatar;avatar.alt = character.name;avatar.className = 'docs-character-reference';
    const details = node('div');details.append(label('p','示範角色已建立 · 本頁暫存','Demo character created · this page only','panel-note'),node('h1', character.name),node('p',character.profile,'docs-character-description'),label('p','參考照片與角色資料保留至重新整理。沒有上傳、訓練或即時生成。','Your reference photo and character details stay until refresh. No upload, training or live generation occurs.','panel-note'));
    details.querySelector('h1').dataset.i18nIgnore = '';
    details.querySelector('.docs-character-description').dataset.i18nIgnore = '';
    header.append(avatar,details);
    const controls = node('div','','workflow-actions');controls.append(back,action('刪除此角色','Delete character', async () => {remove(character.id);await hooks.refresh();hooks.navigate({view:'characters'});}),action('清除所有暫存角色','Clear all session characters', async () => {for(const id of [...characters.keys()]) remove(id);await hooks.refresh();hooks.navigate({view:'characters'});}));
    host.append(header,controls,label('h2','探索角色創作','Explore character workflows'),label('p','下列為晴晴範例的預製 GPU 實測成果，不是依本次輸入生成。自選照片不會套用到這些成果。','Below are prerecorded GPU results for the Qingqing example, not results generated from your inputs. Your chosen photo is not applied to these examples.','panel-note'));
    const grid = node('div','','docs-character-examples');
    for (const [id,zh,en] of [['img2img','換裝改圖','Outfit editing'],['character-angles','角色寫真候選','Portrait candidate'],['img2video','角色影片','Character video']]) {
      const demo = catalog.demos[id];if(!demo) continue;
      const card = node('section','','panel docs-character-example');card.append(label('h3',zh,en),label('p','預製實測範例 · 晴晴','Prerecorded example · Qingqing','panel-note'));
      const output = demo.output?.video || demo.output?.images?.[0];
      if(output) {const media = node(demo.output.video ? 'video' : 'img');media.src = output.src;if(media.tagName === 'VIDEO') {media.controls=true;media.playsInline=true;media.preload='metadata';if(output.poster)media.poster=output.poster;} else {media.alt = en + ' prerecorded example';media.loading='lazy';}card.append(media);}
      if (id === 'character-angles') card.append(label('p','寫真候選不保證臉部完全一致，請比較原始參考。','Portrait candidates do not guarantee identical facial identity; compare the original reference.','panel-note'));
      const link = label('a','開啟互動流程','Open interactive workflow','ghost small workflow-link');link.href = '#/studio/' + id;card.append(link);grid.append(card);
    }
    host.append(grid);window.WebComfyI18n?.translate(host);return true;
  }
  window.WebComfyCharacterSession = {install(app) {catalog = app;},list: () => [...characters.values()],create,render};
})();

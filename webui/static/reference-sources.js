/* External references are selected explicitly; this never submits generation. */
(() => {
  function open({h, api, openSheet, dismissTopSheet, captureSessionContext, assertSessionContext, isStaleSessionError, onSelect, textOnly = false, opener, container, fixedKind, onClose, fixedCategory, fixedCategories=[], compact=false, randomize=false, onRandom, selectedId, selectedVariantId, visualStyle=''}) {
    const session = captureSessionContext();
    const root = h('div', {class:compact?'reference-picker reference-picker-compact':'reference-picker'});
    const controls = h(container ? 'div' : 'form', {class:'reference-filters'});
    const status = h('p', {class:'catalogue-status',role:'status','aria-live':'polite'});
    const grid = h('div', {class:'catalogue-grid'});
    const details = h('div', {class:'reference-detail',hidden:true});
    const results = h('div', {class:'reference-results'}, compact?[controls,grid]:[controls,status,grid]);
    let page=1, sequence=0, total=0, importing=false;
    function select(label, values) {
      const input=h('select', {'aria-label':label}, values.map(([value,text])=>h('option',{value,text})));
      controls.appendChild(h('label',{class:'catalogue-field'},[h('span',{text:label}),input]));
      return input;
    }
    const kind=select('參考類型',[['pose','姿勢／骨架'],['costume','服裝'],...(!textOnly?[['character','人物']]:[])]);
    if (fixedKind) {kind.value=fixedKind;kind.closest('label').hidden=true;}
    const source=select('參考來源',[['','全部來源']]);
    const category=select('參考分類',[['','全部分類']]);
    if (fixedCategory) {category.append(h('option',{value:fixedCategory,text:fixedCategory}));category.value=fixedCategory;category.closest('label').hidden=true;}
    if (fixedCategories.length) category.closest('label').hidden=true;
    if (compact) source.closest('label').hidden=true;
    const search=h('input',{type:'search','aria-label':'搜尋參考',placeholder:compact?'搜尋細項':'名稱、編號、中英文關鍵字',maxlength:160});
    const searchButton=h('button',{type:container?'button':'submit',class:'ghost small',text:'搜尋'});
    controls.append(h('label',{class:'catalogue-field'},[...(!compact?[h('span',{text:'搜尋'})]:[]),search]),searchButton);
    const previous=h('button',{type:'button',class:'ghost small',text:'上一頁',onclick:()=>{page--;load();}});
    const next=h('button',{type:'button',class:'ghost small',text:'下一頁',onclick:()=>{page++;load();}});
    const retry=h('button',{type:'button',class:'ghost small',text:'重新載入',hidden:true,onclick:()=>load(retryRandom)});
    const pagination=h('div',{class:'reference-actions'},[previous,next]);
    results.append(compact?h('div',{class:'reference-footer'},[status,pagination,retry]):h('div',{class:'reference-footer'},[pagination,retry]));
    root.append(h('p',{class:'panel-sub',text:textOnly
      ?'選取後替換此欄位的描述。圖片供挑選參考，生成使用文字描述。'
      :'選取後直接作為這個輸入槽的參考圖。深度圖是一般圖片參考，不代表已啟用骨架控制。',hidden:compact}),
      results,details);
    function current(turn) {assertSessionContext(session);return root.isConnected && turn===sequence;}
    function setSelected(id, variantId) {
      if(id!==selectedId || variantId)selectedVariantId=variantId;
      selectedId=id;
      for(const card of grid.querySelectorAll('button'))card.setAttribute('aria-pressed',String(card.dataset.referenceId===id));
    }
    let retryRandom=randomize;
    async function load(pickRandom=false) {
      const turn=++sequence;
      retryRandom=pickRandom;
      status.textContent='載入中…';grid.replaceChildren();retry.hidden=true;previous.disabled=true;next.disabled=true;
      try {
        const query=new URLSearchParams({kind:kind.value,source:source.value,category:fixedCategory || category.value,q:search.value,page:String(page),...(fixedCategories.length?{categories:fixedCategories.join(',')}:{}),...(visualStyle?{visualStyle}:{}),...(pickRandom?{random:'1'}:{})});
        const data=await api('/api/reference-sources?'+query);
        if(!current(turn))return;
        const keepSource=source.value, keepCategory=category.value;
        const sourceLabels={'custom':'專案自建','ai-pose-gallery.pages.dev':'姿勢圖庫','ai-costume-gallery.pages.dev':'服裝圖庫','ai-costume-anime-gallery.pages.dev':'動漫服裝圖庫','ai-portrait-gallery.pages.dev':'人物圖庫'};
        source.replaceChildren(h('option',{value:'',text:'全部來源'}),...data.sources.map(s=>h('option',{value:s,text:sourceLabels[s] || '參考圖庫'})));
        category.replaceChildren(h('option',{value:'',text:'全部分類'}),...data.categories.map(c=>h('option',{value:c.id,text:c.label})));
        source.value=keepSource;category.value=keepCategory;total=data.total;
        status.textContent=total?`${total} 項 · ${page} / ${Math.ceil(total/data.pageSize)} 頁`:'沒有符合條件的參考，請調整搜尋或分類。';
        pagination.hidden=compact && total<=data.pageSize;
        for(const item of compact?data.items.slice(0,24):data.items) {
          // Eager loading is bounded to this page's first-angle thumbnails.
          // Alternate angles and original images stay behind the lightbox.
          const thumb=h('img',{src:item.variants[0].thumbnail,alt:'',loading:compact?'eager':'lazy',decoding:'async',referrerpolicy:'no-referrer'});
          const fallback=h('span',{class:'photo-reference-unavailable',text:'無預覽',hidden:true});
          thumb.onerror=()=>{thumb.hidden=true;fallback.hidden=false;};
          grid.appendChild(h('button',{type:'button',class:compact?'catalogue-card photo-reference-row':'catalogue-card','data-reference-id':item.id,
            'aria-label':`預覽${item.title}`,'aria-haspopup':'dialog',onclick:event=>{
            if(!compact){detail(item);return;}
            try {assertSessionContext(session);preview(item,event.currentTarget);}
            catch(error){if(!isStaleSessionError(error))status.textContent=error.message;}
          }},[
            h('span',{class:'catalogue-cover'},[thumb,fallback]),h('span',{class:'catalogue-card-copy'},[
              h('strong',{text:item.title}),...(!compact?[h('span',{text:item.code}),h('small',{text:`${item.categoryLabel} · ${item.variants.length} 個視角`})]:[]),
            ]),
          ]));
        }
        previous.disabled=page<=1;next.disabled=page*data.pageSize>=total;
        if(pickRandom && data.randomItem && onRandom)onRandom(data.randomItem);
        setSelected(selectedId);
      } catch(error) {
        if(isStaleSessionError(error)||!root.isConnected||turn!==sequence)return;
        status.textContent=error.message;retry.hidden=false;
      }
    }
    function preview(item, opener) {
      const turn=sequence;
      let variant=item.variants.find(v=>item.id===selectedId && v.id===selectedVariantId) || item.variants[0];
      let zoomed=false, applying=false;
      const image=h('img',{alt:item.title,decoding:'async',draggable:'false',referrerpolicy:'no-referrer'});
      const message=h('span',{class:'catalogue-lightbox-message',role:'status'});
      const stage=h('div',{class:'catalogue-lightbox-stage',tabindex:'0','aria-label':'圖片預覽，點圖片切換原尺寸'},[image,message]);
      const caption=h('div',{class:'catalogue-lightbox-caption','aria-live':'polite'});
      const angle=h('select',{'aria-label':'預覽視角',hidden:item.variants.length<2},item.variants.map(v=>h('option',{value:v.id,text:v.label})));
      const zoom=h('button',{type:'button',class:'ghost small',text:'原尺寸','aria-pressed':'false',onclick:()=>setZoom(!zoomed)});
      const retry=h('button',{type:'button',class:'ghost small',text:'重新載入',hidden:true,onclick:refresh});
      const apply=h('button',{type:'button',class:'primary small',text:item.kind==='pose'?'套用這個姿勢':'套用這套穿著'});
      const content=h('div',{class:'catalogue-lightbox reference-lightbox'},[
        stage,caption,h('div',{class:'catalogue-lightbox-toolbar'},[
          h('div',{class:'catalogue-lightbox-navigation'},[angle]),
          h('div',{class:'catalogue-lightbox-actions'},[retry,zoom,apply]),
        ]),
      ]);
      function setZoom(value) {
        zoomed=value;stage.classList.toggle('is-zoomed',zoomed);
        zoom.textContent=zoomed?'適合畫面':'原尺寸';zoom.setAttribute('aria-pressed',String(zoomed));stage.scrollTo(0,0);
      }
      function refresh() {
        setZoom(false);image.hidden=true;message.hidden=false;message.textContent='載入大圖…';
        retry.hidden=true;zoom.disabled=true;angle.value=variant.id;
        caption.textContent=`${item.title} · ${variant.label}`;
        image.src=variant.image;
      }
      image.onload=()=>{image.hidden=false;message.hidden=true;zoom.disabled=false;};
      image.onerror=()=>{image.hidden=true;message.hidden=false;message.textContent='大圖暫時無法載入，請重新載入。';retry.hidden=false;zoom.disabled=true;};
      angle.onchange=()=>{variant=item.variants.find(v=>v.id===angle.value);refresh();};
      image.onclick=()=>setZoom(!zoomed);
      stage.onkeydown=event=>{
        if(event.target===stage && !zoom.disabled && ['Enter',' '].includes(event.key)) {event.preventDefault();setZoom(!zoomed);}
      };
      apply.onclick=async()=>{
        if(applying)return;
        applying=true;apply.disabled=true;angle.disabled=true;
        try {
          if(!current(turn) || !content.isConnected)return;
          const applied=await onSelect(item,variant,false);
          if(!current(turn) || !content.isConnected)return;
          if(applied!==false){setSelected(item.id,variant.id);dismissTopSheet();}
          else caption.textContent='原本的選項已變更，請關閉預覽後重新選取。';
        } catch(error) {
          if(!isStaleSessionError(error) && content.isConnected)caption.textContent=error.message;
        } finally {applying=false;apply.disabled=false;angle.disabled=false;}
      };
      refresh();
      openSheet({id:'reference-lightbox',title:item.kind==='pose'?'姿勢預覽':'穿著預覽',opener,className:'catalogue-lightbox-sheet reference-lightbox-sheet',content});
    }
    function detail(item) {
      results.hidden=true;details.hidden=false;details.replaceChildren();
      let variant=item.variants[0];
      const angle=h('select',{'aria-label':'參考視角'},item.variants.map(v=>h('option',{value:v.id,text:v.label})));
      const depth=h('input',{type:'checkbox','aria-label':'使用深度圖'});
      const preview=h('img',{alt:item.title,referrerpolicy:'no-referrer'});
      const message=h('p',{role:'status',class:'panel-sub'});
      const button=h('button',{type:'button',class:'primary small',text:textOnly?(item.kind==='pose'?'套用這個姿勢':'套用這套穿著'):'使用這張參考圖'});
      function refresh() {
        if(!variant.depth || textOnly)depth.checked=false;
        depth.disabled=!variant.depth || textOnly;
        preview.hidden=false;preview.src=(depth.checked?variant.depth:variant).image;
        message.textContent='';
      }
      preview.onerror=()=>{preview.hidden=true;message.textContent='預覽暫時無法載入，請稍後重試。';};
      angle.onchange=()=>{variant=item.variants.find(v=>v.id===angle.value);refresh();};
      depth.onchange=refresh;
      button.onclick=async()=>{
        if(importing)return;
        importing=true;button.disabled=true;angle.disabled=true;depth.disabled=true;message.textContent='套用參考中…';
        try {
          assertSessionContext(session);
          if (!root.isConnected) return;
          const applied=await onSelect(item,variant,depth.checked);
          assertSessionContext(session);
          if(!root.isConnected)return;
          if(applied!==false) {if (container) onClose?.(); else dismissTopSheet();}
          else message.textContent='原本的輸入欄位已變更，請重新選取。';
        } catch(error) {if(!isStaleSessionError(error)&&root.isConnected)message.textContent=error.message;}
        finally {importing=false;button.disabled=false;angle.disabled=false;depth.disabled=!variant.depth||textOnly;}
      };
      const back=h('button',{type:'button',class:'ghost small',text:'返回列表',onclick:()=>{if(!importing){details.hidden=true;results.hidden=false;}}});
      details.append(back,h('h3',{text:item.title}),h('div',{class:'reference-preview'},[preview]),
        h('div',{class:'reference-actions'},[h('label',{class:'catalogue-field'},[h('span',{text:'視角'}),angle]),
          ...(!textOnly?[h('label',{},[depth,' 使用深度圖'])]:[])]),
        h('div',{class:'reference-actions'},[button]),message);
      refresh();
    }
    const searchNow=()=>{page=1;load();};
    if (container) {
      searchButton.onclick=searchNow;
      search.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();e.stopPropagation();searchNow();}};
    } else controls.onsubmit=e=>{e.preventDefault();searchNow();};
    kind.onchange=()=>{source.value='';category.value='';page=1;load();};
    source.onchange=category.onchange=()=>{page=1;load();};
    if (container) container.append(root);
    else openSheet({id:'reference-source-picker',title:'姿勢與服裝參考',opener,className:'catalogue-detail-sheet',content:root});
    load(randomize);
    return {setSelected,random:()=>{search.value='';page=1;return load(true);}};
  }
  window.ReferenceSources={open};
})();

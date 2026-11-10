/* Shared portrait prompt composer. Reference browsing is supplied by the host. */
(() => {
  const option = (id, label, text, levels = []) => ({id, label, text, levels});
  const catalog = {
    level: [
      option('dressy', '華服禮裝', 'Clothing style: ornate formal attire.'),
      option('formal', '正式穿著', 'Clothing style: polished professional attire.'),
      option('everyday', '日常包覆', 'Clothing coverage: fully clothed everyday styling.'),
      option('light', '清涼穿著', 'Clothing style: lightweight summer clothing.'),
      option('shoulders', '露肩造型', 'Clothing style: an off-shoulder design.'),
      option('swim', '泳裝造型', 'Clothing style: swimwear for an outdoor beach portrait.'),
    ],
    outfit: [
      option('tee', '素色上衣＋牛仔褲', 'Wearing a plain T-shirt and jeans.', ['everyday','light']),
      option('shirt', '襯衫＋長褲', 'Wearing a button-up shirt and tailored trousers.', ['everyday','formal']),
      option('knit', '針織衫＋長褲', 'Wearing a soft knit sweater and trousers.', ['everyday']),
      option('hoodie', '連帽上衣', 'Wearing a casual hoodie and jeans.', ['everyday']),
      option('trench', '風衣造型', 'Wearing a trench coat over a fully clothed outfit.', ['everyday','formal']),
      option('leather', '皮衣造型', 'Wearing a leather jacket over a T-shirt and trousers.', ['everyday']),
      option('suit', '西裝套裝', 'Wearing a tailored suit with a button-up shirt.', ['formal']),
      option('business-dress', '正式洋裝', 'Wearing an elegant knee-length business dress.', ['formal']),
      option('gown', '晚禮服', 'Wearing an ornate long-sleeved evening gown.', ['dressy']),
      option('qipao', '旗袍', 'Wearing an embroidered high-collar qipao.', ['dressy','formal']),
      option('hanfu', '漢服', 'Wearing layered traditional hanfu robes.', ['dressy']),
      option('kimono', '和服', 'Wearing a traditional kimono with an obi sash.', ['dressy']),
      option('linen', '亞麻襯衫＋短褲', 'Wearing a lightweight linen shirt and knee-length shorts.', ['light']),
      option('summer-dress', '夏日洋裝', 'Wearing a light knee-length summer dress.', ['light']),
      option('sport', '運動上衣＋運動褲', 'Wearing an athletic T-shirt and running shorts.', ['light','everyday']),
      option('shoulder-dress', '露肩長洋裝', 'Wearing an off-shoulder ankle-length dress.', ['shoulders']),
      option('shoulder-top', '露肩上衣＋長褲', 'Wearing an off-shoulder blouse and trousers.', ['shoulders']),
      option('onepiece', '連身泳裝', 'Wearing a one-piece swimsuit.', ['swim']),
      option('swimtrunks', '泳褲＋泳衣上衣', 'Wearing swim trunks and a rash guard.', ['swim']),
    ],
    scene: [
      ['cafe','咖啡廳窗邊','a cozy cafe beside a sunlit window', 'cafe tables, chairs and the sunlit window'],
      ['office','明亮辦公室','a bright modern office', 'office desks, computer monitors and large glass windows'], ['studio','簡潔攝影棚','a minimalist photography studio'],
      ['street','城市街道','a lively city street'], ['night','城市夜景','a city rooftop with softly glowing night lights'],
      ['bookstore','書店','a quiet bookstore with wooden shelves'], ['gallery','美術館','a spacious art gallery'],
      ['home','居家客廳','a comfortable living room'], ['balcony','陽台','a plant-filled balcony'],
      ['garden','花園','a blooming garden', 'flower beds, green shrubs and a garden path'], ['park','公園','a green park with dappled sunlight'],
      ['forest','森林','a peaceful forest'], ['beach','海灘','a sandy beach beside the ocean'],
      ['mountain','山景','a scenic mountain viewpoint'], ['lake','湖畔','a calm lakeside'],
      ['snow','雪景','a snowy landscape'], ['autumn','秋日步道','an autumn path with golden leaves'],
      ['sakura','櫻花樹下','a path lined with cherry blossom trees'],
      ['palace','宮廷','an ornate palace hall'], ['courtyard','古風庭院','a traditional courtyard'],
      ['cyberpunk','未來城市','a futuristic city with colorful neon lights'], ['space','太空站','a futuristic space station'],
    ].map(([id,label,text,details]) => ({...option(id,label,`Setting: ${text}${details ? ', with ' + details + ' visible in the background' : ''}.`), legacyText:`Setting: ${text}.`})),
    pose: [
      ['environment','人物與場景','standing naturally, medium shot, visible surroundings'],
      ['portrait','正面半身肖像','facing the camera in a head-and-shoulders professional portrait, relaxed shoulders, with the entire face clearly visible'],
      ['standing','自然站姿','standing naturally with relaxed arms'], ['walking','慢步行走','walking casually'],
      ['seated','椅上端坐','sitting upright on a chair'], ['bench','長椅坐姿','sitting comfortably on a park bench'],
      ['leaning','倚牆站姿','leaning gently against a wall'], ['turned','側身回眸','turning slightly and looking back at the camera'],
      ['profile','側面肖像','a relaxed side-profile pose'], ['arms','雙手交叉','standing with arms loosely crossed'],
      ['pockets','雙手插口袋','standing casually with hands in pockets'], ['wave','抬手揮手','waving hello naturally'],
      ['reading','手持書本','holding and reading a book'], ['coffee','手持咖啡','holding a coffee cup'],
      ['flowers','手捧花束','holding a bouquet of flowers'], ['looking','望向遠方','looking thoughtfully into the distance'],
    ].map(([id,label,text]) => option(id,label,`Pose: ${text}.`)),
  };
  const fields = [['level','服裝分級'],['outfit','穿著'],['scene','場景'],['pose','姿勢']];
  const bundles = [
    ['咖啡廳日常', {level:'everyday',outfit:'knit',scene:'cafe',pose:'coffee'}],
    ['職場形象', {level:'formal',outfit:'suit',scene:'office',pose:'environment'}],
    ['城市街拍', {level:'everyday',outfit:'leather',scene:'street',pose:'walking'}],
    ['夏日海邊', {level:'light',outfit:'linen',scene:'beach',pose:'looking'}],
    ['花園洋裝', {level:'light',outfit:'summer-dress',scene:'garden',pose:'flowers'}],
    ['古風庭院', {level:'dressy',outfit:'hanfu',scene:'courtyard',pose:'standing'}],
    ['櫻花和服', {level:'dressy',outfit:'kimono',scene:'sakura',pose:'turned'}],
    ['未來城市', {level:'everyday',outfit:'trench',scene:'cyberpunk',pose:'pockets'}],
  ];
  const prefix = 'Image composition.';
  const isReference = value => value?.reference === true;
  const singleLine = value => String(value || '').replace(/[\r\n]+/g, ' ').trim();

  const groups = {"outfit": {"street": "日常穿搭", "autumn": "秋日穿搭", "suit": "西裝穿搭", "spring": "春日穿搭", "summer": "夏日穿搭", "party": "派對禮服", "winter": "冬日穿搭", "lingerie": "內衣", "fantasy": "幻想服飾", "swimwear": "泳裝", "expressive-outfits": "魅力穿搭"}, "pose": {"sitting": "坐姿", "standing": "站姿", "movement": "行走與跑步", "crouching": "蹲姿", "kneeling": "跪立", "gestures": "手勢與表情動作", "twisting": "扭身", "arching": "後仰", "side-sitting": "側坐與 W 坐姿", "lying": "臥姿", "leaning": "前傾", "kneeling-all-fours": "跪姿與四肢支撐", "special": "特殊姿勢", "torso-study": "胸廓與手臂動作", "expressive-upper": "上半身曲線", "pair-standing": "雙人站姿", "close-faces": "靠近與互動", "pair-supine": "雙人仰臥構圖", "pair-rear": "背向互動", "pair-sitting": "雙人坐姿", "pair-back-seated": "背向坐姿", "pair-overlap": "雙人重疊構圖", "pair-side": "雙人側臥", "pair-facing": "面對面坐姿", "standing-embrace": "站姿相擁", "seated-embrace": "坐姿相擁", "embrace": "相擁"}};
  const parentGroups = {
    outfit: [
      {id:'daily',label:'日常與季節',categories:['street','spring','summer','autumn','winter']},
      {id:'formal',label:'正式與禮服',categories:['suit','party']},
      {id:'fantasy',label:'傳統與幻想',categories:['fantasy']},
      {id:'swim',label:'泳裝',categories:['swimwear']},
      {id:'intimate',label:'貼身與魅力',categories:['lingerie','expressive-outfits']},
    ],
    pose: [
      {id:'standing',label:'站立與行走',categories:['standing','movement','leaning']},
      {id:'seated',label:'坐姿與蹲跪',categories:['sitting','side-sitting','crouching','kneeling','kneeling-all-fours']},
      {id:'lying',label:'臥姿與伸展',categories:['lying','arching','twisting']},
      {id:'gesture',label:'手勢與上半身',categories:['gestures','torso-study','expressive-upper','special']},
      {id:'pair',label:'雙人互動',categories:['pair-standing','close-faces','pair-supine','pair-rear','pair-sitting','pair-back-seated','pair-overlap','pair-side','pair-facing']},
      {id:'embrace',label:'相擁',categories:['standing-embrace','seated-embrace','embrace']},
    ],
  };
  const parentOf = (key, category) => parentGroups[key].find(group=>group.categories.includes(category))?.id || '';
  const builtinGroups = {
    outfit: {tee:'street',shirt:'suit',knit:'autumn',hoodie:'street',trench:'autumn',leather:'street',suit:'suit','business-dress':'suit',gown:'party',qipao:'fantasy',hanfu:'fantasy',kimono:'fantasy',linen:'summer','summer-dress':'summer',sport:'street','shoulder-dress':'party','shoulder-top':'summer',onepiece:'swimwear',swimtrunks:'swimwear'},
    pose: {environment:'standing',portrait:'standing',standing:'standing',walking:'movement',seated:'sitting',bench:'sitting',leaning:'standing',turned:'twisting',profile:'standing',arms:'gestures',pockets:'standing',wave:'gestures',reading:'gestures',coffee:'gestures',flowers:'gestures',looking:'standing'},
  };
  const angleLabels = {front:'正面',side:'側面',high:'俯視',low:'仰視',diag:'斜側'};
  const sceneDetails = {cafe:'背景可見咖啡桌椅與灑入陽光的窗戶',office:'背景可見辦公桌、電腦螢幕與大片玻璃窗',garden:'背景可見花圃、綠色灌木與花園步道'};
  const sceneText = item => item ? item.label + (sceneDetails[item.id] ? `，${sceneDetails[item.id]}` : '') : '';
  const poseDetails = {environment:'人物與場景，自然站立，中景，周遭環境清楚可見',portrait:'正面半身肖像，肩膀放鬆，完整臉部清楚可見'};
  const referenceTitle = ref => /\(([^()]*[\u3400-\u9fff][^()]*)\)$/.exec(ref.title)?.[1] || ref.title;
  function groupOf(key, value) {return isReference(value) ? value.category || '' : builtinGroups[key][value] || '';}
  function composeChinese(selection, subject) {
    const lines=[];
    if (selection.scene) lines.push(`場景：${sceneText(catalog.scene.find(x=>x.id===selection.scene))}。`);
    for (const key of ['outfit','pose']) {
      const value=selection[key];if(!value)continue;
      const title=isReference(value)?referenceTitle(value):(key==='pose' && poseDetails[value]) || catalog[key].find(x=>x.id===value)?.label;
      if(!title)continue;
      const category=groups[key][groupOf(key,value)] || '其他';
      const angle=isReference(value)?angleLabels[value.angle] || value.angle || '不限':'不限';
      lines.push(`${key==='pose'?'姿勢':'穿著'}（${category}）：${singleLine(title)}；視角：${singleLine(angle)}。`);
    }
    return lines.length ? ['畫面構圖。',...(['woman','man'].includes(subject)?[`人物：成年${subject==='woman'?'女性':'男性'}。`]:[]),...lines].join('\n') : '';
  }
  function restoreChinese(value) {
    const [first,...rest]=value.split('\n\n');
    if(!first.startsWith('畫面構圖。'))return null;
    const selection={__zh:true};let subject='';
    for(const line of first.split('\n').slice(1)) {
      const scene=/^場景：(.+)。$/.exec(line), ref=/^(姿勢|穿著)（(.+)）：(.+)；視角：(.+)。$/.exec(line);
      if(scene)selection.scene=catalog.scene.find(x=>sceneText(x)===scene[1])?.id;
      else if(ref) {
        const key=ref[1]==='姿勢'?'pose':'outfit';
        selection[key]={reference:true,category:Object.keys(groups[key]).find(id=>groups[key][id]===ref[2]) || '',title:ref[3],angle:ref[4]};
      } else if(line==='人物：成年女性。')subject='woman';
      else if(line==='人物：成年男性。')subject='man';
    }
    return composeChinese(selection,subject)===first
      ? {selection,subject,generated:first,custom:rest.join('\n\n'),manual:false}
      : {selection:{},subject:'',generated:'',custom:value,manual:true};
  }

  function compose(selection, subject = "") {
    if(selection.__zh)return composeChinese(selection,subject);
    const order = selection.__legacyOrder ? fields.map(([key]) => key) : ['scene', 'pose', 'outfit', 'level'];
    const parts = order.map(key => {const item = catalog[key].find(item => item.id === selection[key]);return selection.__legacyOrder && item?.legacyText ? item.legacyText : item?.text;}).filter(Boolean);
    const references = ['pose','outfit'].filter(key => isReference(selection[key])).map(key => {
      const ref = selection[key];
      return `${key === 'pose' ? 'Pose' : 'Outfit'} reference: ${singleLine(ref.title)}; view: ${singleLine(ref.angle)}.`;
    });
    return parts.length || references.length
      ? [[prefix, ...(['woman','man'].includes(subject) ? [`Subject: adult ${subject}.`] : []), ...parts].join(' '), ...references].join('\n') : '';
  }
  function restore(value) {
    const chinese=restoreChinese(value);if(chinese)return chinese;
    const [first,...rest] = value.split('\n\n');
    const selection = {};
    const subject = /^Image composition\. Subject: adult (woman|man)\./.exec(first)?.[1] || '';
    if (first.startsWith(prefix)) {
      const [base, ...references] = first.split('\n');
      for (const [key] of fields) selection[key] = catalog[key].find(item => (base.includes(item.text) || (item.legacyText && base.includes(item.legacyText))))?.id || '';
      for (const line of references) {
        const match = /^(Pose|Outfit) reference: (.+); view: (.+)\.$/.exec(line);
        if (match) selection[match[1] === 'Pose' ? 'pose' : 'outfit'] = {reference:true, title:match[2], angle:match[3]};
      }
      if (compose(selection, subject) === first) return {selection, subject, generated:first, custom:rest.join('\n\n'), manual:false};
      selection.__legacyOrder = true;
      if (compose(selection, subject) === first) return {selection, subject, generated:first, custom:rest.join('\n\n'), manual:false};
    }
    return {selection:{}, subject:'', generated:'', custom:value, manual:first.startsWith(prefix)};
  }
  function mount(input, {h, browseReferences, visualStyle='realistic'}) {
    let {selection, subject, generated, custom, manual}=restore(input.value);
    let writing=false, undo=null, lastValue=input.value;
    const selectedGroups={}, selectedParents={}, controls={}, detailControls={}, summaries={}, hosts={}, pickers={}, randomButtons={};
    const message=h('p',{class:'panel-sub photo-preset-status',role:'status','aria-live':'polite'});
    const rebuild=h('button',{type:'button',class:'ghost small',text:'用選項重建提示詞',hidden:true});
    const undoButton=h('button',{type:'button',class:'ghost small',text:'還原上次提示詞',hidden:true});
    const scene=h('select',{'aria-label':'拍攝場景','data-photo-preset':'scene'},[
      h('option',{value:'',text:'不指定'}),...catalog.scene.map(x=>h('option',{value:x.id,text:x.label})),
    ]);
    function write(value) {
      input.value=value;lastValue=value;writing=true;
      try {input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));}
      finally {writing=false;}
    }
    function sync() {
      scene.value=selection.scene || '';
      for(const key of ['outfit','pose']) {
        if(!controls[key])continue;
        controls[key].value=selectedParents[key] || '';
        const parent=parentGroups[key].find(group=>group.id===selectedParents[key]);
        detailControls[key].replaceChildren(h('option',{value:'',text:'全部細項'}),
          ...(parent?.categories || []).map(id=>h('option',{value:id,text:groups[key][id]})));
        detailControls[key].value=selectedGroups[key] || '';
        detailControls[key].closest('label').hidden=!parent;
        const value=selection[key], summary=summaries[key];summary.replaceChildren();
        summary.hidden=!value || value.categoryOnly;
        randomButtons[key].hidden=!selectedParents[key];
        if(!summary.hidden) {
          const title=isReference(value)?referenceTitle(value):catalog[key].find(x=>x.id===value)?.label;
          const label=value.random?'隨機選用':'已固定';
          summary.append(h('span',{class:'photo-reference-copy',title:`${label}：${title}`},[
            h('strong',{text:`${label}：${title}`}),
          ]));
          if(value.variants?.length>1) {
            const angle=h('select',{'aria-label':`${key==='pose'?'姿勢':'穿著'}視角`},value.variants.map(v=>h('option',{value:v.id,text:angleLabels[v.angle] || v.label})));
            angle.value=value.variantId;angle.onchange=()=>selectReference(key,value.item,value.variants.find(v=>v.id===angle.value),false);
            summary.append(angle);
          }
        }
      }
      rebuild.hidden=!manual || !compose({...selection,__zh:true},subject);undoButton.hidden=!undo;
    }
    function apply(force=false,notice='') {
      if(input.value!==lastValue)acceptInput();
      selection.__zh=true;
      if(manual&&!force){message.textContent='已保留你手動修改的提示詞；可按「用選項重建提示詞」取代內容。';sync();return;}
      const next=compose(selection,subject), value=[next,custom].filter(Boolean).join('\n\n');
      if(input.maxLength>0&&value.length>input.maxLength){manual=true;message.textContent=`提示詞超過 ${input.maxLength} 字，已保留原文，請縮短補充描述。`;sync();return;}
      generated=next;manual=false;write(value);sync();
      message.textContent=notice || (next?'':'已清除選項，保留補充描述。');
    }
    function selectReference(key,item,variant,random) {
      const parent=parentGroups[key].find(group=>group.id===selectedParents[key]);
      if(!input.isConnected || !parent?.categories.includes(item.category) ||
        (selectedGroups[key] && item.category!==selectedGroups[key]))return false;
      selection[key]={reference:true,category:item.category,title:item.title,angle:angleLabels[variant.angle] || variant.label,
        thumbnail:variant.thumbnail,id:item.id,variantId:variant.id,variants:item.variants,item,random};
      if(key==='outfit')selection.level='';
      apply();pickers[key]?.setSelected(item.id,variant.id);return true;
    }
    function showChoices(key,randomize=false) {
      const host=hosts[key];host.replaceChildren();pickers[key]=null;
      const parent=parentGroups[key].find(group=>group.id===selectedParents[key]);host.hidden=!parent;
      if(!parent || !browseReferences)return;
      pickers[key]=browseReferences({container:host,fixedKind:key==='pose'?'pose':'costume',fixedCategory:selectedGroups[key],fixedCategories:parent.categories,
        compact:true,textOnly:true,randomize,selectedId:selection[key]?.id,selectedVariantId:selection[key]?.variantId,
        visualStyle:key==='outfit'?visualStyle:'',
        onSelect:(item,variant)=>selectReference(key,item,variant,false),
        onRandom:(item)=>selectReference(key,item,item.variants[0],true)});
    }
    const grid=h('div',{class:'photo-preset-grid'});
    for(const key of ['outfit','pose']) {
      const label=key==='pose'?'姿勢':'穿著';selectedGroups[key]=groupOf(key,selection[key]);selectedParents[key]=parentOf(key,selectedGroups[key]);
      const select=h('select',{'aria-label':`${label}大分類`,'data-photo-preset':key},[
        h('option',{value:'',text:'不指定'}),...parentGroups[key].map(({id,label})=>h('option',{value:id,text:label})),
      ]);controls[key]=select;
      const detail=h('select',{'aria-label':`${label}細分類`});detailControls[key]=detail;
      summaries[key]=h('div',{class:'photo-reference-selected',hidden:true});
      hosts[key]=h('div',{class:'photo-reference-choices',hidden:true});
      randomButtons[key]=h('button',{type:'button',class:'ghost small',text:'換一個','aria-label':`重新隨機挑選${label}`,hidden:true,onclick:()=>pickers[key]?.random()});
      select.onchange=()=>{
        selectedParents[key]=select.value;selectedGroups[key]='';selection[key]='';
        if(key==='outfit')selection.level='';
        apply(false,select.value?'正在從所選分類隨機挑選細項…':'已取消此分類。');showChoices(key,!!select.value);
      };
      detail.onchange=()=>{
        selectedGroups[key]=detail.value;selection[key]='';
        if(key==='outfit')selection.level='';
        apply(false,'正在從所選細分類隨機挑選…');showChoices(key,true);
      };
      grid.append(h('section',{class:'photo-choice','aria-label':`${label}選擇`},[
        h('div',{class:'photo-choice-heading'},[
          h('label',{class:'field'},[h('span',{class:'field-label',text:label}),select]),randomButtons[key],
        ]),h('label',{class:'field photo-choice-detail',hidden:true},[h('span',{class:'field-label',text:'細分類'}),detail]),summaries[key],hosts[key],
      ]));
    }
    scene.onchange=()=>{selection.scene=scene.value;apply();};
    const refreshChoices=()=>{for(const key of ['outfit','pose']){selectedGroups[key]=groupOf(key,selection[key]);selectedParents[key]=parentOf(key,selectedGroups[key]);showChoices(key);}sync();};
    const quick=h('div',{class:'photo-preset-quick','aria-label':'快速拍攝搭配'},bundles.map(([label,values])=>h('button',{
      type:'button',class:'ghost small',text:label,onclick:()=>{selection={...values};apply();refreshChoices();},
    })));
    const clear=h('button',{type:'button',class:'ghost small',text:'清除拍攝選項',onclick:()=>{selection={};apply();refreshChoices();}});
    rebuild.onclick=()=>{undo={value:input.value,selection:{...selection},generated,custom,manual};custom='';apply(true);};
    undoButton.onclick=()=>{if(!undo)return;const previous=undo;undo=null;({selection,generated,custom,manual}=previous);write(previous.value);refreshChoices();message.textContent='已還原上次提示詞。';};
    function acceptInput() {
      if(writing)return;lastValue=input.value;
      if(generated&&(input.value===generated||input.value.startsWith(generated+'\n\n'))){custom=input.value.slice(generated.length).replace(/^\n\n/,'');manual=false;}
      else if(!generated&&!manual)custom=input.value;
      else{manual=true;message.textContent='手動編輯中，選項不會覆蓋你的修改。';}
      sync();
    }
    input.addEventListener('input',acceptInput);input.addEventListener('change',acceptInput);
    const node=h('section',{class:'photo-presets','aria-label':'拍攝預設'},[
      h('div',{class:'photo-preset-heading'},[h('strong',{text:'畫面搭配'}),h('span',{class:'panel-sub',text:'先選大分類，再挑細項；點小圖預覽後套用。'})]),
      h('div',{class:'photo-preset-toolbar'},[quick,h('label',{class:'field photo-scene'},[h('span',{class:'field-label',text:'場景'}),scene])]),grid,
      h('div',{class:'photo-preset-actions'},[clear,rebuild,undoButton]),message,
    ]);
    sync();queueMicrotask(()=>{if(node.isConnected)refreshChoices();});
    node.setSubject=value=>{const next=['woman','man'].includes(value)?value:'';if(next===subject)return;subject=next;if(!manual&&generated)apply();};
    node.setVisualStyle=value=>{
      if(!['anime','realistic'].includes(value) || value===visualStyle)return;
      visualStyle=value;
      const category=selectedParents.outfit;
      selection.outfit='';
      selection.level='';
      apply(false,category?'畫風已切換，正在重新挑選穿著…':'畫風已切換。');
      showChoices('outfit',!!category);
    };
    return node;
  }
  const api={catalog,bundles,groups,parentGroups,compose,restore,mount};
  if(typeof module!=='undefined')module.exports=api;
  if(typeof window!=='undefined')window.PhotoPromptPresets=api;
})();

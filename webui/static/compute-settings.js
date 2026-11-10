/* Provider credentials are write-only. Never persist them in browser storage. */
(() => {
  'use strict';
  const providers={comfy_cloud:{label:'Comfy Cloud',endpoint:'https://cloud.comfy.org',currency:'點'},runninghub:{label:'RunningHub',endpoint:'https://www.runninghub.ai',currency:'金幣'}};
  const sessions=new Set();
  const lanes={local:'本機',auto:'自動安排',colab:'Google Colab',autoip:'AutoIP',comfy_cloud:'Comfy Cloud',runninghub:'RunningHub'};
  const cleanRecipe=value=>{const {verified,...recipe}=JSON.parse(JSON.stringify(value));return recipe;};
  const el=(tag,attrs={},children=[])=>{
    const node=document.createElement(tag);
    for(const [key,value] of Object.entries(attrs)) {
      if(key==='text')node.textContent=value;
      else if(key.startsWith('on'))node.addEventListener(key.slice(2),value);
      else if(value!==false&&value!=null)node.setAttribute(key,value===true?'':value);
    }
    node.append(...children);return node;
  };
  const field=(label,input)=>{input.setAttribute('aria-label',label);return el('label',{class:'field'},[el('span',{text:label}),input]);};
  const button=(text,action)=>el('button',{type:'button',class:'ghost small',text,onclick:action});
  const publicError=error=>({forbidden:'需要管理員權限。',provider_invalid:'不支援這個平台。',invalid_endpoint:'平台網址不符合允許範圍。',recipe_invalid:'配方格式或欄位綁定不正確，請檢查匯入檔案。',credential_invalid:'金鑰格式不正確，請重新輸入。'}[error?.errorCode]||'設定尚未儲存。請檢查連線與配方格式後再試；新金鑰需重新輸入。');
  function open(opener,{api,openSheet,openColabAccounts,canConfigure=true,initialSection="connections"}) {
    let disposed=false,busy=false,turn=0,pendingRecipes=null,providerData=[],catalog=[],features=[];
    let preferences=null,templateBusy=false,featurePage=1;
    const section=el('select',{},Object.entries({...canConfigure?{connections:'平台連線',templates:'功能配方'}:{},capabilities:'可用功能',preferences:'預設安排'}).map(([value,text])=>el('option',{value,text})));
    section.value=[...section.options].some(item=>item.value===initialSection)?initialSection:'preferences';
    const root=el('div',{class:'compute-settings'});
    const notice=el('p',{class:'compute-note',role:'status','aria-live':'polite',text:'正在讀取平台設定…'});
    const choice=el('select',{},Object.entries(providers).map(([value,item])=>el('option',{value,text:item.label})));
    const enabled=el('input',{type:'checkbox'});
    const endpoint=el('input',{type:'url',required:true,autocomplete:'off',spellcheck:'false',maxlength:512});
    const key=el('input',{type:'password',autocomplete:'new-password',spellcheck:'false',maxlength:4096,placeholder:'保留空白則沿用現有金鑰'});
    const clearKey=el('input',{type:'checkbox'});
    const state=el('p',{class:'compute-note'});
    const recipes=el('ul',{class:'compute-config-recipes'});
    const recipeFile=el('input',{type:'file',class:'compute-file-input',accept:'.json,application/json','aria-label':'匯入配方檔案'});
    const filename=el('span',{class:'compute-note',text:'尚未選取檔案'});
    const recipePicker=field('配方檔案',recipeFile);
    recipePicker.append(el('div',{class:'compute-job-actions'},[button('選擇配方檔案',()=>recipeFile.click()),filename]));
    const importNote=el('p',{class:'compute-note',text:'選取配方 JSON；匯入後按儲存，會取代此平台的配方清單。'});
    const save=el('button',{type:'submit',class:'primary small',text:'儲存平台設定'});
    const form=el('form',{class:'compute-config-form'},[
      state,field('啟用此平台',enabled),field('平台網址',endpoint),field('API 金鑰',key),
      el('p',{class:'compute-note',text:'金鑰只送到伺服器儲存；儲存後不會回填或顯示。'}),
      field('移除已儲存的金鑰',clearKey),
      el('details',{class:'compute-config-advanced'},[
        el('summary',{text:'進階：功能配方'}),recipes,
        el('p',{class:'compute-note',text:'配方指定功能、工作流、輸入綁定與預估費用；未設定的功能會顯示不可用。'}),
        recipePicker,importNote,
      ]),save,
    ]);
    const connectionPanel=el('div',{class:'compute-settings-panel'},[form]);
    if(openColabAccounts)connectionPanel.append(button('管理 Google Colab 帳號',()=>openColabAccounts(opener)));
    connectionPanel.append(button('選用功能範本',()=>{section.value='templates';showSection();}));
    const providerField=field('平台',choice),templatePanel=el('div',{class:'compute-settings-panel'}),capabilityPanel=el('div',{class:'compute-settings-panel'}),preferencePanel=el('div',{class:'compute-settings-panel'});
    root.append(field('設定項目',section),providerField,notice,connectionPanel,templatePanel,capabilityPanel,preferencePanel);
    const live=()=>!disposed&&root.isConnected;
    const destroy=()=>{disposed=true;key.value='';recipeFile.value='';pendingRecipes=null;observer.disconnect();sessions.delete(destroy);};
    const observer=new MutationObserver(()=>{if(!root.isConnected)destroy();});
    sessions.add(destroy);
    function lock(value){busy=value;choice.disabled=value||templateBusy;for(const input of form.querySelectorAll('input,select,button'))input.disabled=value;}
    function renderRecipes(rows){
      const data=Array.isArray(rows)?rows:[];
      recipes.replaceChildren(...data.slice(0,100).map(item=>el('li',{},[
        el('strong',{text:String(item.label||item.feature_id||'功能配方').slice(0,120)}),
        el('span',{class:'compute-note',text:`${String(item.feature_id||'').slice(0,80)} · ${item.verified===true?'已有驗證成果':'尚無驗證成果'}${Number.isFinite(item.estimated_cost)?` · 預估 ${item.estimated_cost} ${providers[choice.value].currency}`:''}`}),
      ])));
      if(!data.length)recipes.append(el('li',{class:'compute-note',text:'尚未設定配方。'}));
    }
    async function refresh(){
      const current=++turn;lock(true);key.value='';clearKey.checked=false;pendingRecipes=null;recipeFile.value='';filename.textContent='尚未選取檔案';
      importNote.textContent='選取配方 JSON；匯入後按儲存，會取代此平台的配方清單。';
      try {
        const data=await api('/api/compute/providers');if(!live()||current!==turn)return;
        providerData=Array.isArray(data.providers)?data.providers:[];
        const item=providerData.find(item=>item.provider===choice.value);
        enabled.checked=item?.enabled===true;endpoint.value=typeof item?.endpoint==='string'?item.endpoint:providers[choice.value].endpoint;
        state.textContent=item?.credential_configured?'金鑰已設定；連線與配方仍會在使用時檢查。':'尚未設定 API 金鑰。';
        renderRecipes(item?.recipes);renderTemplate();notice.textContent='';
      } catch(error){if(live()&&current===turn)notice.textContent='平台設定目前無法讀取，請稍後重新開啟。';}
      finally{if(live()&&current===turn)lock(false);}
    }
    choice.addEventListener('change',()=>{if(!templateBusy)refresh();});
    recipeFile.addEventListener('change',async()=>{
      const selected=recipeFile.files[0];pendingRecipes=null;filename.textContent=selected?.name||'尚未選取檔案';if(!selected)return;
      if(selected.size>1024*1024){recipeFile.value='';importNote.textContent='配方檔案上限為 1 MB。';return;}
      try {
        const parsed=JSON.parse(await selected.text());if(!live()||recipeFile.files[0]!==selected)return;
        const rows=Array.isArray(parsed)?parsed:parsed?.recipes;
        if(!Array.isArray(rows)||rows.length>64||rows.some(item=>!item||typeof item!=='object'||Array.isArray(item)))throw new Error();
        pendingRecipes=rows.map(({verified,...recipe})=>recipe);renderRecipes(pendingRecipes);importNote.textContent=`已讀取 ${rows.length} 個配方；按儲存後取代原清單。`;
      } catch(_){if(live()){recipeFile.value='';importNote.textContent='無法讀取配方清單，請選擇有效的 JSON 檔案。';}}
    });
    form.addEventListener('submit',async event=>{
      event.preventDefault();if(busy||!form.reportValidity())return;
      if(clearKey.checked&&key.value){notice.textContent='請選擇更新金鑰或移除金鑰，不能同時操作。';return;}
      const body={enabled:enabled.checked,endpoint:endpoint.value.trim()};
      if(key.value)body.api_key=key.value;if(clearKey.checked)body.clear_api_key=true;
      if(pendingRecipes!==null)body.recipes=pendingRecipes;
      const provider=choice.value;key.value='';lock(true);notice.textContent='正在儲存…';
      try {
        await api('/api/compute/providers/'+provider,{method:'POST',body:JSON.stringify(body)});
        delete body.api_key;if(!live())return;await refresh();if(live())notice.textContent='平台設定已儲存；尚未啟動生成。';
      } catch(error){delete body.api_key;if(live())notice.textContent=publicError(error);}
      finally{if(live())lock(false);}
    });
    const template=el('select',{required:true,'aria-label':'功能範本'});
    const templateDescription=el('p',{class:'compute-note'}),requirements=el('ul',{class:'compute-requirements'});
    const arity=el('p',{class:'compute-note'}),templateNotice=el('p',{class:'compute-note',role:'status','aria-live':'polite'});
    const confirmed=el('input',{type:'checkbox',required:true});
    const replace=el('input',{type:'checkbox'}),replaceField=field('替換此功能現有配方',replace);
    const cost=el('input',{type:'number',required:true,min:0.000001,step:'any',placeholder:'請依平台估價填寫'});
    const costNote=el('p',{class:'compute-note'});
    const workflowId=el('input',{type:'text',maxlength:128,autocomplete:'off',spellcheck:'false'});
    const workflowField=field('RunningHub 已發布工作流 ID',workflowId);
    const download=button('下載工作流供發布',()=>{
      const item=catalog.find(item=>item.id===template.value);if(!item?.recipe?.workflow)return;
      const url=URL.createObjectURL(new Blob([JSON.stringify(item.recipe.workflow,null,2)],{type:'application/json'}));
      const link=el('a',{href:url,download:String(item.id).replace(/[^a-zA-Z0-9_-]/g,'_')+'-workflow-api.json'});
      link.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
    });
    const templateSave=el('button',{type:'submit',class:'primary small',text:'儲存功能配方'});
    const templateForm=el('form',{class:'compute-config-form'},[
      field('功能範本',template),templateDescription,arity,requirements,
      el('p',{class:'compute-note',text:'範本通過結構與輸入契約檢查，尚未在你的平台或 GPU 實跑。'}),
      download,workflowField,field('每次預估用量',cost),costNote,
      field('已確認平台具備列出的模型與節點',confirmed),replaceField,templateSave,templateNotice,
    ]);
    templatePanel.append(templateForm);
    function renderTemplate(){
      const item=catalog.find(item=>item.id===template.value),configured=providerData.find(item=>item.provider===choice.value);
      templateDescription.textContent=String(item?.description||'選擇功能範本，再確認平台需求。').slice(0,800);
      requirements.replaceChildren(...(item?.requirements||[]).slice(0,24).map(text=>el('li',{text:String(text).slice(0,300)})));
      const slots=item?.input_slots||[];
      arity.textContent=slots.length?'參考素材：'+slots.map(slot=>typeof slot==='string'?slot:slot.label||slot.id||'參考素材').join('、'):'不需要上傳參考素材。';
      const existing=(configured?.recipes||[]).find(recipe=>recipe.feature_id===item?.feature_id);
      replaceField.hidden=!existing;replace.required=!!existing;replace.checked=false;
      workflowField.hidden=choice.value!=='runninghub';workflowId.required=!workflowField.hidden;
      cost.value='';confirmed.checked=false;templateNotice.textContent='';
      costNote.textContent=`費用單位：${providers[choice.value].currency}。範本中的數值不是平台報價；請自行填寫預估費用。`;
      templateSave.disabled=!item||!configured||templateBusy;download.disabled=!item?.recipe?.workflow;
    }
    template.addEventListener('change',renderTemplate);
    async function loadCatalog(){
      try{const data=await api('/api/compute/recipe-catalog');if(!live())return;
        catalog=(data.templates||[]).filter(item=>typeof item.id==='string'&&item.recipe&&item.feature_id);
        template.replaceChildren(el('option',{value:'',text:'請選擇功能範本'}),...catalog.map(item=>el('option',{value:item.id,text:item.label||item.id})));
        renderTemplate();
      }catch(_){if(live()){templateSave.disabled=true;templateNotice.textContent='範本目前無法讀取，請稍後重新開啟。';}}
    }
    templateForm.addEventListener('submit',async event=>{
      event.preventDefault();if(templateBusy||!templateForm.reportValidity())return;
      const item=catalog.find(item=>item.id===template.value);if(!item)return;
      const provider=choice.value,value=cleanRecipe(item.recipe),replacement=replace.checked;
      value.estimated_cost=Number(cost.value);
      if(provider==='runninghub'){delete value.workflow;delete value.partner_nodes;value.workflow_id=workflowId.value.trim();}
      templateBusy=true;templateSave.disabled=true;choice.disabled=true;templateNotice.textContent='正在儲存配方…';
      try{
        const snapshot=await api('/api/compute/providers');if(!live())return;
        const existing=(snapshot.providers||[]).find(entry=>entry.provider===provider)?.recipes||[];
        if(existing.some(entry=>entry.feature_id===value.feature_id)&&!replacement){
          providerData=snapshot.providers||[];renderTemplate();templateNotice.textContent='這個功能已有配方，請確認替換後再儲存。';return;
        }
        const merged=[...existing.filter(entry=>entry.feature_id!==value.feature_id).map(cleanRecipe),value];
        await api('/api/compute/providers/'+provider,{method:'POST',body:JSON.stringify({recipes:merged})});
        if(live()){await refresh();templateNotice.textContent='功能配方已儲存；其他功能配方已保留，尚未啟動生成。';loadCapabilities();}
      }catch(_){if(live())templateNotice.textContent='配方尚未儲存，請檢查工作流 ID、費用與平台設定。';}
      finally{templateBusy=false;if(live()){choice.disabled=false;templateSave.disabled=!catalog.some(item=>item.id===template.value);}}
    });
    const search=el('input',{type:'search',placeholder:'搜尋功能',maxlength:120,'aria-label':'搜尋可用功能'});
    const platformFilter=el('select',{'aria-label':'篩選平台'},[el('option',{value:'',text:'所有平台'}),...Object.entries(lanes).filter(([id])=>id!=='auto').map(([value,text])=>el('option',{value,text}))]);
    const featureList=el('div',{class:'compute-capability-list'}),featureNotice=el('p',{class:'compute-note',role:'status'});
    const pageLabel=el('span',{class:'compute-note'});
    const previous=button('上一頁',()=>{featurePage--;renderCapabilities();}),next=button('下一頁',()=>{featurePage++;renderCapabilities();});
    capabilityPanel.append(el('p',{class:'compute-note',text:'以下是功能支援與設定狀態，不代表已在 GPU 實跑；提交時仍會檢查模型、素材、帳號與費用。'}),search,platformFilter,featureNotice,featureList,el('div',{class:'compute-job-actions'},[previous,pageLabel,next]));
    function renderCapabilities(){
      const rows=features.filter(item=>(item.title+' '+item.id).toLocaleLowerCase().includes(search.value.toLocaleLowerCase()));
      const pages=Math.max(1,Math.ceil(rows.length/10));featurePage=Math.min(pages,Math.max(1,featurePage));
      featureList.replaceChildren(...rows.slice((featurePage-1)*10,featurePage*10).map(item=>el('article',{class:'compute-capability-row'},[
        el('strong',{text:item.title||item.id}),
        ...Object.entries(lanes).filter(([lane])=>lane!=='auto'&&(!platformFilter.value||platformFilter.value===lane)).map(([lane,label])=>{
          const entry=item.providers?.[lane]||{},status={implemented:'已實作',configured:'已設定',unconfigured:'尚未設定',unsupported:'尚不支援'}[entry.status]||'狀態未知';
          return el('div',{'data-capability-lane':lane},[el('span',{text:label}),el('span',{text:status}),...(entry.reason?[el('p',{class:'compute-note',text:String(entry.reason).slice(0,360)})]:[])]);
        }),
      ])));
      pageLabel.textContent=`${featurePage} / ${pages} 頁 · ${rows.length} 項功能`;previous.disabled=featurePage<=1;next.disabled=featurePage>=pages;
    }
    search.addEventListener('input',()=>{featurePage=1;renderCapabilities();});platformFilter.addEventListener('change',renderCapabilities);
    const defaultPlacement=el('select',{'aria-label':'一般預設位置'},Object.entries(lanes).map(([value,text])=>el('option',{value,text})));
    const featureChoice=el('select',{'aria-label':'指定功能'}),featurePlacement=el('select',{'aria-label':'此功能預設位置'},[el('option',{value:'',text:'沿用一般預設'}),...Object.entries(lanes).map(([value,text])=>el('option',{value,text}))]);
    const preferenceList=el('ul',{class:'compute-config-recipes'}),preferenceNotice=el('p',{class:'compute-note',role:'status'});
    const preferenceSave=el('button',{type:'submit',class:'primary small',text:'儲存預設安排',disabled:true});
    const preferenceForm=el('form',{class:'compute-config-form'},[field('一般預設位置',defaultPlacement),field('指定功能',featureChoice),field('此功能預設位置',featurePlacement),preferenceList,preferenceSave,preferenceNotice]);
    preferencePanel.append(el('p',{class:'compute-note',text:'預設套用到新開啟的生成表單；你在單次任務選擇的位置優先，進行中的提交不會變更。'}),preferenceForm);
    function renderPreferences(){
      featurePlacement.value=preferences?.feature_placements?.[featureChoice.value]||'';
      preferenceList.replaceChildren(...Object.entries(preferences?.feature_placements||{}).map(([id,lane])=>el('li',{},[el('span',{text:`${features.find(item=>item.id===id)?.title||id}：${lanes[lane]||'沿用一般預設'}`})])));
      if(!preferenceList.children.length)preferenceList.append(el('li',{class:'compute-note',text:'所有功能沿用一般預設。'}));
    }
    featureChoice.addEventListener('change',renderPreferences);
    featurePlacement.addEventListener('change',()=>{if(!preferences||!featureChoice.value)return;const id=featureChoice.value;if(featurePlacement.value)preferences.feature_placements[id]=featurePlacement.value;else delete preferences.feature_placements[id];renderPreferences();});
    async function loadCapabilities(){
      try{const data=await api('/api/compute/capabilities');if(!live())return;
        features=(data.features||[]).filter(item=>typeof item.id==='string'&&typeof item.title==='string');
        const selected=featureChoice.value;featureChoice.replaceChildren(...features.map(item=>el('option',{value:item.id,text:item.title})));if(features.some(item=>item.id===selected))featureChoice.value=selected;
        featureNotice.textContent='';renderCapabilities();renderPreferences();
      }catch(_){if(live())featureNotice.textContent='功能狀態目前無法讀取。';}
    }
    async function loadPreferences(){
      try{const data=await api('/api/compute/preferences');if(!live())return;
        preferences={default_placement:Object.hasOwn(lanes,data.default_placement)?data.default_placement:'local',feature_placements:Object.fromEntries(Object.entries(data.feature_placements||{}).filter(([id,value])=>/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/.test(id)&&Object.hasOwn(lanes,value)))};
        defaultPlacement.value=preferences.default_placement;preferenceSave.disabled=false;renderPreferences();
      }catch(_){if(live())preferenceNotice.textContent='預設安排目前無法讀取，尚未變更任何設定。';}
    }
    preferenceForm.addEventListener('submit',async event=>{
      event.preventDefault();if(!preferences||preferenceSave.disabled)return;preferenceSave.disabled=true;
      try{await api('/api/compute/preferences',{method:'POST',body:JSON.stringify({default_placement:defaultPlacement.value,feature_placements:preferences.feature_placements})});if(live())preferenceNotice.textContent='預設安排已儲存；現有表單與任務維持原選擇。';}
      catch(_){if(live())preferenceNotice.textContent='預設安排尚未儲存，請稍後再試。';}
      finally{if(live())preferenceSave.disabled=false;}
    });
    function showSection(){
      connectionPanel.hidden=section.value!=='connections';templatePanel.hidden=section.value!=='templates';capabilityPanel.hidden=section.value!=='capabilities';preferencePanel.hidden=section.value!=='preferences';
      providerField.hidden=!['connections','templates'].includes(section.value);notice.hidden=!['connections','templates'].includes(section.value);
    }
    section.addEventListener('change',showSection);
    openSheet({id:'compute-settings',title:'運算平台',opener,content:root,className:'compute-settings-sheet',onOpen:()=>{
      observer.observe(document.body,{childList:true,subtree:true});showSection();if(canConfigure){refresh();loadCatalog();}loadCapabilities();loadPreferences();
    }});
    return {destroy};
  }
  window.ComputeSettings={open,reset:()=>{for(const destroy of [...sessions])destroy();}};
})();

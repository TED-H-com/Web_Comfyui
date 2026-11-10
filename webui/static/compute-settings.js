/* Provider credentials are write-only. Never persist them in browser storage. */
(() => {
  'use strict';
  const providers={comfy_cloud:{label:'Comfy Cloud',endpoint:'https://cloud.comfy.org',currency:'點'},runninghub:{label:'RunningHub',endpoint:'https://www.runninghub.ai',currency:'金幣'}};
  const sessions=new Set();
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
  function open(opener,{api,openSheet,openColabAccounts}) {
    let disposed=false,busy=false,turn=0,pendingRecipes=null;
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
      field('平台',choice),state,field('啟用此平台',enabled),field('平台網址',endpoint),field('API 金鑰',key),
      el('p',{class:'compute-note',text:'金鑰只送到伺服器儲存；儲存後不會回填或顯示。'}),
      field('移除已儲存的金鑰',clearKey),
      el('details',{class:'compute-config-advanced'},[
        el('summary',{text:'進階：功能配方'}),recipes,
        el('p',{class:'compute-note',text:'配方指定功能、工作流、輸入綁定與預估費用；未設定的功能會顯示不可用。'}),
        recipePicker,importNote,
      ]),save,
    ]);
    root.append(notice,form);
    if(openColabAccounts)root.append(button('管理 Google Colab 帳號',()=>openColabAccounts(opener)));
    const live=()=>!disposed&&root.isConnected;
    const destroy=()=>{disposed=true;key.value='';recipeFile.value='';pendingRecipes=null;observer.disconnect();sessions.delete(destroy);};
    const observer=new MutationObserver(()=>{if(!root.isConnected)destroy();});
    sessions.add(destroy);
    function lock(value){busy=value;for(const input of form.querySelectorAll('input,select,button'))input.disabled=value;}
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
        const item=(data.providers||[]).find(item=>item.provider===choice.value);
        enabled.checked=item?.enabled===true;endpoint.value=typeof item?.endpoint==='string'?item.endpoint:providers[choice.value].endpoint;
        state.textContent=item?.credential_configured?'金鑰已設定；連線與配方仍會在使用時檢查。':'尚未設定 API 金鑰。';
        renderRecipes(item?.recipes);notice.textContent='';
      } catch(error){if(live()&&current===turn)notice.textContent='平台設定目前無法讀取，請稍後重新開啟。';}
      finally{if(live()&&current===turn)lock(false);}
    }
    choice.addEventListener('change',refresh);
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
    openSheet({id:'compute-settings',title:'運算平台',opener,content:root,className:'compute-settings-sheet',onOpen:()=>{
      observer.observe(document.body,{childList:true,subtree:true});refresh();
    }});
    return {destroy};
  }
  window.ComputeSettings={open,reset:()=>{for(const destroy of [...sessions])destroy();}};
})();

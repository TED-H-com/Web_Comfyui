/* Shared execution placement. Preview never uploads media or starts work. */
(() => {
  'use strict';
  const mounted = new Map();
  const lanes = {local:'本機',colab:'Google Colab',autoip:'AutoIP',comfy_cloud:'Comfy Cloud',runninghub:'RunningHub'};
  const terminal = new Set(['completed','failed','cancelled','rejected']);
  const uncertain = new Set(['reserved','dispatching','submission_uncertain']);
  const states = {reserved:'已保留任務',dispatching:'正在提交',queued:'排隊中',running:'生成中',cancelling:'正在停止',
    collecting:'整理成果中',completed:'已完成',failed:'執行失敗',cancelled:'已取消',rejected:'未通過提交檢查',submission_uncertain:'提交狀態待確認'};
  const reasons = {
    adapter_unavailable:'尚未設定此執行方式',operation_unavailable:'此功能目前不支援',unsupported_feature:'此功能目前不支援',
    feature_unsupported:'此功能目前不支援',unsupported_inputs:'參考素材不符合此方式的要求',input_unsupported:'參考素材不符合此方式的要求',
    inspection_unavailable:'目前無法確認執行條件',capacity_unknown:'目前無法確認可用容量',capacity_exhausted:'目前沒有可用容量',
    concurrency_limit:'已達同時執行上限',quote_invalid:'目前無法確認用量',quote_unavailable:'目前無法確認用量',
    budget_exceeded:'預估用量超過設定上限',account_required:'請先連接可用帳號',account_unavailable:'目前沒有可用帳號',
    not_authenticated:'帳號尚未連線',insufficient_balance:'可用餘額不足',runtime_unavailable:'執行環境尚未就緒',
    local_unavailable:'本機環境尚未就緒',missing_dependencies:'執行環境尚有缺件',provider_not_configured:'尚未設定服務連線',
    hosted_not_configured:'尚未設定平台連線',hosted_disabled:'此平台已停用',recipe_unavailable:'尚未設定此功能的雲端配方',
    recipe_not_configured:'尚未設定此功能的雲端配方',workflow_not_configured:'尚未設定此功能的雲端工作流',
    provider_unavailable:'平台目前無法使用',provider_auth_failed:'平台金鑰尚未通過驗證',
    hosted_inputs_unsupported:'此雲端配方不支援目前的參考素材',hosted_settings_unsupported:'此雲端配方無法保留目前設定',
    hosted_budget_exceeded:'配方預估用量超過此平台的費用門檻',hosted_deadline_required:'請設定有效的執行期限',
    hosted_concurrency_invalid:'同時執行上限不符合此平台要求',auto_not_verified:'此配方尚無驗證成果，請先手動選用平台',
    input_validation_failed:'參考素材、角色綁定或描述尚未通過檢查',
    local_not_ready:'本機必要模型或元件尚未就緒',colab_not_installed:'尚未安裝 Colab 連線環境',colab_account_unavailable:'沒有符合餘額與使用狀態的 Colab 帳號',
    autoip_operation_unavailable:'此功能尚未提供 AutoIP 執行契約',autoip_feature_unsupported:'此功能尚未提供 AutoIP 執行方式',
    feature_unavailable:'此工具尚未開放生成',operator_required:'此工具需要操作員權限',canonical_binding_required:'此功能必須保留角色與素材綁定',
    settings_invalid:'生成設定格式不正確',setting_unsupported:'所選工作流無法保留部分設定',autoip_options_unsupported:'AutoIP 無法保留目前的模型、採樣或種子設定',
    seed_invalid:'種子設定不符合此工作流',prompt_invalid:'請檢查描述內容及長度',prompt_conflict:'描述欄位互相衝突',character_prompt_invalid:'角色描述不符合此工作流',
    style_invalid:'請選擇支援的畫風',style_conflict:'畫風設定互相衝突',visual_class_required:'請選擇動漫或寫實畫風',
    inputs_invalid:'請檢查參考素材的數量與位置',inputs_unsupported:'此方式不支援目前的參考素材',reference_invalid:'參考素材與描述不一致',
    last_frame_only_unsupported:'此方式需要首幀，不能只提供尾幀',negative_unsupported:'此工作流不支援目前的負向描述',
    engine_option_unsupported:'目前的模型或採樣選項無法套用此工作流',geometry_conflict:'尺寸與比例欄位互相衝突',
    geometry_unsupported:'目前的尺寸或比例不在此工作流支援範圍',duration_invalid:'影片長度不在此工作流支援範圍',
  };
  const el = (tag, attrs={}, children=[]) => {
    const node=document.createElement(tag);
    for(const [key,value] of Object.entries(attrs)) {
      if(key==='text')node.textContent=value;
      else if(key.startsWith('on'))node.addEventListener(key.slice(2),value);
      else if(value!==false && value!=null)node.setAttribute(key,value===true?'':value);
    }
    node.append(...children);return node;
  };
  const button=(text,onclick)=>el('button',{type:'button',class:'ghost small',text,onclick});
  const select=values=>el('select',{},values.map(([value,text])=>el('option',{value,text})));
  const field=(label,control)=>{control.setAttribute('aria-label',label);return el('label',{class:'field compute-field'},[el('span',{text:label}),control]);};
  const number=(value,min,max)=>el('input',{type:'number',value,min,max,step:1,required:true});
  const safeLink=value=>{
    if(typeof value!=='string' || !value.startsWith('/api/') || value.includes('\\') || /[\u0000-\u001f]/.test(value))return null;
    try {const url=new URL(value,location.origin);return url.origin===location.origin&&url.pathname.startsWith('/api/')?url.pathname+url.search+url.hash:null;} catch(_){return null;}
  };
  function metadata(request) {
    const result={};
    for(const key of ['featureId','settings','characterId','action','variantId','computeVisualClass','computeAutoipVideo'])if(request?.[key]!=null)result[key]=request[key];
    if(request?.files && typeof request.files==='object') {
      result.files={};
      for(const [slot,file] of Object.entries(request.files)) {
        if(!file)continue;
        result.files[slot]={};
        // Never let a host accidentally include file pixels in a preview.
        for(const key of ['name','type','size','mimeType'])if(['string','number'].includes(typeof file[key]))result.files[slot][key]=file[key];
        if(!Object.keys(result.files[slot]).length)result.files[slot].name=typeof file.serverFile==='string'?file.serverFile.split(/[\\/]/).pop():slot;
      }
    }
    return result;
  }
  function mount(form,feature,{api,getRequest,onAvailability=()=>{}}) {
    mounted.get(form)?.destroy();
    let disposed=false,turn=0,debounce=null,poll=null,inFlight=null,pending=null;
    const inputValues=new WeakMap();
    let availability={placement:'local',loading:true,eligible:false,selected_lane:null,candidates:[],error:''};
    let jobs=new Map();
    const placement=select([['local','本機'],['auto','自動安排'],['colab','Google Colab'],['autoip','AutoIP'],['comfy_cloud','Comfy Cloud'],['runninghub','RunningHub']]);
    const account=select([['','自動選擇可用帳號']]);
    const visualClass=select([['','未指定'],['anime','動漫'],['realistic','寫實']]);
    const visualField=field('圖片類型',visualClass);visualField.hidden=true;
    const videoDuration=select(Array.from({length:14},(_,index)=>[String(index+2),`${index+2} 秒`]));videoDuration.value='5';
    const videoResolution=select([['480p','480p'],['768p','768p'],['2k','2K']]);
    const videoAspect=select([['16:9','16:9 橫式'],['9:16','9:16 直式']]);
    const videoSummary=el('summary',{text:'AutoIP 影片規格'});
    const videoFields=el('details',{class:'compute-video',hidden:true,open:true},[
      videoSummary,el('div',{class:'compute-grid'},[field('AutoIP 影片秒數',videoDuration),field('AutoIP 解析度',videoResolution),field('AutoIP 畫面比例',videoAspect)]),
      el('p',{class:'compute-note',text:'以上規格只用於 AutoIP；本機與 Colab 沿用下方的幀數與畫布設定。'}),
    ]);
    const gpu=select([['L4','L4'],['G4','G4'],['A100','A100'],['H100','H100']]);
    const minutes=number(60,5,180),units=number(20,5,2000),credits=number(100,1,1000000);
    const comfyCredits=number(100,1,1000000),runninghubCoins=number(100,1,1000000);
    const accountNote=el('p',{class:'compute-note'});
    const notice=el('p',{class:'compute-note',role:'status','aria-live':'polite',text:'正在確認執行條件…'});
    const candidates=el('ul',{class:'compute-candidates','aria-label':'各執行方式的可用條件'});
    const recipe=el('p',{class:'compute-note'});
    const differences=el('ul',{class:'compute-warnings'});
    const refresh=button('更新可用狀態',()=>refreshAll());
    const retry=button('確認並重試原請求',()=>send().catch(()=>{}));retry.hidden=true;
    const controls=el('details',{class:'compute-limits'},[
      el('summary',{text:'執行上限與 Colab 帳號'}),
      el('div',{class:'compute-grid'},[field('最長執行分鐘',minutes),field('Colab 點數停止門檻',units),field('AutoIP 點數上限',credits),field('Comfy Cloud 點數門檻',comfyCredits),field('RunningHub 金幣門檻',runninghubCoins),field('Colab 執行帳號',account),field('Colab GPU',gpu)]),
      el('p',{class:'compute-note',text:'各平台分別檢查預估用量；這些門檻並非平台硬性扣款上限。'}),accountNote,el('p',{class:'compute-note',text:'Colab 環境準備也會用點；停止門檻依餘額變化監測，並非 Google 的硬性扣款上限。'}),
    ]);
    const root=el('section',{class:'compute-placement','aria-label':'生成運算安排'},[
      el('div',{class:'compute-toolbar'},[field('執行位置',placement),refresh]),visualField,videoFields,notice,recipe,differences,
      el('details',{class:'compute-compatibility'},[el('summary',{text:'查看各方式的支援條件'}),candidates]),controls,retry,
    ]);
    const jobList=el('div',{class:'compute-job-list'});
    const jobNotice=el('p',{class:'compute-note',role:'status','aria-live':'polite'});
    const taskRoot=el('section',{class:'compute-tasks','aria-label':'運算任務'},[
      el('div',{class:'compute-task-heading'},[el('strong',{text:'運算任務'}),button('更新任務',()=>refreshJobs())]),jobNotice,jobList,
    ]);
    form.prepend(root);form.append(taskRoot);
    function live(){return !disposed && form.isConnected;}
    function withPlacementOptions(request){
      const current=request?.settings?.visualStyle || request?.settings?.visualClass;
      const needed=['img2img','qwen21-edit','img2video','h3-image-video','first-last-frame'].includes(request?.featureId)
        && ['auto','autoip'].includes(placement.value) && !['anime','realistic'].includes(current);
      visualField.hidden=!needed;
      const videoNeeded=['h3-text-video','h3-image-video'].includes(request?.featureId) && ['auto','autoip'].includes(placement.value);
      videoFields.hidden=!videoNeeded;
      videoSummary.textContent=`AutoIP 影片規格 · ${videoDuration.value} 秒 / ${videoResolution.value} / ${videoAspect.value}`;
      let result=needed && visualClass.value ? {...request,computeVisualClass:visualClass.value} : request;
      if(videoNeeded)result={...result,computeAutoipVideo:{duration_seconds:Number(videoDuration.value),resolution:videoResolution.value,aspect_ratio:videoAspect.value}};
      return result;
    }
    function getLimits(){
      for(const input of [minutes,units,credits,comfyCredits,runninghubCoins])if(!input.checkValidity())throw new Error('請檢查執行上限的數字範圍。');
      return {max_minutes:Number(minutes.value),max_units:Number(units.value),max_credits:Number(credits.value),max_comfy_credits:Number(comfyCredits.value),max_runninghub_coins:Number(runninghubCoins.value),account_id:account.value,gpu:gpu.value};
    }
    function publish(){
      if(!live())return;
      const state={...availability,eligible:availability.eligible && !pending && !inFlight,pending:!!pending,submitting:!!inFlight};
      form.dataset.computePlacement=placement.value;form.dataset.computeEligible=String(state.eligible);
      onAvailability(state);
    }
    function locked(){
      for(const control of [placement,minutes,units,credits,comfyCredits,runninghubCoins,account,gpu,visualClass,videoDuration,videoResolution,videoAspect])control.disabled=!!pending;
      retry.hidden=!pending;retry.disabled=!!inFlight;
      publish();
    }
    function renderAccounts(data){
      const selected=account.value;
      const rows=Array.isArray(data)?data:[];
      account.replaceChildren(el('option',{value:'',text:'自動選擇可用帳號'}),...rows.map(item=>el('option',{
        value:item.id,text:String(item.label||'Google 帳號')+(item.authenticated?'':'（未連線）'),disabled:!item.authenticated,
      })));
      if(selected && !rows.some(item=>item.id===selected))account.append(el('option',{value:selected,text:'原選帳號目前不可用'}));
      account.value=selected;
      const item=rows.find(item=>item.id===selected);
      accountNote.textContent=item?.authenticated && Number.isFinite(item.balance_compute_units)
        ? `此帳號目前可用 ${item.balance_compute_units.toFixed(1)} 點。`
        : selected?'此帳號目前尚未就緒。':`${rows.filter(item=>item.authenticated).length} 個已連接帳號；自動安排時仍會檢查餘額。`;
    }
    function renderPreview(data){
      const rows=Array.isArray(data.candidates)?data.candidates.filter(item=>Object.hasOwn(lanes,item.lane)):[];
      const chosen=rows.find(item=>item.lane===data.selected_lane && item.eligible===true && (placement.value==='auto'||item.lane===placement.value));
      availability={placement:placement.value,loading:false,eligible:!!chosen,selected_lane:chosen?.lane||null,candidates:rows,error:''};
      notice.textContent=pending?'上一筆提交仍待確認，會沿用原請求識別碼。':chosen
        ? `預計使用${lanes[chosen.lane]}；提交時會再次檢查可用條件。`
        : '目前無法用所選方式執行；請查看支援條件或調整設定。';
      candidates.replaceChildren(...Object.entries(lanes).map(([lane,label])=>{
        const item=rows.find(row=>row.lane===lane);
        const eligible=item?.eligible===true;
        const manualOnly=eligible&&item.auto_eligible===false;
        const queue=Number.isInteger(item?.queue_depth)&&item.queue_depth>=0?` · 排隊 ${item.queue_depth}`:'';
        const why=manualOnly?reasons.auto_not_verified:eligible?'':(item?.reasons?.length?item.reasons:['operation_unavailable']).map(code=>Object.hasOwn(reasons,code)?reasons[code]:'目前不符合此方式的執行條件').join('；');
        return el('li',{'data-compute-lane':lane},[el('strong',{text:label}),el('span',{text:eligible?(manualOnly?'僅限手動':'可用')+queue:'不可用'}),...(why?[el('span',{class:'compute-reason',text:why})]:[])]);
      }));
      const selectedRecipe=chosen && data.recipes?.[chosen.lane];
      recipe.textContent=typeof selectedRecipe==='string'?`工作流：${selectedRecipe.slice(0,160)}`:'';recipe.hidden=!recipe.textContent;
      const warnings=chosen?.warnings?.length?chosen.warnings:chosen?.lane==='colab'?data.colab_warnings:chosen?.lane==='autoip'?data.autoip_warnings:[];
      differences.replaceChildren(...(Array.isArray(warnings)?warnings:[]).filter(value=>typeof value==='string').slice(0,8).map(text=>el('li',{text:text.slice(0,360)})));
      differences.hidden=!differences.children.length;
      root.querySelector('.compute-compatibility').open=!chosen;
      renderAccounts(data.accounts);publish();
    }
    async function preview(){
      if(!live())return;
      const current=++turn;
      availability={...availability,placement:placement.value,loading:true,eligible:false,error:''};publish();
      if(!pending)notice.textContent='正在確認執行條件…';
      try {
        const request=metadata(withPlacementOptions(await getRequest()));
        const data=await api('/api/compute/preview',{method:'POST',body:JSON.stringify({request,placement:placement.value,limits:getLimits()})});
        if(live() && current===turn)renderPreview(data);
      } catch(_) {
        if(!live() || current!==turn)return;
        availability={...availability,loading:false,eligible:false,error:'執行條件尚未確認。請檢查上限或更新狀態。'};
        notice.textContent=availability.error;publish();
      }
    }
    function receipt(value,expected){
      if(!value || !Object.hasOwn(lanes,value.lane) || !Object.hasOwn(states,value.state) ||
        typeof value.request_id!=='string' || !/^[\da-f]{8}(-[\da-f]{4}){3}-[\da-f]{12}$/.test(value.request_id) ||
        (expected && value.request_id!==expected))throw new Error('任務回應尚待確認。');
      return value;
    }
    function accept(value){
      const job=receipt(value);
      jobs.set(job.request_id,job);
      if(pending?.id===job.request_id && !uncertain.has(job.state))pending=null;
      renderJobs();locked();return job;
    }
    function renderJobs(){
      jobList.replaceChildren();
      if(!jobs.size){jobList.append(el('p',{class:'compute-note',text:'尚無運算任務。'}));return;}
      for(const job of [...jobs.values()].reverse().slice(0,8)) {
        const actions=el('div',{class:'compute-job-actions'});
        const outputs=Array.isArray(job.artifacts)?[...job.artifacts]:[];
        if(job.result_url && !outputs.some(item=>item.url===job.result_url))outputs.push({name:'查看成果',url:job.result_url});
        for(const [index,output] of outputs.entries()) {
          const url=safeLink(output.url);if(!url)continue;
          actions.append(el('a',{href:url,target:'_blank',rel:'noopener noreferrer',text:String(output.name||`成果 ${index+1}`).slice(0,120)}));
        }
        if(!terminal.has(job.state) && job.cancel_supported!==false)actions.append(button('停止任務',()=>cancel(job.request_id)));
        jobList.append(el('article',{class:'compute-job','data-compute-request':job.request_id},[
          el('div',{class:'compute-job-heading'},[el('strong',{text:states[job.state]}),el('span',{class:'compute-note',text:lanes[job.lane]})]),
          ...(uncertain.has(job.state)?[el('p',{class:'compute-note',text:'請先更新這筆任務；不會自動改用另一個服務。'})]:[]),actions,
        ]));
      }
    }
    async function refreshJobs(){
      if(!live())return;
      try {
        const result=await api('/api/compute/jobs');
        if(!live())return;
        for(const value of [...(result.jobs||[])].reverse())accept(value);
        const active=[...jobs.values()].filter(job=>!terminal.has(job.state)).slice(-8);
        await Promise.allSettled(active.map(async job=>{
          const value=receipt(await api('/api/compute/jobs/'+job.request_id),job.request_id);
          if(live())accept(value);
        }));
        if(!live())return;
        if(pending) {
          try {const result=receipt(await api('/api/compute/jobs/'+pending.id),pending.id);if(live())accept(result);}
          catch(_){/* An absent/unreachable receipt is never permission to clear the identity. */}
        }
        jobNotice.textContent='';renderJobs();
      } catch(_) {if(live())jobNotice.textContent='任務狀態暫時無法更新，已保留目前紀錄。';}
    }
    async function cancel(id){
      if(!live())return;
      jobNotice.textContent='正在要求停止任務…';
      try {
        const result=receipt(await api('/api/compute/jobs/'+id+'/cancel',{method:'POST',body:'{}'}),id);
        if(!live())return;
        accept(result);jobNotice.textContent=terminal.has(result.state)?'任務狀態已更新。':'停止要求已送出，正在等待服務確認。';
      } catch(_) {if(live())jobNotice.textContent='停止狀態尚未確認，請更新任務後再試。';}
    }
    async function refreshAll(){await Promise.allSettled([preview(),refreshJobs()]);}
    function changed(event){
      if(root.contains(event.target) && event.target.tagName==='BUTTON')return;
      const target=event.target;
      if(['INPUT','TEXTAREA','SELECT'].includes(target.tagName)) {
        const value=target.type==='file'?JSON.stringify([...target.files].map(file=>[file.name,file.size,file.lastModified]))
          : JSON.stringify([target.value,target.checked]);
        if(inputValues.get(target)===value)return;
        inputValues.set(target,value);
      }
      turn++;availability={...availability,placement:placement.value,loading:true,eligible:false};publish();
      clearTimeout(debounce);debounce=setTimeout(preview,250);
    }
    form.addEventListener('input',changed);form.addEventListener('change',changed);
    async function send(body,transport=api){
      if(inFlight)return inFlight;
      if(!live())throw new Error('原本的生成表單已關閉。');
      const hadPending=!!pending;
      if(!pending) {
        if(!body || availability.loading || !availability.eligible)throw new Error('請先確認所選方式可執行。');
        const id=crypto.randomUUID();
        pending={id,payload:JSON.stringify({request:withPlacementOptions(body),placement:placement.value,limits:getLimits(),request_id:id})};
      }
      const original=pending;
      const operation=async()=>{
        locked();notice.textContent=hadPending?'正在確認上一筆提交…':'正在提交生成…';
        try {
          if(hadPending) {
            try {
              const value=receipt(await transport('/api/compute/jobs/'+original.id),original.id);
              if(!live())throw new Error('原本的生成表單已關閉。');
              accept(value);return value;
            } catch(error) {
              if(!['compute_request_not_found','request_not_found'].includes(error.errorCode))throw error;
            }
          }
          if(!live())throw new Error('原本的生成表單已關閉。');
          const value=receipt(await transport('/api/compute/jobs',{method:'POST',body:original.payload}),original.id);
          if(!live())throw new Error('原本的生成表單已關閉。');
          accept(value);notice.textContent=uncertain.has(value.state)?'任務已保留，提交狀態仍待確認。':'生成請求已記錄，請在下方查看任務狀態。';
          return value;
        } catch(error) {
          if(error.submissionState==='not_submitted') {
            if(pending===original)pending=null;
            if(live()){notice.textContent='提交檢查未通過，請調整設定後再試。';locked();}
            throw new Error('提交檢查未通過，請調整設定後再試。');
          }
          if(live()){notice.textContent='提交結果尚未確認。請確認原請求；系統會沿用同一識別碼。';locked();}
          throw new Error('提交結果尚未確認，請使用「確認並重試原請求」。');
        }
      };
      inFlight=Promise.resolve().then(operation);
      locked();
      try{return await inFlight;}
      finally{inFlight=null;if(live())locked();}
    }
    function destroy(){
      disposed=true;turn++;clearTimeout(debounce);clearTimeout(poll);
      form.removeEventListener('input',changed);form.removeEventListener('change',changed);
      root.remove();taskRoot.remove();pending=null;jobs.clear();mounted.delete(form);
      delete form.dataset.computePlacement;delete form.dataset.computeEligible;
    }
    const instance={refresh:refreshAll,destroy,submit:send,getState:()=>({...availability,eligible:availability.eligible&&!pending&&!inFlight,pending:!!pending,submitting:!!inFlight})};
    mounted.set(form,instance);renderJobs();queueMicrotask(()=>{if(live())refreshAll();});
    const tick=async()=>{if(!live()){destroy();return;}await refreshJobs();if(live())poll=setTimeout(tick,10000);};
    poll=setTimeout(tick,10000);
    return instance;
  }
  window.ComputePlacement={mount,
    submit(form,body,api){const instance=mounted.get(form);if(!instance)return Promise.reject(new Error('請重新開啟生成表單。'));return instance.submit(body,api);},
    getState(form){return mounted.get(form)?.getState()||null;},
    reset(){for(const instance of [...mounted.values()])instance.destroy();},
  };
})();

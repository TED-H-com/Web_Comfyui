/* Multi-step creation uses durable server identities and verified outputs. */
(() => {
  'use strict';
  const lanes={local:'本機',colab:'Google Colab',autoip:'AutoIP',comfy_cloud:'Comfy Cloud',runninghub:'RunningHub',auto:'自動安排'};
  const states={pending:'等待前一步',preparing:'準備素材',reserved:'已保留任務',dispatching:'正在提交',queued:'等待執行',running:'製作中',collecting:'整理成果中',completed:'已完成',blocked:'等待處理',failed:'執行失敗',rejected:'未通過檢查',submission_uncertain:'提交待確認',cancelling:'正在停止',cancelled:'已停止'};
  const finished=new Set(['completed','cancelled']);
  const instances=new Set();
  const el=(tag,attrs={},children=[])=>{
    const node=document.createElement(tag);
    for(const [key,value] of Object.entries(attrs)){
      if(key==='text')node.textContent=value;
      else if(key.startsWith('on'))node.addEventListener(key.slice(2),value);
      else if(value!==false&&value!=null)node.setAttribute(key,value===true?'':value);
    }
    node.append(...children);return node;
  };
  const field=(label,input)=>{input.setAttribute('aria-label',label);return el('label',{class:'field'},[el('span',{text:label}),input]);};
  const button=(text,action)=>el('button',{type:'button',class:'ghost small',text,onclick:action});
  const safeUrl=value=>{
    if(typeof value!=='string'||!value.startsWith('/api/')||value.includes('\\')||/[\u0000-\u001f]/.test(value))return null;
    try{const url=new URL(value,location.origin);return url.origin===location.origin&&url.pathname.startsWith('/api/')?url.pathname+url.search:null;}catch(_){return null;}
  };
  const fileData=file=>new Promise((resolve,reject)=>{
    const reader=new FileReader();reader.onload=()=>resolve({name:file.name,type:file.type,dataUrl:reader.result});
    reader.onerror=()=>reject(new Error('file_read_failed'));reader.readAsDataURL(file);
  });
  function mount(host,{api,onBack}){
    let disposed=false,poll=null,recipes=[],current=null,pending=null,submitting=false;
    let featureRegistry=[],preferences={default_placement:'local',feature_placements:{}},customSteps=[],stepSequence=0;
    const jobs=new Map(),retryUnknown=new Map(),retryPlacements=new Map(),controls=new Map(),placements=new Map(),fieldRows=new Map();
    const root=el('section',{class:'panel compute-production','data-compute-pipeline-mounted':'true'});
    const notice=el('p',{class:'compute-note',role:'status','aria-live':'polite'});
    const recipe=el('select',{'aria-label':'製作方式'});
    const description=el('p',{class:'compute-note'});
    const inputs=el('div',{class:'compute-production-inputs'}),steps=el('ol',{class:'compute-production-steps'});
    const customHost=el('div',{class:'compute-custom-steps',hidden:true}),stepsHeading=el('h2',{text:'執行步驟'});
    const submit=el('button',{type:'submit',class:'primary small',text:'開始製作',disabled:true});
    const confirm=button('確認原提交',()=>send().catch(()=>{}));confirm.hidden=true;
    const limitsSpec=[['max_minutes','每步最長分鐘',60,5,180],['max_units','Colab 點數停止門檻',20,5,2000],['max_credits','AutoIP 點數上限',100,1,1000000],['max_comfy_credits','Comfy Cloud 點數門檻',100,1,1000000],['max_runninghub_coins','RunningHub 金幣門檻',100,1,1000000]];
    const limitInputs=new Map(limitsSpec.map(([key,label,value,min,max])=>[key,el('input',{type:'number',value,min,max,step:1,required:true,'aria-label':label})]));
    const form=el('form',{class:'compute-production-form'},[
      field('製作方式',recipe),description,inputs,customHost,stepsHeading,steps,
      el('details',{class:'compute-production-limits'},[
        el('summary',{text:'每步執行上限'}),el('div',{class:'compute-grid'},limitsSpec.map(([key,label])=>field(label,limitInputs.get(key)))),
        el('p',{class:'compute-note',text:'每一步分別檢查預估用量；平台門檻並非硬性扣款上限。整個製作可能包含多次計費。'}),
      ]),submit,
    ]);
    const list=el('div',{class:'compute-production-jobs'});
    const listNotice=el('p',{class:'compute-note',role:'status','aria-live':'polite'});
    root.append(el('div',{class:'compute-production-heading'},[
      el('div',{},[el('h1',{text:'多步製作'}),el('p',{class:'compute-note',text:'串接圖片、影片與聲音功能，為每一步選擇運算平台。'})]),
      el('div',{class:'compute-job-actions'},[...(onBack?[button('返回單步創作',onBack)]:[]),button('新增製作',newProduction)]),
    ]),notice,form,confirm,el('div',{class:'compute-production-heading'},[el('h2',{text:'製作紀錄'}),button('更新紀錄',refreshJobs)]),listNotice,list);
    host.append(root);
    const live=()=>!disposed&&root.isConnected;
    function destroy(){disposed=true;delete root.dataset.computePipelineMounted;clearTimeout(poll);pending=null;controls.clear();retryPlacements.clear();instances.delete(destroy);}
    instances.add(destroy);
    function lock(){
      for(const input of form.querySelectorAll('input,textarea,select,button'))input.disabled=!!pending||submitting;
      submit.disabled=!current||!!pending||submitting;confirm.hidden=!pending;confirm.disabled=submitting;
    }
    function newProduction(){
      if(pending||submitting){notice.textContent='請先確認上一筆提交，再建立新的製作。';confirm.focus();return;}
      renderRecipe();notice.textContent='';form.scrollIntoView({block:'start'});recipe.focus();
    }
    function conditionalFields(){
      for(const spec of current?.fields||[]){
        const row=fieldRows.get(spec.id),input=controls.get(spec.id),condition=spec.show_when;
        if(!row||!input)continue;
        const visible=!condition||!Array.isArray(condition.placements)||condition.placements.includes(placements.get(condition.step_id)?.value);
        row.hidden=!visible;input.required=visible&&spec.required===true;
      }
    }
    function preferredPlacement(featureId){const value=preferences.feature_placements?.[featureId]??preferences.default_placement;return Object.hasOwn(lanes,value)?value:'local';}
    function renderRecipe(){
      const custom=recipe.value==='custom';customHost.hidden=!custom;inputs.hidden=steps.hidden=stepsHeading.hidden=custom;
      if(custom){current={id:'custom',fields:[]};description.textContent='新增步驟並選擇功能；素材可來自自己的檔案或前一步已驗證的第一份成果。角色專用功能請由角色工作室使用。';resetCustom();lock();return;}
      current=recipes.find(item=>item.id===recipe.value)||null;controls.clear();placements.clear();fieldRows.clear();inputs.replaceChildren();steps.replaceChildren();
      description.textContent=String(current?.description||'').slice(0,800);
      for(const spec of current?.fields||[]){
        if(!['text','textarea','number','select','file'].includes(spec.type)||typeof spec.id!=='string')continue;
        let input;
        if(spec.type==='select')input=el('select',{},(spec.options||[]).map(item=>el('option',{value:item.value,text:item.label})));
        else if(spec.type==='textarea')input=el('textarea',{rows:3,maxlength:spec.maxLength||spec.max_length||12000});
        else input=el('input',{type:spec.type,min:spec.min,max:spec.max,step:spec.step||1,...(spec.type==='file'?{accept:spec.accept||'image/png,image/jpeg,image/webp'}:{maxlength:spec.maxLength||spec.max_length||12000})});
        if(spec.default!=null&&spec.type!=='file')input.value=String(spec.default);
        const row=field(spec.label||spec.id,input);
        if(['select','number'].includes(spec.type))row.classList.add('compute-production-small-field');
        if(spec.type==='file'){
          input.classList.add('compute-file-input');const filename=el('span',{class:'compute-note',text:'尚未選取檔案'});
          input.addEventListener('change',()=>{filename.textContent=input.files[0]?.name||'尚未選取檔案';});
          row.append(el('div',{class:'compute-job-actions'},[button('選擇素材',()=>input.click()),filename]));
        }
        if(spec.description)row.append(el('span',{class:'compute-note',text:String(spec.description).slice(0,500)}));
        controls.set(spec.id,input);fieldRows.set(spec.id,row);inputs.append(row);
      }
      for(const [index,step] of (current?.steps||[]).entries()){
        const picker=el('select',{'aria-label':`${step.label||`步驟 ${index+1}`}執行位置`},Object.entries(lanes).map(([value,text])=>el('option',{value,text})));
        picker.value=Object.hasOwn(lanes,step.placement)?step.placement:preferredPlacement(step.feature_id);picker.addEventListener('change',conditionalFields);placements.set(step.id,picker);
        const dependencies=(step.depends_on||[]).map(id=>current.steps.find(item=>item.id===id)?.label||'前一步');
        steps.append(el('li',{},[el('div',{},[el('strong',{text:`${index+1}. ${step.label||'製作'}`}),el('p',{class:'compute-note',text:dependencies.length?`使用「${dependencies.join('、')}」的已完成成果。`:'使用本次輸入內容。'}),...(step.description?[el('p',{class:'compute-note',text:step.description})]:[])]),picker]));
      }
      conditionalFields();lock();
    }
    function customField(spec,prefix){
      let input;
      const choices=(spec.options||[]).map(option=>typeof option==='object'?{...option,value:option.value??`${option.width}x${option.height}`}:{value:option,label:String(option)});
      if(['select','aspect'].includes(spec.type))input=el('select',{},choices.map(option=>el('option',{value:option.value,text:option.label||String(option.value)})));
      else if(spec.type==='textarea')input=el('textarea',{rows:Math.min(4,spec.rows||3),maxlength:spec.maxLength||12000,placeholder:spec.placeholder});
      else input=el('input',{type:spec.type==='slider'?'number':spec.type,min:spec.min,max:spec.max,step:spec.step??'any',maxlength:spec.maxLength||12000,placeholder:spec.placeholder});
      input.required=spec.required===true;
      if(spec.type==='checkbox')input.checked=spec.default===true;else if(spec.default!=null)input.value=String(spec.default);
      const row=field(`${prefix} ${spec.label||spec.id}`,input);
      if(spec.description||spec.hint)row.append(el('span',{class:'compute-note',text:String(spec.description||spec.hint).slice(0,400)}));
      return {input,row,read:()=>spec.type==='checkbox'?input.checked:['number','slider'].includes(spec.type)?(input.value===''?undefined:Number(input.value)):['select','aspect'].includes(spec.type)?choices.find(item=>String(item.value)===input.value)?.value??input.value:input.value};
    }
    function resetCustom(){customSteps=[];stepSequence=0;customHost.replaceChildren(el('p',{class:'compute-note',text:'角色專用功能與尚未支援的表單控制不會出現在此選單。'}));addCustom();customHost.append(button('新增步驟',addCustom));}
    function addCustom(){
      if(customSteps.length>=8){notice.textContent='每個自訂製作最多 8 步。';return;}
      const item={id:'step-'+(++stepSequence),fields:new Map(),slots:new Map(),feature:null,touched:false};
      item.root=el('section',{class:'compute-custom-step'});
      item.featureChoice=el('select',{'aria-label':`步驟 ${customSteps.length+1} 功能`,required:true},[el('option',{value:'',text:'請選擇功能'}),...featureRegistry.map(feature=>el('option',{value:feature.id,text:feature.label||feature.id}))]);
      item.placement=el('select',{'aria-label':`步驟 ${customSteps.length+1} 執行位置`},Object.entries(lanes).map(([value,text])=>el('option',{value,text})));
      item.placement.value=preferredPlacement('');
      item.content=el('div',{class:'compute-custom-fields'});
      const remove=button('移除此步驟',()=>{if(customSteps.length===1)return;customSteps=customSteps.filter(step=>step!==item);item.root.remove();refreshCustomSources();});
      item.root.append(el('div',{class:'compute-production-heading'},[el('strong',{text:`步驟 ${customSteps.length+1}`}),remove]),el('div',{class:'compute-custom-selectors'},[field('功能',item.featureChoice),field('執行位置',item.placement)]),item.content);
      customSteps.push(item);const add=customHost.lastElementChild;
      if(add?.tagName==='BUTTON')customHost.insertBefore(item.root,add);else customHost.append(item.root);
      item.featureChoice.setAttribute('aria-label',`步驟 ${customSteps.length} 功能`);item.placement.setAttribute('aria-label',`步驟 ${customSteps.length} 執行位置`);
      item.featureChoice.addEventListener('change',()=>renderCustomFeature(item));
      item.placement.addEventListener('change',()=>{item.touched=true;item.syncExtras?.();});
    }
    function renderCustomFeature(item){
      item.feature=featureRegistry.find(feature=>feature.id===item.featureChoice.value)||null;item.fields.clear();item.slots.clear();item.content.replaceChildren();
      if(!item.feature){refreshCustomSources();return;}
      if(!item.touched)item.placement.value=preferredPlacement(item.feature.id);
      const prefix=item.feature.label||item.feature.id,advanced=el('details',{},[el('summary',{text:'進階設定'})]);
      for(const spec of item.feature.fields){
        const value=customField(spec,prefix);item.fields.set(spec.id,value);
        (['number','slider','checkbox'].includes(spec.type)?advanced:item.content).append(value.row);
      }
      if(advanced.children.length>1)item.content.append(advanced);
      for(const slot of item.feature.uploads||[]){
        const source=el('select',{'aria-label':`${prefix} ${slot.label||slot.id}來源`});
        const file=el('input',{type:'file',class:'compute-file-input',accept:slot.accept,'aria-label':`${prefix} ${slot.label||slot.id}檔案`});
        const filename=el('span',{class:'compute-note',text:'尚未選取檔案'});
        file.addEventListener('change',()=>{filename.textContent=file.files[0]?.name||'尚未選取檔案';});
        const picker=el('div',{class:'compute-job-actions'},[file,button('選擇素材',()=>file.click()),filename]);
        const expected=el('select',{'aria-label':`${prefix} ${slot.label||slot.id}成果類型`},[el('option',{value:'',text:'請確認上游成果類型'}),...(slot.media_kinds||[]).map(value=>el('option',{value,text:{image:'圖片',video:'影片',audio:'聲音'}[value]}))]);
        const typeRow=field('上游成果類型（執行時驗證）',expected);
        item.content.append(field(`${slot.label||slot.id}來源`,source),picker,typeRow);source.setAttribute('aria-label',`${prefix} ${slot.label||slot.id}來源`);expected.setAttribute('aria-label',`${prefix} ${slot.label||slot.id}成果類型`);
        const entry={spec:slot,source,file,picker,expected,typeRow};item.slots.set(slot.id,entry);
        source.addEventListener('change',()=>syncCustomSlot(item,entry));
      }
      const visual=el('select',{},[el('option',{value:'',text:'未指定'}),el('option',{value:'anime',text:'動漫'}),el('option',{value:'realistic',text:'寫實'})]);
      const visualRow=field(`${prefix} 圖片類型`,visual);
      const seconds=el('input',{type:'number',min:2,max:15,step:1,value:8}),resolution=el('select',{},['480p','768p','2k'].map(value=>el('option',{value,text:value}))),aspect=el('select',{},['16:9','9:16'].map(value=>el('option',{value,text:value})));
      const videoRow=el('div',{class:'compute-grid'},[field(`${prefix} AutoIP 秒數`,seconds),field(`${prefix} AutoIP 解析度`,resolution),field(`${prefix} AutoIP 比例`,aspect)]);
      item.extras={visual,seconds,resolution,aspect,visualRow,videoRow};item.content.append(visualRow,videoRow);
      item.syncExtras=()=>{
        const cloud=['auto','autoip'].includes(item.placement.value),style=item.fields.get('visualStyle')?.read()||item.fields.get('visualClass')?.read();
        visualRow.hidden=!(cloud&&['img2img','qwen21-edit','img2video','h3-image-video','first-last-frame'].includes(item.feature.id)&&!['anime','realistic'].includes(style));visual.required=!visualRow.hidden;
        videoRow.hidden=!(cloud&&['h3-text-video','h3-image-video'].includes(item.feature.id));seconds.required=!videoRow.hidden;
      };
      item.content.oninput=item.syncExtras;item.syncExtras();refreshCustomSources();
    }
    function syncCustomSlot(item,entry){
      const prior=customSteps.find(step=>step.id===entry.source.value),fromFile=entry.source.value==='file';
      entry.picker.hidden=!fromFile;entry.file.required=fromFile&&entry.spec.required===true;
      entry.typeRow.hidden=!prior||!!prior.feature?.output_kind;entry.expected.required=!entry.typeRow.hidden;
    }
    function refreshCustomSources(){
      for(const [index,item] of customSteps.entries()){
        item.root.querySelector('strong').textContent=`步驟 ${index+1}`;
        item.featureChoice.setAttribute('aria-label',`步驟 ${index+1} 功能`);item.placement.setAttribute('aria-label',`步驟 ${index+1} 執行位置`);
        for(const entry of item.slots.values()){
          const selected=entry.source.value;
          const choices=customSteps.slice(0,index).filter(prior=>prior.feature&&(entry.spec.media_kinds||[]).length&&(!prior.feature.output_kind||(entry.spec.media_kinds||[]).includes(prior.feature.output_kind)));
          entry.source.replaceChildren(el('option',{value:'',text:entry.spec.required?'請選擇素材來源':'不使用此素材'}),el('option',{value:'file',text:'選擇自己的檔案'}),...choices.map(prior=>el('option',{value:prior.id,text:`步驟 ${customSteps.indexOf(prior)+1} · ${prior.feature.label}第一份成果${prior.feature.output_kind?'':'（類型待確認）'}`})));
          entry.source.value=[...entry.source.options].some(option=>option.value===selected)?selected:'';entry.source.required=entry.spec.required===true;
          syncCustomSlot(item,entry);
        }
      }
    }
    async function customPayload(){
      const plan={version:1,steps:[]};let total=0;
      const limits={...Object.fromEntries([...limitInputs].map(([key,input])=>[key,Number(input.value)])),gpu:'L4',account_id:''};
      for(const item of customSteps){
        if(!item.feature)throw new Error('custom_inputs');
        const request={featureId:item.feature.id,settings:{},files:{}},bindings={},dependencies=new Set();
        for(const [id,value] of item.fields){const result=value.read();if(result!==undefined)request.settings[id]=result;}
        for(const [id,entry] of item.slots){
          if(entry.source.value==='file'&&entry.file.files[0]){total+=entry.file.files[0].size;if(total>32*1024*1024)throw new Error('files_too_large');request.files[id]=await fileData(entry.file.files[0]);}
          else if(entry.source.value&&entry.source.value!=='file'){
            const upstream=customSteps.find(step=>step.id===entry.source.value),kind=upstream?.feature?.output_kind||entry.expected.value;
            if(!(entry.spec.media_kinds||[]).includes(kind))throw new Error('custom_inputs');
            bindings[id]={step_id:upstream.id,artifact_index:0,expected_kind:kind};dependencies.add(upstream.id);
          }
        }
        if(Object.keys(request.files).length+Object.keys(bindings).length<(item.feature.minimumUploads||0))throw new Error('custom_inputs');
        const extra=item.extras;if(extra&&!extra.visualRow.hidden)request.computeVisualClass=extra.visual.value;
        if(extra&&!extra.videoRow.hidden)request.computeAutoipVideo={duration_seconds:Number(extra.seconds.value),resolution:extra.resolution.value,aspect_ratio:extra.aspect.value};
        plan.steps.push({id:item.id,request,placement:item.placement.value,limits:{...limits},bindings,depends_on:[...dependencies]});
      }
      return {pipeline_id:crypto.randomUUID(),plan};
    }
    recipe.addEventListener('change',renderRecipe);
    function receipt(value,expected){
      const result=value?.pipeline||value;
      if(!result||typeof result.pipeline_id!=='string'||!/^[\da-f]{8}(-[\da-f]{4}){3}-[\da-f]{12}$/.test(result.pipeline_id)||!Object.hasOwn(states,result.state)||!Array.isArray(result.steps)||(expected&&result.pipeline_id!==expected))throw new Error('invalid_pipeline_receipt');
      return result;
    }
    function accept(value,expected){
      const item=receipt(value,expected),previous=jobs.get(item.pipeline_id);
      if(previous&&Number.isFinite(previous.updated_at)&&Number.isFinite(item.updated_at)&&item.updated_at<previous.updated_at)return previous;
      if(previous?.state==='completed'&&item.state!=='completed')return previous;
      jobs.set(item.pipeline_id,item);if(pending?.id===item.pipeline_id)pending=null;
      for(const step of item.steps){const key=item.pipeline_id+':'+step.id;if(retryUnknown.has(key)&&step.attempt>retryUnknown.get(key))retryUnknown.delete(key);}
      renderJobs();lock();return item;
    }
    function outputLinks(rows){
      return (Array.isArray(rows)?rows:[]).slice(0,20).flatMap((artifact,index)=>{
        const href=safeUrl(artifact.url);return href?[el('a',{href,target:'_blank',rel:'noopener noreferrer',text:String(artifact.name||`成果 ${index+1}`).slice(0,120)})]:[];
      });
    }
    function renderJobs(){
      list.replaceChildren();if(!jobs.size){list.append(el('p',{class:'compute-note',text:'尚無多步製作紀錄。'}));return;}
      for(const item of [...jobs.values()].reverse().slice(0,12)){
        const actions=el('div',{class:'compute-job-actions'}),completed=item.steps.filter(step=>step.state==='completed').length;
        if(!finished.has(item.state)&&item.state!=='cancelling'){
          actions.append(button('繼續',()=>mutate(item,'resume')),button('停止',()=>mutate(item,'cancel')));
        }
        const rows=item.steps.map((step,index)=>{
          const label=String(step.label||({image:'圖片製作',video:'影片製作',voice:'語音製作',lipsync:'對嘴影片'}[step.id])||`步驟 ${index+1}`),outputs=step.artifacts?.length?step.artifacts:step.job?.artifacts;
          const tools=el('div',{class:'compute-job-actions'},outputLinks(outputs));
          const key=item.pipeline_id+':'+step.id;
          if(['failed','rejected'].includes(step.state)&&!item.cancel_requested&&step.retry_allowed!==false){
            const retryLane=el('select',{'aria-label':`${label}重試平台`},Object.entries(lanes).map(([value,text])=>el('option',{value,text})));
            retryLane.value=retryPlacements.get(key)||(Object.hasOwn(lanes,step.placement)?step.placement:'auto');
            retryLane.disabled=retryUnknown.has(key);retryLane.addEventListener('change',()=>retryPlacements.set(key,retryLane.value));
            const retry=button('重試失敗步驟',()=>mutate(item,'retry',step));retry.disabled=retryUnknown.has(key);
            tools.append(field('重試平台',retryLane),retry);retryLane.setAttribute('aria-label',`${label}重試平台`);
          }
          return el('li',{'data-pipeline-step':step.id},[
            el('div',{class:'compute-production-step-state'},[el('strong',{text:label}),el('span',{text:states[step.state]||'等待確認'})]),
            el('p',{class:'compute-note',text:`${lanes[step.job?.lane]||lanes[step.placement]||'等待安排'}${step.depends_on?.length?' · 依前一步成果執行':''}`}),
            ...(retryUnknown.has(key)?[el('p',{class:'compute-note',text:'重試回應待確認，請更新紀錄。'})]:[]),tools,
          ]);
        });
        list.append(el('article',{class:'compute-production-job','data-pipeline-id':item.pipeline_id},[
          el('div',{class:'compute-production-heading'},[el('strong',{text:states[item.state]}),el('span',{class:'compute-note',text:`${completed} / ${item.steps.length} 步已完成`})]),
          el('ol',{class:'compute-production-step-list'},rows),actions,
        ]));
      }
    }
    async function refreshJobs(){
      if(!live())return;
      try{
        const data=await api('/api/compute/pipelines');if(!live())return;
        for(const item of [...(data.pipelines||[])].reverse())accept(item);
        const active=[...jobs.values()].filter(item=>!finished.has(item.state)).slice(-8);
        await Promise.allSettled(active.map(async item=>{const data=await api('/api/compute/pipelines/'+item.pipeline_id);if(live())accept(data,item.pipeline_id);}));
        if(live())listNotice.textContent='';
      }catch(_){if(live())listNotice.textContent='製作紀錄暫時無法更新，已保留目前狀態。';}
    }
    async function mutate(item,action,step){
      const key=step?item.pipeline_id+':'+step.id:null;
      if(key&&retryUnknown.has(key))return;
      if(key)retryUnknown.set(key,step.attempt||1);
      renderJobs();listNotice.textContent='正在更新製作…';
      try{
        const value=await api('/api/compute/pipelines/'+item.pipeline_id+'/'+action,{method:'POST',body:JSON.stringify(step?{step_id:step.id,placement:retryPlacements.get(key)||(Object.hasOwn(lanes,step.placement)?step.placement:'auto')}:{})});
        if(!live())return;accept(value,item.pipeline_id);listNotice.textContent=action==='cancel'?'停止要求已送出；已完成成果會保留。':'製作狀態已更新。';
      }catch(error){
        if(key&&error.submissionState==='not_submitted')retryUnknown.delete(key);
        if(live()){listNotice.textContent='操作回應尚未確認，請更新紀錄；不會另建相同製作。';renderJobs();}
      }
    }
    async function payload(){
      if(recipe.value==='custom')return customPayload();
      const values={},selectedFiles=[];
      for(const spec of current.fields||[]){
        const control=controls.get(spec.id);if(!control||fieldRows.get(spec.id).hidden)continue;
        if(spec.type==='file'){if(control.files[0])selectedFiles.push([spec.id,control.files[0]]);}
        else if(spec.type==='select')values[spec.id]=(spec.options||[]).find(item=>String(item.value)===control.value)?.value??control.value;
        else values[spec.id]=spec.type==='number'?Number(control.value):control.value;
      }
      if(selectedFiles.reduce((sum,[,file])=>sum+file.size,0)>32*1024*1024)throw new Error('files_too_large');
      for(const [id,file] of selectedFiles)values[id]=await fileData(file);
      return {pipeline_id:crypto.randomUUID(),recipe_id:current.id,inputs:values,placements:Object.fromEntries([...placements].map(([id,control])=>[id,control.value])),limits:{...Object.fromEntries([...limitInputs].map(([key,input])=>[key,Number(input.value)])),gpu:'L4',account_id:''}};
    }
    async function send(){
      if(submitting||!live())return;if(!pending&&!form.reportValidity())return;
      const retrying=!!pending;submitting=true;lock();notice.textContent=retrying?'正在確認原提交…':'正在提交製作…';
      try{
        if(!pending){const body=await payload();if(!live())return;pending={id:body.pipeline_id,body:JSON.stringify(body)};lock();}
        const original=pending;
        if(retrying){
          try{const result=await api('/api/compute/pipelines/'+original.id);if(live())accept(result,original.id);notice.textContent='已找到原製作。';return;}
          catch(error){if(!['pipeline_not_found','compute_pipeline_not_found'].includes(error.errorCode))throw error;}
        }
        const value=await api('/api/compute/pipelines',{method:'POST',body:original.body});
        if(live()){accept(value,original.id);notice.textContent='製作已建立，執行情況顯示於下方。';}
      }catch(error){
        if(error.submissionState==='not_submitted')pending=null;
        if(live())notice.textContent=pending?'提交回應尚未確認；請確認原提交，避免重複製作。':error.message==='files_too_large'?'參考檔案合計上限為 32 MB。':'尚未建立製作，請檢查必要內容與連線後再試。';
      }finally{if(live()){submitting=false;lock();}}
    }
    form.addEventListener('submit',event=>{event.preventDefault();send();});
    async function init(){
      try{
        const [data,prefs,registry]=await Promise.all([api('/api/compute/pipeline-recipes'),api('/api/compute/preferences').catch(()=>null),api('/api/compute/pipeline-features').catch(()=>null)]);if(!live())return;
        if(prefs)preferences=prefs;
        featureRegistry=(registry?.features||[]).filter(item=>!item.characterOnly&&typeof item.id==='string'&&Array.isArray(item.fields)&&item.fields.every(field=>['text','textarea','select','aspect','number','slider','checkbox'].includes(field.type)));
        recipes=(data.recipes||[]).filter(item=>typeof item.id==='string'&&Array.isArray(item.fields)&&Array.isArray(item.steps));
        recipe.replaceChildren(...recipes.map(item=>el('option',{value:item.id,text:item.label||item.id})),...(featureRegistry.length?[el('option',{value:'custom',text:'自訂多步流程'})]:[]));renderRecipe();
        if(!recipes.length&&!featureRegistry.length)notice.textContent='目前沒有可用的多步製作方式。';
      }catch(_){if(live())notice.textContent='製作方式目前無法讀取，請稍後再試。';}
    }
    async function tick(){if(!live()){destroy();return;}await refreshJobs();if(live())poll=setTimeout(tick,10000);}
    queueMicrotask(()=>{init();tick();});
    return {destroy,refresh:refreshJobs};
  }
  window.ComputePipeline={mount,reset:()=>{for(const destroy of [...instances])destroy();}};
})();

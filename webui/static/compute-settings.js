/* Session-owned connections. Credential inputs are write-only and never persisted. */
(() => {
  'use strict';
  const sessions=new Set();
  const providers={
    local:{name:'本機',line:'使用這台主機的 GPU，依共用佇列排隊。'},
    colab:{name:'Google Colab',line:'使用你的 Google 帳號與 Colab 運算額度。',link:'https://research.google.com/colaboratory/intl/en-GB/faq.html',linkText:'訂閱與 CU 說明'},
    autoip:{name:'AutoIP',line:'使用你的 AutoIP 帳號生成圖片與影片。',link:'https://autoip.app/dashboard/api-keys',linkText:'取得 AutoIP API 金鑰'},
    comfy_cloud:{name:'Comfy Cloud',line:'在 Comfy 官方雲端執行支援的工作流。',link:'https://platform.comfy.org/profile/api-keys',linkText:'取得 Comfy API 金鑰'},
    runninghub:{name:'RunningHub',line:'使用你的 RunningHub 帳號執行工作流。',link:'https://www.runninghub.ai/runninghub-api-doc-en/doc-8287463',linkText:'RunningHub 官方金鑰說明'},
  };
  const el=(tag,attrs={},children=[])=>{const node=document.createElement(tag);for(const [key,value] of Object.entries(attrs)){
    if(key==='text')node.textContent=value;else if(key.startsWith('on'))node.addEventListener(key.slice(2),value);
    else if(value!==false&&value!=null)node.setAttribute(key,value===true?'':value);
  }node.append(...children);return node;};
  const button=(text,action,attrs={})=>el('button',{type:'button',class:'ghost small',text,onclick:action,...attrs});
  const label=(text,input)=>el('label',{class:'field'},[el('span',{text}),input]);
  const stamp=value=>value?new Date(value*1000).toLocaleString('zh-TW',{hour12:false}):'尚未查詢';
  const errorText=error=>({revision_conflict:'設定已更新，請重新整理後再試。',test_rate_limited:'請稍候 30 秒再測試。',forbidden:'目前帳號沒有這項操作權限。',provider_unavailable:'平台暫時無法使用。',invalid_request:'設定格式不正確；金鑰請勿包含空白或換行。',connection_in_use:'仍需此連線確認原任務，完成後可移除。',migration_required:'舊金鑰等待安全遷移，無須重新輸入。'}[error?.errorCode]||'操作未完成，請重新整理後再試。');
  function open(opener,{api,openSheet,isAdmin=false}){
    let disposed=false,busy=false,snapshot=null,order=[],features=null,colabSession=null,sessionTimer=null;
    const root=el('div',{class:'compute-settings'}),notice=el('p',{class:'console-notice',role:'status','aria-live':'polite'});
    const main=el('div'),admin=el('div',{hidden:true}),tabs=el('div',{class:'console-actions'});
    const mine=button('我的運算平台',()=>show(false),{'aria-pressed':'true'}),manage=button('平台管理',()=>show(true),{'aria-pressed':'false'});
    if(isAdmin)tabs.append(mine,manage);
    root.append(tabs,notice,main,admin);
    const live=()=>!disposed&&root.isConnected;
    const clearSecrets=()=>root.querySelectorAll('input[type=password]').forEach(input=>{input.value='';});
    const destroy=()=>{disposed=true;clearTimeout(sessionTimer);clearSecrets();observer.disconnect();sessions.delete(destroy);};
    const observer=new MutationObserver(()=>{if(!root.isConnected)destroy();});sessions.add(destroy);
    async function load(){
      [snapshot,colabSession]=await Promise.all([api('/api/compute/connections'),api('/api/colab/session').catch(()=>null)]);
      if(live()){render();scheduleSession();}
    }
    function scheduleSession(){
      clearTimeout(sessionTimer);sessionTimer=setTimeout(async()=>{
        if(!live())return;
        if(!document.hidden&&!busy){
          try{colabSession=await api('/api/colab/session');
            const target=root.querySelector('[data-colab-session]');
            if(target&&!target.contains(document.activeElement))target.replaceWith(sessionControls());
          }catch(_){const status=root.querySelector('[data-session-status]');if(status)status.textContent='工作階段狀態暫時無法確認';}
        }
        if(live())scheduleSession();
      },10000);
    }
    function sessionControls(){
      const data=colabSession?.session,settings=colabSession?.settings||{idle_minutes:20,max_units:120};
      const known=['closed','ready','closing','stopping','released_unsettled','requesting','allocating','uncertain','arming','uploading','bootstrap','bootstrap_launch','allocation_absent'].includes(data?.phase);
      const phase=data?.phase||'unknown',closing=phase!=='closed'&&(data?.close_requested||['closing','stopping','released_unsettled'].includes(phase)),opening=known&&!closing&&!['closed','ready'].includes(phase);
      let status=known?'未開啟':'工作階段狀態暫時無法確認';
      if(closing)status='關閉中';
      else if(opening)status=`搶機中：第 ${data.attempts||0} 次，下一次 ${data.retry_at?new Date(data.retry_at*1000).toLocaleTimeString('zh-TW',{hour:'2-digit',minute:'2-digit',hour12:false}):'確認配置中'}`;
      else if(phase==='ready')status=`可用：${data.gpu_name||data.gpu} · 本次已用 ${Number(data.units_observed||0).toFixed(2)} CU · ${data.active_job?'工作執行中':`閒置倒數 ${Math.ceil((data.idle_seconds||0)/60)} 分鐘`}`;
      const post=(action,body={})=>run(async()=>{colabSession=await api('/api/colab/session/'+action,{method:'POST',body:JSON.stringify(body)});},'工作階段設定已更新。');
      const idle=el('input',{type:'number',min:10,max:60,step:1,value:settings.idle_minutes,'aria-label':'閒置逾時（分鐘）'});
      const cap=el('input',{type:'number',min:1,step:1,value:settings.max_units,'aria-label':'每階段 CU 上限'});
      const unknown=(colabSession?.session_ledger||[]).filter(x=>x.usage_unknown).length;
      const total=(colabSession?.session_ledger||[]).reduce((n,x)=>n+Number(x.observed_amount??x.amount??0),0);
      return el('section',{'data-colab-session':'', 'aria-label':'Colab 工作階段'},[
        el('p',{class:'console-status','data-session-status':'',role:'status',text:status}),
        el('div',{class:'console-actions'},[button(phase==='closed'?'開啟工作階段':'關閉工作階段',()=>post(phase==='closed'?'open':'close'),{disabled:!!closing||!known}),
          ...(opening&&!closing?[button('取消搶機',()=>post('close'))]:[])]),
        el('details',{},[el('summary',{text:'閒置時間與 CU 上限'}),label('閒置逾時（10–60 分鐘）',idle),label('每階段 CU 上限',cap),
          button('儲存工作階段設定',()=>{if(idle.reportValidity()&&cap.reportValidity())post('settings',{idle_minutes:Number(idle.value),max_units:Number(cap.value)});}),
          el('p',{class:'console-meta',text:'最長 6 小時。關閉工作階段會等目前工作結束；閒置或達到上限會釋放 GPU。'})]),
        el('p',{class:'console-meta',text:`工作階段帳目（準備與閒置）：已觀測 ${total.toFixed(2)} CU${unknown?` · ${unknown} 筆待對帳`:''}；各工作另列。`})]);
    }
    async function run(action,message='已儲存。'){
      if(busy)return;busy=true;notice.textContent='處理中…';root.setAttribute('aria-busy','true');
      root.querySelectorAll('button').forEach(b=>b.disabled=true);
      try{const result=await action();if(!live())return;if(result?.connections)snapshot=result;else snapshot=await api('/api/compute/connections');
        if(live()){render();notice.textContent=result?.notice||message;}}
      catch(error){if(live()){clearSecrets();render();notice.textContent=errorText(error);}}
      finally{busy=false;if(live()){root.removeAttribute('aria-busy');mine.disabled=manage.disabled=false;}}
    }
    function show(showAdmin){main.hidden=showAdmin;admin.hidden=!showAdmin;mine.setAttribute('aria-pressed',String(!showAdmin));manage.setAttribute('aria-pressed',String(showAdmin));if(showAdmin)loadAdmin();}
    async function loadAdmin(){
      admin.textContent='正在讀取平台管理…';
      try{const data=await api('/api/compute/platform-admin');if(!live())return;
        admin.replaceChildren(el('h3',{text:'本機使用權與連線'}),el('p',{text:'查看誰已連接平台；每人自行管理金鑰與雲端費用。'}),
          ...data.users.map(user=>{const input=el('input',{type:'checkbox',checked:user.local_allowed,'aria-label':`${user.email} 本機使用權`});
            input.addEventListener('change',async()=>{input.disabled=true;try{await api('/api/compute/platform-admin/local-grant',{method:'PATCH',body:JSON.stringify({email:user.email,allowed:input.checked})});await load();await loadAdmin();notice.textContent='本機使用權已更新。';}catch(error){input.checked=!input.checked;input.disabled=false;notice.textContent=errorText(error);}});
            return el('article',{class:'console-admin-row'},[el('strong',{text:user.email}),label('本機使用權',input),el('p',{text:user.connections.filter(c=>c.credential_present).map(c=>`${providers[c.provider].name}${c.use_enabled?' · 使用中':''}`).join('、')||'尚未連接雲端平台'})]);}));
      }catch(error){if(live())admin.textContent=errorText(error);}
    }
    function render(){
      if(!snapshot)return;
      clearSecrets();const prefs=snapshot.preferences;order=[...prefs.auto_order];
      main.replaceChildren(el('p',{class:'console-intro',text:'連接自己的帳號，再選擇要使用的平台。'}),
        el('p',{text:'這些連線只供你的帳號使用；雲端費用由各平台向你收取。'}),el('p',{class:'console-meta',text:`Castle 帳號：${snapshot.owner}`}),routing(prefs),
        el('section',{class:'console-grid','aria-label':'五個運算平台'},snapshot.connections.map(card)),exceptions(prefs));
    }
    function routing(prefs){
      const choices=el('select',{'aria-label':'新任務預設'},[el('option',{value:'',text:'尚未選擇',disabled:true}),el('option',{value:'auto',text:'自動安排'}),
        ...snapshot.connections.map(c=>el('option',{value:c.provider,disabled:!c.use_enabled||!c.can_enable,text:providers[c.provider].name+(!c.use_enabled||!c.can_enable?' · '+(c.disabled_reason||'尚未開啟使用'):'')}))]);
      choices.value=prefs.default_placement||'';
      const orderList=el('ol',{class:'console-order','aria-label':'自動安排順序'});
      function drawOrder(){orderList.replaceChildren(...order.map((id,index)=>el('li',{},[el('span',{text:`${index+1}. ${providers[id].name}`}),
        button('↑',()=>move(index,-1),{disabled:index===0,'aria-label':`${providers[id].name} 上移`}),
        button('↓',()=>move(index,1),{disabled:index===order.length-1,'aria-label':`${providers[id].name} 下移`})])));
        if(!order.length)orderList.append(el('li',{text:'先開啟你要使用的平台。'}));}
      function move(index,delta){const id=order[index];[order[index],order[index+delta]]=[order[index+delta],order[index]];drawOrder();
        orderList.querySelector(`[aria-label="${providers[id].name} ${delta<0?'上移':'下移'}"]`)?.focus();}
      drawOrder();
      const saveOrder=button('儲存順序',()=>save(true),{disabled:!order.length});
      const saveDefault=button('儲存預設',()=>save(choices.value==='auto'&&prefs.default_placement!=='auto'),{disabled:!choices.value});
      const note=el('p',{class:'console-meta',text:prefs.auto_policy==='legacy_load'?'目前沿用負載安排；儲存順序後改依你的排序。':'依序選擇可用且支援這次需求的平台。'});
      choices.addEventListener('change',()=>{saveDefault.disabled=!choices.value;note.textContent=choices.value==='auto'&&prefs.default_placement!=='auto'?'請確認右方順序，再儲存預設。':'只影響新任務；現有任務維持原平台。';});
      function save(withOrder){return run(()=>api('/api/compute/preferences',{method:'POST',body:JSON.stringify({default_placement:choices.value||null,
        feature_placements:prefs.feature_placements,revision:prefs.revision,...withOrder?{auto_order:order}:{}})}),'新任務的安排已儲存。');}
      return el('section',{class:'console-routing'},[
        el('div',{},[el('h3',{text:'新任務預設'}),el('div',{class:'console-preference-row'},[label('執行位置',choices),saveDefault])]),
        el('div',{},[el('h3',{text:'自動安排順序'}),el('div',{class:'console-actions'},[orderList,saveOrder]),note])]);
    }
    function card(data){
      const id=data.provider,info=providers[id],reasonId=`console-${id}-reason`,testReasonId=`console-${id}-test-reason`;
      const toggle=button('使用',()=>run(()=>api(`/api/compute/connections/${id}`,{method:'PATCH',body:JSON.stringify({use_enabled:!data.use_enabled,revision:data.revision})}),'使用狀態已更新。'),{
        class:'ghost small console-toggle',role:'switch','aria-checked':String(data.use_enabled),'aria-label':`使用 ${info.name}`,'aria-describedby':reasonId,disabled:!data.use_enabled&&!data.can_enable});
      const testReason=el('p',{id:testReasonId,class:'console-meta',text:data.test_disabled_reason});
      const test=button('測試連線',()=>run(()=>api(`/api/compute/connections/${id}/test`,{method:'POST',body:JSON.stringify({revision:data.revision})}),'連線檢查完成；請查看平台狀態。'),{disabled:!data.test_available,'aria-describedby':testReasonId});
      const testActions=el('div',{class:'console-actions'},[test]);
      const content=el('article',{class:'console-card','data-provider':id},[
        el('div',{class:'console-card-head'},[el('h3',{text:info.name}),toggle]),el('p',{text:info.line}),
        el('p',{id:reasonId,class:'console-status',text:data.status_message+(data.disabled_reason&&!data.status_message.includes(data.disabled_reason)&&data.disabled_code!=='recipe_not_ready'?' · '+data.disabled_reason:'')}),
      ]);
      if(data.safe_reason)content.append(el('p',{class:'console-reason',text:data.safe_reason}));
      if(data.use_enabled&&snapshot.preferences.default_placement===id)content.append(el('p',{class:'console-meta',text:`關閉後，新任務預設改為${snapshot.connections.some(c=>c.provider!==id&&c.use_enabled)?'自動安排':'尚未選擇'}。`}));
      const controls=el('div',{class:'console-credential'});
      if(id==='local')controls.append(el('p',{text:'不需要帳號或金鑰。'}));
      else if(id==='colab')controls.append(button(data.credential_present?'重新使用 Google 登入':'使用 Google 登入',()=>login(controls)),sessionControls());
      else{
        let editing=!data.credential_present;
        const key=el('input',{type:'password',autocomplete:'new-password',spellcheck:'false',maxlength:4096,'aria-label':`${info.name} API 金鑰`});
        const inputReason=el('p',{class:'console-meta',text:'請貼上完整金鑰。'});
        const save=button('儲存金鑰',()=>{const credential=key.value;key.value='';return run(()=>api(`/api/compute/connections/${id}/credential`,{method:'PUT',body:JSON.stringify({credential,revision:data.revision,operation_id:crypto.randomUUID()})}),'金鑰已儲存，請測試連線。');},{class:'primary small',disabled:true});
        testActions.prepend(save);
        key.addEventListener('input',()=>{const valid=key.value.length>=16&&key.value===key.value.trim()&&!/\s/.test(key.value);save.disabled=!valid;
          test.disabled=editing||!data.test_available;testReason.textContent=editing?'請先儲存新金鑰。':data.test_disabled_reason;
          inputReason.textContent=valid?'儲存後會清空欄位。':'請貼上完整金鑰，勿包含空白或換行。';});
        function showKey(){controls.replaceChildren();save.hidden=!editing;test.disabled=editing||!data.test_available;testReason.textContent=editing?'請先儲存新金鑰。':data.test_disabled_reason;
          if(editing){controls.append(label('API 金鑰',key),inputReason,...data.credential_present?[button('取消更換',()=>{key.value='';editing=false;showKey();})]:[]);}
          else controls.append(el('div',{class:'console-actions'},[
            button('更換金鑰',()=>{editing=true;showKey();key.focus();}),button('移除連線',()=>{controls.replaceChildren(el('p',{text:'移除後需重新輸入金鑰。'}),button('確認移除',()=>run(()=>api(`/api/compute/connections/${id}/credential`,{method:'DELETE',body:JSON.stringify({revision:data.revision})}),'連線已移除。')),button('保留連線',showKey));})]));
          controls.append(el('a',{href:info.link,target:'_blank',rel:'noopener noreferrer',text:info.linkText}));
        }
        showKey();
      }
      const balances=data.balances?.length?data.balances.map(b=>`${b.value} ${b.unit}（${stamp(b.observed_at)}）`).join('、'):(id==='local'?'不適用':'暫無法取得');
      const details=el('details',{},[el('summary',{text:'說明'}),el('p',{text:'測試只確認帳號與餘額，不會生成或配置 GPU。關閉使用不影響已接受的任務。'}),
        el('p',{text:`最後確認：${stamp(data.checked_at)}`}),el('p',{text:`上次查詢餘額：${balances}`})]);
      if(data.credential_saved_at||data.legacy_modified_at)details.append(el('p',{text:`${data.credential_saved_at?'金鑰更新':'舊設定最後修改'}：${stamp(data.credential_saved_at||data.legacy_modified_at)}`}));
      if(id==='colab')details.append(el('p',{text:'訂閱方案：未知。使用自己的 Google 帳號；有訂閱也不保證 GPU 可分配。'}),el('a',{href:info.link,target:'_blank',rel:'noopener noreferrer',text:info.linkText}));
      if(data.account_type)details.append(el('p',{text:`帳號類型：${data.account_type}`}));
      if(id==='runninghub')controls.append(el('p',{class:'console-meta',text:'金鑰類型：'+(data.key_type_label||'尚未確認金鑰類型')}));
      content.append(controls,el('div',{class:'console-test'},[testActions,testReason]),details);return content;
    }
    async function login(target){
      if(busy)return;busy=true;notice.textContent='正在準備 Google 登入…';
      const post=(path,body={})=>api('/api/colab/'+path,{method:'POST',body:JSON.stringify(body)});
      try{const state=await api('/api/colab');if(!state.installed)throw new Error();
        const account=state.accounts[0]||await post('accounts',{label:'我的 Google 帳號'});
        const flow=await post(`accounts/${account.id}/login`),url=new URL(flow.authorization_url);
        if(url.protocol!=='https:'||url.hostname!=='accounts.google.com')throw new Error();
        const code=el('input',{type:'password',autocomplete:'off','aria-label':'Google 授權碼'});
        const finish=button('完成登入',()=>{const value=code.value;code.value='';return run(async()=>{await post(`accounts/${account.id}/finish`,{flow_id:flow.flow_id,code:value});await post(`accounts/${account.id}/refresh`);},'Google 帳號已連接；可開啟使用。');});
        if(live()){target.replaceChildren(el('a',{href:url.href,target:'_blank',rel:'noopener noreferrer',text:'開啟 Google 授權頁面'}),label('Google 授權碼',code),finish);notice.textContent='在 Google 選擇自己的帳號，再貼上授權碼。';}
      }catch(_){if(live())notice.textContent='Google 登入暫時無法開啟，請稍後再試。';}finally{busy=false;}
    }
    function exceptions(prefs){
      const details=el('details',{class:'console-exceptions'},[el('summary',{text:'特定功能例外'})]);
      const rows=el('div');details.append(rows);
      function draw(){rows.replaceChildren(...Object.entries(prefs.feature_placements).map(([id,lane])=>el('p',{text:`${features?.find(f=>f.id===id)?.title||id}：${lane==='auto'?'自動安排':providers[lane]?.name||lane}${lane!=='auto'&&!snapshot.connections.some(c=>c.provider===lane&&c.use_enabled&&c.can_enable)?' · 待調整':''}`})));
        if(!Object.keys(prefs.feature_placements).length)rows.append(el('p',{text:'所有功能沿用新任務預設。'}));}
      draw();details.addEventListener('toggle',async()=>{if(!details.open||details.dataset.loaded)return;
        try{features=(await api('/api/compute/capabilities')).features;if(!live())return;details.dataset.loaded='true';draw();
          const feature=el('select',{'aria-label':'指定功能'},features.map(f=>el('option',{value:f.id,text:f.title})));
          const choice=el('select',{'aria-label':'功能預設平台'},[el('option',{value:'',text:'沿用一般預設'}),el('option',{value:'auto',text:'自動安排'}),...snapshot.connections.map(c=>el('option',{value:c.provider,text:providers[c.provider].name,disabled:!c.use_enabled||!c.can_enable}))]);
          details.append(label('指定功能',feature),label('執行位置',choice),button('儲存例外',()=>{const overrides={...prefs.feature_placements};if(choice.value)overrides[feature.value]=choice.value;else delete overrides[feature.value];return run(()=>api('/api/compute/preferences',{method:'POST',body:JSON.stringify({default_placement:prefs.default_placement,feature_placements:overrides,revision:prefs.revision})}));}));
        }catch(_){notice.textContent='功能列表暫時無法讀取。';}});return details;
    }
    openSheet({id:'compute-settings',title:'我的運算平台',opener,content:root,className:'compute-settings-sheet',onOpen:()=>{
      observer.observe(document.body,{childList:true,subtree:true});notice.textContent='正在讀取你的平台…';load().then(()=>{if(live())notice.textContent='';}).catch(()=>{if(live())notice.textContent='目前無法讀取設定，請稍後重新開啟。';});
    }});return {destroy};
  }
  window.ComputeSettings={open,reset:()=>{for(const destroy of [...sessions])destroy();}};
})();

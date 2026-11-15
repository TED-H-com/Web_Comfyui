/* Approved calibration inputs are resolved by the authenticated server. */
(() => {
 'use strict';
 const features=new Set(['dance','video-replace','voice-speak','voxcpm-tts']);
 function mount(root,changed,{api,feature,submit,limits}={}) {
  const field=document.createElement('label');field.className='field compute-field';field.hidden=true;
  const title=document.createElement('span');title.textContent='校準素材';
  const select=document.createElement('select');select.setAttribute('aria-label','校準素材');
  select.add(new Option('使用目前表單素材',''));field.append(title,select);root.append(field);
  const note=document.createElement('p');note.className='compute-note';note.hidden=true;root.append(note);
  const run=document.createElement('button');run.type='button';run.className='ghost small';run.textContent='使用校準素材開始生成';run.hidden=true;root.append(run);
  const estimateLabel=document.createElement('label');estimateLabel.className='field compute-field';estimateLabel.hidden=true;
  const estimateText=document.createElement('span');estimateText.textContent='Colab A100 預估 CU／小時（依帳號資源面板，可留空）';
  const estimate=document.createElement('input');estimate.type='number';estimate.min='0.01';estimate.step='any';estimate.setAttribute('aria-label','Colab A100 預估 CU／小時');estimateLabel.append(estimateText,estimate);root.append(estimateLabel);
  const quote=document.createElement('button');quote.type='button';quote.className='ghost small';quote.textContent='更新帳號報價';quote.hidden=true;root.append(quote);
  quote.addEventListener('click',async()=>{quote.disabled=true;try{const refreshed=await api('/api/compute/parity/refresh',{method:'POST',body:JSON.stringify({request:{...selected(),remote_voice_consent:check.checked},limits:limits(),estimates:estimate.value?{colab_cu_per_hour:Number(estimate.value)}:{}})});showEstimates(refreshed);await changed();}catch(_){note.hidden=false;note.textContent='報價尚未確認，請查看平台條件。';}finally{quote.disabled=false;}});
  const label=document.createElement('label');label.className='field compute-field';label.hidden=true;
  const check=document.createElement('input');check.type='checkbox';
  const text=document.createElement('span');text.textContent='僅本次：同意把聲音、參考文字與專屬權重上傳至所選遠端平台';label.append(check,text);root.append(label);
  const grid=document.createElement('div');grid.className='compute-grid';grid.hidden=true;grid.append(field,estimateLabel);
  const actions=document.createElement('div');actions.className='compute-job-actions';actions.append(quote,run);root.append(grid,note,actions,label);
  const estimateNote=document.createElement('p');estimateNote.className='compute-note';estimateNote.hidden=true;root.append(estimateNote);
  function showEstimates(data){
   const rows=Array.isArray(data)?data:(data.results||data.lanes||[]);
   const values=(Array.isArray(rows)?rows:Object.values(rows)).filter(r=>r.quote&&(r.lane==='autoip'||(['runninghub','comfy_cloud'].includes(r.lane)&&r.quote.is_estimate)));
   estimateNote.textContent=values.map(r=>{const q=r.quote,m=q.measurement||{};if(r.lane==='autoip')return `AutoIP：報價上限 ${q.amount} ${q.unit}（原價）；離峰 ${q.off_peak_amount??'未提供'} ${q.unit} 可能適用，非保證`;return `${r.lane==='runninghub'?'RunningHub':'Comfy Cloud'}：${Number(q.amount).toFixed(3)} ${q.unit} · 估計，非官方報價 · 來源 ID：${m.request_id||'未提供'} · ${m.case_id||'帳號估計'} Local wall ${m.wall_seconds?Number(m.wall_seconds).toFixed(1)+' 秒':''} · 測量時間：${q.measured_at||m.measured_at||q.source_checked_at||'未提供'} · 倍率：${q.safety_factor||'未提供'} · 版本：${m.mapping_version||q.estimate_version||'帳號估計'}`;}).join('；');
   estimateNote.hidden=!values.length;
  }
  let rows=[],signature='';
  const selected=()=>select.value?{featureId:feature.id,settings:{},parity_case_id:select.value}:null;
  select.addEventListener('change',()=>{estimateNote.hidden=true;estimateNote.textContent='';run.hidden=quote.hidden=estimateLabel.hidden=note.hidden=!select.value;note.textContent='使用核准的原角色素材與功能預設設定；下方表單自訂值不套用於校準。';check.checked=false;changed();});
  check.addEventListener('change',changed);
  run.addEventListener('click',async()=>{run.disabled=true;try{await submit(selected());}catch(_){note.textContent='請確認平台就緒狀態；待查任務請使用原任務查詢。';}finally{run.disabled=false;}});
  if(features.has(feature?.id)&&api)api('/api/compute/parity/cases').then(data=>{
   rows=(data.cases||[]).filter(c=>c.feature===feature.id);grid.hidden=field.hidden=!rows.length;
   for(const c of rows){const o=new Option(c.label+(c.ready?'':' · '+c.reason),c.case_id);o.disabled=!c.ready;select.add(o);}
  }).catch(()=>{});
  return {selected,request(request,lane){
   request=selected()||request;
   const next=JSON.stringify([request?.featureId,request?.settings,request?.parity_case_id,lane]);if(signature&&signature!==next)check.checked=false;signature=next;
   label.hidden=!(['voice-speak','voxcpm-tts'].includes(request?.featureId));
   return features.has(request?.featureId)?{...request,remote_voice_consent:!label.hidden&&check.checked}:request;
  },reset(){check.checked=false;}};
 }
 function render(placement,rows){for(const option of placement.options){const row=rows.find(x=>x.lane===option.value);if(!row?.recipe_mode_label)continue;option.disabled=row.eligible!==true;const base={local:'本機',colab:'Google Colab',autoip:'AutoIP',comfy_cloud:'Comfy Cloud',runninghub:'RunningHub'}[row.lane];option.textContent=`${base} · ${row.recipe_mode_label}`;option.title=row.recipe_difference||'';}}
 window.CastleComputeParity={mount,render};
})();

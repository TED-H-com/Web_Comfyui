// Run the real function body in an isolated browser-state harness; no HTTP.
const fs=require('fs'),path=require('path'),assert=require('assert');
const source=fs.readFileSync(path.join(__dirname,'../webui/static/compute-placement.js'),'utf8');
const refresh=source.slice(source.indexOf('    async function refreshJobs(){'),source.indexOf('    async function cancel(id){'));
const manual=source.slice(source.indexOf('    async function refreshOne(id){'),source.indexOf('    async function refreshJobs(){'));
const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
(async()=>{
 for(const fallback of [false,true]) {
  const run=new AsyncFunction('assert','fallback',`
   const id='11111111-1111-4111-8111-111111111111', historical='22222222-2222-4222-8222-222222222222';
   const calls=[],jobs=new Map(),currentJobs=new Map([[id,{lane:'colab',caseId:'dance.anime.chatgpt'}]]);
   let selected='dance.anime.chatgpt',pending={id},nextCursor=null,olderPagesLoaded=false;
   const placement={value:'colab'},parity={selected:()=>({parity_case_id:selected})};
   const terminal=new Set(['completed']),live=()=>true,cursor=x=>x,receipt=x=>x,renderJobs=()=>{},jobNotice={};
   const job={request_id:id,state:'submission_uncertain'};
   const accept=value=>{if(value?.request_id)jobs.set(value.request_id,value);};
   const api=async url=>{calls.push(url);if(url==='/api/compute/jobs')return {jobs:[{request_id:historical,state:'submission_uncertain'},...(fallback?[]:[job])]};return job;};
   ${refresh}\n${manual}
   const gets=()=>calls.filter(u=>u.includes('/jobs/'));
   await refreshJobs();assert.deepEqual(gets(),['/api/compute/jobs/'+id]);
   if(fallback)jobs.delete(id);
   calls.length=0;selected='dance.anime.claude';await refreshJobs();assert.deepEqual(gets(),[]);
   calls.length=0;selected='dance.anime.chatgpt';placement.value='local';await refreshJobs();assert.deepEqual(gets(),[]);
   calls.length=0;placement.value='colab';await refreshJobs();assert.deepEqual(gets(),['/api/compute/jobs/'+id]);
   assert.equal(pending.id,id);assert(!calls.some(u=>u.endsWith(historical)));
   calls.length=0;selected='dance.anime.claude';await refreshOne(id);assert.deepEqual(gets(),['/api/compute/jobs/'+id]);
   calls.length=0;await refreshOne(historical);assert.deepEqual(gets(),['/api/compute/jobs/'+historical]);
   assert.equal(pending.id,id);
  `);
  await run(assert,fallback);
 }
 console.log('I1 active and pending fallback: scope, resume, historical and manual passed');
})().catch(error=>{console.error(error);process.exit(1);});

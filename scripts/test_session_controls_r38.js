'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const source=fs.readFileSync(path.join(__dirname,'../webui/static/compute-settings.js'),'utf8');
const fn=source.slice(source.indexOf('    function sessionControls(){'),source.indexOf('    async function run('));
function render(snapshot){
 const ctx={colabSession:snapshot,el:(tag,props,children=[])=>({tag,...props,children}),button:(text,click,props)=>({tag:'button',text,...props}),label:(text,child)=>({tag:'label',text,children:[child]}),run:()=>{throw Error('no operations')},api:()=>{throw Error('no network')}};
 vm.runInNewContext(fn+';result=sessionControls()',ctx);
 return {status:ctx.result.children[0].text,open:ctx.result.children[1].children[0]};
}
let result=render({session:{phase:'closed',close_requested:true}});
assert.equal(result.status,'未開啟');assert.equal(result.open.text,'開啟工作階段');assert.equal(result.open.disabled,false);
for(const phase of ['closing','stopping','released_unsettled']){
 result=render({session:{phase,close_requested:false}});assert.equal(result.status,'關閉中');assert.equal(result.open.disabled,true);
}
for(const snapshot of [null,{}, {session:{}},{session:{phase:'surprise'}}]){
 result=render(snapshot);assert.equal(result.status,'工作階段狀態暫時無法確認');assert.equal(result.open.disabled,true);
}
console.log('session controls: 8 offline regressions passed');

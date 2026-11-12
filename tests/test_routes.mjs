import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const ctx={document:{querySelector:()=>null},location:{hash:'',replace(hash){this.hash=hash;}}};
vm.createContext(ctx);
vm.runInContext(readFileSync(new URL('../static/overview.js',import.meta.url),'utf8'),ctx);
vm.runInContext(readFileSync(new URL('../static/company-context.js',import.meta.url),'utf8'),ctx);
vm.runInContext(readFileSync(new URL('../static/app.js',import.meta.url),'utf8').split("$('#quit').onclick")[0],ctx);
vm.runInContext("render=()=>{};state={invoices:[{id:10,kind:'purchase',status:'draft'},{id:11,kind:'purchase',status:'issued'}]}",ctx);
vm.runInContext(readFileSync(new URL('../static/editor.js',import.meta.url),'utf8'),ctx);
for(const hash of ['#new/purchase']){
  ctx.location.hash=hash;ctx.route();assert.equal(ctx.location.hash,'#journal');
}
for(const [hash,renderer] of [['#document/10','renderDocument'],['#edit/10','renderEditor'],['#payment/11','renderPaymentEditor']]){
  ctx.location.hash=hash;ctx.route();ctx[renderer]();assert.equal(ctx.location.hash,'expenses');
}
ctx.location.hash='#new/sale';ctx.route();assert.equal(vm.runInContext('view',ctx),'new');
ctx.location.hash='#invoices';ctx.route();assert.equal(vm.runInContext('view',ctx),'invoices');
ctx.location.hash='#payables';ctx.route();assert.equal(ctx.location.hash,'#expenses');
ctx.location.hash='#expenses/123';ctx.route();assert.equal(vm.runInContext('view',ctx),'expenses');
console.log('Expenses and bank journal routes remain separate; payables alias opens expenses without document generation.');

ctx.history={replaceState(_state,_title,hash){ctx.location.hash=hash}};
ctx.confirm=()=>false;
vm.runInContext("editorDirty=true;lastCompanyRoute='#expenses/123'",ctx);
ctx.location.hash='#overview';ctx.route();
assert.equal(ctx.location.hash,'#expenses/123','Cancel restores the editor URL');
assert.equal(vm.runInContext('view',ctx),'expenses','Cancel does not replace the editor');
assert.equal(vm.runInContext('editorDirty',ctx),true,'Cancel retains unsaved work');
ctx.confirm=()=>true;ctx.location.hash='#overview';ctx.route();
assert.equal(vm.runInContext('view',ctx),'overview');
assert.equal(vm.runInContext('editorDirty',ctx),false,'Confirmed discard clears the warning');
console.log('Unsaved navigation checks passed: cancel preserves the editor; discard allows navigation.');

ctx.document.querySelector=()=>({});ctx.notify=()=>{};
vm.runInContext("editorDirty=true;lastCompanyRoute='#edit/2'",ctx);
ctx.location.hash='#overview';ctx.route();
assert.equal(ctx.location.hash,'#edit/2','Pending saves block navigation');
assert.equal(vm.runInContext('editorDirty',ctx),true,'Pending saves do not discard edits');
ctx.document.querySelector=()=>null;
console.log('Pending-save navigation checks passed.');

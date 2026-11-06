import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const ctx={location:{hash:'',replace(hash){this.hash=hash;}}};
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

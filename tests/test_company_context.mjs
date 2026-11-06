import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const ctx=vm.createContext({});
vm.runInContext(readFileSync(new URL('../static/company-context.js',import.meta.url),'utf8'),ctx);
const raw={companies:[{id:1,name:'EXAMPLE'},{id:2,name:'Example Services'}],invoices:[{id:1,company_id:1},{id:2,company_id:2}],journal:[{id:1,company_id:1},{id:2,company_id:2}],bank_entries:[{id:1,company_id:1},{id:2,company_id:2}],contacts:[{id:1,company_ids:[1]},{id:2,company_ids:[2]},{id:3,company_ids:[1,2]}],catalog:[{id:'a',company_ids:[1]},{id:'b',company_ids:[2]}]};
for(const id of [1,2]){
 const scoped=ctx.scopeCompanyState(raw,id);
 for(const key of ['invoices','journal','bank_entries'])assert(scoped[key].every(r=>r.company_id===id),key+' leaks another company');
 assert.equal(scoped.contacts.length,2);assert.equal(scoped.catalog.length,1);assert.equal(scoped.companies.length,2,'Switcher and separate-expense form keep both company identities');
 assert.equal(scoped.all_journal.length,2,'Explicit separate-expense duplicate checks can still find the destination company');
}
assert.equal(ctx.scopeCompanyState(raw,99).invoices.length,0,'Invalid selection must never fall back to all records');
ctx.raw=raw;vm.runInContext('allState=raw',ctx);
assert.equal(ctx.companyRouteOwner('expenses','2'),2);assert.equal(ctx.companyRouteOwner('document','1'),1);
console.log('Company context checks passed: financial separation, contacts, services and deep-link ownership.');

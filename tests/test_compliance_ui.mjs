import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';

const calls=[];
const ctx={
  today:()=> '2026-11-06',esc:s=>String(s??''),field:()=>'',area:()=>'',
  $:()=>({textContent:''}),notify:()=>{},
  openDialog:(html,submit)=>{ctx.submit=submit},
  api:async(path,payload)=>{calls.push({path,payload});return {...payload,id:1,revision:1}},
};
vm.createContext(ctx);
vm.runInContext(readFileSync(new URL('../static/compliance.js',import.meta.url),'utf8'),ctx);
vm.runInContext(`renderCompliance=async()=>{};complianceWork={company:{id:2},window:{year:2026,start:'2026-01-01'},profile:{accountant:'Accountant'},items:[{kind:'filing',key:'pp30:2026-10',title:'P.P.30',period:'2026-10',status:'unknown',revision:0,files:[]}]};complianceEdit(complianceWork.items[0]);`,ctx);
await ctx.submit(new Map([['status','filed'],['filed_on','2026-11-05'],['reference','RD-123']]));
assert.equal(calls.length,1,'Unsaved default filing task must be editable');
assert.equal(calls[0].payload.key,'pp30:2026-10');
assert.equal(calls[0].payload.company_id,2);

vm.runInContext(`complianceWork.items.push({id:9,key:'vat:2026-10',kind:'vat'});complianceEdit({kind:'vat',revision:0,files:[]});`,ctx);
await assert.rejects(()=>ctx.submit(new Map([['month','2026-10'],['notes','Source report']])),/already exists/);
assert.equal(calls.length,1,'Duplicate monthly worksheet should be edited instead');

vm.runInContext(`complianceEdit({kind:'cit',id:10,key:'cit:annual',stage:'annual',revision:1,files:[]});`,ctx);
await assert.rejects(()=>ctx.submit(new Map([['stage','half_year'],['notes','Source report']])),/separate worksheet/);
console.log('Compliance editor saves default filings, prevents duplicate worksheets and protects saved worksheet periods.');

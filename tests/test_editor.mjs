import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const ctx={state:{currencies:{THB:2,USD:2,JPY:0,KWD:3}},esc:s=>String(s??''),money:(n,c)=>`${c} ${n}`};vm.createContext(ctx);
vm.runInContext(readFileSync(new URL('../static/editor.js',import.meta.url),'utf8'),ctx);
const row=(quantity,unit_price)=>({querySelector:s=>({value:s.includes('quantity')?quantity:unit_price})});
assert.equal(ctx.liveLineTotal(row('2.5','100.01'),'THB'),25003);
assert.equal(ctx.liveLineTotal(row('0.5','0.01'),'THB'),1);
assert.equal(ctx.liveLineTotal(row('1.5','1'),'JPY'),2);
assert.equal(ctx.liveLineTotal(row('1.5','1.001'),'KWD'),1502);
assert.equal(ctx.liveLineTotal(row('1','1.001'),'THB'),null);
assert.equal(ctx.docMinor('4541.12','THB'),454112);
assert.equal(ctx.docMinor('0.29','THB'),29);
assert.equal(ctx.docMinor('1.001','KWD'),1001);
assert.equal(ctx.docMinor('1','JPY'),1);
assert.equal(ctx.docMinor('1.2','JPY'),null);
assert.equal(ctx.docMinor('-1','THB'),null);
assert.equal(ctx.docMinor('NaN','THB'),null);
assert.equal(ctx.docVatMinor(10001,'7'),700);
assert.equal(ctx.docVatMinor(100,'0.50'),1);
assert.equal(ctx.docVatMinor(100,'101'),null);
assert.equal(ctx.docVatMinor(100,''),0);
const single=ctx.docLine({description:'One-time service',service:'',quantity:'1',unit_price:'100'});
assert.match(single, /Add service period/);
assert.match(single, /name="service"[^>]*hidden/);
const recurring=ctx.docLine({description:'Monthly service',service:'November 2026',quantity:'1',unit_price:'100'});
assert.match(recurring, /value="November 2026"/);
assert.match(recurring, /class="add-service-period" hidden/);
console.log('Document-editor amount and optional service-period assertions passed.');

assert.match(ctx.docBank({bank_name:'Example Bank',bank_account_name:'Example',bank_account_number:'001-02'}), /001-02/);
assert.equal(ctx.docBank({}), '');

const vatLine=ctx.docLine({description:'VAT-exclusive service',quantity:'1',unit_price:'100.00',total_minor:10000},false,'THB','7');
assert.match(vatLine, /class="doc-line-vat">7%<\/td><td class="doc-line-total">THB 100.00<\/td>/);
assert.match(ctx.docLine({description:'No VAT',quantity:'1',unit_price:'100.00'},false,'THB',''), /class="doc-line-vat">—<\/td>/);

const signers=ctx.docSignatures({name:'Seller Co',contact_name:'Seller Person'},{name:'Buyer Co',contact_name:'Buyer Person'},'2026-11-05');
for(const value of ['Seller Co','Seller Person','Buyer Co','Buyer Person','November 5, 2026'])assert.ok(signers.includes(value));
assert.ok(!signers.includes('__________________'));

assert.equal(ctx.docMoney(350000,'THB'), 'THB 3,500.00');

ctx.state.bank_entries=[
 {id:1,company_id:1,currency:'THB',direction:'in',date:'2026-11-02',amount_minor:9700,matched_record:''},
 {id:2,company_id:2,currency:'THB',direction:'in',date:'2026-11-02',amount_minor:9700,matched_record:''},
 {id:3,company_id:1,currency:'USD',direction:'in',date:'2026-11-02',amount_minor:9700,matched_record:''},
 {id:4,company_id:1,currency:'THB',direction:'out',date:'2026-11-02',amount_minor:9700,matched_record:''},
 {id:5,company_id:1,currency:'THB',direction:'in',date:'2026-11-02',amount_minor:9700,matched_record:'payment:99'},
];
assert.deepEqual([...ctx.receiptBankCandidates({company_id:1,currency:'THB'},9700,'2026-11-02')].map(b=>b.id),[1]);
assert.equal(ctx.receiptBankCandidates({company_id:1,currency:'THB'},10000,'2026-11-02').length,0,'Matches use cash received, excluding withholding');
console.log('Receipt bank candidates respect company, currency, direction, cash amount and existing links.');

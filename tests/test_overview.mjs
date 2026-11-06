import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const context=vm.createContext({today:()=> '2026-11-06'});
vm.runInContext(readFileSync(new URL('../static/overview.js',import.meta.url),'utf8'),context);
const company=(id,name)=>({id,name});
const line=(code,total,description='Consultation')=>({code,description,total_minor:total});
const payment=(amount,date,extra={})=>({amount_minor:amount,data:{date,withholding_minor:300},reversed:null,...extra});
const invoice=(id,extra={})=>({id,kind:'sale',status:'issued',company_id:1,currency:'THB',payments:[],data:{issue_date:'2026-11-01',company:{name:'EXAMPLE'},lines:[line('S1',10000)]},...extra});
const records={companies:[company(1,'EXAMPLE'),company(2,'Example Services')],invoices:[
  invoice(1,{payments:[payment(9700,'2026-11-02')]}),
  invoice(2,{status:'draft',payments:[payment(100000,'2026-11-03')]}),
  invoice(3,{status:'void',payments:[payment(100000,'2026-11-03')]}),
  invoice(4,{payments:[payment(9000,'2026-11-02',{reversed:'2026-11-03'})]}),
  invoice(5,{company_id:2,payments:[payment(20000,'2026-10-01')],data:{issue_date:'2026-10-01',company:{name:'Example Services'},lines:[line('S1',20000,'Other service')]}}),
  invoice(6,{currency:'USD',payments:[payment(500000,'2026-11-02')]}),
  invoice(7,{kind:'purchase',payments:[payment(5000,'2026-11-01')]}),
  invoice(8,{payments:[payment(1000,'2026-11-05')],data:{issue_date:'2026-01-01',company:{name:'EXAMPLE'},lines:[line('S1',1000)]}}),
  invoice(9,{payments:[payment(50000,'2026-11-20')],data:{issue_date:'2026-11-20',company:{name:'EXAMPLE'},lines:[line('S1',50000)]}})
],journal:[
  {company_id:1,currency:'THB',payment_status:'paid',payment_date:'2026-11-04',paid_amount_minor:9700,amount_minor:10000,date:'2026-08-01'},
  {company_id:1,currency:'THB',payment_status:'unpaid',amount_minor:100000},
  {company_id:1,currency:'THB',payment_status:'unknown',amount_minor:100000},
  {company_id:1,currency:'THB',payment_status:'paid',amount_minor:100000,date:'2026-11-01'},
  {company_id:1,currency:'THB',payment_status:'paid',payment_date:'2026-01-01',paid_amount_minor:99999}
]};
let result=context.overviewData(records,{end:'2026-11-06'});
assert.equal(result.series.length,6);assert.equal(result.series[0].month,'2026-06');
assert.equal(result.incoming,30700,'Cash excludes WHT, reversed payments, drafts, voids and other currencies');
assert.equal(result.outgoing,14700,'Cash out uses confirmed payment amount/date, including legacy payable payments');
assert.equal(result.net,16000);assert.equal(result.needsPaymentDetails,1);
assert.equal(result.companies.find(c=>c.id===1).sales_minor,20000);
assert.equal(result.companies.find(c=>c.id===1).in_minor,10700,'Cash can be received for an invoice issued outside the period');
assert.equal(result.services.length,2,'Same service codes at different companies remain separate');
assert.equal(result.services.find(s=>s.company==='EXAMPLE').invoices.size,2);
result=context.overviewData(records,{company:'2',end:'2026-11-06'});
assert.equal(result.incoming,20000);assert.equal(result.outgoing,0);assert.equal(result.services.length,1);assert.equal(result.companies.length,1);
result=context.overviewData(records,{currency:'USD',months:12,end:'2026-11-06'});
assert.equal(result.incoming,500000);assert.equal(result.outgoing,0);assert.equal(result.series[0].month,'2025-12');
result=context.overviewData({companies:[],invoices:[],journal:[]},{end:'2026-11-06'});
assert.equal(result.incoming,0);assert.equal(result.net,0);assert.equal(result.services.length,0);
console.log('Overview checks passed: cash vs tax, payment dates, invoice dates, statuses, companies, currencies, legacy expenses and empty states.');

const bankRecords={companies:[{id:1,name:'A'}],invoices:[],journal:[],bank_entries:[
 {company_id:1,currency:'THB',date:'2026-11-01',direction:'in',amount_minor:10000,reviewed:false,cash_flow:false},
 {company_id:1,currency:'THB',date:'2026-11-02',direction:'in',amount_minor:20000,reviewed:true,cash_flow:true},
 {company_id:1,currency:'THB',date:'2026-11-02',direction:'out',amount_minor:3000,reviewed:true,cash_flow:true},
 {company_id:1,currency:'THB',date:'2026-11-02',direction:'in',amount_minor:50000,reviewed:true,cash_flow:true,matched_record:'payment:1'},
 {company_id:2,currency:'THB',date:'2026-11-02',direction:'in',amount_minor:60000,reviewed:true,cash_flow:true}
]};
const bankData=context.overviewData(bankRecords,{company:'1',currency:'THB',months:6,end:'2026-11-06'});
assert.equal(bankData.incoming,20000);assert.equal(bankData.outgoing,3000);assert.equal(bankData.needsBankReview,1);
assert.equal(bankData.companies[0].sales_minor,0,'Bank deposits must not become sales');
console.log('Bank cash flow excludes unreviewed, matched and other-company entries.');

const reconciled={companies:[company(1,'A')],invoices:[],journal:[
 {id:1,company_id:1,currency:'THB',date:'2026-08-01',payment_status:'paid',payment_date:'2026-10-01',paid_amount_minor:100000,unmatched_cash_minor:60000},
 {id:2,company_id:1,currency:'THB',duplicate_of:1,date:'2026-08-01',payment_status:'paid',payment_date:'2026-10-01',paid_amount_minor:100000}
],bank_entries:[
 {company_id:1,currency:'THB',date:'2026-11-01',direction:'out',amount_minor:40000,reviewed:true,cash_flow:false,matched_record:'expense:1',linked_expense_id:1},
 {company_id:1,currency:'THB',date:'2026-11-02',direction:'out',amount_minor:60000,reviewed:false,cash_flow:false,matched_record:'expense:1',linked_expense_id:1}
]};
result=context.overviewData(reconciled,{company:'1',end:'2026-11-06'});
assert.equal(result.outgoing,100000,'Reviewed bank cash replaces manual cash; duplicate expense and unreviewed bank excluded');
assert.equal(result.series.find(r=>r.month==='2026-10').out_minor,60000);
assert.equal(result.series.find(r=>r.month==='2026-11').out_minor,40000);
reconciled.journal[0].unmatched_cash_minor=0;reconciled.bank_entries[1].reviewed=true;
assert.equal(context.overviewData(reconciled,{end:'2026-11-06'}).outgoing,100000,'Completing the second bank review does not count cash twice');
console.log('Reconciled cash checks passed: partial payments, dates and duplicate expenses.');

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

const datedInvoice=(id,date,{customer=10,name='Customer A',sales=10000,...extra}={})=>invoice(id,{contact_id:customer,balance_minor:0,...extra,data:{issue_date:date,due_date:'2026-11-01',contact:{name},company:{name:'EXAMPLE'},lines:[line('S1',sales)]}});
const yearly={companies:records.companies,invoices:[
 datedInvoice(21,'2025-01-01',{balance_minor:9700,payments:[payment(300,'2026-11-02')]}),
 datedInvoice(22,'2026-01-02',{balance_minor:5000,name:'Old customer name',sales:10000}),
 datedInvoice(23,'2026-11-02',{name:'Renamed customer',sales:15000}),
 datedInvoice(24,'2026-04-01',{customer:11,name:'Customer B',sales:40000}),
 datedInvoice(25,'2026-04-01',{company_id:2,customer:10,name:'Other company',sales:80000,balance_minor:80000}),
 datedInvoice(26,'2026-04-01',{currency:'USD',balance_minor:100000}),
 datedInvoice(27,'2026-04-01',{status:'draft',balance_minor:100000}),
 datedInvoice(28,'2026-04-01',{status:'void',balance_minor:100000}),
 datedInvoice(29,'2026-11-20',{balance_minor:100000}),
 datedInvoice(30,'2026-04-01',{balance_minor:0,payments:[payment(9700,'2026-05-01')]}),
],journal:[
 {id:31,company_id:1,currency:'THB',date:'2025-05-01',payment_status:'paid'},
 {id:32,company_id:1,currency:'THB',date:'2026-05-01',payment_status:'paid'},
 {id:33,company_id:1,currency:'THB',date:'2026-05-01',payment_status:'paid',duplicate_of:32},
],bank_entries:[
 {id:41,company_id:1,currency:'THB',date:'2025-01-02',amount_minor:10000,direction:'in',reviewed:true,cash_flow:true,shared_documents:[{id:1}]},
 {id:42,company_id:1,currency:'THB',date:'2025-12-31',amount_minor:2000,direction:'out',reviewed:false,cash_flow:false},
 {id:43,company_id:1,currency:'THB',date:'2026-02-01',amount_minor:100,direction:'in',reviewed:false,cash_flow:false,shared_documents:[{id:2}],documents:[]},
 {id:44,company_id:2,currency:'THB',date:'2025-01-02',amount_minor:50000,direction:'in',reviewed:false,cash_flow:false},
 {id:45,company_id:1,currency:'USD',date:'2025-01-02',amount_minor:50000,direction:'in',reviewed:false,cash_flow:false}
]};
result=context.overviewData(yearly,{company:'1',year:'2025',end:'2026-11-06'});
assert.equal(result.start,'2025-01-01');assert.equal(result.end,'2025-12-31');assert.equal(result.series.length,12);assert.equal(result.series[11].month,'2025-12');
assert.equal(result.incoming,10000);assert.equal(result.outgoing,0);
assert.deepEqual([...result.attention.bank],[42]);assert.deepEqual([...result.attention.documents],[42]);assert.deepEqual([...result.attention.expenses],[31]);
assert.equal(result.outstanding,14700,'Current saved balances include all issue years and WHT credits, regardless of selected year');
assert.deepEqual([...result.openInvoices],[21,22]);assert.deepEqual([...result.attention.overdue],[21,22]);
assert.equal(result.customers[0].sales_minor,10000,'Historical sales use historical invoice issue dates');
result=context.overviewData(yearly,{company:'1',year:2026,end:'2026-11-06'});
assert.equal(result.series.length,11);assert.equal(result.series[0].month,'2026-01');assert.equal(result.end,'2026-11-06');
assert.deepEqual([...result.attention.bank],[43]);assert.deepEqual([...result.attention.documents],[],'Supporting documents shared from linked expenses prevent false missing-document tasks');
assert.deepEqual([...result.attention.expenses],[32],'Older incomplete expenses and duplicate documents do not enter this year’s tasks');
assert.equal(result.customers[0].name,'Customer B');assert.equal(result.customers[0].sales_minor,40000);
assert.equal(result.customers[1].invoices.size,3,'Customer snapshots are grouped by contact ID');
assert.equal(result.customers[1].name,'Renamed customer');assert.equal(result.customers[1].sales_minor,35000);
assert.equal(result.outstanding,14700);assert.equal(result.incoming,10000,'Cash counts actual cash paid, excluding withholding credits');
yearly.invoices[1].data.due_date='2026-11-06';
assert.deepEqual([...context.overviewData(yearly,{company:'1',year:2026,end:'2026-11-06'}).attention.overdue],[21],'Invoices due today are not overdue');
yearly.invoices[0].data.due_date='';
assert.equal(context.overviewData(yearly,{company:'1',year:2026,end:'2026-11-06'}).attention.overdue.length,0,'No due date is not overdue');
assert.equal(context.overviewData(yearly,{year:2026,end:'2026-01-06'}).series.length,1,'January shows a single month');
assert.deepEqual([...context.overviewYears(yearly,'2026','2023')],['2026','2025','2023'],'Years include record years, previous year and retained selection but exclude future dates');
console.log('Year, current outstanding/WHT, overdue, top customer and attention drill data checks passed.');

context.state={currencies:{THB:2}};context.money=(n,c)=>c+' '+n;context.esc=String;
const januaryChart=context.cashFlowChart([{month:'2026-01',in_minor:0,out_minor:0}],'THB');
assert(!januaryChart.includes('NaN')&&!januaryChart.includes('Infinity'),'January’s single-month chart has finite coordinates');
console.log('Single-month chart coordinates remain valid.');

result=context.overviewData(records,{company:'1',from:'2026-11-02',to:'2026-11-04',end:'2026-11-06'});
assert.equal(result.start,'2026-11-02');assert.equal(result.end,'2026-11-04');assert.equal(result.series.length,1);
assert.equal(result.incoming,9700);assert.equal(result.outgoing,9700);
assert.equal(result.cashRecords.length,2,'Cash records use the exact same date and counting rules as the totals');
assert.equal(result.cashRecords.filter(r=>r.direction==='in').reduce((sum,r)=>sum+r.amount_minor,0),result.incoming);
assert.equal(result.cashRecords.filter(r=>r.direction==='out').reduce((sum,r)=>sum+r.amount_minor,0),result.outgoing);
let receipts=context.overviewData(records,{company:'1',year:2026,source:'invoice',end:'2026-11-06'});
assert.equal(receipts.incoming,10700);assert.equal(receipts.outgoing,0);
assert(receipts.cashRecords.every(r=>r.source==='invoice'));
assert.equal(receipts.services[0].sales_minor,context.overviewData(records,{company:'1',year:2026,end:'2026-11-06'}).services[0].sales_minor,'Cash source does not change invoice sales');
let expenses=context.overviewData(reconciled,{company:'1',source:'expense',end:'2026-11-06'});
assert.equal(expenses.outgoing,100000);assert.equal(expenses.cashRecords.length,2,'Zero manual residuals do not create duplicate cash records');
assert(expenses.cashRecords.every(r=>r.source==='expense'),'Bank payments linked to expenses remain expense cash');
assert.equal(context.overviewData(reconciled,{source:'bank',end:'2026-11-06'}).outgoing,0,'Other bank movements exclude already-linked expense cash');
result=context.overviewData(bankRecords,{company:'1',from:'2026-11-02',to:'2026-11-02',source:'bank',end:'2026-11-06'});
assert.equal(result.incoming,20000);assert.equal(result.outgoing,3000);assert.equal(result.cashRecords.length,2);
result=context.overviewData(yearly,{company:'1',from:'2025-12-31',to:'2026-01-02',end:'2026-11-06'});
assert.deepEqual([...result.series.map(r=>r.month)],['2025-12','2026-01']);
assert.equal(result.customers[0].sales_minor,10000);assert.equal(result.outstanding,14700);assert.equal(result.asOf,'2026-11-06');
for(const [from,to] of [['2026-02-30','2026-03-01'],['2026-11-03','2026-11-02'],['2026-11-01','2026-11-07'],['','2026-11-02']]){
 assert.throws(()=>context.overviewData(records,{from,to,end:'2026-11-06'}),/valid dates/);
}
assert.equal(context.overviewRange({from:'2024-02-29',to:'2024-02-29',end:'2026-11-06'}).series.length,1);
const incomingChart=context.cashFlowChart([{month:'2026-11',in_minor:100,out_minor:10000}],'THB','in');
assert.equal((incomingChart.match(/<path /g)||[]).length,1);assert(!incomingChart.includes('stroke-dasharray'));
const outgoingChart=context.cashFlowChart([{month:'2026-11',in_minor:100,out_minor:10000}],'THB','out');
assert.equal((outgoingChart.match(/<path /g)||[]).length,1);assert(outgoingChart.includes('stroke-dasharray'));
assert(!incomingChart.includes('NaN')&&!outgoingChart.includes('Infinity'));
console.log('Exact date ranges, leap dates, cash sources, cash-record reconciliation, unchanged sales/current balances and single-direction charts passed.');

const interactiveChart=context.cashFlowChart([{month:'2026-09',in_minor:12345,out_minor:654112}],'THB');
assert.ok(interactiveChart.includes(`data-detail="${context.overviewMonth('2026-09')} · Money in: THB 12345 · Money out: THB 654112"`),'Month interaction passes exact amounts to the currency formatter for both directions');
assert.match(interactiveChart,/class="chart-zone" tabindex="0"/,'Months are keyboard accessible');
assert(!/data-detail="[^"]*Money in:/.test(outgoingChart),'Outgoing interaction hides incoming amounts');
console.log('Interactive chart checks passed: exact amounts, keyboard targets and filtered values.');

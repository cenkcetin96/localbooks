import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const expense={id:1,company_id:1,currency:'THB',supplier:'Vendor Co Ltd',supplier_tax_id:'123',date:'2026-10-01',document_number:'INV-1',amount_minor:10000,documents:[{sha256:'abc'}],linked_banks:[{date:'2026-10-03',reviewed:false}]};
const state={journal:[expense,{...expense,id:2,duplicate_of:1}],bank_entries:[{id:1,linked_expense_id:1},{id:2}]};
const ctx=vm.createContext({state});vm.runInContext(readFileSync(new URL('../static/reconciliation.js',import.meta.url),'utf8'),ctx);
assert.equal(ctx.journalDisplayRows(state).length,2,'Default journal shows expense and unmatched bank, hides duplicate and linked bank');
assert.equal(ctx.journalDisplayRows(state,'bank').length,2);
assert.equal(ctx.journalDisplayRows(state,'duplicates')[0].id,2);
assert.equal(ctx.expenseDuplicates({...expense,document_number:'INV 1'})[0].entry.id,1);
assert.equal(ctx.expenseDuplicates({...expense,company_id:2}).length,0);
assert.equal(ctx.expenseDuplicates({...expense,supplier_tax_id:'456',sha256:''}).length,0);
assert.equal(ctx.expenseDuplicates({...expense,document_number:'OTHER',date:'2026-11-01',sha256:''}).length,0);
assert.equal(ctx.expenseDuplicates({...expense,document_number:'OTHER',date:'2026-11-01',sha256:'abc'})[0].exact,true,'Same uploaded file detected beyond date window');
assert.equal(ctx.journalReviewMatches({...expense,record_type:'document'},'needs_review'),true);
assert.equal(ctx.journalReviewMatches({...expense,record_type:'document'},'reviewed'),false);
assert.equal(ctx.expenseStatus({...expense,payment_status:'unknown',linked_cash_minor:10000}),'Paid · bank linked');
console.log('Reconciliation UI helper checks passed.');

state.companies=[{id:1,name:'Example Services'},{id:2,name:'EXAMPLE'}];
vm.runInContext(readFileSync(new URL('../static/attachment-review.js',import.meta.url),'utf8'),ctx);
assert.match(ctx.attachmentMismatch({bank:{company_id:1}},{suggested_company_id:2}),/belongs to EXAMPLE/);
assert.equal(ctx.attachmentMismatch({bank:{company_id:1}},{suggested_company_id:1}),'');
assert.equal(ctx.attachmentMismatch({bank:{company_id:1}},{}),'','An unknown company requires manual review, not an invented mismatch');
console.log('Attachment company mismatch helper checks passed.');

assert.equal(ctx.expensePaymentState({payment_status:'unknown',amount_minor:10000,linked_cash_minor:5000}),'partial');
assert.equal(ctx.expensePaymentState({payment_status:'unknown',amount_minor:10000,linked_cash_minor:10000}),'paid');

state.invoices=[{kind:'sale',status:'issued',company_id:1,currency:'THB',payments:[{amount_minor:9700,data:{date:'2026-11-02'},reversed:null}]}];
const deposit={company_id:1,currency:'THB',direction:'in',date:'2026-11-02',amount_minor:9700};
assert.equal(ctx.bankCashCollision(deposit),true,'Existing receipts require a link or distinct-payment confirmation');
assert.equal(ctx.bankCashCollision({...deposit,company_id:2}),false);
assert.equal(ctx.bankCashCollision({...deposit,amount_minor:10000}),false,'Cash excludes customer withholding');
state.invoices[0].payments[0].reversed='2026-11-12';
assert.equal(ctx.bankCashCollision(deposit),false,'Reversed receipts cannot block a new bank cash classification');
console.log('Bank cash collision checks include active receipts and exclude reversals.');

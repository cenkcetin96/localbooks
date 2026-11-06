'use strict';
function expenseIdentity(v){return String(v||'').normalize('NFKC').toLocaleLowerCase().replace(/[^\p{L}\p{N}\p{M}]/gu,'')}
function sameExpenseSupplier(a,b){const ta=expenseIdentity(a.supplier_tax_id),tb=expenseIdentity(b.supplier_tax_id);if(ta&&tb)return ta===tb;if(a.supplier_id&&b.supplier_id&&Number(a.supplier_id)===Number(b.supplier_id))return true;return !!expenseIdentity(a.supplier)&&expenseIdentity(a.supplier)===expenseIdentity(b.supplier)}
function expenseDuplicates(candidate,exclude=null){return (state.all_journal||state.journal||[]).filter(e=>e.id!==exclude&&!e.duplicate_of&&e.company_id===candidate.company_id).flatMap(e=>{const same=e.currency===candidate.currency&&sameExpenseSupplier(e,candidate),number=expenseIdentity(candidate.document_number),file=candidate.sha256&&e.documents?.some(d=>d.sha256===candidate.sha256),exact=same&&number&&number===expenseIdentity(e.document_number),days=Math.abs(Date.parse(e.date)-Date.parse(candidate.date))/86400000;return file||exact||(same&&e.amount_minor===candidate.amount_minor&&days<=7)?[{entry:e,reason:file?'This file is already attached':exact?'Same supplier and document number':'Same supplier and amount within 7 days',exact:!!(file||exact)}]:[]})}
function expensePaymentState(e){
 if(e.payment_status==='paid'||e.linked_cash_minor>=e.amount_minor)return 'paid';
 if(e.linked_cash_minor>0)return 'partial';
 return e.payment_status==='unpaid'?'unpaid':'unknown';
}
function expenseStatus(e){if(e.duplicate_of)return 'Duplicate document';if(e.payment_status==='paid')return 'Paid';if(e.linked_cash_minor>=e.amount_minor)return 'Paid · bank linked';if(e.linked_cash_minor>0)return 'Part paid · bank linked';return e.payment_status==='unpaid'?'Unpaid':'Not specified'}
function journalDisplayRows(records,source='all'){
 const docs=(records.journal||[]).filter(e=>source==='duplicates'?!!e.duplicate_of:!e.duplicate_of).map(e=>({...e,record_type:'document'}));
 const bank=(records.bank_entries||[]).filter(e=>source==='bank'||!e.linked_expense_id).map(e=>({...e,record_type:'bank'}));
 return source==='duplicates'||source==='document'?docs:source==='bank'?bank:[...docs,...bank];
}
function journalReviewMatches(e,filter){
 if(filter==='missing_docs')return !(e.record_type==='bank'?sharedBankDocuments(e):e.documents||[]).length;
 if(e.record_type==='bank')return filter==='needs_review'?!e.reviewed:!!e.reviewed;
 return filter==='needs_review'?e.linked_banks?.some(b=>!b.reviewed):!!e.linked_banks?.length&&e.linked_banks.every(b=>b.reviewed);
}
function sharedBankDocuments(e){return e.shared_documents||e.documents||[]}
function expenseDocuments(id){
 const e=state.journal.find(e=>e.id===id);if(!e)return;
 openDialog(`<h2>Expense documents & payments</h2><p>${esc(e.supplier)} · ${esc(e.document_number||'Expense #'+e.id)} · ${esc(money(e.amount_minor,e.currency))}</p><div class="bank-document-list">${(e.documents||[]).map((d,i)=>`<div><span>${esc(d.name)}<small class="sub">${esc(d.source==='expense_original'?'Expense original':d.source==='bank_attachment'?'Bank payment supporting document':'Supporting document')}</small></span><button type="button" class="secondary small" data-review-file="${i}">Review</button><button type="button" class="secondary small" data-expense-doc="${i}">View</button><a class="button secondary small" href="${d.url}">Download</a></div>`).join('')}</div>${e.linked_banks?.length?`<h3>Linked bank payments</h3>${e.linked_banks.map(b=>`<div class="linked-payment"><span>${esc(b.date)} · ${money(b.amount_minor,e.currency)} · ${b.reviewed?'Reviewed':'Needs review'}</span><button type="button" class="secondary small" data-expense-bank="${b.id}">Open payment</button></div>`).join('')}<p class="hint">${money(e.linked_cash_minor,e.currency)} confirmed by bank. ${money(e.unmatched_cash_minor,e.currency)} recorded cash remains outside these reviewed bank links.</p>`:'<p class="hint">No linked bank payments yet. Open a bank transaction and choose this expense to link it.</p>'}<label class="field">Add document · scans automatically<input name="files" type="file" multiple accept=".pdf,image/png,image/jpeg" required></label>`,async()=>{});
 $('#dialog [data-close]').textContent='Close';$('#dialog button[type=submit]').textContent='Attach documents';
 $('#dialog').querySelectorAll('[data-expense-doc]').forEach(b=>b.onclick=()=>{const d=e.documents[Number(b.dataset.expenseDoc)];showDocumentPreview({...d,title:'Expense supporting document',detail:e.supplier})});
 $('#dialog').querySelectorAll('[data-expense-bank]').forEach(b=>b.onclick=()=>{$('#dialog').close();location.hash='journal/'+b.dataset.expenseBank});
  bindAttachmentPicker({expense:e});
}
function expenseDuplicateDialog(id){
 const source=state.journal.find(e=>e.id===id);if(!source)return;
 const possible=state.journal.filter(e=>e.id!==id&&!e.duplicate_of&&e.company_id===source.company_id&&e.currency===source.currency&&e.amount_minor===source.amount_minor&&sameExpenseSupplier(e,source));
 openDialog(`<h2>Keep one expense record</h2><p>Mark this document as a duplicate of the primary expense. Both originals stay saved, supporting files are shared, and bank links move to the primary record. The duplicate is excluded from cash totals.</p><label class="field">Primary expense<select name="target_id" required><option value="">Choose the expense to keep</option>${possible.map(e=>`<option value="${e.id}">${esc(e.document_number||'Expense #'+e.id)} · ${esc(e.date)} · ${esc(e.supplier)}</option>`).join('')}</select></label><label class="check"><input type="checkbox" required><span>These documents refer to the same expense.</span></label>`,async f=>{await api('/api/journal/duplicate',{id,target_id:Number(f.get('target_id'))});await refresh();renderJournal();notify('Documents combined under one primary expense. Originals retained.')});$('#dialog button[type=submit]').textContent='Keep one expense';
}
function bindExpenseReconciliation(file,scan,entry){
 const form=$('#journal-form');
 const panel=document.createElement('section');panel.className='expense-reconciliation';form.prepend(panel);
 function candidate(){const f=new FormData(form);return {...Object.fromEntries(f),company_id:Number(f.get('company_id')),amount_minor:docMinor(f.get('amount'),f.get('currency')),sha256:scan.sha256||''}}
 function update(){const c=candidate(),dups=expenseDuplicates(c,entry?.id),oldConfirm=form.querySelector('[name=duplicate_confirmed]')?.checked,oldSeparate=form.querySelector('[name=separate_payment_confirmed]')?.checked,oldLink=form.querySelector('[name=link_bank_id]')?.value||'';
  const banks=(state.bank_entries||[]).filter(b=>b.company_id===c.company_id&&b.currency===c.currency&&b.direction==='out'&&(!b.matched_record||b.matched_record==='expense:'+entry?.id)&&b.amount_minor<=c.amount_minor);
  const cash=docMinor(form.elements.paid_amount?.value||'',c.currency),collisions=banks.filter(b=>b.reviewed&&b.cash_flow&&b.date===form.elements.payment_date.value&&b.amount_minor===cash);
  panel.innerHTML=`${dups.length?`<div class="callout warning"><b>Possible existing expense</b>${dups.map(({entry:e,reason})=>`<div class="duplicate-candidate"><span>${esc(e.document_number||'Expense #'+e.id)} · ${esc(e.date)} · ${money(e.amount_minor,e.currency)}<small class="sub">${esc(reason)}</small></span>${!entry&&file?`<button type="button" class="secondary small" data-add-existing="${e.id}">Add file to this expense</button>`:''}</div>`).join('')}${!entry?`<label class="check"><input name="duplicate_confirmed" type="checkbox" ${oldConfirm?'checked':''}><span>This is a separate expense; create another record.</span></label>`:'<button type="button" class="secondary small" data-combine>Keep one expense</button>'}</div>`:''}${entry?.linked_banks?.length?`<div class="callout"><b>${entry.linked_banks.length} linked bank payment(s)</b><div>${money(entry.linked_cash_minor,entry.currency)} reviewed bank cash · ${money(entry.unmatched_cash_minor,entry.currency)} other recorded cash. Cash is counted once.</div><button type="button" class="secondary small" data-open-documents>Documents & payments</button></div>`:''}${banks.length?`<label class="field">Link an existing bank payment · optional<select name="link_bank_id"><option value="">No new bank link</option>${banks.map(b=>`<option value="${b.id}" ${String(b.id)===oldLink?'selected':''}>${esc(b.date)} · ${money(b.amount_minor,b.currency)} · ${esc(b.counterparty||b.description).slice(0,90)}</option>`).join('')}</select></label><p class="hint">Links the payment and its evidence to this expense. A reviewed bank payment replaces the corresponding manual cash amount in the overview.</p>`:''}${collisions.length?`<p class="callout warning">A reviewed bank transaction already records this cash payment. Link it above to prevent counting it twice.</p><label class="check"><input type="checkbox" name="separate_payment_confirmed" ${oldSeparate?'checked':''}><span>This is a separate cash payment from the matching bank transaction.</span></label>`:''}`;
  panel.querySelectorAll('[data-add-existing]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{await api('/api/journal/attach',{id:Number(b.dataset.addExisting),company_id:c.company_id,...file});await refresh();renderJournal();notify('File added to the existing expense. No additional expense or payment created.')}catch(error){notify(error.message,true);b.disabled=false}});
  if(panel.querySelector('[data-combine]'))panel.querySelector('[data-combine]').onclick=()=>expenseDuplicateDialog(entry.id);
  if(panel.querySelector('[data-open-documents]'))panel.querySelector('[data-open-documents]').onclick=()=>expenseDocuments(entry.id);
 }
 for(const input of form.querySelectorAll('input,select'))if(!panel.contains(input))input.addEventListener('change',update);
 update();
}

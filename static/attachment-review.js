'use strict';
// One file per review keeps the document, its fields and the selected link together.
function bindAttachmentPicker(target){
 const dialog=$('#dialog'),input=dialog.querySelector('input[type=file]');
 dialog.querySelector('button[type=submit]').hidden=true;dialog.querySelector('button[type=submit]').disabled=true;
 input.multiple=false;
 input.onchange=()=>{if(input.files[0])scanAttachedDocument(target,input.files[0])};
 dialog.querySelectorAll('[data-review-file]').forEach(b=>b.onclick=async()=>{
  const doc=(target.bank?sharedBankDocuments(target.bank):target.expense.documents)[Number(b.dataset.reviewFile)];
  try{const r=await fetch(doc.url||'/bank-support/'+doc.id);if(!r.ok)throw Error('Could not open the saved document.');await scanAttachedDocument(target,new File([await r.blob()],doc.name,{type:doc.media_type}))}catch(error){notify(error.message,true)}
 });
}
async function scanAttachedDocument(target,upload){
 if(!upload.size||upload.size>20*1024*1024){notify('Choose a PDF, PNG or JPEG up to 20 MB.',true);return}
 const dialog=$('#dialog');
 openDialog('<h2>Scanning document…</h2><p role="status">Reading the document locally. Review the extracted details before saving.</p>',async()=>{});
 dialog.querySelector('button[type=submit]').hidden=true;dialog.querySelector('button[type=submit]').disabled=true;
 const scanningForm=dialog.querySelector('form');let scan;
 try{
  const file={name:upload.name,content:await fileBase64(upload)};
  try{scan=await api('/api/scan',{content:file.content,preview:true})}catch(error){scan={error:error.message}}
  if(!dialog.open||dialog.querySelector('form')!==scanningForm)return;
  attachmentReview(target,file,scan,upload);
 }catch(error){if(dialog.open&&dialog.querySelector('form')===scanningForm){dialog.close();notify(error.message,true)}}
}
function attachmentMismatch(target,scan){
 const record=target.bank||target.expense;
 if(scan.suggested_company_id&&scan.suggested_company_id!==record.company_id){
  const detected=state.companies.find(c=>c.id===scan.suggested_company_id)?.name||'another company',selected=state.companies.find(c=>c.id===record.company_id)?.name||'this company';
  return `This document belongs to ${detected}; the selected record belongs to ${selected}. It cannot be attached to this record. Use Save as separate expense to keep this file under the correct company.`;
 }
 return '';
}
function attachmentReview(target,file,scan,upload){
 const bank=target.bank,expense=target.expense,company=bank?.company_id||expense.company_id,currency=scan.suggested_currency||bank?.currency||expense.currency;
 const mismatch=attachmentMismatch(target,scan);
 const preview=URL.createObjectURL(upload),isPdf=upload.type==='application/pdf'||file.name.toLowerCase().endsWith('.pdf');
 const previous=(bank?sharedBankDocuments(bank):expense.documents).find(d=>d.sha256===scan.sha256)?.review;
 const values=previous||{supplier:scan.suggested_supplier||'',supplier_tax_id:scan.suggested_supplier_tax_id||'',date:scan.suggested_date||'',amount:scan.suggested_amount||'',currency,document_number:scan.suggested_number||'',document_type:scan.suggested_document_type||'other',description:scan.suggested_description||''};
 openDialog(`<h2>Review document</h2><p>${esc(state.companies.find(c=>c.id===company)?.name)}${bank?' · '+esc(bank.date)+' · '+(bank.direction==='in'?'Money in ':'Money out ')+esc(money(bank.amount_minor,bank.currency)):' · Expense '+esc(expense.document_number||'#'+expense.id)}</p><div class="attachment-layout"><div class="attachment-preview">${isPdf?`<div class="attachment-preview-pages">${scan.previews?.length?scan.previews.map((src,i)=>`<small>Page ${i+1}</small><img alt="Attached document page ${i+1}" src="${src}">`).join(''):'<p>PDF preview unavailable. Check the original file and extracted text.</p>'}</div>`:`<img alt="Attached document preview" src="${preview}">`}<small>${esc(file.name)}</small>${scan.text?`<details><summary>Extracted text · all scanned pages</summary><pre class="attachment-text">${esc(scan.text)}</pre></details>`:''}</div><div class="attachment-fields"><section id="attachment-separate" hidden><p class="callout">Save this document as an independent expense. The original bank transaction and its links stay unchanged.</p><label class="field">Expense company<select name="expense_company_id">${options(state.companies,scan.suggested_company_id||company)}</select></label><p id="attachment-company-error" class="hint" hidden></p></section>${scan.error?`<p class="callout warning">Scan could not fill the fields: ${esc(scan.error)}. You can enter them below or save the file for later.</p>`:'<p class="hint">Check the extracted details against the original. Scanning reads the file; it does not confirm that it belongs to the selected record.</p>'}${mismatch?`<div id="attachment-mismatch" class="callout warning" role="alert"><b>This document does not match the selected company</b><p>${esc(mismatch)}</p></div>`:''}${!mismatch&&bank?.direction==='in'&&scan.suggested_party_role==='supplier'?'<p class="callout warning">This is a supplier document for a purchase, while the selected bank row is money in. Attach it only if it supports a refund or another related incoming movement.</p>':''}${!mismatch&&bank&&scan.suggested_date&&Date.parse(scan.suggested_date)-Date.parse(bank.date)>31*86400000?`<p class="callout warning">The document date ${esc(scan.suggested_date)} is later than the bank transaction ${esc(bank.date)}. Check that these records belong together.</p>`:''}<div class="grid">${field(bank?.direction==='in'?'Name on document':'Supplier / name on document','supplier',values.supplier,'text',false,'maxlength="200"')}${field('Tax ID · optional','supplier_tax_id',values.supplier_tax_id,'text',false,'maxlength="80"')}${field('Document date','date',values.date,'date')}${field('Document number','document_number',values.document_number,'text',false,'maxlength="80"')}${field('Document total','amount',values.amount,'number',false,'min="0" step="any"')}<label class="field">Document currency<select name="currency">${Object.keys(state.currencies).map(c=>`<option ${c===values.currency?'selected':''}>${c}</option>`).join('')}</select></label><label class="field">Document type<select name="document_type"><option value="invoice" ${values.document_type==='invoice'?'selected':''}>Invoice</option><option value="receipt" ${values.document_type==='receipt'?'selected':''}>Receipt</option><option value="other" ${values.document_type==='other'?'selected':''}>Other evidence</option></select></label>${bank?`<label class="field">Classification<select name="category">${Object.entries(bankCategories).map(([v,l])=>`<option value="${v}" ${v===bank.category?'selected':''}>${esc(l)}</option>`).join('')}</select></label>`:''}</div><details><summary>Document notes</summary>${area('Description','description',values.description,'maxlength="1000"')}</details><p class="hint" id="attachment-difference"></p>${bank?`<label class="field">Save document as<select name="action">${bank.matched_record?'':`<option value="evidence">Evidence for this bank transaction</option>${bank.direction==='out'?'<option value="create_expense">New supplier expense, linked to this payment</option>':''}`}<option value="link">Link to an existing expense or receipt</option></select></label><label class="field" id="attachment-link">Existing record<select name="matched_record"></select></label>`:'<p class="hint">This file will be added to the existing expense. Its financial details stay as recorded.</p>'}<div id="attachment-duplicates"></div>${bank?`<div id="attachment-cash"><label class="check"><input type="checkbox" name="cash_flow" ${bank.cash_flow?'checked':''}><span>Include this separate bank movement in overview cash flow.</span></label><div id="attachment-collision"></div></div>`:''}<p role="alert" id="attachment-error" hidden></p></div></div>`,async(f,form)=>{
  const separate=form.dataset.separate==='true';
  if(mismatch&&!separate)throw Error(mismatch);
  const document=Object.fromEntries(['supplier','supplier_tax_id','date','document_number','amount','currency','document_type','description'].map(k=>[k,f.get(k)]));
  const payload={...(bank?{bank_id:bank.id,revision:bank.revision}:{expense_id:expense.id}),file,document,action:separate?'separate_expense':bank?f.get('action'):'evidence',company_id:separate?Number(f.get('expense_company_id')):undefined,existing_expense_id:separate?Number(f.get('existing_expense_id'))||undefined:undefined,matched_record:bank?form.elements.matched_record.value:'',category:f.get('category'),counterparty:bank?(bank.counterparty||document.supplier):'',reviewed:bank?form.dataset.saveLater!=='true':false,cash_flow:form.elements.cash_flow?.checked===true,separate_payment_confirmed:form.elements.separate_payment_confirmed?.checked===true,duplicate_confirmed:form.elements.duplicate_confirmed?.checked===true};
  const signature=JSON.stringify(payload);
  if(form.dataset.signature!==signature){form.dataset.signature=signature;form.dataset.requestKey=crypto.randomUUID()}
  payload.request_key=form.dataset.requestKey;
  let result;try{result=await api('/api/document/review',payload)}catch(error){const el=$('#attachment-error');el.hidden=false;el.textContent=error.message;throw error}
  await refresh();
  if(result.separate){
   if(!$('#main form')){applyCompanyContext(payload.company_id);journalYear='all';journalSearch='';journalPage=0;journalSource='document';expensePaymentFilter='all';if(location.hash!=='#expenses')location.hash='expenses';else renderJournal()}
   notify(result.reused?'Document added to the existing expense. Original bank transaction unchanged.':'Separate expense saved. Original bank transaction unchanged.');
  }else{if(bank&&$('#bank-form'))bankEdit(bank.id);else if(!$('#main form'))renderJournal();notify(bank?(payload.reviewed?'Document saved and transaction reviewed.':'Document saved for later review.'):'Document added to the existing expense.')}

 });
 const dialog=$('#dialog'),form=dialog.querySelector('form'),sourceSubtitle=form.querySelector('h2+p').textContent;dialog.classList.add('attachment-review');
 dialog.addEventListener('close',()=>{URL.revokeObjectURL(preview);dialog.classList.remove('attachment-review')},{once:true});
 const submit=form.querySelector('button[type=submit]');submit.textContent=bank?'Save & mark reviewed':'Save document';submit.disabled=!!mismatch;
 const separateButton=document.createElement('button');separateButton.type='button';separateButton.className='secondary';separateButton.textContent='Save as separate expense';submit.before(separateButton);
 separateButton.onclick=()=>{form.dataset.separate=form.dataset.separate==='true'?'false':'true';update()};
 let later;
 if(bank){later=document.createElement('button');later.type='button';later.className='secondary';later.textContent='Save for later';later.disabled=!!mismatch;later.onclick=()=>{form.dataset.saveLater='true';form.requestSubmit(submit)};submit.before(later);submit.addEventListener('click',()=>form.dataset.saveLater='false')}

 function update(){
  const separate=form.dataset.separate==='true';
  for(const input of form.querySelectorAll('.attachment-fields input,.attachment-fields select,.attachment-fields textarea'))input.disabled=false;
  const document=Object.fromEntries(new FormData(form)),selectedCompany=separate?Number(form.elements.expense_company_id.value):company,candidate={...document,company_id:selectedCompany,amount_minor:docMinor(document.amount,document.currency),sha256:scan.sha256||''};
  const duplicates=bank?.direction==='in'&&!separate?[]:expenseDuplicates(candidate,separate?null:expense?.id),action=form.elements.action?.value;
  form.querySelector('h2').textContent=separate?'Save separate expense':'Review document';form.querySelector('h2+p').textContent=separate?(state.companies.find(c=>c.id===selectedCompany)?.name||'')+' · Separate expense, no bank link':sourceSubtitle;
  $('#attachment-separate').hidden=!separate;
  if($('#attachment-mismatch'))$('#attachment-mismatch').hidden=separate;
  const wrongCompany=separate&&scan.suggested_company_id&&scan.suggested_company_id!==selectedCompany;
  $('#attachment-company-error').hidden=!wrongCompany;$('#attachment-company-error').textContent='Choose the company detected in the document: '+(state.companies.find(c=>c.id===scan.suggested_company_id)?.name||'');
  submit.disabled=separate?!!wrongCompany:!!mismatch;
  submit.textContent=separate?'Save separate expense':bank?'Save & mark reviewed':'Save document';
  separateButton.textContent=separate?'Back to original record':'Save as separate expense';
  if(later){later.hidden=separate;later.disabled=!!mismatch}

  const amount=docMinor(document.amount,document.currency),sameCurrency=bank&&document.currency===bank.currency;
  $('#attachment-difference').textContent=bank&&amount>0?(sameCurrency?(amount===bank.amount_minor?'Amounts are equal. Check the company, date and purpose before saving.':'Document total '+money(amount,document.currency)+' · bank payment '+money(bank.amount_minor,bank.currency)+'. Check partial payment or other differences.'):'Document currency differs from the bank currency. Review before linking.') : '';
  const dupBox=$('#attachment-duplicates'),confirmed=form.elements.duplicate_confirmed?.checked,previousExisting=form.elements.existing_expense_id?.value;
  if(separate){
   const exact=duplicates.filter(d=>d.exact),chosen=previousExisting!==undefined?previousExisting:exact.length===1?String(exact[0].entry.id):'';
   dupBox.innerHTML=duplicates.length?`<div class="callout warning"><b>Possible existing expense</b><label class="field">Existing expense · avoid duplicates<select name="existing_expense_id"><option value="" ${exact.length?'disabled':''}>${exact.length?'Choose the saved expense':'Create a new, separate expense'}</option>${duplicates.map(d=>`<option value="${d.entry.id}" ${String(d.entry.id)===chosen?'selected':''}>${esc(d.entry.document_number||'Expense #'+d.entry.id)} · ${esc(d.entry.supplier)} · ${money(d.entry.amount_minor,d.entry.currency)}</option>`).join('')}</select></label>${!exact.length&&!chosen?`<label class="check"><input name="duplicate_confirmed" type="checkbox" ${confirmed?'checked':''}><span>I checked: this is a different expense.</span></label>`:''}<p class="hint">Choosing a saved expense adds this file without creating another expense or payment.</p></div>`:'';
   if(form.elements.existing_expense_id){form.elements.existing_expense_id.required=!!exact.length;form.elements.existing_expense_id.onchange=update;if(form.elements.existing_expense_id.value)submit.textContent='Save to existing expense'}
   for(const name of ['supplier','date','amount'])form.elements[name].required=!form.elements.existing_expense_id?.value;
   if(bank){for(const name of ['category','action','matched_record'])form.elements[name].closest('.field').hidden=true;$('#attachment-cash').hidden=true}
   $('#attachment-difference').hidden=true;
   return;
  }
  $('#attachment-difference').hidden=false;
  if(bank){form.elements.category.closest('.field').hidden=false;form.elements.action.closest('.field').hidden=false}
  for(const name of ['supplier','date','amount'])form.elements[name].required=action==='create_expense';
  dupBox.innerHTML=duplicates.length?`<div class="callout warning"><b>Possible existing expense</b>${duplicates.map(d=>`<div>${esc(d.entry.document_number||'Expense #'+d.entry.id)} · ${esc(d.entry.supplier)}<small class="sub">${esc(d.reason)}</small></div>`).join('')}${bank?'<p>Link a compatible existing record to reuse it. Check its payment links if it is unavailable.</p>':''}${action==='create_expense'&&!duplicates.some(d=>d.exact)?`<label class="check"><input name="duplicate_confirmed" type="checkbox" ${confirmed?'checked':''}><span>I checked: this is a separate expense.</span></label>`:''}</div>`:'';
  if(!bank){if(mismatch)for(const input of form.querySelectorAll('.attachment-fields input,.attachment-fields select,.attachment-fields textarea'))input.disabled=true;return}
  const select=form.elements.matched_record,old=select.value;
  const choices=bankMatchOptions(bank).map(c=>({...c,suggested:duplicates.some(d=>'expense:'+d.entry.id===c.value)||c.label.startsWith('Suggested · ')||!!document.document_number&&c.label.includes(document.document_number)})).sort((a,b)=>Number(b.suggested)-Number(a.suggested));
  const linkOption=form.elements.action.querySelector('option[value="link"]');
  linkOption.hidden=!choices.length;linkOption.disabled=!choices.length;
  if(!choices.length&&form.elements.action.value==='link')form.elements.action.value='evidence';
  const currentAction=form.elements.action.value;
  select.innerHTML='<option value="">Choose an existing record</option>'+choices.map(c=>`<option value="${c.value}" ${c.value===(old||bank.matched_record)?'selected':''}>${c.suggested&&!c.label.startsWith('Suggested · ')?'Suggested · ':''}${esc(c.label)}</option>`).join('');
  $('#attachment-link').hidden=currentAction!=='link';select.required=currentAction==='link';select.disabled=currentAction!=='link';
  $('#attachment-cash').hidden=currentAction!=='evidence';
  for(const k of ['supplier','date','amount'])form.elements[k].required=action==='create_expense';
  if(action==='create_expense'&&form.elements.category.value==='unclassified')form.elements.category.value='expense';
  if(action==='link'&&select.value.startsWith('expense:')&&form.elements.category.value==='unclassified')form.elements.category.value='expense';
  const collision=bankCashCollision(bank);
  if(collision&&!$('#attachment-collision input'))$('#attachment-collision').innerHTML='<label class="check"><input type="checkbox" name="separate_payment_confirmed"><span>This is a different payment from an existing receipt or paid expense.</span></label>';
  $('#attachment-collision').hidden=!collision;
  if(mismatch){
   for(const name of ['category','action','matched_record'])form.elements[name].closest('.field').hidden=true;
   $('#attachment-cash').hidden=true;$('#attachment-difference').hidden=true;
   for(const input of form.querySelectorAll('.attachment-fields input,.attachment-fields select,.attachment-fields textarea'))input.disabled=true;
  }
 }
 for(const input of form.querySelectorAll('input,select'))input.addEventListener('change',update);
 update();
 // Suggest the matching record without confirming it for the user.
 if(bank&&!bank.matched_record){const first=form.elements.matched_record.querySelector('option[value]:not([value=""])');if(first?.textContent.startsWith('Suggested · ')){form.elements.action.value='link';form.elements.matched_record.value=first.value;update()}}
}

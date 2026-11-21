'use strict';
function mountAccountingTabs(kind,id){
  const main=$('#main'),header=main.querySelector('.head');if(!header)return;
  const general=document.createElement('div'),panel=document.createElement('div'),tabs=document.createElement('div');
  general.id='transaction-general';general.setAttribute('role','tabpanel');general.setAttribute('aria-labelledby','transaction-general-tab');
  panel.id='transaction-accountant';panel.hidden=true;panel.setAttribute('role','tabpanel');panel.setAttribute('aria-labelledby','transaction-accountant-tab');
  tabs.className='transaction-tabs';tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','Transaction details');
  tabs.innerHTML='<button type="button" role="tab" id="transaction-general-tab" aria-controls="transaction-general" aria-selected="true">General</button><button type="button" role="tab" id="transaction-accountant-tab" aria-controls="transaction-accountant" aria-selected="false" tabindex="-1">Accountant</button>';
  while(header.nextSibling)general.append(header.nextSibling);
  main.append(tabs,general,panel);
  let loaded=false,onAccountant=false,request=0;
  async function select(accountant,entryWork=null){
    if(accountant===onAccountant&&!entryWork)return;
    if(editorDirty){
      if(!onAccountant){notify('Save your transaction changes before opening Accountant.',true);return}
      if(!discardEditorChanges())return;
      loaded=false;panel.innerHTML='';
    }
    onAccountant=accountant;general.hidden=accountant;panel.hidden=!accountant;
    [...tabs.children].forEach((b,i)=>{const selected=Boolean(i)===accountant;b.setAttribute('aria-selected',String(selected));b.tabIndex=selected?0:-1});
    if(accountant&&entryWork){request++;loaded=true;renderAccountingEntry(panel,entryWork);return}
    if(accountant&&!loaded){
      const current=++request;panel.innerHTML='<p role="status">Loading accounting entry…</p>';
      try{
        const company=Number(activeCompany),work=await api(`/api/accounting?company=${company}&kind=${kind}&id=${id}`);
        if(panel.isConnected&&current===request){loaded=true;renderAccountingEntry(panel,work)}
      }catch(error){if(current!==request)return;loaded=false;panel.innerHTML=`<p class="callout warning">${esc(error.message)}</p><button type="button" class="secondary">Retry</button>`;panel.querySelector('button').onclick=()=>{onAccountant=false;select(true)}}
    }
  }
  panel.openAccounting=work=>select(true,work);
  [...tabs.children].forEach((b,i)=>b.onclick=()=>select(Boolean(i)));
  tabs.onkeydown=e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const next=e.key==='Home'?0:e.key==='End'?1:onAccountant?0:1;tabs.children[next].click();tabs.children[next].focus()};
}

function accountingNumber(n,currency){return (n/10**state.currencies[currency]).toFixed(state.currencies[currency])}
function accountingLine(r,accounts,currency,editable){
  return `<tr><td><select aria-label="Account" data-account ${editable?'':'disabled'}>${accounts.filter(a=>a.active||a.code===r.code).map(a=>`<option value="${esc(a.code)}" ${a.code===r.code?'selected':''}>${esc(a.code)} · ${esc(a.name)}${a.active?'':' (inactive)'}</option>`).join('')}</select></td>${['debit','credit'].map(side=>`<td><input aria-label="${side==='debit'?'Debit':'Credit'}" data-${side} type="number" min="0" step="${10**-state.currencies[currency]}" value="${accountingNumber(r[side+'_minor'],currency)}" ${editable?'':'disabled'} required></td>`).join('')}<td><input aria-label="Cost center" data-cost maxlength="100" value="${esc(r.cost_center)}" ${editable?'':'disabled'}></td><td class="accounting-anchor">${editable?'MANUAL':'AUTO'}</td><td><button type="button" class="quiet small" data-remove aria-label="Remove accounting line" ${editable?'':'disabled'}>×</button></td></tr>`;
}

function renderAccountingEntry(panel,work){
  const {source:src,entry,accounts}=work,currency=entry.currency,manual=entry.mode==='manual',currencyChanged=currency!==src.currency;
  panel.innerHTML=`<section class="card accounting-card"><div class="accounting-heading"><div><div class="eyebrow">Accountant</div><h2>GL entry</h2><p class="hint">${esc(src.description)} · ${esc(currency)} · ${work.saved?'Saved working entry':'Suggested entry · not saved'}</p></div><a class="button secondary small" href="#accounts">Chart of accounts</a></div>${src.blocked?`<p class="callout warning">${esc(src.blocked)}</p>`:''}${work.stale?'<p class="callout warning" id="accounting-stale">The source transaction changed after this entry was saved. These are the previous figures. Reset to automatic, or switch to Manual and review every line before saving.</p>':''}${src.warnings.map(s=>`<p class="callout">${esc(s)}</p>`).join('')}${src.related?`<p><button type="button" class="secondary small" id="accounting-related">Open linked ${esc(src.related.kind)} entry</button></p>`:''}<form id="accounting-form"><div class="grid accounting-fields">${field('Accounting date','date',entry.date,'date',true)}<label class="field">Book ID<select name="book">${Object.entries(work.books).map(([value,label])=>`<option value="${value}" ${value===entry.book?'selected':''}>${esc(label)}</option>`).join('')}</select></label><label class="field">Calculation<select name="mode"><option value="auto" ${manual?'':'selected'}>AUTO · from saved transaction</option><option value="manual" ${manual?'selected':''}>Manual</option></select></label><div class="span2">${field('Description','description',entry.description,'text',true,'maxlength="1000"')}</div><div class="span2">${area('Note','note',entry.note,'maxlength="2000"')}</div></div><p class="hint" id="accounting-mode-note">AUTO recalculates debit and credit lines from the saved source when you save. Manual lets you change accounts, amounts and cost centers.</p><div class="table-wrap"><table class="accounting-table"><thead><tr><th>Account</th><th class="right">Debit · ${esc(currency)}</th><th class="right">Credit · ${esc(currency)}</th><th>Cost center</th><th>Anchor</th><th></th></tr></thead><tbody>${entry.lines.map(r=>accountingLine(r,accounts,currency,manual)).join('')}</tbody><tfoot><tr><th>Total</th><td class="right" id="accounting-debit-total"></td><td class="right" id="accounting-credit-total"></td><td colspan="3"><span id="accounting-balance" role="status" aria-live="polite"></span></td></tr></tfoot></table></div><div class="accounting-footer"><button type="button" class="secondary small" id="accounting-add" ${manual?'':'hidden'}>＋ Add line</button><div class="actions"><button type="button" class="secondary" id="accounting-reset">Reset to automatic</button><button type="submit" id="accounting-save">Save entry</button></div></div><p id="accounting-error" class="callout warning" role="alert" hidden></p><p class="meta-note">Working entry for accountant review. Saving does not change the source document, its PDF, payment status, cash-flow totals or tax worksheets.</p></form></section>`;
  const form=panel.querySelector('form'),body=form.querySelector('tbody'),save=form.querySelector('[type=submit]');let reviewed=!work.stale,busy=false;
  function rows(){return [...body.rows].map(row=>({code:row.querySelector('[data-account]').value,debit_minor:docMinor(row.querySelector('[data-debit]').value,currency),credit_minor:docMinor(row.querySelector('[data-credit]').value,currency),cost_center:row.querySelector('[data-cost]').value}))}
  function update(){
    const lines=rows(),valid=lines.length>=2&&lines.every(r=>r.debit_minor!==null&&r.credit_minor!==null&&(r.debit_minor>0)!==(r.credit_minor>0)&&accounts.some(a=>a.code===r.code&&a.active));
    const debit=lines.reduce((sum,r)=>sum+(r.debit_minor||0),0),credit=lines.reduce((sum,r)=>sum+(r.credit_minor||0),0),balanced=valid&&debit===credit;
    form.querySelector('#accounting-debit-total').textContent=money(debit,currency);form.querySelector('#accounting-credit-total').textContent=money(credit,currency);
    const status=form.querySelector('#accounting-balance');status.textContent=balanced?'Balanced':debit!==credit?'Difference '+money(Math.abs(debit-credit),currency):'Check accounts and amounts';status.className=balanced?'badge paid':'badge unpaid';
    save.disabled=busy||!balanced||!!src.blocked||!reviewed||currencyChanged;
  }
  function bindRows(){body.querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>{b.closest('tr').remove();editorDirty=true;update()})}
  function setLines(lines){body.innerHTML=lines.map(r=>accountingLine(r,accounts,currency,form.elements.mode.value==='manual')).join('');bindRows();update()}
  form.elements.mode.onchange=()=>{
    const manual=form.elements.mode.value==='manual';
    if(manual){reviewed=true;body.querySelectorAll('input,select,button').forEach(el=>el.disabled=false);body.querySelectorAll('.accounting-anchor').forEach(el=>el.textContent='MANUAL')}
    else{reviewed=true;setLines(work.automatic.lines)}
    form.querySelector('#accounting-add').hidden=!manual;editorDirty=true;update();
  };
  form.querySelector('#accounting-add').onclick=()=>{if(body.rows.length>=100){notify('An entry can have up to 100 lines.',true);return}const account=accounts.find(a=>a.active);body.insertAdjacentHTML('beforeend',accountingLine({code:account?.code||'',debit_minor:0,credit_minor:0,cost_center:''},accounts,currency,true));bindRows();editorDirty=true;update()};
  form.querySelector('#accounting-reset').onclick=()=>{if(editorDirty&&!confirm('Replace unsaved accounting changes with the automatic suggestion?'))return;renderAccountingEntry(panel,{...work,entry:structuredClone(work.automatic),stale:false});editorDirty=true};
  form.addEventListener('input',update);bindRows();update();
  form.onsubmit=async e=>{
    e.preventDefault();if(save.disabled||busy)return;busy=true;form.dataset.saving='true';update();form.querySelector('#accounting-error').hidden=true;
    try{
      const saved=await api('/api/accounting/entry',{...Object.fromEntries(new FormData(form)),company_id:src.company_id,source_kind:src.kind,source_id:src.id,revision:work.revision,currency,source_fingerprint:src.fingerprint,lines:rows()});
      editorDirty=false;renderAccountingEntry(panel,saved);notify('Accounting entry saved.');
    }catch(error){const note=form.querySelector('#accounting-error');note.textContent=error.message;note.hidden=false}
    finally{busy=false;form.dataset.saving='false';update()}
  };
  if(src.blocked)form.querySelectorAll('input,select,textarea,button').forEach(el=>el.disabled=true);
  if(src.related)panel.querySelector('#accounting-related').onclick=async()=>{
    if(!discardEditorChanges())return;
    try{const related=await api(`/api/accounting?company=${src.company_id}&kind=${src.related.kind}&id=${src.related.id}`);renderAccountingEntry(panel,related)}catch(error){notify(error.message,true)}
  };
}

async function renderAccounts(){
  const company=Number(activeCompany);
  $('#main').innerHTML=head('Chart of accounts','Company account codes for transaction entries.',company?'<button id="account-add">＋ Add account</button>':'');
  if(!company){$('#main').insertAdjacentHTML('beforeend','<p>Add a company to set up account codes.</p>');return}
  try{
    const result=await api('/api/accounting/accounts?company='+company);
    if(view!=='accounts'||Number(activeCompany)!==company)return;
    $('#main').insertAdjacentHTML('beforeend',`<p class="callout">Starter codes include the accounts in your reference, plus common asset, liability, equity, income and expense accounts. Confirm them against ${esc(activeCompanyRecord().name)}’s chart. <a href="#compliance">VAT, withholding and corporate-tax worksheets →</a></p><div class="actions accounting-tools"><button class="secondary" id="account-mappings">Automatic account mappings</button><button class="secondary" id="account-balances">Calculate account balances</button><a class="button secondary" href="#thai-reporting">Thai reporting mappings</a></div><section id="account-balances-panel" hidden></section><section class="card table-wrap"><div class="toolbar"><input id="account-search" aria-label="Search account codes" placeholder="Search code or account name…"><select id="account-type" aria-label="Filter account type"><option value="">All account types</option>${['asset','liability','equity','income','expense','suspense'].map(t=>`<option>${t}</option>`).join('')}</select></div><table id="accounts-table"><thead><tr><th>Code</th><th>Account</th><th>Type</th><th>Status</th><th></th></tr></thead><tbody>${result.accounts.map((a,i)=>`<tr data-account-type="${a.type}"><td class="number">${esc(a.code)}</td><td>${esc(a.name)}</td><td>${esc(a.type)}</td><td>${a.active?'Active':'Inactive'}</td><td class="right"><button class="secondary small" data-edit-account="${i}">Edit</button></td></tr>`).join('')}</tbody></table></section>`);
    $('#account-add').onclick=()=>editAccount(company,result.accounts);
    document.querySelectorAll('[data-edit-account]').forEach(b=>b.onclick=()=>editAccount(company,result.accounts,result.accounts[Number(b.dataset.editAccount)]));
    function filter(){const q=$('#account-search').value.trim().toLowerCase(),type=$('#account-type').value;$('#accounts-table').querySelectorAll('tbody tr').forEach(row=>row.hidden=!row.textContent.toLowerCase().includes(q)||(type&&row.dataset.accountType!==type))}
    $('#account-search').oninput=filter;$('#account-type').onchange=filter;
    $('#account-mappings').onclick=()=>editAccountMappings(company,result);
    $('#account-balances').onclick=()=>renderAccountBalances(company);
  }catch(error){notify(error.message,true)}
}

function editAccountMappings(company,work){
  const m=work.mappings;
  openDialog(`<h2>Automatic account mappings</h2><p>Choose the accounts used by AUTO for this company. Existing entries retain their figures and are flagged for review when an applicable mapping changes.</p><div class="grid">${Object.entries(m.roles).map(([role,code])=>`<label class="field">${esc(m.labels[role])}<select name="${role}">${work.accounts.filter(a=>a.active||a.code===code).map(a=>`<option value="${esc(a.code)}" ${a.code===code?'selected':''} ${a.active?'':'disabled'}>${esc(a.code)} · ${esc(a.name)}${a.active?'':' (inactive)'}</option>`).join('')}</select></label>`).join('')}</div>`,async f=>{
    await api('/api/accounting/mappings',{company_id:company,revision:m.revision,roles:Object.fromEntries(f)});await renderAccounts();notify('Automatic account mappings saved.');
  });
}

async function renderAccountBalances(company){
  const panel=$('#account-balances-panel');if(!panel)return;
  panel.hidden=false;const year=today().slice(0,4);
  panel.innerHTML=`<section class="card"><h2>Saved-entry balances</h2><p class="hint">Debits, credits and net movement from saved accounting entries. Source records that changed or became inactive are excluded and listed for review. Opening balances and unsaved transactions are not included.</p><div class="toolbar accounting-balance-filters"><label>From<input id="account-balance-start" aria-label="Balance period start" type="date" value="${year}-01-01"></label><label>To<input id="account-balance-end" aria-label="Balance period end" type="date" value="${year}-12-31"></label><label>Currency<select id="account-balance-currency">${Object.keys(state.currencies).map(c=>`<option ${c==='THB'?'selected':''}>${c}</option>`).join('')}</select></label><button type="button" id="account-balance-calculate">Calculate</button></div><div id="account-balance-results"></div></section>`;
  let request=0;
  async function calculate(){
    const current=++request,result=panel.querySelector('#account-balance-results');result.innerHTML='<p role="status">Calculating…</p>';
    const params=new URLSearchParams({company,start:panel.querySelector('#account-balance-start').value,end:panel.querySelector('#account-balance-end').value,currency:panel.querySelector('#account-balance-currency').value});
    try{
      const b=await api('/api/accounting/balances?'+params);
      if(!panel.isConnected||current!==request)return;
      result.innerHTML=`<p>${b.included} saved entries included · ${b.excluded.length} need review · <b>${b.balanced?'Debits and credits balance':'Check the difference'}</b></p><div class="table-wrap"><table><thead><tr><th>Code / account</th><th class="right">Debit movement</th><th class="right">Credit movement</th><th class="right">Net debit</th><th class="right">Net credit</th></tr></thead><tbody>${b.accounts.map(a=>`<tr><td><b>${esc(a.code)}</b> · ${esc(a.name)}</td><td class="right">${money(a.debit_minor,b.currency)}</td><td class="right">${money(a.credit_minor,b.currency)}</td><td class="right">${money(a.net_debit_minor,b.currency)}</td><td class="right">${money(a.net_credit_minor,b.currency)}</td></tr>`).join('')||'<tr><td colspan="5">Save transaction accounting entries to calculate their balances here.</td></tr>'}</tbody><tfoot><tr><th>Total</th>${['debit_minor','credit_minor','net_debit_minor','net_credit_minor'].map(k=>`<th class="right">${money(b.totals[k],b.currency)}</th>`).join('')}</tr></tfoot></table></div>${b.excluded.length?`<details><summary>Entries that need review</summary><ul>${b.excluded.map(e=>`<li><a href="${esc(e.href)}">${esc(e.source_kind)} #${e.source_id}</a> · ${esc(e.reason)}</li>`).join('')}</ul></details>`:''}<p class="meta-note">${esc(b.currency)} only. Net amounts are the difference between debit and credit movements in this period. This is not a complete trial balance or a tax return.</p><a class="button secondary small" href="/api/accounting/balances/export?${esc(params.toString())}">Export balances CSV</a>`;
    }catch(error){if(panel.isConnected&&current===request)result.innerHTML=`<p class="callout warning">${esc(error.message)}</p>`}
  }
  panel.querySelector('#account-balance-calculate').onclick=calculate;await calculate();
}
function editAccount(company,accounts,account=null){
  const a=account||{code:'',name:'',type:'expense',active:true,revision:0};
  openDialog(`<h2>${account?'Edit':'Add'} account</h2><div class="grid">${field('Account code','code',a.code,'text',true,`maxlength="20" pattern="[A-Za-z0-9][A-Za-z0-9.\\-]*" ${account?'readonly':''}`)}${field('Account name','name',a.name,'text',true,'maxlength="200"')}<label class="field">Type<select name="type">${['asset','liability','equity','income','expense','suspense'].map(t=>`<option ${t===a.type?'selected':''}>${t}</option>`).join('')}</select></label><label class="check"><input name="active" type="checkbox" ${a.active?'checked':''}>Active for new entries</label></div><p class="hint">Codes stay attached to saved entries. Deactivate an account when it should no longer be selected.</p>`,async f=>{
    if(!account&&accounts.some(a=>a.code===f.get('code').trim()))throw new Error('That code already exists. Edit the existing account.');
    await api('/api/accounting/account',{...Object.fromEntries(f),company_id:company,active:f.get('active')==='on',revision:a.revision});await renderAccounts();notify('Account saved.');
  });
}

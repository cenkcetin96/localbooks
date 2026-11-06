'use strict';
let activeCompany=null,allState=null,lastCompanyRoute='';
function scopeCompanyState(raw,companyId){
 const id=Number(companyId),owned=row=>Number(row.company_id)===id,used=row=>(row.company_ids||[raw.companies[0]?.id]).includes(id);
 return {...raw,company_id:id,all_journal:raw.journal,invoices:(raw.invoices||[]).filter(owned),journal:(raw.journal||[]).filter(owned),bank_entries:(raw.bank_entries||[]).filter(owned),contacts:(raw.contacts||[]).filter(used),catalog:(raw.catalog||[]).filter(used)};
}
function activeCompanyRecord(){return state?.companies.find(c=>c.id===Number(activeCompany))}
function activeCompanies(){return state?.companies.filter(c=>c.id===Number(activeCompany))||[]}
function applyCompanyContext(id){
 activeCompany=id?String(id):null;
 try{if(activeCompany)localStorage.setItem('localbooks.company',activeCompany);else localStorage.removeItem('localbooks.company')}catch{}
 state=scopeCompanyState(allState,activeCompany);
 filters.company=activeCompany;journalCompany=activeCompany;overviewFilters.company=activeCompany;complianceCompany=Number(activeCompany);complianceWork=null;complianceRequest++;
 renderCompanySwitcher();
}
function companySwitchBlocked(){return !!document.querySelector('#main form,dialog[open]')||!!bankImportDraft}
function resetCompanyFilters(){
 overviewDrill=null;filters.q='';filters.status='all';journalYear='all';journalSearch='';journalReviewFilter='all';journalPage=0;journalSource='document';expensePaymentFilter='all';complianceMonth='all';
}
function renderCompanySwitcher(){
 const holder=document.querySelector('#company-context');if(!holder)return;
 holder.innerHTML=`<label for="active-company">Company</label><select id="active-company" aria-label="Active company" ${allState.companies.length?'':'disabled'}>${allState.companies.length?options(allState.companies,activeCompany):'<option>Add a company first</option>'}</select>`;
 holder.querySelector('select').onchange=e=>{
  const id=e.target.value;if(id===activeCompany)return;
  if(companySwitchBlocked()){e.target.value=activeCompany;notify(bankImportDraft?'Finish or discard the statement import before switching company.':'Save or close the open editor before switching company.',true);return}
  applyCompanyContext(id);resetCompanyFilters();selected=null;notify('Switched to '+activeCompanyRecord().name);
  const page=['document','new','edit','payment'].includes(view)?'invoices':view;
  if(location.hash!=='#'+page)location.hash=page;else render();
 };
}
function companyRouteOwner(page,id){
 const records=['document','edit','payment'].includes(page)?allState?.invoices:page==='expenses'?allState?.journal:page==='journal'?allState?.bank_entries:null;
 return records?.find(r=>r.id===Number(id))?.company_id;
}
function companyUsageFields(record={}){
 const ids=record.company_ids||[Number(activeCompany)];
 return `<details class="company-usage"><summary>Used by companies</summary>${state.companies.map(c=>`<label class="check"><input type="checkbox" name="used_by" value="${c.id}" ${ids.includes(c.id)?'checked':''}>${esc(c.name)}</label>`).join('')}<p class="hint">Choose the company lists where these details are available. Existing documents keep their company associations.</p></details>`;
}

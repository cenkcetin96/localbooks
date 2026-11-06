'use strict';

function overviewData(records,{company='all',currency='THB',months=6,year=null,end=today()}={}){
  const selectedYear=year!==null&&/^\d{4}$/.test(String(year))?Number(year):null;
  const currentYear=Number(end.slice(0,4)),month=Number(end.slice(5,7));
  const count=selectedYear===null?months:selectedYear===currentYear?month:12;
  const series=Array.from({length:count},(_,i)=>{const d=selectedYear===null?new Date(currentYear,month-months+i,1):new Date(selectedYear,i,1);return {month:`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`,in_minor:0,out_minor:0}});
  const start=series[0].month+'-01',finish=selectedYear!==null&&selectedYear<currentYear?selectedYear+'-12-31':end;
  const byMonth=new Map(series.map(row=>[row.month,row]));
  const within=date=>typeof date==='string'&&date>=start&&date<=finish;
  const selected=row=>(company==='all'||String(row.company_id)===String(company))&&row.currency===currency;
  const companies=new Map(records.companies.filter(c=>company==='all'||String(c.id)===String(company)).map(c=>[c.id,{id:c.id,name:c.name,sales_minor:0,in_minor:0,invoices:0}]));
  const services=new Map(),customers=new Map(),attention={bank:[],documents:[],expenses:[],overdue:[]},openInvoices=[];
  let outstanding=0,overdue=0,needsPaymentDetails=0,bankCount=0,reviewedBankCount=0;
  for(const invoice of records.invoices){
    if(!selected(invoice)||invoice.status!=='issued')continue;
    const issuer=companies.get(invoice.company_id);
    if(invoice.kind==='sale'&&invoice.data.issue_date<=end&&Number.isSafeInteger(invoice.balance_minor)&&invoice.balance_minor>0){
      outstanding+=invoice.balance_minor;openInvoices.push(invoice.id);
      if(invoice.data.due_date&&invoice.data.due_date<end){overdue+=invoice.balance_minor;attention.overdue.push(invoice.id)}
    }
    for(const payment of invoice.payments){
      if(payment.reversed||!within(payment.data.date))continue;
      const bucket=byMonth.get(payment.data.date.slice(0,7));
      if(invoice.kind==='sale'){bucket.in_minor+=payment.amount_minor;if(issuer)issuer.in_minor+=payment.amount_minor}
      else bucket.out_minor+=payment.amount_minor;
    }
    if(invoice.kind!=='sale'||!within(invoice.data.issue_date))continue;
    if(issuer)issuer.invoices++;
    const customerId=invoice.contact_id??invoice.data.contact?.id;
    const name=invoice.data.contact?.name||'Customer not specified',customerKey=JSON.stringify([invoice.company_id,customerId??name.trim().toLocaleLowerCase()]);
    if(!customers.has(customerKey))customers.set(customerKey,{id:customerId,name,sales_minor:0,invoices:new Set(),latest:invoice.data.issue_date});
    const customer=customers.get(customerKey);customer.invoices.add(invoice.id);
    if(invoice.data.issue_date>customer.latest){customer.name=name;customer.latest=invoice.data.issue_date}
    for(const line of invoice.data.lines){
      if(issuer)issuer.sales_minor+=line.total_minor;
      customer.sales_minor+=line.total_minor;
      const key=JSON.stringify([invoice.company_id,line.code||line.description.trim().toLocaleLowerCase()]);
      if(!services.has(key))services.set(key,{name:line.description.split(/\r?\n/)[0],code:line.code||'',company:issuer?.name||invoice.data.company.name,sales_minor:0,invoices:new Set()});
      const service=services.get(key);service.sales_minor+=line.total_minor;service.invoices.add(invoice.id);
    }
  }
  for(const expense of records.journal||[]){
    if(!selected(expense)||expense.duplicate_of||expense.payment_status!=='paid')continue;
    if(!expense.payment_date||!Number.isSafeInteger(expense.paid_amount_minor)){
      if(within(expense.date)){needsPaymentDetails++;attention.expenses.push(expense.id)}
      continue;
    }
    const residual=Number.isSafeInteger(expense.unmatched_cash_minor)?expense.unmatched_cash_minor:expense.paid_amount_minor;
    if(within(expense.payment_date))byMonth.get(expense.payment_date.slice(0,7)).out_minor+=residual;
  }
  for(const entry of records.bank_entries||[]){
    if(!selected(entry)||!within(entry.date))continue;
    bankCount++;if(entry.reviewed)reviewedBankCount++;else attention.bank.push(entry.id);
    if(!(entry.shared_documents||entry.documents||[]).length)attention.documents.push(entry.id);
    if(entry.reviewed&&entry.linked_expense_id){byMonth.get(entry.date.slice(0,7)).out_minor+=entry.amount_minor;continue}
    if(!entry.reviewed||!entry.cash_flow||entry.matched_record)continue;
    const bucket=byMonth.get(entry.date.slice(0,7));
    if(entry.direction==='in'){bucket.in_minor+=entry.amount_minor;const issuer=companies.get(entry.company_id);if(issuer)issuer.in_minor+=entry.amount_minor}
    else bucket.out_minor+=entry.amount_minor;
  }
  const incoming=series.reduce((n,row)=>n+row.in_minor,0),outgoing=series.reduce((n,row)=>n+row.out_minor,0);
  return {series,start,end:finish,asOf:end,incoming,outgoing,net:incoming-outgoing,outstanding,openInvoices,overdue,attention,bankCount,reviewedBankCount,companies:[...companies.values()].sort((a,b)=>b.sales_minor-a.sales_minor),services:[...services.values()].sort((a,b)=>b.sales_minor-a.sales_minor),customers:[...customers.values()].sort((a,b)=>b.sales_minor-a.sales_minor||a.name.localeCompare(b.name)),needsPaymentDetails,needsBankReview:attention.bank.length};
}
function overviewYears(records,current=today().slice(0,4),chosen=null){
 const years=new Set([String(current),String(Number(current)-1)]);
 const add=date=>{if(typeof date==='string'&&/^\d{4}-/.test(date)&&date.slice(0,4)<=String(current))years.add(date.slice(0,4))};
 for(const invoice of records.invoices||[]){add(invoice.data.issue_date);for(const payment of invoice.payments||[])add(payment.data.date)}
 for(const expense of records.journal||[]){add(expense.date);add(expense.payment_date)}
 for(const bank of records.bank_entries||[])add(bank.date);
 if(chosen&&/^\d{4}$/.test(String(chosen))&&String(chosen)<=String(current))years.add(String(chosen));
 return [...years].sort().reverse();
}

let overviewFilters={company:'all',currency:'THB',months:6,year:null},overviewDrill=null;
function overviewMonth(value){return new Date(Number(value.slice(0,4)),Number(value.slice(5,7))-1,1).toLocaleDateString('en-US',{month:'short',year:'2-digit'})}
function cashFlowChart(series,currency){
  const width=900,height=204,left=90,right=18,top=15,bottom=32;
  const peak=Math.max(...series.flatMap(row=>[row.in_minor,row.out_minor]))||100*10**state.currencies[currency];
  const x=i=>series.length===1?(left+width-right)/2:left+i*(width-left-right)/(series.length-1),y=n=>height-bottom-(n/peak)*(height-top-bottom);
  const compact=n=>new Intl.NumberFormat('en-US',{notation:'compact',maximumFractionDigits:1}).format(n/10**state.currencies[currency]);
  const grid=[0,.5,1].map(f=>`<line x1="${left}" x2="${width-right}" y1="${y(peak*f)}" y2="${y(peak*f)}" stroke="#e5e5e5"/><text x="${left-10}" y="${y(peak*f)+4}" text-anchor="end">${esc(compact(peak*f))}</text>`).join('');
  const path=key=>series.map((row,i)=>`${i?'L':'M'}${x(i).toFixed(1)},${y(row[key]).toFixed(1)}`).join(' ');
  const labels=series.map((row,i)=>`<text class="chart-month" data-skip="${i%2===1&&i!==series.length-1}" x="${x(i)}" y="${height-8}" text-anchor="${i===0?'start':i===series.length-1?'end':'middle'}">${esc(overviewMonth(row.month))}</text>`).join('');
  const dots=series.map((row,i)=>`<g><title>${esc(overviewMonth(row.month))}: money in ${esc(money(row.in_minor,currency))}; money out ${esc(money(row.out_minor,currency))}</title><circle cx="${x(i)}" cy="${y(row.in_minor)}" r="3" fill="#171717"/><circle cx="${x(i)}" cy="${y(row.out_minor)}" r="3" fill="#fff" stroke="#777" stroke-width="1.5"/></g>`).join('');
  return `<svg class="cash-chart" data-months="${series.length}" viewBox="0 0 ${width} ${height}" role="img" aria-label="Monthly money in and out in ${esc(currency)}. Exact values are in the monthly figures table below."><g>${grid}${labels}</g><path d="${path('in_minor')}" fill="none" stroke="#171717" stroke-width="2.5"/><path d="${path('out_minor')}" fill="none" stroke="#777" stroke-width="2.5" stroke-dasharray="6 5"/>${dots}</svg>`;
}
function overviewFilterBanner(page){
 return overviewDrill?.page===page?`<div class="overview-drill" id="overview-drill-banner"><span>${esc(overviewDrill.label)}</span><button class="secondary small" id="overview-clear-filter">Clear overview filter</button></div>`:'';
}
function clearOverviewDrill(){overviewDrill=null;$('#overview-drill-banner')?.remove()}
function bindOverviewFilterClear(renderPage){const button=$('#overview-clear-filter');if(button)button.onclick=()=>{clearOverviewDrill();renderPage()}}
function openOverviewRecords(page,ids,label,review='all'){
 overviewDrill={page,ids:new Set(ids),label};
 if(page==='invoices'){filters.q='';filters.status=review==='outstanding'?'unpaid':'all'}
 else{journalYear=String(overviewFilters.year);journalSearch='';journalPage=0;journalSource='document';expensePaymentFilter='all';journalReviewFilter=review}
 location.hash=page;
}
function renderOverview(){
  const f=overviewFilters;if(!f.year)f.year=today().slice(0,4);
  const data=overviewData(state,f),currency=f.currency,years=overviewYears(state,today().slice(0,4),f.year);
  const period=`${data.start} → ${data.end}`,currentNote=`Current balances · ${data.asOf}`;
  const stat=(label,value,note,action='')=>`<${action?'button':'div'} class="stat ${action?'overview-stat-action':''}" ${action?`type="button" id="${action}"`:''}><span class="stat-label">${label}</span><span class="stat-value">${money(value,currency)}</span><span class="stat-note">${note}</span></${action?'button':'div'}>`;
  const attention=[['bank','Bank transactions to review',data.attention.bank.length,'Selected year'],['documents','Bank rows without documents',data.attention.documents.length,'Selected year'],['expenses','Expense payment details',data.attention.expenses.length,'Selected year'],['overdue','Overdue invoices',data.attention.overdue.length,'Current outstanding']];
  $('#main').innerHTML=head('Overview',esc(activeCompanyRecord()?.name||'Cash movement and sales'),`<button class="secondary" id="overview-refresh">Refresh</button><button data-new>＋ New invoice</button>`)+`<section class="overview-filters" aria-label="Overview filters"><label class="field">Currency<select id="overview-currency">${Object.keys(state.currencies).map(c=>`<option ${c===currency?'selected':''}>${c}</option>`).join('')}</select></label><label class="field">Year<select id="overview-year">${years.map(year=>`<option ${year===String(f.year)?'selected':''}>${year}</option>`).join('')}</select></label><p class="muted">Cash and sales · ${esc(period)}</p></section><div class="stats overview-stats">${stat('Money in',data.incoming,'Selected year · confirmed cash')}${stat('Money out',data.outgoing,'Selected year · confirmed cash')}${stat('Net movement',data.net,'Selected year · not profit')}${stat('Outstanding invoices',data.outstanding,`${data.openInvoices.length} open · balance now ↗`,'overview-outstanding')}</div><div class="overview-main"><section class="card overview-chart"><div class="chart-heading"><div><h2>Money in & out</h2><p class="hint">${esc(f.year)} · ${esc(currency)}${data.bankCount?' · '+data.reviewedBankCount+' / '+data.bankCount+' bank rows reviewed':''}</p></div><div class="chart-legend"><span><i></i>Money in</span><span><i class="out"></i>Money out</span></div></div>${cashFlowChart(data.series,currency)}${!data.incoming&&!data.outgoing?'<p class="chart-empty">No confirmed cash in this period. Unreviewed bank rows are excluded.</p>':''}<details class="monthly-figures"><summary>Monthly figures</summary><div class="table-wrap"><table><thead><tr><th>Month</th><th class="right">Money in</th><th class="right">Money out</th><th class="right">Net</th></tr></thead><tbody>${data.series.map(row=>`<tr><td>${overviewMonth(row.month)}</td><td class="right">${money(row.in_minor,currency)}</td><td class="right">${money(row.out_minor,currency)}</td><td class="right">${money(row.in_minor-row.out_minor,currency)}</td></tr>`).join('')}</tbody></table></div></details></section><section class="card overview-attention" aria-labelledby="overview-attention-title"><div class="breakdown-heading"><h2 id="overview-attention-title">Needs attention</h2><p>Review the records behind these figures.</p></div>${attention.map(([key,label,count,note])=>`<button type="button" data-overview-task="${key}" ${count?'':'disabled'}><span>${label}<small>${note}</small></span><b>${count.toLocaleString()}</b><span aria-hidden="true">↗</span></button>`).join('')}${attention.every(([, ,n])=>!n)?'<p class="hint overview-attention-clear">No pending items in this view.</p>':''}</section></div><div class="overview-breakdowns"><section class="card table-wrap"><div class="breakdown-heading"><h2>Sales by service / product</h2><p>Top 5 · issued sales before VAT · ${esc(f.year)}</p></div>${data.services.length?`<table><thead><tr><th>Service</th><th class="right">Invoices</th><th class="right">Sales</th></tr></thead><tbody>${data.services.slice(0,5).map(s=>`<tr><td><b>${esc(s.name)}</b>${s.code?`<span class="sub">${esc(s.code)}</span>`:''}</td><td class="right">${s.invoices.size}</td><td class="right">${money(s.sales_minor,currency)}</td></tr>`).join('')}</tbody></table>`:'<div class="overview-empty">No issued sales in this period.</div>'}</section><section class="card table-wrap"><div class="breakdown-heading"><h2>Top customers</h2><p>Top 5 · issued sales before VAT · ${esc(f.year)}</p></div>${data.customers.length?`<table><thead><tr><th>Customer</th><th class="right">Invoices</th><th class="right">Sales</th></tr></thead><tbody>${data.customers.slice(0,5).map((c,i)=>`<tr><td><button type="button" class="overview-customer-link" data-overview-customer="${i}">${esc(c.name)} ↗</button></td><td class="right">${c.invoices.size}</td><td class="right">${money(c.sales_minor,currency)}</td></tr>`).join('')}</tbody></table>`:'<div class="overview-empty">No issued customer invoices in this period.</div>'}</section></div><p class="meta-note overview-definitions">Cash and sales follow the selected year. ${esc(currentNote)}: outstanding and overdue invoices use current saved balances, including payments and withholding credits. Missing-document and review counts can overlap. Figures cover records saved in LocalBooks.</p>`;
  $('#overview-currency').onchange=e=>{f.currency=e.target.value;renderOverview()};
  $('#overview-year').onchange=e=>{f.year=e.target.value;renderOverview()};
  $('#overview-outstanding').disabled=!data.openInvoices.length;
  $('#overview-outstanding').onclick=()=>openOverviewRecords('invoices',data.openInvoices,`Outstanding invoices now · ${currency}`,'outstanding');
  document.querySelectorAll('[data-overview-task]').forEach(button=>button.onclick=()=>{
   const key=button.dataset.overviewTask,ids=data.attention[key];
   if(key==='overdue')openOverviewRecords('invoices',ids,`Overdue invoices now · ${currency}`,'outstanding');
   else if(key==='expenses')openOverviewRecords('expenses',ids,`Expenses missing payment details · ${f.year} · ${currency}`);
   else openOverviewRecords('journal',ids,`${key==='bank'?'Bank transactions to review':'Bank rows without supporting documents'} · ${f.year} · ${currency}`,key==='bank'?'needs_review':'missing_docs');
  });
  document.querySelectorAll('[data-overview-customer]').forEach(button=>button.onclick=()=>{const c=data.customers[Number(button.dataset.overviewCustomer)];openOverviewRecords('invoices',[...c.invoices],`${c.name} · issued invoices · ${f.year} · ${currency}`)});
  $('#overview-refresh').onclick=async e=>{e.target.disabled=true;try{await refresh();renderOverview()}catch(error){notify(error.message,true);e.target.disabled=false}};
  bindCommon();
}

'use strict';

function overviewRange({year=null,months=6,end=today(),from='',to=''}={}){
  const valid=date=>/^\d{4}-\d{2}-\d{2}$/.test(date)&&!Number.isNaN(Date.parse(date+'T00:00:00Z'))&&new Date(date+'T00:00:00Z').toISOString().slice(0,10)===date;
  let start,finish;
  if(from||to){
    if(!valid(from)||!valid(to)||from>to||to>end)throw new RangeError('Choose valid dates, with From on or before To and no dates after today.');
    start=from;finish=to;
  }else{
    const selectedYear=year!==null&&/^\d{4}$/.test(String(year))?Number(year):null;
    const currentYear=Number(end.slice(0,4)),month=Number(end.slice(5,7));
    const first=selectedYear===null?new Date(currentYear,month-months,1):new Date(selectedYear,0,1);
    start=`${first.getFullYear()}-${String(first.getMonth()+1).padStart(2,'0')}-01`;
    finish=selectedYear!==null&&selectedYear<currentYear?selectedYear+'-12-31':end;
    if(start>finish)throw new RangeError('Choose a period on or before today.');
  }
  const first=Number(start.slice(0,4))*12+Number(start.slice(5,7))-1,last=Number(finish.slice(0,4))*12+Number(finish.slice(5,7))-1;
  const series=Array.from({length:last-first+1},(_,i)=>({month:`${Math.floor((first+i)/12)}-${String((first+i)%12+1).padStart(2,'0')}`,in_minor:0,out_minor:0}));
  return {start,finish,series};
}
function overviewData(records,{company='all',currency='THB',months=6,year=null,end=today(),from='',to='',source='all'}={}){
  const {start,finish,series}=overviewRange({year,months,end,from,to});
  const byMonth=new Map(series.map(row=>[row.month,row]));
  const within=date=>typeof date==='string'&&date>=start&&date<=finish;
  const selected=row=>(company==='all'||String(row.company_id)===String(company))&&row.currency===currency;
  const companies=new Map(records.companies.filter(c=>company==='all'||String(c.id)===String(company)).map(c=>[c.id,{id:c.id,name:c.name,sales_minor:0,in_minor:0,invoices:0}]));
  const cashRecords=[];
  function addCash(row){
    if(!within(row.date)||(source!=='all'&&row.source!==source)||row.amount_minor===0)return;
    const bucket=byMonth.get(row.date.slice(0,7));
    bucket[row.direction==='in'?'in_minor':'out_minor']+=row.amount_minor;
    if(row.direction==='in'&&companies.has(row.company_id))companies.get(row.company_id).in_minor+=row.amount_minor;
    cashRecords.push(row);
  }
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
      addCash({date:payment.data.date,source:invoice.kind==='sale'?'invoice':'expense',direction:invoice.kind==='sale'?'in':'out',amount_minor:payment.amount_minor,company_id:invoice.company_id,name:invoice.data.contact?.name||'Payment',reference:payment.number||invoice.number||'',page:'document',record_id:invoice.id});
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
    addCash({date:expense.payment_date,source:'expense',direction:'out',amount_minor:residual,company_id:expense.company_id,name:expense.supplier||'Expense payment',reference:expense.document_number||'',page:'expenses',record_id:expense.id});
  }
  for(const entry of records.bank_entries||[]){
    if(!selected(entry)||!within(entry.date))continue;
    bankCount++;if(entry.reviewed)reviewedBankCount++;else attention.bank.push(entry.id);
    if(!(entry.shared_documents||entry.documents||[]).length)attention.documents.push(entry.id);
    const cash={date:entry.date,source:entry.linked_expense_id?'expense':'bank',direction:entry.direction,amount_minor:entry.amount_minor,company_id:entry.company_id,name:entry.counterparty||entry.description||'Bank movement',reference:entry.reference||entry.account||'',page:'journal',record_id:entry.id};
    if(entry.reviewed&&entry.linked_expense_id){addCash({...cash,direction:'out'});continue}
    if(!entry.reviewed||!entry.cash_flow||entry.matched_record)continue;
    addCash(cash);
  }
  const incoming=series.reduce((n,row)=>n+row.in_minor,0),outgoing=series.reduce((n,row)=>n+row.out_minor,0);
  return {series,cashRecords:cashRecords.sort((a,b)=>b.date.localeCompare(a.date)),start,end:finish,asOf:end,incoming,outgoing,net:incoming-outgoing,outstanding,openInvoices,overdue,attention,bankCount,reviewedBankCount,companies:[...companies.values()].sort((a,b)=>b.sales_minor-a.sales_minor),services:[...services.values()].sort((a,b)=>b.sales_minor-a.sales_minor),customers:[...customers.values()].sort((a,b)=>b.sales_minor-a.sales_minor||a.name.localeCompare(b.name)),needsPaymentDetails,needsBankReview:attention.bank.length};
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

let overviewFilters={company:'all',currency:'THB',months:6,year:null,period:'year',month:null,from:'',to:'',source:'all',flow:'all',cashOpen:false,cashPage:0},overviewDrill=null;
function overviewMonth(value){return new Date(Number(value.slice(0,4)),Number(value.slice(5,7))-1,1).toLocaleDateString('en-US',{month:'short',year:'2-digit'})}
function cashFlowChart(series,currency,flow='all'){
  const width=900,height=204,left=90,right=18,top=15,bottom=32;
  const peak=Math.max(...series.flatMap(row=>flow==='in'?[row.in_minor]:flow==='out'?[row.out_minor]:[row.in_minor,row.out_minor]))||100*10**state.currencies[currency];
  const x=i=>series.length===1?(left+width-right)/2:left+i*(width-left-right)/(series.length-1),y=n=>height-bottom-(n/peak)*(height-top-bottom);
  const compact=n=>new Intl.NumberFormat('en-US',{notation:'compact',maximumFractionDigits:1}).format(n/10**state.currencies[currency]);
  const grid=[0,.5,1].map(f=>`<line x1="${left}" x2="${width-right}" y1="${y(peak*f)}" y2="${y(peak*f)}" stroke="#e5e5e5"/><text x="${left-10}" y="${y(peak*f)+4}" text-anchor="end">${esc(compact(peak*f))}</text>`).join('');
  const path=key=>series.map((row,i)=>`${i?'L':'M'}${x(i).toFixed(1)},${y(row[key]).toFixed(1)}`).join(' ');
  const labels=series.map((row,i)=>i%Math.ceil(series.length/12)!==0&&i!==series.length-1?'':`<text class="chart-month" data-skip="${series.length>6&&i%Math.ceil(series.length/6)!==0&&i!==series.length-1}" x="${x(i)}" y="${height-8}" text-anchor="${i===0?'start':i===series.length-1?'end':'middle'}">${esc(overviewMonth(row.month))}</text>`).join('');
  const dots=series.map((row,i)=>`${flow!=='out'?`<circle cx="${x(i)}" cy="${y(row.in_minor)}" r="3" fill="#171717"/>`:''}${flow!=='in'?`<circle cx="${x(i)}" cy="${y(row.out_minor)}" r="3" fill="#fff" stroke="#777" stroke-width="1.5"/>`:''}`).join('');
  const zones=series.map((row,i)=>{
    const start=i===0?left:(x(i-1)+x(i))/2,end=i===series.length-1?width-right:(x(i)+x(i+1))/2;
    const detail=`${overviewMonth(row.month)} · ${flow!=='out'?'Money in: '+money(row.in_minor,currency):''}${flow==='all'?' · ':''}${flow!=='in'?'Money out: '+money(row.out_minor,currency):''}`;
    return `<g class="chart-zone" tabindex="0" role="img" aria-label="${esc(detail)}" data-detail="${esc(detail)}" data-x="${x(i)/width}"><rect x="${start}" y="${top}" width="${end-start}" height="${height-bottom-top}" fill="transparent"/><line x1="${x(i)}" x2="${x(i)}" y1="${top}" y2="${height-bottom}"/></g>`;
  }).join('');
  const label=flow==='all'?'money in and out':flow==='in'?'money in':'money out';
  return `<div class="cash-chart-interactive"><div class="chart-tooltip" role="status" hidden></div><svg class="cash-chart" data-months="${series.length}" viewBox="0 0 ${width} ${height}" role="group" aria-label="Monthly ${label} in ${esc(currency)}. Hover, tap or focus a month for values."><g>${grid}${labels}</g>${flow!=='out'?`<path d="${path('in_minor')}" fill="none" stroke="#171717" stroke-width="2.5"/>`:''}${flow!=='in'?`<path d="${path('out_minor')}" fill="none" stroke="#777" stroke-width="2.5" stroke-dasharray="6 5"/>`:''}${dots}${zones}</svg></div>`;
}
function bindCashChart(){
  const chart=document.querySelector('.cash-chart-interactive');
  const tooltip=chart.querySelector('.chart-tooltip');
  const clear=()=>{chart.querySelectorAll('.chart-zone.active').forEach(zone=>zone.classList.remove('active'));tooltip.hidden=true};
  chart.querySelectorAll('.chart-zone').forEach(zone=>{
    const show=()=>{
      clear();zone.classList.add('active');tooltip.textContent=zone.dataset.detail;tooltip.hidden=false;
      const width=chart.clientWidth,half=tooltip.offsetWidth/2;
      tooltip.style.left=Math.max(half,Math.min(width-half,Number(zone.dataset.x)*width))+'px';
    };
    zone.onpointerenter=show;zone.onfocus=show;zone.onclick=show;zone.onblur=clear;
    zone.onkeydown=e=>{if(e.key==='Escape'){clear();zone.blur()}};
  });
  chart.onpointerleave=()=>{if(!chart.contains(document.activeElement))clear()};

}
function overviewFilterBanner(page){
 return overviewDrill?.page===page?`<div class="overview-drill" id="overview-drill-banner"><span>${esc(overviewDrill.label)}</span><button class="secondary small" id="overview-clear-filter">Clear overview filter</button></div>`:'';
}
function clearOverviewDrill(){overviewDrill=null;$('#overview-drill-banner')?.remove()}
function bindOverviewFilterClear(renderPage){const button=$('#overview-clear-filter');if(button)button.onclick=()=>{clearOverviewDrill();renderPage()}}
function openOverviewRecords(page,ids,label,review='all'){
 overviewDrill={page,ids:new Set(ids),label};
 if(page==='invoices'){filters.q='';filters.status=review==='outstanding'?'unpaid':'all'}
 else{journalYear='all';journalSearch='';journalPage=0;journalSource='document';expensePaymentFilter='all';journalReviewFilter=review}
 location.hash=page;
}
const overviewSources={all:'All cash sources',invoice:'Invoice payments',expense:'Expense payments',bank:'Other bank movements'};
function renderOverview(){
  const f=overviewFilters,now=today();
  if(!f.year)f.year=now.slice(0,4);
  if(!f.month)f.month=now.slice(5,7);
  const monthCount=String(f.year)===now.slice(0,4)?Number(now.slice(5,7)):12;
  f.month=String(Math.min(Number(f.month),monthCount)).padStart(2,'0');
  let from='',to='';
  if(f.period==='month'){
    from=`${f.year}-${f.month}-01`;
    to=`${f.year}-${f.month}-${new Date(Number(f.year),Number(f.month),0).getDate()}`;
    if(to>now)to=now;
  }else if(f.period==='custom'){from=f.from;to=f.to}
  const data=overviewData(state,{...f,from,to}),currency=f.currency,years=overviewYears(state,now.slice(0,4),f.year);
  const period=`${data.start} → ${data.end}`,currentNote=`Current balances · ${data.asOf}`;
  const choices=(items,value)=>items.map(([id,label])=>`<option value="${esc(id)}" ${String(id)===String(value)?'selected':''}>${esc(label)}</option>`).join('');
  const selectedCard={'in':'overview-money-in','out':'overview-money-out','all':'overview-net'}[f.flow];
  const stat=(label,value,note,action='')=>`<${action?'button':'div'} class="stat ${action?'overview-stat-action':''}" ${action?`type="button" id="${action}" ${action!=='overview-outstanding'?`aria-pressed="${action===selectedCard}"`:''}`:''}><span class="stat-label">${label}</span><span class="stat-value">${money(value,currency)}</span><span class="stat-note">${note}</span></${action?'button':'div'}>`;
  const attention=[['bank','Bank transactions to review',data.attention.bank.length,'Selected dates'],['documents','Bank rows without documents',data.attention.documents.length,'Selected dates'],['expenses','Expense payment details',data.attention.expenses.length,'Selected dates'],['overdue','Overdue invoices',data.attention.overdue.length,'Current outstanding']];
  const showIn=f.flow!=='out',showOut=f.flow!=='in',flowName=f.flow==='in'?'Money in':f.flow==='out'?'Money out':'Money in & out';
  const cashRows=data.cashRecords.filter(row=>f.flow==='all'||row.direction===f.flow),pageSize=25;
  f.cashPage=Math.min(f.cashPage,Math.max(0,Math.ceil(cashRows.length/pageSize)-1));
  const start=f.cashPage*pageSize,shown=cashRows.slice(start,start+pageSize);
  const cashTable=f.cashOpen?`<section class="card table-wrap overview-cash-records" id="overview-cash-records" aria-labelledby="overview-cash-title">
    <div class="breakdown-heading"><h2 id="overview-cash-title">${flowName} · cash records</h2><p>${esc(period)} · ${esc(overviewSources[f.source])} · ${cashRows.length.toLocaleString()} records</p></div>
    ${shown.length?`<table><thead><tr><th>Date</th><th>Counterparty / source</th><th>Reference</th>${showIn?'<th class="right">Money in</th>':''}${showOut?'<th class="right">Money out</th>':''}</tr></thead><tbody>${shown.map(row=>`<tr><td class="cash-date">${esc(row.date)}</td><td><b>${esc(row.name)}</b><span class="sub">${esc(overviewSources[row.source])}</span></td><td><a href="#${row.page}/${row.record_id}">${esc(row.reference||'View record')} ↗</a></td>${showIn?`<td class="right">${row.direction==='in'?money(row.amount_minor,currency):'—'}</td>`:''}${showOut?`<td class="right">${row.direction==='out'?money(row.amount_minor,currency):'—'}</td>`:''}</tr>`).join('')}</tbody></table>`:'<div class="overview-empty">No confirmed cash records for these filters.</div>'}
    ${cashRows.length>pageSize?`<div class="bank-pagination"><button type="button" class="secondary small" id="overview-cash-prev" ${f.cashPage===0?'disabled':''}>Previous</button><span>${start+1}–${Math.min(start+pageSize,cashRows.length)} of ${cashRows.length}</span><button type="button" class="secondary small" id="overview-cash-next" ${start+pageSize>=cashRows.length?'disabled':''}>Next</button></div>`:''}
  </section>`:'';
  $('#main').innerHTML=head('Overview',esc(activeCompanyRecord()?.name||'Cash movement and sales'),`<button data-new>＋ New invoice</button>`)+`
  <section class="overview-filters" aria-label="Overview filters">
    <label class="field">Currency<select id="overview-currency">${choices(Object.keys(state.currencies).map(c=>[c,c]),currency)}</select></label>
    <label class="field">Period<select id="overview-period">${choices([['calendar','Year & month'],['custom','Custom dates']],f.period==='custom'?'custom':'calendar')}</select></label>
    ${f.period!=='custom'?`<label class="field overview-year-field">Year<select id="overview-year">${choices(years.map(y=>[y,y]),f.year)}</select></label>`:''}
    ${f.period!=='custom'?`<label class="field overview-month-field">Month<select id="overview-month">${choices([['all','All months'],...Array.from({length:monthCount},(_,i)=>[String(i+1).padStart(2,'0'),new Date(2000,i,1).toLocaleDateString('en-US',{month:'long'})])],f.period==='month'?f.month:'all')}</select></label>`:''}
    ${f.period==='custom'?`<label class="field overview-date-field">From<input type="date" id="overview-from" min="1900-01-01" max="${now}" value="${esc(f.from)}" required></label><label class="field overview-date-field">To<input type="date" id="overview-to" min="1900-01-01" max="${now}" value="${esc(f.to)}" required></label><button type="button" class="secondary" id="overview-apply">Apply dates</button>`:''}
    <label class="field overview-source-field">Cash source<select id="overview-source">${choices(Object.entries(overviewSources),f.source)}</select></label>
    <button type="button" class="quiet" id="overview-reset">Reset filters</button>
    ${f.period==='custom'?'<p class="overview-filter-error" id="overview-date-error" role="alert" hidden></p>':''}
    <p class="muted">Cash and sales · ${esc(period)}</p>
  </section>
  <div class="stats overview-stats">
    ${stat('Money in',data.incoming,'Selected dates · confirmed cash ↗','overview-money-in')}
    ${stat('Money out',data.outgoing,'Selected dates · confirmed cash ↗','overview-money-out')}
    ${stat('Net movement',data.net,'Selected dates · not profit ↗','overview-net')}
    ${stat('Outstanding invoices',data.outstanding,`${data.openInvoices.length} open · balance now ↗`,'overview-outstanding')}
  </div>
  <div class="overview-main">
    <section class="card overview-chart">
      <div class="chart-heading"><div><h2>${flowName}</h2><p class="hint">${esc(currency)} · ${esc(overviewSources[f.source])}</p></div><div class="chart-legend">${showIn?'<span><i></i>Money in</span>':''}${showOut?'<span><i class="out"></i>Money out</span>':''}</div></div>
      <div class="overview-chart-controls"><button type="button" class="secondary small" id="overview-show-cash" aria-expanded="${f.cashOpen}" aria-controls="overview-cash-records">${f.cashOpen?'Hide':'View'} cash records (${cashRows.length})</button></div>
      ${cashFlowChart(data.series,currency,f.flow)}
      ${!(showIn&&data.incoming||showOut&&data.outgoing)?'<p class="chart-empty">No confirmed cash for these filters. Unreviewed bank rows are excluded.</p>':''}
      <details class="monthly-figures" ${f.monthlyOpen?'open':''}><summary>Monthly figures</summary><div class="table-wrap"><table><thead><tr><th>Month</th>${showIn?'<th class="right">Money in</th>':''}${showOut?'<th class="right">Money out</th>':''}${f.flow==='all'?'<th class="right">Net</th>':''}</tr></thead><tbody>${data.series.map(row=>`<tr><td>${overviewMonth(row.month)}</td>${showIn?`<td class="right">${money(row.in_minor,currency)}</td>`:''}${showOut?`<td class="right">${money(row.out_minor,currency)}</td>`:''}${f.flow==='all'?`<td class="right">${money(row.in_minor-row.out_minor,currency)}</td>`:''}</tr>`).join('')}</tbody><tfoot><tr><th>Selected dates</th>${showIn?`<th class="right">${money(data.incoming,currency)}</th>`:''}${showOut?`<th class="right">${money(data.outgoing,currency)}</th>`:''}${f.flow==='all'?`<th class="right">${money(data.net,currency)}</th>`:''}</tr></tfoot></table></div></details>
    </section>
    <section class="card overview-attention" aria-labelledby="overview-attention-title"><div class="breakdown-heading"><h2 id="overview-attention-title">Needs attention</h2><p>Review the records behind these figures.</p></div>${attention.map(([key,label,count,note])=>`<button type="button" data-overview-task="${key}" ${count?'':'disabled'}><span>${label}<small>${note}</small></span><b>${count.toLocaleString()}</b><span aria-hidden="true">↗</span></button>`).join('')}${attention.every(([, ,n])=>!n)?'<p class="hint overview-attention-clear">No pending items in this view.</p>':''}</section>
  </div>
  ${cashTable}
  <div class="overview-breakdowns">
    <section class="card table-wrap"><div class="breakdown-heading"><h2>Sales by service / product</h2><p>Top 5 · issued sales before VAT · selected dates</p></div>${data.services.length?`<table><thead><tr><th>Service</th><th class="right">Invoices</th><th class="right">Sales</th></tr></thead><tbody>${data.services.slice(0,5).map(s=>`<tr><td><b>${esc(s.name)}</b>${s.code?`<span class="sub">${esc(s.code)}</span>`:''}</td><td class="right">${s.invoices.size}</td><td class="right">${money(s.sales_minor,currency)}</td></tr>`).join('')}</tbody></table>`:'<div class="overview-empty">No issued sales for these dates.</div>'}</section>
    <section class="card table-wrap"><div class="breakdown-heading"><h2>Top customers</h2><p>Top 5 · issued sales before VAT · selected dates</p></div>${data.customers.length?`<table><thead><tr><th>Customer</th><th class="right">Invoices</th><th class="right">Sales</th></tr></thead><tbody>${data.customers.slice(0,5).map((c,i)=>`<tr><td><button type="button" class="overview-customer-link" data-overview-customer="${i}">${esc(c.name)} ↗</button></td><td class="right">${c.invoices.size}</td><td class="right">${money(c.sales_minor,currency)}</td></tr>`).join('')}</tbody></table>`:'<div class="overview-empty">No issued customer invoices for these dates.</div>'}</section>
  </div>
  <p class="meta-note overview-definitions">Cash and sales follow the selected dates. Cash source filters cash totals, chart and records. ${esc(currentNote)}: outstanding and overdue invoices use current saved balances across all dates, including payments and withholding credits. Missing-document and review counts can overlap. Figures cover records saved in LocalBooks.</p>`;
  bindCashChart();
  const rerender=()=>{f.cashPage=0;renderOverview()};
  $('#overview-currency').onchange=e=>{f.currency=e.target.value;rerender()};
  $('#overview-source').onchange=e=>{f.source=e.target.value;rerender()};
  $('#overview-period').onchange=e=>{f.period=e.target.value==='custom'?'custom':'year';if(f.period==='custom'){f.from=data.start;f.to=data.end}rerender()};
  if($('#overview-year'))$('#overview-year').onchange=e=>{f.year=e.target.value;rerender()};
  if($('#overview-month'))$('#overview-month').onchange=e=>{f.period=e.target.value==='all'?'year':'month';if(e.target.value!=='all')f.month=e.target.value;rerender()};
  if($('#overview-apply'))$('#overview-apply').onclick=()=>{
    const first=$('#overview-from'),last=$('#overview-to');
    if(!first.reportValidity()||!last.reportValidity())return;
    try{overviewRange({from:first.value,to:last.value,end:now});f.from=first.value;f.to=last.value;rerender()}catch(error){const message=$('#overview-date-error');message.textContent=error.message;message.hidden=false}
  };
  $('#overview-reset').onclick=()=>{Object.assign(f,{currency:'THB',year:now.slice(0,4),period:'year',month:now.slice(5,7),from:'',to:'',source:'all',flow:'all',cashOpen:false,cashPage:0});renderOverview()};
  $('#overview-show-cash').onclick=()=>{f.cashOpen=!f.cashOpen;renderOverview()};
  document.querySelector('.monthly-figures').ontoggle=e=>{f.monthlyOpen=e.target.open};
  for(const [id,flow] of [['overview-money-in','in'],['overview-money-out','out'],['overview-net','all']])$('#'+id).onclick=()=>{f.flow=flow;f.cashOpen=true;rerender();$('#overview-cash-records').scrollIntoView({block:'nearest'})};
  if($('#overview-cash-prev'))$('#overview-cash-prev').onclick=()=>{f.cashPage--;renderOverview()};
  if($('#overview-cash-next'))$('#overview-cash-next').onclick=()=>{f.cashPage++;renderOverview()};
  $('#overview-outstanding').disabled=!data.openInvoices.length;
  $('#overview-outstanding').onclick=()=>openOverviewRecords('invoices',data.openInvoices,`Outstanding invoices now · ${currency}`,'outstanding');
  document.querySelectorAll('[data-overview-task]').forEach(button=>button.onclick=()=>{
    const key=button.dataset.overviewTask,ids=data.attention[key];
    if(key==='overdue')openOverviewRecords('invoices',ids,`Overdue invoices now · ${currency}`,'outstanding');
    else if(key==='expenses')openOverviewRecords('expenses',ids,`Expenses missing payment details · ${period} · ${currency}`);
    else openOverviewRecords('journal',ids,`${key==='bank'?'Bank transactions to review':'Bank rows without supporting documents'} · ${period} · ${currency}`,key==='bank'?'needs_review':'missing_docs');
  });
  document.querySelectorAll('[data-overview-customer]').forEach(button=>button.onclick=()=>{const c=data.customers[Number(button.dataset.overviewCustomer)];openOverviewRecords('invoices',[...c.invoices],`${c.name} · issued invoices · ${period} · ${currency}`)});
  bindCommon();
}

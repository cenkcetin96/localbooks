"""Balanced, source-linked accountant working entries; no automatic ledger posting."""
import datetime as dt
import csv
import hashlib
import io
import json
import re
import thai_reporting

SCHEMA = '''
CREATE TABLE IF NOT EXISTS accounting_accounts(company_id INTEGER NOT NULL REFERENCES companies(id), code TEXT NOT NULL, data TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(company_id,code));
CREATE TABLE IF NOT EXISTS accounting_entries(company_id INTEGER NOT NULL REFERENCES companies(id), source_kind TEXT NOT NULL, source_id INTEGER NOT NULL, data TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(company_id,source_kind,source_id));
CREATE TABLE IF NOT EXISTS accounting_mappings(company_id INTEGER PRIMARY KEY REFERENCES companies(id), data TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1);
'''
TABLES = {'accounting_accounts', 'accounting_entries', 'accounting_mappings'}
BOOKS = {'sales':'4 · Sales journal', 'purchases':'5 · Purchase journal', 'receipts':'2 · Cash receipts journal', 'payments':'3 · Cash payments journal', 'general':'1 · General journal'}
TYPES = ('asset', 'liability', 'equity', 'income', 'expense', 'suspense')
DEFAULTS = [
    ('1101000','Cash','asset'), ('1102000','Bank','asset'),
    ('1201000','Account Receivable - Domestic','asset'),
    ('1202000','Withholding tax receivable','asset'), ('1203000','Input VAT','asset'),
    ('2101000','Accounts payable - Domestic','liability'), ('2341000','VAT/GST','liability'),
    ('4001000','Sales','income'), ('4002000','Sale discounts','income'),
    ('5001000','General expenses','expense'), ('9990000','Suspense - review required','suspense'),
    ('1301000','Inventory','asset'), ('1401000','Prepaid expenses','asset'),
    ('1402000','Refundable deposits','asset'), ('1501000','Equipment','asset'),
    ('1591000','Accumulated depreciation','asset'), ('2201000','Accrued expenses','liability'),
    ('2301000','Withholding tax payable','liability'), ('2302000','Payroll tax payable','liability'),
    ('2303000','Social security payable','liability'), ('2304000','Corporate income tax payable','liability'),
    ('2401000','Loans payable','liability'), ('3101000','Share capital','equity'),
    ('3201000','Retained earnings','equity'), ('4003000','Service income','income'),
    ('4004000','Other income','income'), ('5002000','Cost of goods sold','expense'),
    ('5101000','Salaries and wages','expense'), ('5102000','Employer social security','expense'),
    ('5201000','Rent','expense'), ('5202000','Utilities','expense'),
    ('5203000','Telephone and internet','expense'), ('5204000','Travel and transport','expense'),
    ('5205000','Professional and accounting fees','expense'), ('5206000','Bank fees','expense'),
    ('5207000','Marketing','expense'), ('5301000','Depreciation expense','expense'),
    ('5401000','Interest expense','expense'), ('5501000','Corporate income tax expense','expense'),
]
ROLE_DEFAULTS = {
    'cash':('Cash receipts','1101000'), 'bank':('Bank movements / non-cash receipts','1102000'),
    'receivable':('Customer receivables','1201000'), 'withheld':('Customer withholding credits','1202000'),
    'payable':('Supplier payables','2101000'), 'output_vat':('Invoice output VAT','2341000'),
    'sales':('Invoice sales','4001000'), 'expenses':('Supplier document costs','5001000'),
    'suspense':('Unmatched bank movements','9990000'),
}


def text(value, label, limit, required=False):
    if not isinstance(value, str) or len(value.strip()) > limit or (required and not value.strip()):
        raise ValueError(f'{label}: enter {"1–" if required else "up to "}{limit} characters.')
    return value.strip()


def integer(value, label, low=0, high=999999999999):
    if type(value) is not int or not low <= value <= high:raise ValueError(f'{label}: invalid integer amount or ID.')
    return value


def accounts(store, company):
    store.party('companies', company)
    rows={code:dict(code=code,name=name,type=kind,active=True,revision=0) for code,name,kind in DEFAULTS}
    for r in store.db.execute('SELECT * FROM accounting_accounts WHERE company_id=?',(company,)):
        rows[r['code']]=dict(code=r['code'],revision=r['revision'],**json.loads(r['data']))
    return sorted(rows.values(),key=lambda a:a['code'])


def account_data(data):
    code=text(data.get('code'),'Account code',20,True)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*',code):raise ValueError('Account code: use letters, numbers, dots or hyphens.')
    name=text(data.get('name'),'Account name',200,True)
    if data.get('type') not in TYPES or type(data.get('active')) is not bool:raise ValueError('Choose an account type and active status.')
    item=dict(name=name,type=data['type'],active=data['active'])
    if 'thai_reporting' in data:item['thai_reporting']=thai_reporting.mapping_data(data['thai_reporting'])
    return code,item


def save_account(store, data):
    with store.transaction():
        company=integer(data.get('company_id'),'Company',1)
        code,item=account_data(data)
        old=next((a for a in accounts(store,company) if a['code']==code),None)
        if 'thai_reporting' in item:
            mapped=thai_reporting.resolved(dict(code=code,**item),thai_reporting.profile(store,company))
            if not mapped['compatible'] or (item['thai_reporting']['reviewed'] and not mapped['usable']):raise ValueError('Thai reporting review needs a compatible DBD category and the current confirmed company basis/account details.')
        elif old and old.get('thai_reporting'):
            item['thai_reporting']=dict(old['thai_reporting'])
            if (item['name'],item['type'])!=(old['name'],old['type']):item['thai_reporting']['reviewed']=False
        revision=old['revision'] if old else 0
        if data.get('revision')!=revision:raise ValueError('This account changed in another window. Reload before saving.')
        store.db.execute('INSERT INTO accounting_accounts VALUES(?,?,?,?) ON CONFLICT(company_id,code) DO UPDATE SET data=excluded.data,revision=excluded.revision',(company,code,json.dumps(item,ensure_ascii=False),revision+1))
        store.event('save_account',company,code+' · '+item['name'])
        return dict(code=code,revision=revision+1,**item)


def mappings(store, company):
    store.party('companies',company)
    row=store.db.execute('SELECT * FROM accounting_mappings WHERE company_id=?',(company,)).fetchone()
    return dict(roles=json.loads(row['data']) if row else {role:default for role,(_,default) in ROLE_DEFAULTS.items()},labels={role:label for role,(label,_) in ROLE_DEFAULTS.items()},revision=row['revision'] if row else 0)


def mapping_data(data, active):
    if not isinstance(data,dict) or data.keys()!=ROLE_DEFAULTS.keys() or any(type(code) is not str or code not in active for code in data.values()):raise ValueError('Choose an active company account for every automatic mapping.')
    return data


def save_mappings(store, data):
    with store.transaction():
        company=integer(data.get('company_id'),'Company',1)
        old=mappings(store,company)
        if data.get('revision')!=old['revision']:raise ValueError('Account mappings changed in another window. Reload before saving.')
        roles=mapping_data(data.get('roles'),{a['code'] for a in accounts(store,company) if a['active']})
        store.db.execute('INSERT INTO accounting_mappings VALUES(?,?,?) ON CONFLICT(company_id) DO UPDATE SET data=excluded.data,revision=excluded.revision',(company,json.dumps(roles),old['revision']+1))
        store.event('save_account_mappings',company,'Automatic transaction accounts')
        return mappings(store,company)


def line(code, debit=0, credit=0):return dict(code=code,debit_minor=debit,credit_minor=credit,cost_center='')


def source(store, kind, id, company):
    integer(id,'Source',1);integer(company,'Company',1)
    warnings=[];related=None;blocked=''
    if kind=='invoice':
        row=store.db.execute('SELECT company_id,currency,number,status,kind,total_minor,data FROM invoices WHERE id=?',(id,)).fetchone()
        if not row or row['kind']!='sale':raise ValueError('Sales invoice not found.')
        d=json.loads(row['data']);day=d['issue_date'];currency=row['currency'];owner=row['company_id']
        title=(row['number'] or 'Draft #'+str(id))+' · '+d['contact']['name'];book='sales'
        vat=d.get('vat_minor',0);lines=[line('1201000',row['total_minor']),line('4001000',credit=row['total_minor']-vat)]
        if vat:lines.append(line('2341000',credit=vat))
        if row['status']=='draft':warnings.append('Draft invoice: this is a preview. Issue the invoice before saving its accounting entry.')
        if row['status']!='issued':blocked='Only issued, active invoices can have a saved accounting entry.'
    elif kind=='payment':
        row=store.db.execute('SELECT p.*,i.company_id,i.currency,i.status,i.kind FROM payments p JOIN invoices i ON i.id=p.invoice_id WHERE p.id=?',(id,)).fetchone()
        if not row or row['kind']!='sale':raise ValueError('Customer receipt not found.')
        d=json.loads(row['data']);day=d['date'];currency=row['currency'];owner=row['company_id'];title=row['number'];book='receipts'
        related=dict(kind='invoice',id=row['invoice_id'],href='#document/'+str(row['invoice_id']))
        cash='1101000' if d['method'].strip().casefold()=='cash' else '1102000'
        withheld=d.get('withholding_minor',0)
        lines=[line(cash,row['amount_minor']),line('1201000',credit=row['amount_minor']+withheld)]
        if withheld:lines.append(line('1202000',withheld))
        if row['reversed'] or row['status']!='issued':blocked='This receipt was reversed or its invoice is inactive. Retain the saved entry as history; handle corrections with your accountant.'
        warnings.append('Confirm the cash or bank account against the payment method and evidence.')
    elif kind=='expense':
        row=store.db.execute('SELECT company_id,data FROM journal_entries WHERE id=?',(id,)).fetchone()
        if not row:raise ValueError('Expense not found.')
        d=json.loads(row['data']);day=d['date'];currency=d['currency'];owner=row['company_id'];book='purchases'
        title=(d.get('document_number') or 'Expense #'+str(id))+' · '+d['supplier']
        lines=[line('5001000',d['amount_minor']),line('2101000',credit=d['amount_minor'])]
        warnings.append('Suggestion records the supplier document against accounts payable. Input VAT and supplier withholding are not inferred. Record payment settlement separately; this tab does not create it.')
        if d.get('duplicate_of'):blocked='This is a duplicate document. Use the primary expense accounting entry.';related=dict(kind='expense',id=d['duplicate_of'],href='#expenses/'+str(d['duplicate_of']))
    elif kind=='bank':
        row=store.db.execute('SELECT e.data,s.company_id,s.currency,s.account FROM bank_entries e JOIN bank_statements s ON s.id=e.statement_id WHERE e.id=?',(id,)).fetchone()
        if not row:raise ValueError('Bank transaction not found.')
        d=json.loads(row['data']);day=d['date'];currency=row['currency'];owner=row['company_id']
        title=d.get('counterparty') or d['description'];incoming=d['direction']=='in';book='receipts' if incoming else 'payments'
        match=d.get('matched_record','');offset='9990000'
        if match.startswith('expense:'):
            offset='2101000';related=dict(kind='expense',id=int(match.split(':')[1]),href='#expenses/'+match.split(':')[1])
            warnings.append('Linked expense settlement: debit accounts payable, credit bank. The supplier cost belongs to the expense entry.')
        elif match.startswith('payment:'):
            pid=int(match.split(':')[1]);p=store.db.execute('SELECT invoice_id FROM payments WHERE id=?',(pid,)).fetchone()
            related=dict(kind='payment',id=pid,href='#document/'+str(p['invoice_id'])) if p else None
            blocked='This bank movement is linked to a customer receipt. Use the receipt accounting entry to avoid recording cash twice.'
        else:warnings.append('Unmatched bank movement uses suspense. Select Manual and confirm the offset account; bank descriptions do not establish sales, VAT or tax treatment.')
        lines=[line('1102000',d['amount_minor']) if incoming else line(offset,d['amount_minor']),line(offset,credit=d['amount_minor']) if incoming else line('1102000',credit=d['amount_minor'])]
    else:raise ValueError('Choose invoice, payment, expense or bank.')
    if owner!=company:raise ValueError('This record belongs to another company.')
    # Only source fields, never blobs or later invoice payment balances, determine freshness.
    evidence={k:row[k] for k in row.keys() if k not in ('pdf','html','created','request_key')}
    roles=mappings(store,company)['roles'];mapped={default:roles[role] for role,(_,default) in ROLE_DEFAULTS.items()}
    evidence['account_mapping']={r['code']:mapped[r['code']] for r in lines}
    lines=[dict(r,code=mapped[r['code']]) for r in lines]
    fingerprint=hashlib.sha256(json.dumps(evidence,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return dict(kind=kind,id=id,company_id=company,currency=currency,date=day,description=title,book=book,lines=lines,warnings=warnings,blocked=blocked,related=related,fingerprint=fingerprint)


def entry_data(data, codes):
    if data.get('mode') not in ('auto','manual') or data.get('book') not in BOOKS:raise ValueError('Choose a calculation mode and journal book.')
    try:day=dt.date.fromisoformat(data.get('date')).isoformat()
    except (ValueError,TypeError):raise ValueError('Enter a valid accounting date.')
    rows=data.get('lines')
    if not isinstance(rows,list) or not 2<=len(rows)<=100:raise ValueError('Add between 2 and 100 accounting lines.')
    lines=[]
    for r in rows:
        if not isinstance(r,dict) or r.get('code') not in codes:raise ValueError('Choose a saved account for every line.')
        debit=integer(r.get('debit_minor'),'Debit');credit=integer(r.get('credit_minor'),'Credit')
        if (debit>0)==(credit>0):raise ValueError('Each line needs a debit or credit amount, on one side only.')
        lines.append(dict(code=r['code'],debit_minor=debit,credit_minor=credit,cost_center=text(r.get('cost_center',''),'Cost center',100)))
    if sum(r['debit_minor'] for r in lines)!=sum(r['credit_minor'] for r in lines):raise ValueError('Debits and credits must balance before saving.')
    currency=data.get('currency')
    if currency not in ('THB','USD','EUR','GBP','SGD','AUD','CAD','JPY','KWD'):raise ValueError('Choose a supported accounting currency.')
    return dict(mode=data['mode'],book=data['book'],date=day,currency=currency,description=text(data.get('description'),'Description',1000,True),note=text(data.get('note',''),'Note',2000),lines=lines,source_fingerprint=text(data.get('source_fingerprint'),'Source fingerprint',64,True))


def workspace(store, kind, id, company):
    with store.lock:
        src=source(store,kind,id,company)
        row=store.db.execute('SELECT data,revision FROM accounting_entries WHERE company_id=? AND source_kind=? AND source_id=?',(company,kind,id)).fetchone()
        automatic=dict(mode='auto',book=src['book'],date=src['date'],currency=src['currency'],description=src['description'],note='',lines=src['lines'],source_fingerprint=src['fingerprint'])
        saved=json.loads(row['data']) if row else None
        return dict(source=src,accounts=accounts(store,company),books=BOOKS,automatic=automatic,entry=saved or automatic,revision=row['revision'] if row else 0,saved=bool(row),stale=bool(saved and saved['source_fingerprint']!=src['fingerprint']))


def save_entry(store, data):
    with store.transaction():
        company=data.get('company_id');kind=data.get('source_kind');id=data.get('source_id')
        work=workspace(store,kind,id,company);src=work['source']
        if src['blocked']:raise ValueError(src['blocked'])
        if data.get('revision')!=work['revision']:raise ValueError('This accounting entry changed in another window. Reload before saving.')
        if data.get('source_fingerprint')!=src['fingerprint']:raise ValueError('The source transaction changed. Reload and review its accounting entry.')
        candidate=dict(data)
        if candidate.get('currency')!=src['currency']:raise ValueError('The source currency changed. Reset to automatic before reviewing this entry.')
        candidate['currency']=src['currency']
        if candidate.get('mode')=='auto':candidate['lines']=src['lines']
        active={a['code'] for a in work['accounts'] if a['active']}
        item=entry_data(candidate,active)
        item['updated']=dt.datetime.now(dt.timezone.utc).isoformat()
        store.db.execute('INSERT INTO accounting_entries VALUES(?,?,?,?,?) ON CONFLICT(company_id,source_kind,source_id) DO UPDATE SET data=excluded.data,revision=excluded.revision',(company,kind,id,json.dumps(item,ensure_ascii=False),work['revision']+1))
        store.event('save_accounting_entry',id,kind+' · '+item['mode']+' · '+src['currency'])
        return workspace(store,kind,id,company)


def validate_backup(db, tables):
    if not TABLES.intersection(tables):return
    if not {'accounting_accounts','accounting_entries'}.issubset(tables):raise ValueError('Backup has incomplete accounting tables.')
    saved={}
    for company,code,raw,revision in db.execute('SELECT * FROM accounting_accounts'):
        account_data(dict(code=code,**json.loads(raw)));integer(revision,'Account revision',1)
        saved.setdefault(company,set()).add(code)
    defaults={a[0] for a in DEFAULTS}
    if 'accounting_mappings' in tables:
        for company,raw,revision in db.execute('SELECT * FROM accounting_mappings'):
            mapping_data(json.loads(raw),defaults|saved.get(company,set()));integer(revision,'Mapping revision',1)
    for company,kind,id,raw,revision in db.execute('SELECT * FROM accounting_entries'):
        tables_by_kind={'invoice':'invoices','payment':'payments','expense':'journal_entries','bank':'bank_entries'}
        if kind not in tables_by_kind:raise ValueError('Backup has an invalid accounting source.')
        table=tables_by_kind[kind]
        query='SELECT i.company_id FROM payments p JOIN invoices i ON i.id=p.invoice_id WHERE p.id=?' if kind=='payment' else 'SELECT s.company_id FROM bank_entries e JOIN bank_statements s ON s.id=e.statement_id WHERE e.id=?' if kind=='bank' else f'SELECT company_id FROM {table} WHERE id=?'
        owner=db.execute(query,(id,)).fetchone()
        if not owner or owner[0]!=company:raise ValueError('Backup has a broken accounting source link.')
        entry_data(json.loads(raw),defaults|saved.get(company,set()));integer(revision,'Entry revision',1)


def export_records(store, company, window):
    entries=[]
    for r in store.db.execute('SELECT * FROM accounting_entries WHERE company_id=? ORDER BY source_kind,source_id',(company,)):
        d=json.loads(r['data'])
        if not window['start']<=d['date']<=window['end']:continue
        src=source(store,r['source_kind'],r['source_id'],company)
        entries.append(dict(source_kind=r['source_kind'],source_id=r['source_id'],revision=r['revision'],stale=not src or d['source_fingerprint']!=src['fingerprint'],inactive=bool(not src or src['blocked']),**d))
    return dict(accounts=accounts(store,company),mappings=mappings(store,company),entries=entries)


def balances(store, company, start, end, currency):
    with store.lock:
        start=dt.date.fromisoformat(start).isoformat();end=dt.date.fromisoformat(end).isoformat()
        if start>end:raise ValueError('The end date must follow the start date.')
        if currency not in ('THB','USD','EUR','GBP','SGD','AUD','CAD','JPY','KWD'):raise ValueError('Choose a supported currency.')
        records=export_records(store,company,dict(start=start,end=end));totals={};included=[];excluded=[]
        for e in records['entries']:
            if e['currency']!=currency:continue
            if e['stale'] or e['inactive']:
                kind=e['source_kind'];id=e['source_id']
                href='#document/'+str(store.db.execute('SELECT invoice_id FROM payments WHERE id=?',(id,)).fetchone()[0]) if kind=='payment' else '#'+{'invoice':'document','bank':'journal','expense':'expenses'}[kind]+'/'+str(id)
                excluded.append(dict(source_kind=kind,source_id=id,href=href,reason='Source is inactive, reversed, duplicate or linked to another entry' if e['inactive'] else 'Source transaction or automatic account mapping changed'))
                continue
            included.append(e)
            for r in e['lines']:
                t=totals.setdefault(r['code'],dict(debit_minor=0,credit_minor=0))
                t['debit_minor']+=r['debit_minor'];t['credit_minor']+=r['credit_minor']
        chart={a['code']:a for a in records['accounts']};rows=[]
        for code,t in sorted(totals.items()):
            net=t['debit_minor']-t['credit_minor']
            rows.append(dict(code=code,name=chart[code]['name'],type=chart[code]['type'],**t,net_debit_minor=max(net,0),net_credit_minor=max(-net,0)))
        totals={key:sum(r[key] for r in rows) for key in ('debit_minor','credit_minor','net_debit_minor','net_credit_minor')}
        return dict(company_id=company,start=start,end=end,currency=currency,accounts=rows,totals=totals,included=len(included),balanced=totals['debit_minor']==totals['credit_minor'] and totals['net_debit_minor']==totals['net_credit_minor'],excluded=excluded)


def balances_csv(report):
    output=io.StringIO();writer=csv.writer(output)
    def cell(value):
        s=str(value)
        return "'"+s if s.lstrip().startswith(('=','+','-','@')) else s
    writer.writerow(['Account code','Account','Type','Currency','Period debit_minor','Period credit_minor','Net debit_minor','Net credit_minor'])
    for r in report['accounts']:writer.writerow([cell(r['code']),cell(r['name']),r['type'],report['currency'],r['debit_minor'],r['credit_minor'],r['net_debit_minor'],r['net_credit_minor']])
    writer.writerow(['TOTAL','','',report['currency'],*[report['totals'][k] for k in ('debit_minor','credit_minor','net_debit_minor','net_credit_minor')]])
    return ('\ufeff'+output.getvalue()).encode()

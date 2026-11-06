"""Company compliance working papers and accountant handoff, not tax submission."""
import base64
import calendar
import csv
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import html
import io
import json
from pathlib import Path
import re
import secrets
import zipfile

from scan import file_type

SCHEMA = '''
CREATE TABLE IF NOT EXISTS compliance_profiles(company_id INTEGER PRIMARY KEY REFERENCES companies(id), data TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS compliance_records(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), year INTEGER NOT NULL, key TEXT NOT NULL, data TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, UNIQUE(company_id,year,key));
CREATE TABLE IF NOT EXISTS compliance_files(id INTEGER PRIMARY KEY, record_id INTEGER NOT NULL REFERENCES compliance_records(id), category TEXT NOT NULL, name TEXT NOT NULL, media_type TEXT NOT NULL, content BLOB NOT NULL, sha256 TEXT NOT NULL, created TEXT NOT NULL);
'''
TABLES = {'compliance_profiles', 'compliance_records', 'compliance_files'}
SOURCES = {
    'rd_calendar': 'https://www.rd.go.th/62348.html',
    'corporate_tax': 'https://www.rd.go.th/english/6044.html',
    'vat': 'https://www.rd.go.th/english/6043.html',
    'filing_and_accounts': 'https://efiling.rd.go.th/rd-cms/ask?question=15',
}
STATUSES = ('unknown', 'preparing', 'needs_review', 'approved', 'filed', 'paid', 'not_applicable')


def text(value, limit=1000, required=False):
    value = str(value or '').strip()
    if len(value) > limit or (required and not value):
        raise ValueError(f'Enter {"1–" if required else "up to "}{limit} characters.')
    return value


def day(value):
    try:
        return dt.date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError):
        raise ValueError('Enter a valid date.')


def integer(value, low, high):
    if isinstance(value, bool) or not re.fullmatch(r'\d+', str(value)) or not low <= int(value) <= high:
        raise ValueError(f'Enter a whole number between {low} and {high}.')
    return int(value)


def money(value):
    try:
        n = Decimal(str(value))
        if not n.is_finite() or n < 0 or n > 999999999 or n.as_tuple().exponent < -2:
            raise ValueError()
        return int(n * 100)
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('Enter a nonnegative THB amount with at most two decimal places.')


def amount(value):
    return format(Decimal(value) / 100, '.2f')


def shift_month(d, months, end_of_month=False):
    total = d.year * 12 + d.month - 1 + months
    year, month = divmod(total, 12)
    last = calendar.monthrange(year, month + 1)[1]
    return dt.date(year, month + 1, last if end_of_month else min(d.day, last))


def profile_data(data):
    if not isinstance(data, dict):raise ValueError('Invalid compliance profile.')
    for key in ('year_end_confirmed', 'vat_registered'):
        if key in data and type(data[key]) is not bool:raise ValueError('Invalid company setting.')
    end = text(data.get('year_end'), 5)
    if end:
        if not re.fullmatch(r'\d{2}-\d{2}', end):raise ValueError('Year-end must be MM-DD, or blank until known.')
        day('2001-' + end)  # Annual year-end must exist in non-leap years too.
    confirmed = data.get('year_end_confirmed') is True
    if confirmed and not end:raise ValueError('Enter the year-end before confirming it.')
    return dict(year_end=end, year_end_confirmed=confirmed,
                employees=integer(data.get('employees', 0), 0, 100000),
                vat_registered=data.get('vat_registered') is True,
                start_year=integer(data.get('start_year', 2026), 2000, 2100),
                accountant=text(data.get('accountant'), 200), notes=text(data.get('notes'), 2000))


def profile(store, company_id):
    company = store.party('companies', company_id)
    row = store.db.execute('SELECT * FROM compliance_profiles WHERE company_id=?', (company_id,)).fetchone()
    if row:return dict(company_id=company['id'], revision=row['revision'], **json.loads(row['data']))
    return dict(company_id=company['id'], revision=0, year_end='', year_end_confirmed=False,
                employees=0, vat_registered=bool(company.get('vat_registered')),
                start_year=dt.date.today().year,
                accountant='', notes='Confirm historical VAT/payroll applicability with your accountant; current settings do not prove earlier obligations.')


def save_profile(store, data):
    with store.lock, store.db:
        previous = profile(store, data.get('company_id'))
        if data.get('revision') != previous['revision']:raise ValueError('Company settings changed. Reload before saving.')
        out = profile_data(data)
        store.db.execute('INSERT INTO compliance_profiles(company_id,data,revision) VALUES(?,?,?) ON CONFLICT(company_id) DO UPDATE SET data=excluded.data,revision=excluded.revision',
                         (previous['company_id'], json.dumps(out, ensure_ascii=False), previous['revision'] + 1))
        store.event('compliance_profile', previous['company_id'], 'Updated compliance settings; year-end ' + (out['year_end'] or 'unconfirmed'))
        return profile(store, previous['company_id'])


def period(p, year):
    year = integer(year, 2000, 2100)
    if not p['year_end']:
        return dict(year=year, start=f'{year}-01-01', end=f'{year}-12-31', provisional=True)
    end = dt.date.fromisoformat(f'{year}-' + p['year_end'])
    start = dt.date.fromisoformat(f'{year-1}-' + p['year_end']) + dt.timedelta(days=1)
    return dict(year=year, start=start.isoformat(), end=end.isoformat(), provisional=not p['year_end_confirmed'])


def tasks(p, year):
    window = period(p, year)
    start = dt.date.fromisoformat(window['start'])
    month = start.replace(day=1)
    out = []
    def add(key, title, kind, tax_period, due='', applicability='Review applicability'):
        out.append(dict(key=key, title=title, kind=kind, period=tax_period, base_due=due,
                        applicability=applicability, status='unknown', revision=0, files=[]))
    # Include every tax month intersecting the fiscal period, even for mid-month year-ends.
    while month.isoformat() <= window['end']:
        m = month.strftime('%Y-%m')
        next_month = shift_month(month, 1)
        for form, title, due_day, applies in [
            ('pp30', 'P.P.30 · VAT', 15, 'Expected from current VAT setting' if p['vat_registered'] else 'Review VAT registration dates'),
            ('pnd3', 'P.N.D.3 · Individual payees', 7, 'If applicable withholding payments occurred'),
            ('pnd53', 'P.N.D.53 · Company payees', 7, 'If applicable withholding payments occurred'),
            ('pnd1', 'P.N.D.1 · Payroll withholding', 7, 'Expected from current payroll setting' if p['employees'] else 'Review whether payroll existed'),
            ('sso', 'Social Security · Payroll', 0, 'Expected from current payroll setting' if p['employees'] else 'Review whether employees existed'),
            ('foreign', 'Overseas payments · P.P.36 / P.N.D.54 review', 0, 'Review foreign software, ads and services'),
        ]:
            # Omit inactive recurring VAT/payroll tasks; saved historical tasks remain visible below.
            if form == 'pp30' and not p['vat_registered']:continue
            if form in ('pnd1', 'sso') and not p['employees']:continue
            add(f'{form}:{m}', title, 'filing', m, next_month.replace(day=due_day).isoformat() if due_day else '', applies)
        month = next_month
    end = dt.date.fromisoformat(window['end'])
    known = bool(p['year_end'])
    half_end = shift_month(start, 6) - dt.timedelta(days=1)
    for key, title, due in [
        ('pnd51', 'P.N.D.51 · Half-year corporate tax', shift_month(half_end, 2, half_end.day == calendar.monthrange(half_end.year, half_end.month)[1]).isoformat() if known else ''),
        ('pnd50', 'P.N.D.50 · Annual corporate tax', (end + dt.timedelta(days=150)).isoformat() if known else ''),
        ('agm', 'AGM · Approve audited accounts', shift_month(end, 4, end.day == calendar.monthrange(end.year, end.month)[1]).isoformat() if known else ''),
        ('dbd', 'DBD · File approved financial statements', ''),
        ('shareholders', 'Shareholder list · After AGM', ''),
        ('payroll_annual', 'Annual payroll returns and certificates · Review', ''),
    ]:
        if key == 'payroll_annual' and not p['employees']:continue
        add(key, title, 'filing', str(year), due)
    for key, title in [
        ('opening', 'Opening balances and prior-year accounts'),
        ('statements', 'All bank accounts and complete statements'),
        ('sales', 'Sales invoices and customer receipts'),
        ('expenses', 'Supplier invoices and expense originals'),
        ('wht_credits', 'Customer withholding certificates and credits'),
        ('assets', 'Fixed assets, depreciation and inventory review'),
        ('adjustments', 'Accruals, related-company balances and tax adjustments'),
        ('accountant', 'Accountant review and closing adjustments'),
        ('audit', 'Signed audited financial statements'),
    ]:
        add('close:' + key, title, 'checklist', str(year))
    return window, out


def calculation(data):
    kind = data.get('kind')
    out = dict(kind=kind, notes=text(data.get('notes'), 2000, True), reviewed=data.get('reviewed') is True)
    if kind == 'vat':
        out['month'] = text(data.get('month'), 7, True)
        if not re.fullmatch(r'\d{4}-\d{2}', out['month']):raise ValueError('Enter a tax month as YYYY-MM.')
        day(out['month'] + '-01')
        for key in ('output_vat', 'eligible_input_vat', 'carry_forward'):
            out[key] = money(data.get(key, '0'))
        net = out['output_vat'] - out['eligible_input_vat'] - out['carry_forward']
        out.update(payable=max(net, 0), credit=max(-net, 0))
    elif kind == 'wht':
        out['excluded'] = data.get('excluded') is True
        out.update(payment_date=day(data.get('payment_date')), payee=text(data.get('payee'), 200, True),
                   tax_id=text(data.get('tax_id'), 80, True), income_category=text(data.get('income_category'), 200, True),
                   source=text(data.get('source'), 200, True), form=data.get('form'))
        if out['form'] not in ('pnd3', 'pnd53', 'pnd54'):raise ValueError('Select a withholding return.')
        out['tax_base'] = money(data.get('tax_base'))
        try:
            rate = Decimal(str(data.get('rate')))
            if not rate.is_finite() or not 0 <= rate <= 100 or rate.as_tuple().exponent < -2:raise ValueError()
        except (ValueError, InvalidOperation):raise ValueError('Enter a withholding rate from 0 to 100, with at most two decimals.')
        out['rate'] = str(rate)
        out['withheld'] = int((Decimal(out['tax_base']) * rate / 100).to_integral_value(rounding=ROUND_HALF_UP))
        out['cash_before_other_adjustments'] = out['tax_base'] - out['withheld']
    elif kind == 'cit':
        out['stage'] = data.get('stage')
        if out['stage'] not in ('half_year', 'annual'):raise ValueError('Select half-year or annual.')
        for key in ('income', 'expenses', 'addbacks', 'deductions', 'losses', 'annual_tax', 'wht_credits', 'half_year_paid'):
            out[key] = money(data.get(key, '0'))
        out['adjusted_profit'] = out['income'] - out['expenses'] + out['addbacks'] - out['deductions'] - out['losses']
        gross = int((Decimal(out['annual_tax']) / 2).to_integral_value(rounding=ROUND_HALF_UP)) if out['stage'] == 'half_year' else out['annual_tax']
        # Credits must be approved for this specific return; never infer them from cash differences.
        credits = out['wht_credits'] + (out['half_year_paid'] if out['stage'] == 'annual' else 0)
        out.update(tax_before_credits=gross, payable=max(0, gross-credits), excess_credits=max(0, credits-gross))
    else:raise ValueError('Select a supported worksheet.')
    return out


def record_data(data):
    if not isinstance(data, dict):raise ValueError('Invalid compliance record.')
    kind = data.get('kind')
    if kind in ('vat', 'wht', 'cit'):return calculation(data)
    if kind not in ('filing', 'checklist', 'document'):raise ValueError('Select a valid compliance record.')
    status = data.get('status', 'unknown')
    allowed = STATUSES if kind == 'filing' else ('unknown', 'requested', 'received', 'reviewed', 'not_applicable')
    if status not in allowed:raise ValueError('Select a valid status.')
    out = dict(kind=kind, title=text(data.get('title'), 200, True), period=text(data.get('period'), 20, True), status=status,
               owner=text(data.get('owner'), 200), notes=text(data.get('notes'), 2000), verified_due='', filed_on='', paid_on='', reference='')
    for key in ('verified_due', 'filed_on', 'paid_on'):
        if data.get(key):out[key] = day(data[key])
    out['reference'] = text(data.get('reference'), 200)
    for key in ('declared_amount', 'paid_amount'):
        out[key] = amount(money(data[key])) if data.get(key) not in (None, '') else ''
    if status == 'not_applicable' and not out['notes']:raise ValueError('Explain why this item does not apply.')
    if status in ('filed', 'paid') and (not out['filed_on'] or not out['reference']):raise ValueError('Add the filing date and acceptance reference.')
    if status == 'paid' and not out['paid_on']:raise ValueError('Add the actual payment date.')
    if out['filed_on'] and out['filed_on'] > dt.date.today().isoformat() or out['paid_on'] and out['paid_on'] > dt.date.today().isoformat():raise ValueError('Actual filing/payment dates cannot be in the future.')
    return out


def save_record(store, data):
    company_id = integer(data.get('company_id'), 1, 2147483647)
    year = integer(data.get('year'), 2000, 2100)
    key = text(data.get('key'), 100)
    item = record_data(data)
    with store.lock, store.db:
        p = profile(store, company_id)
        window, defaults = tasks(p, year)
        if not key:key = ('wht:' if item['kind'] == 'wht' else 'doc:') + secrets.token_hex(12)
        old = store.db.execute('SELECT * FROM compliance_records WHERE company_id=? AND year=? AND key=?', (company_id, year, key)).fetchone()
        if data.get('revision', 0) != (old['revision'] if old else 0):raise ValueError('This record changed. Reload before saving.')
        existing = json.loads(old['data']) if old else None
        expected = next((x for x in defaults if x['key'] == key), None)
        if expected:
            if item['kind'] != expected['kind']:raise ValueError('Record type does not match this task.')
            item.update(title=expected['title'], period=expected['period'])
        elif item['kind'] in ('filing', 'checklist') and not (existing and existing['kind'] == item['kind']):
            raise ValueError('Unknown compliance task.')
        if item['kind'] == 'vat' and key != 'vat:' + item['month']:raise ValueError('VAT worksheet key must match its tax month.')
        if item['kind'] == 'cit' and key != 'cit:' + item['stage']:raise ValueError('Corporate worksheet key must match its stage.')
        if item['kind'] == 'wht' and not key.startswith('wht:') or item['kind'] == 'document' and not key.startswith('doc:'):raise ValueError('Invalid record key.')
        if existing and existing['kind'] != item['kind']:raise ValueError('Record type cannot be changed.')
        if item['kind'] in ('vat', 'wht'):
            month = item.get('month') or item['payment_date'][:7]
            if not window['start'][:7] <= month <= window['end'][:7]:raise ValueError('This worksheet belongs to a different accounting year.')
        if item['kind'] == 'wht' and not window['start'] <= item['payment_date'] <= window['end']:raise ValueError('Payment date falls outside this accounting period.')
        item['_year_end'] = p['year_end']
        store.db.execute('INSERT INTO compliance_records(company_id,year,key,data) VALUES(?,?,?,?) ON CONFLICT(company_id,year,key) DO UPDATE SET data=excluded.data,revision=compliance_records.revision+1', (company_id, year, key, json.dumps(item, ensure_ascii=False)))
        row = store.db.execute('SELECT id,revision FROM compliance_records WHERE company_id=? AND year=? AND key=?', (company_id, year, key)).fetchone()
        store.event('compliance_record', row['id'], f'{company_id} / {year} / {key}')
        return dict(id=row['id'], revision=row['revision'], key=key, **item)


def attach(store, data):
    record_id = integer(data.get('record_id'), 1, 2147483647)
    category = data.get('category')
    if category not in ('supporting', 'filing_acceptance', 'tax_payment'):raise ValueError('Select a document category.')
    try:content = base64.b64decode(data.get('content', ''), validate=True)
    except (TypeError, ValueError):raise ValueError('Invalid file encoding.')
    if not 0 < len(content) <= 20*1024*1024:raise ValueError('Choose a PDF, PNG or JPEG up to 20 MB.')
    media = {'pdf':'application/pdf', 'png':'image/png', 'jpg':'image/jpeg'}[file_type(content)]
    name = text(Path(str(data.get('name', ''))).name, 200, True)
    with store.lock, store.db:
        if not store.db.execute('SELECT 1 FROM compliance_records WHERE id=?', (record_id,)).fetchone():raise ValueError('Save the record before attaching documents.')
        id = store.db.execute('INSERT INTO compliance_files(record_id,category,name,media_type,content,sha256,created) VALUES(?,?,?,?,?,?,?)',
                              (record_id, category, name, media, content, hashlib.sha256(content).hexdigest(), dt.datetime.now(dt.timezone.utc).isoformat())).lastrowid
        store.event('compliance_document', record_id, category + ': ' + name)
        return dict(id=id)


def workspace(store, company_id, year):
    with store.lock:
        p = profile(store, company_id)
        window, defaults = tasks(p, year)
        records = {}
        for r in store.db.execute('SELECT * FROM compliance_records WHERE company_id=? AND year=? ORDER BY id', (company_id, window['year'])):
            records[r['key']] = dict(id=r['id'], key=r['key'], revision=r['revision'], **json.loads(r['data']), files=[])
        for f in store.db.execute('SELECT f.id,f.record_id,f.category,f.name,f.media_type,f.created FROM compliance_files f JOIN compliance_records r ON r.id=f.record_id WHERE r.company_id=? AND r.year=? ORDER BY f.id', (company_id, window['year'])):
            for r in records.values():
                if r['id'] == f['record_id']:r['files'].append(dict(f));break
        merged = {t['key']:dict(t, **records.get(t['key'], {})) for t in defaults}
        merged.update({k:v for k,v in records.items() if k not in merged})
        items = list(merged.values())
        for item in items:
            if '_year_end' in item and item['_year_end'] != p['year_end'] and (item['kind'] == 'cit' or item['kind'] == 'checklist' or item.get('period') == str(year)):
                item['period_review_required'] = True
        for task in items:
            if task['kind'] != 'filing':continue
            form, _, month = task['key'].partition(':')
            linked = []
            if form in ('pnd3', 'pnd53'):
                linked = [r for r in items if r['kind'] == 'wht' and r['form'] == form and r['payment_date'].startswith(month) and not r.get('excluded')]
            elif form == 'pp30':linked = [r for r in items if r['kind'] == 'vat' and r['month'] == month]
            elif form in ('pnd50','pnd51'):linked = [r for r in items if r['kind'] == 'cit' and r['stage'] == ('annual' if form == 'pnd50' else 'half_year')]
            if linked:
                task['working_amount_minor'] = sum(r['withheld'] if r['kind'] == 'wht' else r['payable'] for r in linked)
                task['working_reviewed'] = all(r['reviewed'] and not r.get('period_review_required') for r in linked)
        source = source_records(store, company_id, window)
        missing = []
        for e in source['bank_entries']:
            reasons = []
            if not e['reviewed']:reasons.append('Transaction needs classification/review')
            if not e['documents'] and not e.get('matched_record'):reasons.append('Supporting document missing')
            if reasons:missing.append(dict(source='bank', id=e['id'], date=e['date'], title=e['counterparty'] or e['description'], reason='; '.join(reasons), href='#journal/' + str(e['id'])))
        for i in source['invoices']:
            for pay in i['payments']:
                if pay['reversed'] or not window['start'] <= pay['data']['date'] <= window['end']:continue
                if pay['data'].get('withholding_minor', 0) and not any(a['category'] == 'withholding_certificate' and a['payment_id'] == pay['id'] for a in i['attachments']):
                    missing.append(dict(source='payment', id=pay['id'], date=pay['data']['date'], title=i['data']['contact']['name'], reason='Customer withholding certificate missing or not linked to this payment', href='#document/' + str(i['id'])))
        for r in items:
            if r.get('period_review_required'):
                missing.append(dict(source='period', id=r['id'], date=str(year), title=r.get('title', 'Corporate tax worksheet'), reason='Year-end changed since this record was saved; review period and deadlines', key=r['key']))
            saved_month = r.get('month') or r.get('payment_date', '')[:7]
            if saved_month and not window['start'][:7] <= saved_month <= window['end'][:7]:
                missing.append(dict(source='worksheet', id=r['id'], date=saved_month, title=r['kind'].upper() + ' worksheet', reason='Saved month falls outside the revised accounting period; accountant review needed', key=r['key']))
            if r['kind'] == 'document' and r['status'] not in ('reviewed', 'not_applicable'):
                missing.append(dict(source='request', id=r['id'], date=r['period'], title=r['title'], reason=r['status'].replace('_', ' '), key=r['key']))
            if r['kind'] == 'filing' and r['status'] in ('filed', 'paid'):
                categories = {f['category'] for f in r['files']}
                reason = []
                if 'filing_acceptance' not in categories:reason.append('Filing acceptance evidence missing')
                if r['status'] == 'paid' and 'tax_payment' not in categories:reason.append('Payment receipt missing')
                if reason:missing.append(dict(source='filing', id=r['id'], date=r['period'], title=r['title'], reason='; '.join(reason), key=r['key']))
        candidates = []
        for i in source['invoices']:
            if i['kind'] == 'sale' and i['status'] == 'issued' and i['currency'] == 'THB' and window['start'] <= i['data']['issue_date'] <= window['end']:
                candidates.append(dict(id=i['id'], date=i['data']['issue_date'], number=i['number'], customer=i['data']['contact']['name'], subtotal_minor=i['data'].get('subtotal_minor', i['total_minor']), vat_minor=i['data'].get('vat_minor', 0), tax_invoice=bool(i['data'].get('tax_invoice'))))
        return dict(company=store.party('companies', company_id), profile=p, window=window, items=items, missing=missing,
                    candidates=candidates, counts={k:len(source[k]) for k in ('invoices', 'journal', 'bank_entries')}, sources=SOURCES)


def source_records(store, company_id, window):
    def inside(date):return window['start'] <= (date or '') <= window['end']
    invoices = [store.invoice(r['id']) for r in store.db.execute('SELECT id FROM invoices WHERE company_id=? ORDER BY id', (company_id,))]
    bank=[e for e in store.bank_entries() if e['company_id']==company_id and inside(e['date'])]
    expenses=[e for e in store.journal() if e['company_id']==company_id]
    selected={e['id'] for e in expenses if inside(e['date']) or inside(e.get('payment_date'))}
    selected.update(e['linked_expense_id'] for e in bank if e.get('linked_expense_id'))
    selected.update(e['duplicate_of'] for e in expenses if e['id'] in selected and e.get('duplicate_of'))
    journal=[e for e in expenses if e['id'] in selected or e.get('duplicate_of') in selected]
    return dict(invoices=[i for i in invoices if inside(i['data']['issue_date']) or any(inside(p['data']['date']) for p in i['payments'])],journal=journal,bank_entries=bank)


def validate_backup(db, tables):
    if not TABLES & tables:return
    if not TABLES <= tables:raise ValueError('Incomplete compliance tables in backup.')
    for data, revision in db.execute('SELECT data,revision FROM compliance_profiles'):
        profile_data(json.loads(data));integer(revision, 1, 2147483647)
    for year, key, data, revision in db.execute('SELECT year,key,data,revision FROM compliance_records'):
        integer(year, 2000, 2100);text(key, 100, True);integer(revision, 1, 2147483647)
        item = json.loads(data)
        if '_year_end' in item:
            profile_data(dict(year_end=item['_year_end']))
        stored = {k:v for k,v in item.items() if k != '_year_end'}
        # Stored money is in minor units; validate by recalculating from display units.
        if item.get('kind') in ('vat', 'wht', 'cit'):
            inputs = dict(item)
            fields = {'vat':('output_vat','eligible_input_vat','carry_forward'), 'wht':('tax_base',), 'cit':('income','expenses','addbacks','deductions','losses','annual_tax','wht_credits','half_year_paid')}[item['kind']]
            for field in fields:
                if type(item.get(field)) is not int:raise ValueError('Invalid worksheet amount.')
                inputs[field] = amount(item[field])
            if calculation(inputs) != stored:raise ValueError('Inconsistent worksheet calculation in backup.')
        else:record_data(item)
    for content, checksum, category, media in db.execute('SELECT content,sha256,category,media_type FROM compliance_files'):
        if not 0 < len(content) <= 20*1024*1024 or hashlib.sha256(content).hexdigest() != checksum or category not in ('supporting','filing_acceptance','tax_payment'):raise ValueError('Damaged compliance document.')
        if {'pdf':'application/pdf','png':'image/png','jpg':'image/jpeg'}[file_type(content)] != media:raise ValueError('Invalid compliance document type.')


def export_pack(store, company_id, year):
    with store.lock:
        work = workspace(store, company_id, year)
        source = source_records(store, company_id, work['window'])
        buffer = io.BytesIO()
        manifest = []
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as z:
            def write(path, content, source_type='', source_id=''):
                if isinstance(content, str):content = content.encode('utf-8')
                z.writestr(path, content)
                manifest.append([path, source_type, source_id, len(content), hashlib.sha256(content).hexdigest()])
            def csv_file(path, rows):
                stream = io.StringIO();writer = csv.writer(stream)
                for row in rows:
                    writer.writerow([("'" + str(v)) if str(v).lstrip().startswith(('=','+','-','@')) and not re.fullmatch(r'-\d+(\.\d+)?', str(v)) else str(v) for v in row])
                write(path, '\ufeff' + stream.getvalue())
            def filename(name):return re.sub(r'[^\w. -]', '_', Path(name).name)[:160] or 'document'
            write('README.txt', f"LocalBooks accountant review pack\nCompany: {work['company']['name']}\nPeriod: {work['window']['start']} to {work['window']['end']}\nYear-end confirmed: {work['profile']['year_end_confirmed']}\nGenerated: {dt.datetime.now(dt.timezone.utc).isoformat()}\n\nWorking papers only. No tax return has been submitted by this app. Unknown means unverified, not unfiled. Baseline dates exclude online extensions and holiday adjustments. Confirm applicability, tax points, eligibility, credits and rates with the accountant. Worksheets do not automatically reconcile or post journal entries.\n\nIf year-end is blank, records use a provisional calendar year. If it changes, review both old and new year exports; saved tasks and worksheets are retained under the year where entered. Monthly returns follow full calendar months, even when a fiscal period starts mid-month. Current VAT and payroll settings are not historical evidence.\n\nrecords.json contains complete selected records. CSV monetary source columns explicitly ending in _minor are integer currency units (THB: divide by 100; JPY: 1; KWD: 1000). Worksheet amount columns use THB. Source bank statements and invoice/payment histories may include dates outside the selected period as context, but all records belong to this company. Draft invoices without saved PDF are included as JSON, not rendered as final invoices.\n")
            write('records.json', json.dumps(dict(workspace=work, records=source), ensure_ascii=False, indent=2))
            csv_file('filings-and-close.csv', [['Key','Kind','Period','Title','Status','Owner','Verified due','Base due','Filed on','Paid on','Reference','Declared amount THB','Paid amount THB','Notes']] + [[r['key'],r['kind'],r.get('period',''),r.get('title',''),r.get('status',''),r.get('owner',''),r.get('verified_due',''),r.get('base_due',''),r.get('filed_on',''),r.get('paid_on',''),r.get('reference',''),r.get('declared_amount',''),r.get('paid_amount',''),r.get('notes','')] for r in work['items'] if r['kind'] in ('filing','checklist','document')])
            csv_file('missing-documents.csv', [['Source','ID','Period / date','Title','Reason']] + [[r['source'],r['id'],r['date'],r['title'],r['reason']] for r in work['missing']])
            csv_file('sales-and-purchases.csv', [['ID','Kind','Number','Status','Date','Currency','Subtotal_minor','VAT_minor','Total_minor','Contact']] + [[i['id'],i['kind'],i['number'] or '',i['status'],i['data']['issue_date'],i['currency'],i['data'].get('subtotal_minor',''),i['data'].get('vat_minor',0),i['total_minor'],i['data']['contact']['name']] for i in source['invoices']])
            csv_file('payments-and-customer-wht.csv', [['ID','Invoice ID','Number','Date','Currency','Cash_minor','Withheld_minor','Reversed']] + [[p['id'],i['id'],p['number'],p['data']['date'],i['currency'],p['amount_minor'],p['data'].get('withholding_minor',0),p['reversed'] or ''] for i in source['invoices'] for p in i['payments']])
            csv_file('expense-journal.csv', [['ID','Date','Supplier','Supplier tax ID','Document number','Currency','Amount_minor','Payment status','Payment date','Cash paid_minor','Description','Linked bank IDs','Reviewed bank cash_minor','Unmatched manual cash_minor']] + [[e['id'],e['date'],e['supplier'],e.get('supplier_tax_id',''),e.get('document_number',''),e['currency'],e['amount_minor'],e['payment_status'],e.get('payment_date',''),e.get('paid_amount_minor',''),e.get('description',''),';'.join(str(b['id']) for b in e.get('linked_banks',[])),e.get('linked_cash_minor',0),e.get('unmatched_cash_minor',0)] for e in source['journal'] if not e.get('duplicate_of')])
            csv_file('duplicate-expense-documents.csv', [['Document ID','Primary expense ID','Document number','Original file']] + [[e['id'],e['duplicate_of'],e.get('document_number',''),e['name']] for e in source['journal'] if e.get('duplicate_of')])
            reviewed_documents=[['Record type','Record ID','Bank date','Bank amount_minor','File','Name on document','Tax ID','Document date','Document number','Document total','Document currency','Document type','Description','Reviewed at']]
            for kind,records in [('expense',source['journal']),('bank',source['bank_entries'])]:
                for record in records:
                    for doc in record.get('shared_documents',record.get('documents',[])):
                        review=doc.get('review')
                        if review:reviewed_documents.append([kind,record['id'],record['date'] if kind=='bank' else '',record['amount_minor'] if kind=='bank' else '',doc['name'],review.get('supplier',''),review.get('supplier_tax_id',''),review.get('date',''),review.get('document_number',''),review.get('amount',''),review.get('currency',''),review.get('document_type',''),review.get('description',''),review.get('reviewed_at','')])
            csv_file('document-reviews.csv',reviewed_documents)
            csv_file('bank-transactions.csv', [['ID','Statement ID','Account','Date','Currency','Direction','Amount_minor','Counterparty','Description','Category','Reviewed','Matched record','Notes']] + [[e['id'],e['statement_id'],e['account'],e['date'],e['currency'],e['direction'],e['amount_minor'],e['counterparty'],e['description'],e['category'],e['reviewed'],e['matched_record'],e['notes']] for e in source['bank_entries']])
            worksheet_rows = [['Key','Worksheet','Field','Value','Unit','Reviewed','Source / assumptions']]
            money_fields = {'output_vat','eligible_input_vat','carry_forward','payable','credit','tax_base','withheld','cash_before_other_adjustments','income','expenses','addbacks','deductions','losses','annual_tax','wht_credits','half_year_paid','adjusted_profit','tax_before_credits','excess_credits'}
            for r in work['items']:
                if r['kind'] in ('vat','wht','cit'):
                    for key, value in r.items():
                        if key in ('files','notes','reviewed','id','revision','key','kind','_year_end'):continue
                        worksheet_rows.append([r['key'],r['kind'],key,amount(value) if key in money_fields else value,'THB' if key in money_fields else '',r['reviewed'],r['notes']])
                for f in r['files']:
                    raw = store.db.execute('SELECT content FROM compliance_files WHERE id=?', (f['id'],)).fetchone()[0]
                    write(f"compliance/{r['id']}/{f['id']}-{filename(f['name'])}", raw, 'compliance_file', f['id'])
            csv_file('calculation-worksheets.csv', worksheet_rows)
            csv_file('supplier-withholding-schedule.csv', [['Key','Payment date','Return','Payee','Tax ID','Income category','Source reference','Tax base THB','Rate percent','Withheld THB','Reviewed','Excluded from totals','Notes']] + [[r['key'],r['payment_date'],r['form'],r['payee'],r['tax_id'],r['income_category'],r['source'],amount(r['tax_base']),r['rate'],amount(r['withheld']),r['reviewed'],r['excluded'],r['notes']] for r in work['items'] if r['kind']=='wht'])
            csv_file('vat-working-papers.csv', [['Tax month','Output VAT THB','Eligible input VAT THB','Credit brought forward THB','Payable THB','Credit working balance THB','Reviewed','Notes']] + [[r['month'],amount(r['output_vat']),amount(r['eligible_input_vat']),amount(r['carry_forward']),amount(r['payable']),amount(r['credit']),r['reviewed'],r['notes']] for r in work['items'] if r['kind']=='vat'])
            for i in source['invoices']:
                row = store.db.execute('SELECT pdf,html FROM invoices WHERE id=?', (i['id'],)).fetchone()
                if row['pdf']:write(f"invoices/{i['id']}.pdf", row['pdf'], 'invoice', i['id'])
                if row['html']:write(f"invoices/{i['id']}.html", row['html'], 'invoice', i['id'])
                for p in i['payments']:
                    raw = store.db.execute('SELECT pdf FROM payments WHERE id=?', (p['id'],)).fetchone()[0]
                    if raw:write(f"receipts/{p['id']}.pdf", raw, 'payment', p['id'])
                for a in i['attachments']:
                    raw = store.db.execute('SELECT content FROM attachments WHERE id=?', (a['id'],)).fetchone()[0]
                    write(f"invoice-attachments/{a['id']}-{filename(a['name'])}", raw, 'attachment', a['id'])
            for e in source['journal']:
                raw = store.db.execute('SELECT content FROM journal_entries WHERE id=?', (e['id'],)).fetchone()[0]
                write(f"expenses/{e['id']}-{filename(e['name'])}", raw, 'expense', e['id'])
                for doc in store.db.execute('SELECT id,name,content FROM journal_attachments WHERE journal_id=?',(e['id'],)):
                    write(f"expense-documents/{doc['id']}-{filename(doc['name'])}",doc['content'],'expense_attachment',doc['id'])
            statements = set()
            for e in source['bank_entries']:
                if e['statement_id'] not in statements:
                    raw = store.db.execute('SELECT content FROM bank_statements WHERE id=?', (e['statement_id'],)).fetchone()[0]
                    write(f"bank-statements/{e['statement_id']}-{filename(e['name'])}", raw, 'bank_statement', e['statement_id']);statements.add(e['statement_id'])
                for a in e['documents']:
                    raw = store.db.execute('SELECT content FROM bank_attachments WHERE id=?', (a['id'],)).fetchone()[0]
                    write(f"bank-documents/{a['id']}-{filename(a['name'])}", raw, 'bank_document', a['id'])
            escape = lambda value:html.escape(str(value))
            rows = ''.join('<tr>' + ''.join('<td>' + escape(v) + '</td>' for v in [r.get('period',''),r.get('title',r['kind']),r.get('status','Reviewed' if r.get('reviewed') else 'Working paper'),r.get('owner',''),r.get('verified_due') or r.get('base_due',''),r.get('notes','')]) + '</tr>' for r in work['items'])
            write('summary.html', '<!doctype html><html><meta charset="utf-8"><title>Accountant review pack</title><style>body{font:14px Arial;margin:32px;color:#222}td,th{border:1px solid #ccc;padding:8px;text-align:left}table{border-collapse:collapse;width:100%}p{max-width:900px}</style><h1>' + escape(work['company']['name']) + ' · Accountant review</h1><p>' + escape(work['window']['start'] + ' to ' + work['window']['end']) + '</p><p>Working papers only. Confirm year-end, applicability, rates, tax points and credits. Baseline dates exclude extensions and holiday adjustments. See missing-documents.csv and README.txt.</p><table><tr><th>Period</th><th>Task</th><th>Status</th><th>Owner</th><th>Due / baseline</th><th>Notes</th></tr>' + rows + '</table></html>')
            csv_file('manifest.csv', [['Path','Source type','Source ID','Bytes','SHA256']] + manifest.copy())
        return buffer.getvalue()

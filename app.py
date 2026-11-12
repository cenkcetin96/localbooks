#!/usr/bin/env python3
"""LocalBooks: loopback-only, standard-library invoice and receipt manager."""
import argparse
from contextlib import contextmanager
import base64
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import fcntl
import hashlib
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import csv
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import unicodedata
import urllib.parse
import webbrowser

from documents import render_document, make_pdf
from scan import scan_document, file_type, ScanError
from statements import scan_statement,match_key,csv_text,clean_bank_description,MAX_ROWS
import compliance
import reconciliation

ROOT = Path(__file__).resolve().parent
MAX_FILE = 20 * 1024 * 1024
DOCUMENT_COMPANY_FIELDS = ('signature','bank_name','bank_account_name','bank_account_number','bank_branch','bank_swift')
CURRENCIES = {'THB': 2, 'USD': 2, 'EUR': 2, 'GBP': 2, 'SGD': 2, 'AUD': 2, 'CAD': 2, 'JPY': 0, 'KWD': 3}


class ValidationError(Exception):
    pass


def text(value, name, limit=500, required=False):
    value = str(value or '').strip()
    if (required and not value) or len(value) > limit:
        raise ValidationError(f'{name}: enter {"1–" if required else "up to "}{limit} characters.')
    return value


def identity_key(value):
    normalized=unicodedata.normalize('NFKC',str(value or '')).casefold()
    return ''.join(c for c in normalized if unicodedata.category(c)[0] in 'LMN')

def date(value, name):
    try:
        return dt.date.fromisoformat(str(value)).isoformat()
    except (ValueError, TypeError):
        raise ValidationError(f'{name}: use a valid date.')


def decimal(value, name, places=6):
    try:
        n = Decimal(str(value))
        if not n.is_finite() or n < 0 or n > Decimal('999999999') or n.as_tuple().exponent < -places:
            raise ValueError()
        return n
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError(f'{name}: enter a nonnegative number with at most {places} decimal places.')


def amount_minor(value, currency):
    places = CURRENCIES[currency]
    n = decimal(value, 'Amount', places)
    return int((n * (10 ** places)).to_integral_value(rounding=ROUND_HALF_UP))


def calculate(lines, currency):
    if currency not in CURRENCIES:
        raise ValidationError('Select a supported currency.')
    if not isinstance(lines, list) or not 1 <= len(lines) <= 100:
        raise ValidationError('Add between 1 and 100 items.')
    result = []
    scale = 10 ** CURRENCIES[currency]
    for item in lines:
        description = text(item.get('description'), 'Description', 1200, True)
        service = text(item.get('service'), 'Service period', 200)
        unit = text(item.get('unit'), 'Unit', 40)
        code = text(item.get('code'), 'Item code', 80)
        qty = decimal(item.get('quantity'), 'Quantity')
        price = decimal(item.get('unit_price'), 'Unit price', CURRENCIES[currency])
        if qty <= 0:
            raise ValidationError('Quantity must be greater than zero.')
        total = int((qty * price * scale).to_integral_value(rounding=ROUND_HALF_UP))
        if total > 999999999999:
            raise ValidationError('Line total is too large.')
        result.append(dict(description=description, service=service, unit=unit, code=code, quantity=str(qty), unit_price=str(price), total_minor=total))
    total = sum(i['total_minor'] for i in result)
    if total <= 0:
        raise ValidationError('The document total must be greater than zero.')
    return result, total


def calculate_vat(subtotal, rate):
    if rate in (None,''):return '',0
    value=decimal(rate,'VAT rate',2)
    if value>100:raise ValidationError('VAT rate cannot exceed 100%.')
    return str(value),int((Decimal(subtotal)*value/100).to_integral_value(rounding=ROUND_HALF_UP))


SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT OR IGNORE INTO metadata VALUES('format','localbooks-v1');
INSERT OR IGNORE INTO metadata VALUES('database_id',lower(hex(randomblob(16))));
CREATE TABLE IF NOT EXISTS companies(id INTEGER PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS contacts(id INTEGER PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS invoices(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), contact_id INTEGER NOT NULL REFERENCES contacts(id), kind TEXT NOT NULL CHECK(kind IN ('sale','purchase')), status TEXT NOT NULL CHECK(status IN ('draft','issued','void')), number TEXT, data TEXT NOT NULL, total_minor INTEGER NOT NULL, currency TEXT NOT NULL, html TEXT, pdf BLOB, created TEXT NOT NULL, issued TEXT, void_reason TEXT, revision INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS numbers(company_id INTEGER NOT NULL REFERENCES companies(id), number TEXT NOT NULL, PRIMARY KEY(company_id,number));
CREATE TABLE IF NOT EXISTS counters(company_id INTEGER NOT NULL REFERENCES companies(id), prefix TEXT NOT NULL, year TEXT NOT NULL, next INTEGER NOT NULL, PRIMARY KEY(company_id,prefix,year));
CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES invoices(id), number TEXT NOT NULL, amount_minor INTEGER NOT NULL CHECK(amount_minor>0), data TEXT NOT NULL, html TEXT, pdf BLOB, created TEXT NOT NULL, reversed TEXT, reverse_reason TEXT, request_key TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS attachments(id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES invoices(id), payment_id INTEGER REFERENCES payments(id), name TEXT NOT NULL, media_type TEXT NOT NULL, content BLOB NOT NULL, sha256 TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, created TEXT NOT NULL, action TEXT NOT NULL, entity_id INTEGER, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS journal_entries(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), data TEXT NOT NULL, name TEXT NOT NULL, media_type TEXT NOT NULL, content BLOB NOT NULL, sha256 TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS journal_attachments(id INTEGER PRIMARY KEY, journal_id INTEGER NOT NULL REFERENCES journal_entries(id), name TEXT NOT NULL, media_type TEXT NOT NULL, content BLOB NOT NULL, sha256 TEXT NOT NULL, created TEXT NOT NULL, UNIQUE(journal_id,sha256));
CREATE TABLE IF NOT EXISTS bank_statements(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), account TEXT NOT NULL, currency TEXT NOT NULL, name TEXT NOT NULL, media_type TEXT NOT NULL, content BLOB NOT NULL, sha256 TEXT NOT NULL, created TEXT NOT NULL, UNIQUE(company_id,account,sha256));
CREATE TABLE IF NOT EXISTS bank_entries(id INTEGER PRIMARY KEY, statement_id INTEGER NOT NULL REFERENCES bank_statements(id), row_key TEXT NOT NULL, match_key TEXT NOT NULL, data TEXT NOT NULL, original_data TEXT NOT NULL, created TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, UNIQUE(statement_id,row_key));
CREATE TABLE IF NOT EXISTS bank_attachments(id INTEGER PRIMARY KEY, entry_id INTEGER NOT NULL REFERENCES bank_entries(id), name TEXT NOT NULL, media_type TEXT NOT NULL, content BLOB NOT NULL, sha256 TEXT NOT NULL, created TEXT NOT NULL, UNIQUE(entry_id,sha256));
CREATE INDEX IF NOT EXISTS bank_entry_match ON bank_entries(match_key);
CREATE INDEX IF NOT EXISTS bank_entry_cash ON bank_entries(json_extract(data,'$.date'),json_extract(data,'$.direction'),json_extract(data,'$.amount_minor'),json_extract(data,'$.balance_minor'));
'''

SCHEMA += compliance.SCHEMA


class Store:
    def __init__(self, directory, pdf_renderer=make_pdf):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'localbooks.sqlite3'
        self.lock = threading.RLock()
        self.document_scans = {}
        self.pdf_renderer = pdf_renderer
        self.db = self.connect()
        self.db.executescript(SCHEMA)
        self.db.commit()
        os.chmod(self.path, 0o600)
        self.clean_bank_descriptions()

    def connect(self):
        db = sqlite3.connect(self.path, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA journal_mode=DELETE')
        return db

    @contextmanager
    def transaction(self):
        with self.lock:
            if self.db.in_transaction:
                yield
            else:
                with self.db:
                    self.db.execute('BEGIN IMMEDIATE')
                    yield

    def event(self, action, entity, detail=''):
        self.db.execute('INSERT INTO events(created,action,entity_id,detail) VALUES(?,?,?,?)', (dt.datetime.now(dt.timezone.utc).isoformat(), action, entity, detail))

    def party(self, table, id):
        row = self.db.execute(f'SELECT * FROM {table} WHERE id=?', (id,)).fetchone()
        if not row:
            raise ValidationError('Record not found.')
        out=dict(id=row['id'], **json.loads(row['data']))
        if table=='contacts':out['role']=self.contact_role(out);out['company_ids']=self.contact_company_ids(out)
        return out

    def company_ids(self,values):
        valid={r[0] for r in self.db.execute('SELECT id FROM companies')}
        if not isinstance(values,list) or not values or any(type(v) is not int or v not in valid for v in values):raise ValidationError('Choose at least one saved company.')
        return sorted(set(values))

    def contact_company_ids(self,contact):
        linked={r[0] for r in self.db.execute('SELECT company_id FROM invoices WHERE contact_id=?',(contact['id'],))}
        linked.update(r[0] for r in self.db.execute("SELECT company_id FROM journal_entries WHERE json_extract(data,'$.supplier_id')=?",(contact['id'],)))
        owners=set(contact.get('company_ids',[]))|linked
        first=self.db.execute('SELECT id FROM companies ORDER BY id LIMIT 1').fetchone()
        return sorted(owners) if owners else [first[0]] if first else []

    def contact_role(self, contact):
        if 'role' in contact:return contact['role']
        # Older backups have no role; infer it without rewriting their records.
        supplier=self.db.execute("SELECT 1 FROM journal_entries WHERE json_extract(data,'$.supplier_id')=? UNION SELECT 1 FROM invoices WHERE contact_id=? AND kind='purchase' LIMIT 1",(contact['id'],contact['id'])).fetchone()
        customer=self.db.execute("SELECT 1 FROM invoices WHERE contact_id=? AND kind='sale' LIMIT 1",(contact['id'],)).fetchone()
        return 'both' if supplier and customer else 'supplier' if supplier else 'customer'

    def contacts(self, role=None, company_id=None):
        records=[dict(id=r['id'],**json.loads(r['data'])) for r in self.db.execute('SELECT id,data FROM contacts ORDER BY id')]
        for record in records:record['role']=self.contact_role(record);record['company_ids']=self.contact_company_ids(record)
        return [r for r in records if (role is None or r['role'] in (role,'both')) and (company_id is None or company_id in r['company_ids'])]

    def save_party(self, table, data):
        if table not in ('companies', 'contacts'):
            raise ValidationError('Invalid record type.')
        out = {k:text(data.get(k), k.replace('_',' ').title(), n, k=='name') for k,n in [('name',200),('legal_name',200),('address',1200),('email',200),('tax_label',40),('tax_id',80),('phone',80),('contact_name',200),('contact_phone',80),('logo_text',8),('branch',100),('payment_instructions',500)]}
        out['vat_registered']=(data.get('vat_registered') in (True,'on')) if 'vat_registered' in data else bool(self.party(table,data['id']).get('vat_registered')) if data.get('id') else False
        if table=='contacts':
            out['role']=data.get('role',self.party(table,data['id'])['role'] if data.get('id') else 'customer')
            if out['role'] not in ('customer','supplier','both'):raise ValidationError('Choose Customer, Supplier, or Both.')
            if 'company_ids' in data:out['company_ids']=self.company_ids(data['company_ids'])
            elif data.get('company_id'):out['company_ids']=self.company_ids([data['company_id']])
            elif data.get('id'):
                previous=json.loads(self.db.execute('SELECT data FROM contacts WHERE id=?',(data['id'],)).fetchone()[0])
                if 'company_ids' in previous:out['company_ids']=previous['company_ids']
        if table == 'companies':
            previous=self.party(table,data['id']) if data.get('id') else {}
            out['invoice_number_format']=data.get('invoice_number_format',previous.get('invoice_number_format','annual'))
            if out['invoice_number_format'] not in ('annual','monthly'):
                raise ValidationError('Choose annual or monthly invoice numbering.')
            for key,limit in [('bank_name',120),('bank_account_name',200),('bank_account_number',80),('bank_branch',120),('bank_swift',11)]:
                out[key]=text(data.get(key,previous.get(key,'')),key.replace('_',' ').title(),limit)
            out['bank_swift']=out['bank_swift'].upper()
            if out['bank_swift'] and not re.fullmatch(r'[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?',out['bank_swift']):
                raise ValidationError('SWIFT/BIC must be 8 or 11 letters and numbers, with a valid bank and country prefix.')
            if any(out[k] for k in DOCUMENT_COMPANY_FIELDS[1:]) and not all(out[k] for k in ('bank_name','bank_account_name','bank_account_number')):
                raise ValidationError('Enter the bank name, account holder and account number, or leave all bank fields blank.')
            out['code'] = text(data.get('code'), 'Company code', 12, True).upper()
            if not re.fullmatch('[A-Z0-9-]+', out['code']):
                raise ValidationError('Company code must contain letters, numbers, or hyphens.')
            for c in self.db.execute('SELECT id,data FROM companies'):
                if c['id'] != data.get('id') and json.loads(c['data'])['code'] == out['code']:
                    raise ValidationError('That company code already exists.')
            for field,label in [('logo','logo'),('signature','signature')]:
                value=data.get(field)
                if value is None:
                    value=self.party(table,data['id']).get(field,'') if data.get('id') else ''
                if value:
                    try:
                        prefix,encoded=value.split(',',1);raw=base64.b64decode(encoded,validate=True)
                        if len(raw)>2*1024*1024 or not ((prefix=='data:image/png;base64' and raw.startswith(b'\x89PNG\r\n\x1a\n')) or (prefix=='data:image/jpeg;base64' and raw.startswith(b'\xff\xd8\xff'))):raise ValueError()
                    except Exception:raise ValidationError(f'Choose a PNG or JPEG {label} up to 2 MB.')
                out[field]=value
        with self.lock, self.db:
            if data.get('id'):
                self.party(table,data['id'])
                self.db.execute(f'UPDATE {table} SET data=? WHERE id=?', (json.dumps(out,ensure_ascii=False),data['id']))
                id=data['id']
            else:
                id=self.db.execute(f'INSERT INTO {table}(data) VALUES(?)',(json.dumps(out,ensure_ascii=False),)).lastrowid
            self.event('save_'+table,id)
        return self.party(table,id)

    def catalog(self):
        row=self.db.execute("SELECT value FROM metadata WHERE key='catalog'").fetchone()
        items=json.loads(row[0]) if row else []
        first=self.db.execute('SELECT id FROM companies ORDER BY id LIMIT 1').fetchone()
        linked={}
        for owner,data in self.db.execute('SELECT company_id,data FROM invoices'):
            for line in json.loads(data).get('lines',[]):linked.setdefault((line.get('code',''),identity_key(line.get('description'))),set()).add(owner)
        for item in items:
            if 'company_ids' not in item:item['company_ids']=sorted(linked.get((item.get('code',''),identity_key(item.get('name'))),[])) or ([first[0]] if first else [])
        return items

    def save_catalog_item(self, data):
        name=text(data.get('name'), 'Name', 1200, True)
        currency=data.get('currency')
        if currency not in CURRENCIES:raise ValidationError('Select a supported currency.')
        price=str(decimal(data.get('price'), 'Unit price', CURRENCIES[currency]))
        with self.lock, self.db:
            items=self.catalog()
            item_id=data.get('id') or secrets.token_hex(12)
            if data.get('id') and not any(x['id']==item_id for x in items):raise ValidationError('Item not found.')
            item=dict(id=item_id,name=name,currency=currency,price=price,unit=text(data.get('unit'),'Unit',40),code=text(data.get('code'),'Item code',80))
            previous=next((x for x in items if x['id']==item_id),{})
            owners=data.get('company_ids', [data['company_id']] if data.get('company_id') else previous.get('company_ids') or ([self.db.execute('SELECT id FROM companies ORDER BY id LIMIT 1').fetchone()[0]] if self.db.execute('SELECT id FROM companies LIMIT 1').fetchone() else []))
            if owners:item['company_ids']=self.company_ids(owners)
            items=[x for x in items if x['id']!=item_id]+[item]
            self.db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('catalog',?)",(json.dumps(items,ensure_ascii=False),))
            self.event('save_catalog_item',None,name)
        return item

    def delete_catalog_item(self, item_id, company_id=None):
        with self.lock, self.db:
            items=self.catalog()
            if not any(x['id']==item_id for x in items):raise ValidationError('Item not found.')
            item=next(x for x in items if x['id']==item_id)
            if company_id is not None:
                if company_id not in item.get('company_ids',[]):raise ValidationError('This item belongs to a different company.')
                item['company_ids']=[id for id in item['company_ids'] if id!=company_id]
            self.db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('catalog',?)",(json.dumps([x for x in items if x['id']!=item_id or company_id is not None and x.get('company_ids')],ensure_ascii=False),))
            self.event('delete_catalog_item',None)
        return {'ok':True}

    def journal(self):
        entries=[dict(id=r['id'],company_id=r['company_id'],name=r['name'],media_type=r['media_type'],sha256=r['sha256'],created=r['created'],**json.loads(r['data'])) for r in self.db.execute('SELECT id,company_id,data,name,media_type,sha256,created FROM journal_entries ORDER BY id DESC')]
        reviews={r['key']:json.loads(r['value']) for r in self.db.execute("SELECT key,value FROM metadata WHERE key LIKE 'document_review:%'")}
        by_id={e['id']:e for e in entries};groups={id:[e] for id,e in by_id.items()}
        for e in entries:
            if e.get('duplicate_of') in groups:groups[e['duplicate_of']].append(e)
        attached={}
        for r in self.db.execute('SELECT id,journal_id,name,media_type,sha256,created FROM journal_attachments ORDER BY id'):
            attached.setdefault(r['journal_id'],[]).append(dict(r,url='/journal-support/'+str(r['id']),source='expense_attachment'))
        bank_docs={}
        for r in self.db.execute('SELECT id,entry_id,name,media_type,sha256,created FROM bank_attachments ORDER BY id'):
            bank_docs.setdefault(r['entry_id'],[]).append(dict(r,url='/bank-support/'+str(r['id']),source='bank_attachment'))
        linked={}
        for r in self.db.execute('SELECT e.id,e.data,s.company_id,s.currency,s.account FROM bank_entries e JOIN bank_statements s ON s.id=e.statement_id ORDER BY e.id'):
            data=json.loads(r['data']);match=re.fullmatch(r'expense:(\d+)',data.get('matched_record',''))
            if match:linked.setdefault(int(match[1]),[]).append(dict(id=r['id'],company_id=r['company_id'],currency=r['currency'],account=r['account'],**data))
        for e in entries:
            group=groups.get(e.get('duplicate_of') or e['id'],[e]);primary=by_id.get(e.get('duplicate_of'),e)
            documents=[]
            for doc in group:
                documents.append(dict(id=doc['id'],name=doc['name'],media_type=doc['media_type'],sha256=doc['sha256'],url='/journal-file/'+str(doc['id']),source='expense_original'))
                documents.extend(attached.get(doc['id'],[]))
            banks=linked.get(primary['id'],[])
            for bank in banks:documents.extend(bank_docs.get(bank['id'],[]))
            seen=set();e['documents']=[]
            for doc in documents:
                if doc['sha256'] not in seen:e['documents'].append(dict(doc,review=reviews.get('document_review:'+str(e['company_id'])+':'+doc['sha256'])));seen.add(doc['sha256'])
            e['linked_banks']=banks;e['linked_cash_minor']=sum(b['amount_minor'] for b in banks if b['reviewed'])
            e['bank_total_minor']=sum(b['amount_minor'] for b in banks)
            e['unmatched_cash_minor']=max(0,reconciliation.manual_cash(primary)-e['linked_cash_minor'])
            e['duplicate_ids']=[d['id'] for d in group if d['id']!=primary['id']]
        return entries

    def attach_journal_document(self,data):
        with self.transaction():
            e=next((e for e in self.journal() if e['id']==data.get('id')),None)
            if not e:raise ValidationError('Expense not found.')
            if e.get('duplicate_of'):raise ValidationError('Attach documents to the primary expense instead.')
            if data.get('company_id') is not None and data['company_id']!=e['company_id']:raise ValidationError('This document belongs to a different company.')
            try:content=base64.b64decode(data.get('content',''),validate=True)
            except (ValueError,TypeError):raise ValidationError('Invalid file encoding.')
            if not 0<len(content)<=MAX_FILE:raise ValidationError('Choose a document up to 20 MB.')
            media={'pdf':'application/pdf','png':'image/png','jpg':'image/jpeg'}[file_type(content)]
            name=text(Path(str(data.get('name',''))).name,'File name',200,True);checksum=hashlib.sha256(content).hexdigest()
            if checksum not in {d['sha256'] for d in e['documents']}:
                self.db.execute('INSERT OR IGNORE INTO journal_attachments(journal_id,name,media_type,content,sha256,created) VALUES(?,?,?,?,?,?)',(e['id'],name,media,content,checksum,dt.datetime.now(dt.timezone.utc).isoformat()))
                self.event('attach_expense_document',e['id'],name)
        return {'id':e['id']}

    def mark_expense_duplicate(self,data):
        with self.lock,self.db:
            self.db.execute('BEGIN IMMEDIATE')
            entries=self.journal();by_id={e['id']:e for e in entries};source=by_id.get(data.get('id'));target=by_id.get(data.get('target_id'))
            if not source:raise ValidationError('Expense not found.')
            original=json.loads(self.db.execute('SELECT data FROM journal_entries WHERE id=?',(source['id'],)).fetchone()[0])
            if data.get('restore') is True:
                if not source.get('duplicate_of'):raise ValidationError('This expense is already separate.')
                original.pop('duplicate_of',None);original['separate_payment_confirmed']=True
                self.db.execute('UPDATE journal_entries SET data=? WHERE id=?',(json.dumps(original,ensure_ascii=False),source['id']))
                self.event('restore_separate_expense',source['id'],'Restored as a separate expense; existing bank links stay with the primary record.')
                return {'id':source['id']}
            if not target or source['id']==target['id'] or source.get('duplicate_of') or target.get('duplicate_of'):raise ValidationError('Choose two separate, primary expenses.')
            if source['company_id']!=target['company_id'] or source['currency']!=target['currency'] or source['amount_minor']!=target['amount_minor'] or not reconciliation.same_supplier(source,target):raise ValidationError('Duplicate expenses must have the same company, supplier, currency and total. Different payments should be linked as payments, not marked duplicate.')
            if reconciliation.manual_cash(source)>reconciliation.manual_cash(target):raise ValidationError('Keep the expense with the complete cash-paid details as the primary record.')
            if source['bank_total_minor']+target['bank_total_minor']>reconciliation.payment_limit(target):raise ValidationError('Combined bank payments exceed the primary expense cash total. Review links before combining records.')
            child_ids=[source['id'],*source['duplicate_ids']]
            for child_id in child_ids:
                d=json.loads(self.db.execute('SELECT data FROM journal_entries WHERE id=?',(child_id,)).fetchone()[0]);d['duplicate_of']=target['id']
                self.db.execute('UPDATE journal_entries SET data=? WHERE id=?',(json.dumps(d,ensure_ascii=False),child_id))
            for bank in source['linked_banks']:
                raw=self.db.execute('SELECT data FROM bank_entries WHERE id=?',(bank['id'],)).fetchone()[0];d=json.loads(raw);d['matched_record']='expense:'+str(target['id']);d['cash_flow']=False
                self.db.execute('UPDATE bank_entries SET data=?,revision=revision+1 WHERE id=?',(json.dumps(d,ensure_ascii=False),bank['id']))
            self.event('mark_duplicate_expense',source['id'],'Primary expense #'+str(target['id'])+'; all originals retained.')
        return {'id':target['id']}

    def clean_bank_descriptions(self):
        """Repair display text and duplicate keys, retaining immutable source evidence."""
        with self.lock:
            changes=[]
            for row in self.db.execute('SELECT e.*,s.currency FROM bank_entries e JOIN bank_statements s ON s.id=e.statement_id'):
                data=json.loads(row['data']);origin=json.loads(row['original_data'])
                cleaned=clean_bank_description(data.get('description',''))
                description_changed=bool(cleaned) and cleaned!=data.get('description','')
                key=row['match_key']
                # Source descriptions remain untouched; use their cleaned form only
                # to align overlap detection with the corrected scanner.
                if clean_bank_description(origin.get('description',''))!=origin.get('description',''):
                    try:key=match_key(self.bank_row(origin,row['currency']))
                    except ValidationError:pass
                if not description_changed and key==row['match_key']:continue
                if description_changed:data['description']=cleaned
                changes.append((row['id'],row['revision'],data,key,description_changed))
            if not changes:return 0
            copies=self.directory/'safety-copies';copies.mkdir(exist_ok=True,mode=0o700)
            backup=copies/('Before-bank-footer-cleanup-'+dt.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(3)+'.sqlite3')
            backup.write_bytes(self.backup());backup.chmod(0o600)
            cleaned_count=0
            with self.db:
                for id,revision,data,key,description_changed in changes:
                    updated=self.db.execute('UPDATE bank_entries SET data=?,match_key=?,revision=revision+? WHERE id=? AND revision=?',(json.dumps(data,ensure_ascii=False),key,int(description_changed),id,revision))
                    if updated.rowcount:
                        if description_changed:cleaned_count+=1
                        self.event('clean_bank_footer',id,'Removed bank publishing/contact footer text; original statement and source row retained.')
            return cleaned_count

    def bank_entries(self):
        reviews={r['key']:json.loads(r['value']) for r in self.db.execute("SELECT key,value FROM metadata WHERE key LIKE 'document_review:%'")}
        documents={}
        for r in self.db.execute('SELECT id,entry_id,name,media_type,sha256,created FROM bank_attachments ORDER BY id'):
            documents.setdefault(r['entry_id'],[]).append(dict(r))
        entries=[dict(id=r['id'],statement_id=r['statement_id'],company_id=r['company_id'],account=r['account'],currency=r['currency'],name=r['name'],media_type=r['media_type'],revision=r['revision'],source_page=json.loads(r['original_data']).get('page',1),source_text=json.loads(r['original_data']).get('source_text',''),documents=documents.get(r['id'],[]),**json.loads(r['data'])) for r in self.db.execute('SELECT e.*,s.company_id,s.account,s.currency,s.name,s.media_type FROM bank_entries e JOIN bank_statements s ON s.id=e.statement_id ORDER BY e.id DESC')]

        expenses={e['id']:e for e in self.journal()}
        for e in entries:
            match=re.fullmatch(r'expense:(\d+)',e.get('matched_record',''));expense=expenses.get(int(match[1])) if match else None
            e['linked_expense_id']=expense['id'] if expense else None
            for d in e['documents']:d['review']=reviews.get('document_review:'+str(e['company_id'])+':'+d['sha256'])
            own=[dict(d,url='/bank-support/'+str(d['id']),source='bank_attachment') for d in e['documents']]
            e['shared_documents']=(expense['documents'] if expense else own)
        return entries

    def bank_context(self,data):
        company=self.party('companies',data.get('company_id'))
        account=re.sub(r'[ -]','',text(data.get('account'),'Bank account',40,True))
        if not re.fullmatch(r'\d{6,34}',account):raise ValidationError('Enter the full bank account number, with digits, spaces or hyphens only.')
        currency=data.get('currency')
        if currency not in CURRENCIES:raise ValidationError('Select a supported currency.')
        return company['id'],account,currency

    def bank_row(self,row,currency):
        day=date(row.get('date'),'Transaction date')
        if day>dt.date.today().isoformat():raise ValidationError('Transaction date cannot be in the future.')
        if row.get('direction') not in ('in','out'):raise ValidationError('Choose money in or money out.')
        amount=amount_minor(row.get('amount'),currency)
        if amount<=0:raise ValidationError('Transaction amount must be greater than zero.')
        balance=row.get('balance');balance_minor=None
        if balance not in ('',None):
            raw=str(balance).strip();balance_minor=amount_minor(raw.lstrip('-'),currency)*(-1 if raw.startswith('-') else 1)
        return dict(date=day,direction=row['direction'],amount_minor=amount,balance_minor=balance_minor,balance=str(balance) if balance not in ('',None) else '',description=text(clean_bank_description(row.get('description')),'Description',1000,True),reference=text(row.get('reference'),'Reference',200),time=text(row.get('time'),'Time',30),counterparty=text(row.get('counterparty'),'Counterparty',200),category='unclassified',reviewed=False,cash_flow=False,matched_record='',notes='')

    def bank_duplicate(self,company,account,currency,item,checksum='',row_key=''):
        scope=(company,account,currency)
        base='SELECT e.id FROM bank_entries e JOIN bank_statements s ON s.id=e.statement_id WHERE s.company_id=? AND s.account=? AND s.currency=? AND '
        if checksum:
            old=self.db.execute(base+'s.sha256=? AND e.row_key=?',(*scope,checksum,row_key)).fetchone()
            if old:return 'duplicate',old['id']
        key=match_key(item)
        if key:
            old=self.db.execute(base+'e.match_key=? AND (?=\'\' OR s.sha256!=?)',(*scope,key,checksum,checksum)).fetchone()
            if old:return 'duplicate',old['id']
        cash=base+"json_extract(e.data,'$.date')=? AND json_extract(e.data,'$.direction')=? AND json_extract(e.data,'$.amount_minor')=? AND (?='' OR s.sha256!=?)"
        args=(*scope,item['date'],item['direction'],item['amount_minor'],checksum,checksum)
        if item['balance_minor'] is None:old=self.db.execute(cash+' LIMIT 1',args).fetchone()
        else:
            old=self.db.execute(cash+" AND json_extract(e.data,'$.balance_minor')=? LIMIT 1",(*args,item['balance_minor'])).fetchone()
            if not old:old=self.db.execute(cash+" AND json_extract(e.data,'$.balance_minor') IS NULL LIMIT 1",args).fetchone()
        return ('possible',old['id']) if old else ('new',None)

    def check_bank_rows(self,data):
        with self.lock:
            company,account,currency=self.bank_context(data);out=[]
            rows=data.get('rows')
            if not isinstance(rows,list) or not 1<=len(rows)<=MAX_ROWS:raise ValidationError('Select a statement with 1–20,000 transactions.')
            for row in rows:
                try:
                    item=self.bank_row(row,currency);status,id=self.bank_duplicate(company,account,currency,item,data.get('sha256',''),row.get('row_key',''))
                    out.append(dict(row_key=row.get('row_key'),status=status,existing_id=id))
                except ValidationError as error:out.append(dict(row_key=row.get('row_key'),status='invalid',error=str(error)))
            return out

    def import_bank(self,data):
        company,account,currency=self.bank_context(data)
        if data.get('account_confirmed') is not True:raise ValidationError('Confirm this bank account belongs to the selected company.')
        try:content=base64.b64decode(data.get('content',''),validate=True)
        except (ValueError,TypeError):raise ValidationError('Invalid file encoding.')
        if not 0<len(content)<=MAX_FILE:raise ValidationError('Choose a statement up to 20 MB.')
        name=text(Path(str(data.get('name',''))).name,'Statement file name',200,True)
        if name.lower().endswith('.csv'):media='text/csv'
        else:media={'pdf':'application/pdf','png':'image/png','jpg':'image/jpeg'}[file_type(content)]
        checksum=hashlib.sha256(content).hexdigest();rows=data.get('rows')
        if not isinstance(rows,list) or not 1<=len(rows)<=MAX_ROWS:raise ValidationError('Select 1–20,000 transactions to import.')
        prepared=[];keys=set()
        for row in rows:
            key=text(row.get('row_key'),'Source row',80,True)
            if key in keys:raise ValidationError('The same source row was selected more than once.')
            keys.add(key);prepared.append((key,self.bank_row(row,currency),row))
        with self.lock,self.db:
            self.db.execute('BEGIN IMMEDIATE')
            existing=self.db.execute('SELECT id,currency FROM bank_statements WHERE company_id=? AND account=? AND sha256=?',(company,account,checksum)).fetchone()
            if existing and existing['currency']!=currency:raise ValidationError('This statement is already saved in a different currency. Review the previous import.')
            created=dt.datetime.now(dt.timezone.utc).isoformat()
            sid=existing['id'] if existing else self.db.execute('INSERT INTO bank_statements(company_id,account,currency,name,media_type,content,sha256,created) VALUES(?,?,?,?,?,?,?,?)',(company,account,currency,name,media,content,checksum,created)).lastrowid
            added=[];skipped=[]
            for key,item,row in prepared:
                status,id=self.bank_duplicate(company,account,currency,item,checksum,key)
                if status=='duplicate' or (status=='possible' and row.get('allow_possible_duplicate') is not True):skipped.append(dict(row_key=key,status=status,existing_id=id));continue
                origin={k:row.get(k) for k in ('date','direction','amount','balance','description','reference','time')}
                try:page=int(row.get('page') or 1)
                except (ValueError,TypeError):raise ValidationError('Source page must be between 1 and 200.')
                if not 1<=page<=200:raise ValidationError('Source page must be between 1 and 200.')
                origin.update(page=page,source_text=text(row.get('source_text'),'Original row',2500))
                id=self.db.execute('INSERT INTO bank_entries(statement_id,row_key,match_key,data,original_data,created) VALUES(?,?,?,?,?,?)',(sid,key,match_key(item),json.dumps(item,ensure_ascii=False),json.dumps(origin,ensure_ascii=False),created)).lastrowid;added.append(id)
            if added:self.event('import_bank_statement',sid,f'{name}: {len(added)} transactions imported; {len(skipped)} duplicates skipped')
            elif not existing:self.db.execute('DELETE FROM bank_statements WHERE id=?',(sid,))
            return dict(ids=added,imported=len(added),skipped=skipped)

    def save_bank_entry(self,data):
        with self.transaction():
            old=self.db.execute('SELECT e.*,s.company_id,s.currency FROM bank_entries e JOIN bank_statements s ON s.id=e.statement_id WHERE e.id=?',(data.get('id'),)).fetchone()
            if not old:raise ValidationError('Bank transaction not found.')
            if data.get('revision')!=old['revision']:raise ValidationError('This transaction changed in another window. Reload before editing.')
            item=self.bank_row(data,old['currency'])
            category=data.get('category','unclassified')
            if category not in ('unclassified','income','expense','transfer','loan','capital','tax','refund','personal','other'):raise ValidationError('Select a valid classification.')
            reviewed=data.get('reviewed') is True
            match=text(data.get('matched_record'),'Existing record',80)
            if match:
                kind,_,id=match.partition(':')
                if kind=='payment':
                    record=self.db.execute('SELECT p.amount_minor,p.data,p.reversed,i.company_id,i.currency,i.kind FROM payments p JOIN invoices i ON i.id=p.invoice_id WHERE p.id=?',(id,)).fetchone()
                    valid=record and not record['reversed'] and record['company_id']==old['company_id'] and record['currency']==old['currency'] and record['amount_minor']==item['amount_minor'] and json.loads(record['data'])['date']==item['date'] and item['direction']==('in' if record['kind']=='sale' else 'out')
                elif kind=='expense':
                    record=self.db.execute('SELECT company_id,data FROM journal_entries WHERE id=?',(id,)).fetchone();d=json.loads(record['data']) if record else {}
                    linked=self.db.execute("SELECT coalesce(sum(json_extract(data,'$.amount_minor')),0) FROM bank_entries WHERE json_extract(data,'$.matched_record')=? AND id!=?",(match,old['id'])).fetchone()[0]
                    valid=record and not d.get('duplicate_of') and record['company_id']==old['company_id'] and d.get('currency')==old['currency'] and item['direction']=='out' and linked+item['amount_minor']<=reconciliation.payment_limit(d)
                    if valid and category=='unclassified':category='expense'
                else:valid=False
                if not valid:raise ValidationError('Choose a compatible expense for this company/currency with enough cash remaining, or an exact existing receipt payment.')
                if kind=='payment' and self.db.execute("SELECT 1 FROM bank_entries WHERE json_extract(data,'$.matched_record')=? AND id!=?",(match,old['id'])).fetchone():raise ValidationError('That receipt payment is already linked to another bank transaction.')
            if reviewed and category=='unclassified':raise ValidationError('Classify the transaction before marking it reviewed.')
            if data.get('separate_payment_confirmed') is not True and not match and data.get('cash_flow') is True:
                if self.db.execute("SELECT 1 FROM payments p JOIN invoices i ON i.id=p.invoice_id WHERE p.reversed IS NULL AND i.kind=? AND i.company_id=? AND i.currency=? AND p.amount_minor=? AND json_extract(p.data,'$.date')=?",('sale' if item['direction']=='in' else 'purchase',old['company_id'],old['currency'],item['amount_minor'],item['date'])).fetchone():raise ValidationError('An existing receipt already records this cash movement. Link it, or confirm this is a separate payment.')
                for expense in self.journal():
                    if not expense.get('duplicate_of') and expense['company_id']==old['company_id'] and expense['currency']==old['currency'] and expense.get('payment_date')==item['date'] and reconciliation.manual_cash(expense)==item['amount_minor'] and item['direction']=='out':raise ValidationError('A paid expense already records this cash movement. Link it, or confirm this is a separate payment.')
            cash=data.get('cash_flow') is True
            if cash and (not reviewed or match):raise ValidationError('Only reviewed, unmatched transactions can be added to overview cash flow.')
            item.update(category=category,reviewed=reviewed,cash_flow=cash,matched_record=match,separate_payment_confirmed=data.get('separate_payment_confirmed') is True,notes=text(data.get('notes'),'Notes',1000))
            self.db.execute('UPDATE bank_entries SET data=?,revision=revision+1 WHERE id=?',(json.dumps(item,ensure_ascii=False),old['id']))
            self.event('review_bank_transaction',old['id'],f'{item["date"]} {item["direction"]} {item["amount_minor"]} {old["currency"]}')
        return next(e for e in self.bank_entries() if e['id']==old['id'])

    def attach_bank_document(self,data):
        with self.transaction():
            if not self.db.execute('SELECT 1 FROM bank_entries WHERE id=?',(data.get('id'),)).fetchone():raise ValidationError('Bank transaction not found.')
            try:content=base64.b64decode(data.get('content',''),validate=True)
            except (ValueError,TypeError):raise ValidationError('Invalid file encoding.')
            if not 0<len(content)<=MAX_FILE:raise ValidationError('Choose a supporting document up to 20 MB.')
            media={'pdf':'application/pdf','png':'image/png','jpg':'image/jpeg'}[file_type(content)]
            name=text(Path(str(data.get('name',''))).name,'File name',200,True);checksum=hashlib.sha256(content).hexdigest()
            self.db.execute('INSERT OR IGNORE INTO bank_attachments(entry_id,name,media_type,content,sha256,created) VALUES(?,?,?,?,?,?)',(data['id'],name,media,content,checksum,dt.datetime.now(dt.timezone.utc).isoformat()))
            self.event('attach_bank_document',data['id'],name)
        return next(e for e in self.bank_entries() if e['id']==data['id'])

    def review_attachment(self,data):
        """Save evidence, its reviewed details and the chosen link as one transaction."""
        key=text(data.get('request_key'),'Request key',80,True)
        if not re.fullmatch(r'[A-Za-z0-9-]{16,80}',key):raise ValidationError('Invalid request key.')
        fingerprint=hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        with self.transaction():
            old=self.db.execute('SELECT value FROM metadata WHERE key=?',('attachment_request:'+key,)).fetchone()
            if old:
                saved=json.loads(old[0])
                if saved['fingerprint']!=fingerprint:raise ValidationError('This request key was used for different details. Reopen the document review.')
                return saved['result']
            bank=next((b for b in self.bank_entries() if b['id']==data.get('bank_id')),None) if data.get('bank_id') else None
            expense=next((e for e in self.journal() if e['id']==data.get('expense_id')),None) if data.get('expense_id') else None
            if bool(bank)==bool(expense):raise ValidationError('Choose one bank transaction or expense.')
            separate=data.get('action')=='separate_expense'
            if bank and not separate and data.get('revision')!=bank['revision']:raise ValidationError('This transaction changed in another window. Reopen its document review.')
            if expense and expense.get('duplicate_of'):raise ValidationError('Attach to the primary expense instead.')
            file=data.get('file') or {}
            try:raw=base64.b64decode(file.get('content',''),validate=True)
            except (ValueError,TypeError):raise ValidationError('Invalid file encoding.')
            if not 0<len(raw)<=MAX_FILE:raise ValidationError('Choose a document up to 20 MB.')
            try:file_type(raw)
            except ScanError as error:raise ValidationError(str(error))
            name=text(Path(str(file.get('name',''))).name,'File name',200,True)
            digest=hashlib.sha256(raw).hexdigest();company=self.party('companies',data.get('company_id'))['id'] if separate else (bank or expense)['company_id']
            scan=self.document_scans.get(digest)
            if scan is None:
                companies=[dict(id=r['id'],**json.loads(r['data'])) for r in self.db.execute('SELECT id,data FROM companies')]
                try:scan=scan_document(raw,companies=companies)
                except ScanError:scan={}
            detected=scan.get('suggested_company_id')
            if detected and detected!=company:
                detected_name=self.party('companies',detected)['name']
                raise ValidationError('This document belongs to '+detected_name+', not '+self.party('companies',company)['name']+'. Choose Save as separate expense under the correct company.')
            incoming=data.get('document') or {};details={}
            for field,limit in [('supplier',200),('supplier_tax_id',80),('document_number',80),('description',1000)]:details[field]=text(incoming.get(field),field.replace('_',' ').title(),limit)
            details['date']=date(incoming['date'],'Document date') if incoming.get('date') else ''
            details['currency']=incoming.get('currency','')
            if details['currency'] not in CURRENCIES:raise ValidationError('Select a document currency.')
            details['amount']=str(incoming.get('amount') or '').strip()
            if details['amount']:amount_minor(details['amount'],details['currency'])
            details['document_type']=incoming.get('document_type','other')
            if details['document_type'] not in ('invoice','receipt','other'):raise ValidationError('Select a document type.')
            match=bank.get('matched_record','') if bank else 'expense:'+str(expense['id'])
            action=data.get('action','evidence')
            separate_id=None;reused=False
            if separate:
                candidate=dict(company_id=company,**details,sha256=digest,amount_minor=amount_minor(details['amount'] or '0',details['currency']))
                entries=self.journal();candidates=reconciliation.duplicate_candidates(entries,candidate)
                existing=data.get('existing_expense_id')
                if existing:
                    if existing not in {c['id'] for c in candidates}:raise ValidationError('Choose a suggested existing expense for this company.')
                    separate_id=existing;reused=True
                    self.attach_journal_document(dict(id=existing,name=name,content=file['content']))
                else:
                    if any(c['exact'] for c in candidates):raise ValidationError('This expense is already saved. Choose the existing expense to add this document.')
                    result=self.save_journal(dict(**details,company_id=company,payment_status='unknown',name=name,content=file['content'],duplicate_confirmed=data.get('duplicate_confirmed') is True))
                    separate_id=result['id']
                match=''
            elif bank:
                if action=='create_expense':
                    if bank['matched_record']:raise ValidationError('This bank transaction is already linked. Use the existing record.')
                    if bank['direction']!='out':raise ValidationError('Only money out can create a supplier expense.')
                    if details['currency']!=bank['currency']:raise ValidationError('Expense currency must match the bank transaction.')
                    candidate=dict(company_id=company,**details,sha256=digest,amount_minor=amount_minor(details['amount'],details['currency']))
                    candidates=reconciliation.duplicate_candidates(self.journal(),candidate)
                    if any(c['exact'] for c in candidates):raise ValidationError('This document already belongs to an expense. Choose Link existing record instead.')
                    result=self.save_journal(dict(**details,company_id=company,payment_status='unknown',name=name,content=file['content'],duplicate_confirmed=data.get('duplicate_confirmed') is True))
                    match='expense:'+str(result['id'])
                elif action=='link':match=text(data.get('matched_record'),'Existing record',80,True)
                elif action=='evidence':
                    if bank['matched_record']:raise ValidationError('This transaction is already linked. Keep its existing link.')
                    match=''
                else:raise ValidationError('Select how to save this document.')
                self.save_bank_entry(dict(id=bank['id'],revision=bank['revision'],date=bank['date'],direction=bank['direction'],amount=str(Decimal(bank['amount_minor'])/(10**CURRENCIES[bank['currency']])),balance=bank['balance'],description=bank['description'],reference=bank['reference'],time=bank.get('time',''),counterparty=text(data.get('counterparty'),'Counterparty',200),category=data.get('category','unclassified'),notes=bank.get('notes',''),matched_record=match,reviewed=data.get('reviewed') is True,cash_flow=data.get('cash_flow') is True and not match,separate_payment_confirmed=data.get('separate_payment_confirmed') is True))
                if match.startswith('expense:'):self.attach_journal_document(dict(id=int(match.split(':')[1]),name=name,content=file['content']))
                else:self.attach_bank_document(dict(id=bank['id'],name=name,content=file['content']))
            else:
                if action!='evidence':raise ValidationError('This document is already attached to an expense.')
                self.attach_journal_document(dict(id=expense['id'],name=name,content=file['content']))
            details.update(name=name,reviewed_at=dt.datetime.now(dt.timezone.utc).isoformat())
            self.db.execute('INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)',('document_review:'+str(company)+':'+digest,json.dumps(details,ensure_ascii=False)))
            result=dict(bank_id=bank['id'] if bank and not separate else None,expense_id=separate_id if separate else int(match.split(':')[1]) if match.startswith('expense:') else None,matched_record=match,separate=separate,reused=reused)
            self.db.execute('INSERT INTO metadata(key,value) VALUES(?,?)',('attachment_request:'+key,json.dumps(dict(fingerprint=fingerprint,result=result))))
            self.event('review_attachment',separate_id if separate else bank['id'] if bank else expense['id'],name+'; '+('separate expense; source record unchanged' if separate else match or 'bank evidence'))
            return result

    def export_bank(self,company='',year=''):
        output=io.StringIO();writer=csv.writer(output);writer.writerow(['Company','Account','Date','Direction','Currency','Amount','Counterparty','Description','Reference','Classification','Review status','Included in cash flow','Matched record','Supporting documents','Statement','Source page','Notes'])
        def cell(value):
            value=str(value or '');return "'"+value if value.lstrip().startswith(('=','+','-','@')) else value
        with self.lock:
            names={r['id']:json.loads(r['data'])['name'] for r in self.db.execute('SELECT id,data FROM companies')}
            for row in sorted(self.bank_entries(),key=lambda r:(r['date'],r['id'])):
                if company and str(row['company_id'])!=company:continue
                if year and not row['date'].startswith(year+'-'):continue
                writer.writerow([cell(v) for v in [names[row['company_id']],row['account'],row['date'],row['direction'],row['currency'],format(Decimal(row['amount_minor'])/(10**CURRENCIES[row['currency']]),f'.{CURRENCIES[row["currency"]]}f'),row['counterparty'],row['description'],row['reference'],row['category'],'Reviewed' if row['reviewed'] else 'Needs review','Yes' if row['cash_flow'] else 'No',row['matched_record'],'; '.join(d['name'] for d in row['documents']),row['name'],row['source_page'],row['notes']]])
        return ('\ufeff'+output.getvalue()).encode('utf-8')

    def journal_supplier(self, data):
        # The journal transaction owns both the supplier and expense writes.
        incoming={'name':text(data.get('supplier'),'Supplier',200,True)}
        for key,limit in [('tax_id',80),('address',1200),('email',200),('phone',80),('branch',100)]:
            incoming[key]=text(data.get('supplier_'+key),'Supplier '+key.replace('_',' '),limit)
        incoming['tax_label']='Tax ID / เลขประจำตัวผู้เสียภาษี' if incoming['tax_id'] else ''
        contacts=self.contacts('supplier',data.get('company_id'))
        tax=identity_key(incoming['tax_id']);name=identity_key(incoming['name'])
        if not name:raise ValidationError('Supplier name must contain letters or numbers.')
        if incoming['tax_id'] and not tax:raise ValidationError('Enter a supplier tax ID or leave it blank.')
        selected=data.get('supplier_id')
        if selected not in (None,''):
            if isinstance(selected,bool) or not str(selected).isdigit():raise ValidationError('Select a saved supplier.')
            supplier=next((c for c in contacts if c['id']==int(selected)),None)
            if not supplier:raise ValidationError('Saved supplier not found. Select it again.')
            if tax and supplier.get('tax_id') and identity_key(supplier['tax_id'])!=tax:
                raise ValidationError('The selected supplier has a different tax ID. Choose the correct supplier or automatic matching.')
        else:
            matches=[c for c in contacts if tax and identity_key(c.get('tax_id'))==tax]
            if not matches:
                matches=[c for c in contacts if name in {identity_key(c.get('name')),identity_key(c.get('legal_name'))} and (not tax or not c.get('tax_id') or identity_key(c['tax_id'])==tax)]
            if len(matches)>1:raise ValidationError('More than one saved supplier matches. Choose the correct record from Saved supplier.')
            supplier=matches[0] if matches else None
        created=supplier is None
        if created:
            supplier={**incoming,'vat_registered':False,'role':'supplier','company_ids':[data['company_id']]}
            supplier['id']=self.db.execute('INSERT INTO contacts(data) VALUES(?)',(json.dumps(supplier,ensure_ascii=False),)).lastrowid
            self.event('save_contacts',supplier['id'],'Created from expense document: '+incoming['name'])
        else:
            changed=False
            for key,value in incoming.items():
                if key!='name' and value and not supplier.get(key):supplier[key]=value;changed=True
            if changed:
                self.db.execute('UPDATE contacts SET data=? WHERE id=?',(json.dumps({k:v for k,v in supplier.items() if k!='id'},ensure_ascii=False),supplier['id']))
                self.event('save_contacts',supplier['id'],'Added missing supplier details from expense document')
        return supplier,created

    def save_journal(self, data):
        entry_id=data.get('id')
        if entry_id is not None:
            with self.lock:existing=self.db.execute('SELECT name,content FROM journal_entries WHERE id=?',(entry_id,)).fetchone()
            if not existing:raise ValidationError('Journal entry not found.')
            content=existing['content'];data={**data,'name':existing['name']}
        else:
            try:content=base64.b64decode(data.get('content',''),validate=True)
            except (ValueError,TypeError):raise ValidationError('Invalid file encoding.')
        if not 0<len(content)<=MAX_FILE:raise ValidationError('Files must be between 1 byte and 20 MB.')
        try:kind=file_type(content)
        except ScanError as error:raise ValidationError(str(error))
        name=text(Path(str(data.get('name',''))).name,'File name',200,True)
        currency=data.get('currency')
        if currency not in CURRENCIES:raise ValidationError('Select a supported currency.')
        amount=amount_minor(data.get('amount'),currency)
        if amount<=0:raise ValidationError('Amount must be greater than zero.')
        doc_type=data.get('document_type')
        if doc_type not in ('invoice','receipt','other'):raise ValidationError('Select a document type.')
        payment_status=data.get('payment_status')
        if payment_status not in ('unknown','unpaid','paid'):raise ValidationError('Select a payment status.')
        details=dict(supplier=text(data.get('supplier'),'Supplier',200,True),date=date(data.get('date'),'Document date'),document_number=text(data.get('document_number'),'Document number',80),description=text(data.get('description'),'Description',1000),document_type=doc_type,payment_status=payment_status,currency=currency,amount_minor=amount)
        if payment_status=='paid' and (data.get('payment_date') or data.get('paid_amount') not in (None,'')):
            paid_date=date(data.get('payment_date'),'Payment date')
            paid_amount=amount_minor(data.get('paid_amount'),currency)
            if paid_date>dt.date.today().isoformat():raise ValidationError('Payment date cannot be in the future.')
            if not 0<=paid_amount<=amount:raise ValidationError('Cash paid must be between zero and the document total.')
            details.update(payment_date=paid_date,paid_amount_minor=paid_amount)
        digest=hashlib.sha256(content).hexdigest()
        with self.transaction():
            company=self.party('companies',data.get('company_id'))
            if self.db.execute('SELECT 1 FROM journal_entries WHERE company_id=? AND sha256=? AND id!=?',(company['id'],digest,entry_id or 0)).fetchone():raise ValidationError('This file is already in this company’s journal.')
            if not entry_id and self.db.execute('SELECT 1 FROM journal_attachments a JOIN journal_entries e ON e.id=a.journal_id WHERE e.company_id=? AND a.sha256=?',(company['id'],digest)).fetchone():raise ValidationError('This file is already attached to an expense. Open its documents instead of creating another expense.')
            previous=json.loads(self.db.execute('SELECT data FROM journal_entries WHERE id=?',(entry_id,)).fetchone()[0]) if entry_id else {}
            if previous.get('duplicate_of'):raise ValidationError('Edit the primary expense or restore this record as a separate expense first.')
            candidates=reconciliation.duplicate_candidates(self.journal(),dict(company_id=company['id'],**details,supplier_id=data.get('supplier_id'),supplier_tax_id=data.get('supplier_tax_id')),entry_id)
            if not entry_id and candidates and data.get('duplicate_confirmed') is not True:raise ValidationError('A similar expense already exists. Add this document to that expense, or confirm it is a separate expense.')
            details['separate_payment_confirmed']=data.get('separate_payment_confirmed') is True
            linked=self.db.execute("SELECT e.data,s.company_id,s.currency FROM bank_entries e JOIN bank_statements s ON s.id=e.statement_id WHERE json_extract(e.data,'$.matched_record')=?",('expense:'+str(entry_id),)).fetchall() if entry_id else []
            if any(r['company_id']!=company['id'] or r['currency']!=currency for r in linked):raise ValidationError('Unlink bank payments before changing this expense company or currency.')
            if sum(json.loads(r['data'])['amount_minor'] for r in linked)>reconciliation.payment_limit(details):raise ValidationError('Linked bank payments exceed the edited expense cash total. Review those links first.')
            selected_bank=data.get('link_bank_id')
            if selected_bank:
                bank=self.db.execute('SELECT e.*,s.company_id,s.currency FROM bank_entries e JOIN bank_statements s ON s.id=e.statement_id WHERE e.id=?',(selected_bank,)).fetchone()
                bd=json.loads(bank['data']) if bank else {}
                if not bank or bank['company_id']!=company['id'] or bank['currency']!=currency or bd.get('direction')!='out' or (bd.get('matched_record') and bd['matched_record']!='expense:'+str(entry_id)):raise ValidationError('Choose an unlinked bank payment for this company and currency.')
                others=sum(json.loads(r['data'])['amount_minor'] for r in linked)
                if bd.get('matched_record')!='expense:'+str(entry_id):others+=bd['amount_minor']
                if others>reconciliation.payment_limit(details):raise ValidationError('Selected bank payment exceeds the expense cash total.')
            if details.get('payment_date') and not details['separate_payment_confirmed']:
                for bank in self.bank_entries():
                    if bank['company_id']==company['id'] and bank['currency']==currency and bank['direction']=='out' and bank['date']==details['payment_date'] and bank['amount_minor']==reconciliation.manual_cash(details) and bank['reviewed'] and bank.get('cash_flow') and bank.get('matched_record')!='expense:'+str(entry_id) and bank['id']!=selected_bank:raise ValidationError('A bank transaction already records this cash payment. Link it to the expense, or confirm a separate payment.')
            if entry_id:
                children=[e for e in self.journal() if e.get('duplicate_of')==entry_id]
                covered=max(reconciliation.manual_cash(details),sum(json.loads(r['data'])['amount_minor'] for r in linked if json.loads(r['data'])['reviewed']))
                if any(reconciliation.manual_cash(e)>covered for e in children):raise ValidationError('The primary expense must retain the cash payment recorded on its duplicate documents. Review bank links or payment details first.')
            supplier,supplier_created=self.journal_supplier(data)
            details.update(supplier_id=supplier['id'],supplier=supplier['name'])
            for key in ('tax_id','address','email','phone','branch'):details['supplier_'+key]=supplier.get(key,'')
            if entry_id and any(e['company_id']!=company['id'] or e['currency']!=currency or e['amount_minor']!=amount or not reconciliation.same_supplier(e,details) for e in children):raise ValidationError('Restore duplicate documents as separate expenses before changing the primary company, supplier, currency or total.')
            media={'pdf':'application/pdf','png':'image/png','jpg':'image/jpeg'}[kind]
            if entry_id:
                self.db.execute('UPDATE journal_entries SET company_id=?,data=? WHERE id=?',(company['id'],json.dumps(details,ensure_ascii=False),entry_id))
            else:
                entry_id=self.db.execute('INSERT INTO journal_entries(company_id,data,name,media_type,content,sha256,created) VALUES(?,?,?,?,?,?,?)',(company['id'],json.dumps(details,ensure_ascii=False),name,media,content,digest,dt.datetime.now(dt.timezone.utc).isoformat())).lastrowid
            if selected_bank:
                bd['matched_record']='expense:'+str(entry_id);bd['cash_flow']=False
                if bd.get('category')=='unclassified':bd['category']='expense'
                self.db.execute('UPDATE bank_entries SET data=?,revision=revision+1 WHERE id=?',(json.dumps(bd,ensure_ascii=False),selected_bank))
                self.event('link_expense_bank',entry_id,'Bank transaction #'+str(selected_bank))
            self.event('save_journal',entry_id,name)
        return {'id':entry_id,'supplier_id':supplier['id'],'supplier_created':supplier_created}

    def invoice(self, id):
        row = self.db.execute('SELECT * FROM invoices WHERE id=?',(id,)).fetchone()
        if not row:
            raise ValidationError('Document not found.')
        out = dict(row)
        out.pop('pdf');out.pop('html')
        out['data'] = json.loads(out['data'])
        out['payments'] = []
        for p in self.db.execute('SELECT * FROM payments WHERE invoice_id=? ORDER BY id',(id,)):
            p=dict(p);p.pop('pdf');p.pop('html');p['data']=json.loads(p['data']);out['payments'].append(p)
        out['cash_paid_minor'] = sum(p['amount_minor'] for p in out['payments'] if not p['reversed'])
        out['withheld_minor'] = sum(p['data'].get('withholding_minor',0) for p in out['payments'] if not p['reversed'])
        out['paid_minor'] = out['cash_paid_minor'] + out['withheld_minor']
        out['balance_minor'] = out['total_minor'] - out['paid_minor']
        out['payment_status'] = 'paid' if out['balance_minor']==0 else ('partial' if out['paid_minor'] else 'unpaid')
        out['attachments'] = [dict(x) for x in self.db.execute("SELECT a.id,a.payment_id,a.name,a.media_type,a.sha256,a.created,length(a.content) size,coalesce(m.value,'general') AS category FROM attachments a LEFT JOIN metadata m ON m.key='attachment_type:' || a.id WHERE a.invoice_id=?",(id,))]
        return out

    def state(self):
        with self.lock:
            return {**{t:[dict(id=r['id'],**json.loads(r['data'])) for r in self.db.execute(f'SELECT * FROM {t} ORDER BY id')] for t in ('companies',)},'contacts':self.contacts(),'catalog':self.catalog(),'journal':self.journal(),'bank_entries':self.bank_entries(),'invoices':[self.invoice(x['id']) for x in self.db.execute('SELECT id FROM invoices ORDER BY id DESC')], 'currencies':CURRENCIES,'data_path':str(self.directory),'events':[dict(x) for x in self.db.execute('SELECT * FROM events ORDER BY id DESC LIMIT 100')]}

    def save_draft(self, data):
        with self.lock, self.db:
            company=self.party('companies',data.get('company_id'))
            contact=self.party('contacts',data.get('contact_id'))
            if contact['role'] not in ('customer','both'):raise ValidationError('Select a customer. For a supplier you also sell to, set its type to Both.')
            raw_contact=json.loads(self.db.execute('SELECT data FROM contacts WHERE id=?',(contact['id'],)).fetchone()[0])
            if 'company_ids' in raw_contact and company['id'] not in contact['company_ids']:raise ValidationError('Choose a customer used by this company.')
            # Inline edits belong to this draft; reusable contact/company records
            # remain explicit, separate records. Issuing snapshots these values.
            for name,party in [('company',company),('contact',contact)]:
                overrides=data.get(name+'_details')
                if overrides is not None:
                    if not isinstance(overrides,dict):raise ValidationError('Invalid document party details.')
                    for key,limit in [('name',200),('legal_name',200),('address',1200),('email',200),('tax_label',40),('tax_id',80),('phone',80),('contact_name',200),('contact_phone',80),('branch',100)]:
                        party[key]=text(overrides.get(key,party.get(key,'')),key.replace('_',' ').title(),limit,key=='name')
            kind=data.get('kind')
            if kind!='sale':
                raise ValidationError('Supplier documents must be uploaded through Expense journal. Only sales invoices can be generated.')
            lines,subtotal=calculate(data.get('lines'),data.get('currency'))
            rate,vat=calculate_vat(subtotal,data.get('vat_rate'))
            if rate!='' and not company.get('vat_registered'):raise ValidationError('Mark the seller VAT-registered in My companies before adding VAT.')
            tax_invoice=data.get('tax_invoice') is True
            if tax_invoice and rate=='':raise ValidationError('A tax invoice requires a sales invoice and an explicit VAT rate.')
            total=subtotal+vat
            payload=dict(vat_rate=rate,vat_minor=vat,subtotal_minor=subtotal,tax_invoice=tax_invoice,company=company,contact=contact,lines=lines,currency=data['currency'],issue_date=date(data.get('issue_date'),'Issue date'),due_date=date(data.get('due_date'),'Due date') if data.get('due_date') else '',reference=text(data.get('reference'),'Reference',200),notes=text(data.get('notes'),'Notes',1000),payment_instructions=text(data.get('payment_instructions',company.get('payment_instructions','')),'Payment instructions',500),language='en')
            if payload['due_date'] and payload['due_date'] < payload['issue_date']:
                raise ValidationError('Due date cannot be before issue date.')
            if data.get('id'):
                existing=self.invoice(data['id'])
                if existing['status']!='draft': raise ValidationError('Issued documents cannot be edited. Void and create a new draft.')
                if data.get('revision')!=existing['revision']: raise ValidationError('This draft changed in another window. Reload before editing.')
                if existing['kind']!=kind: raise ValidationError('Document type cannot change.')
                self.db.execute('UPDATE invoices SET company_id=?,contact_id=?,data=?,total_minor=?,currency=?,revision=revision+1 WHERE id=?',(company['id'],contact['id'],json.dumps(payload,ensure_ascii=False),total,data['currency'],data['id']))
                id=data['id']
            else:
                id=self.db.execute('INSERT INTO invoices(company_id,contact_id,kind,status,data,total_minor,currency,created) VALUES(?,?,?,\'draft\',?,?,?,?)',(company['id'],contact['id'],kind,json.dumps(payload,ensure_ascii=False),total,data['currency'],dt.datetime.now(dt.timezone.utc).isoformat())).lastrowid
            self.event('save_draft',id)
            return self.invoice(id)

    def duplicate_invoice(self, id):
        with self.lock:
            inv=self.invoice(id)
            if inv['kind']!='sale':raise ValidationError('Only sales invoices can be duplicated.')
            d=inv['data'];issued=dt.date.today()
            due=(issued+(dt.date.fromisoformat(d['due_date'])-dt.date.fromisoformat(d['issue_date']))).isoformat() if d.get('due_date') else ''
            return self.save_draft(dict(
                company_id=inv['company_id'],contact_id=inv['contact_id'],kind='sale',currency=inv['currency'],
                company_details=d['company'],contact_details=d['contact'],issue_date=issued.isoformat(),due_date=due,
                **{key:d[key] for key in ('lines','vat_rate','tax_invoice','reference','notes','payment_instructions') if key in d}))

    def reserve_number(self, company, prefix, issue_date, custom=''):
        if custom:
            number=text(custom,'Document number',80,True)
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]*',number):
                raise ValidationError('Document numbers may use letters, numbers, hyphens, slashes, underscores, and periods.')
            try:self.db.execute('INSERT INTO numbers VALUES(?,?)',(company['id'],number))
            except sqlite3.IntegrityError:raise ValidationError('That document number is already used. Choose another number.')
            return number
        monthly=prefix=='INV' and company.get('invoice_number_format')=='monthly'
        year=issue_date[:7].replace('-','') if monthly else issue_date[:4]
        if monthly:prefix='IV'
        row=self.db.execute('SELECT next FROM counters WHERE company_id=? AND prefix=? AND year=?',(company['id'],prefix,year)).fetchone()
        n=row['next'] if row else 1
        while True:
            number=f"{company['code']}-{prefix}-{issue_date[:7]}-{n:05d}" if monthly else f"{company['code']}-{prefix}-{year}-{n:04d}"
            n+=1
            try:self.db.execute('INSERT INTO numbers VALUES(?,?)',(company['id'],number));break
            except sqlite3.IntegrityError:continue
        self.db.execute('INSERT INTO counters VALUES(?,?,?,?) ON CONFLICT(company_id,prefix,year) DO UPDATE SET next=excluded.next',(company['id'],prefix,year,n))
        return number

    def issue(self, id, custom='', expected_revision=None, tax_confirmed=False):
        with self.lock, self.db:
            self.db.execute('BEGIN IMMEDIATE')
            inv=self.invoice(id)
            if inv['kind']!='sale':raise ValidationError('Supplier documents must be uploaded through Expense journal. Payable generation is no longer available.')
            if inv['status']!='draft': raise ValidationError('Only a draft can be issued.')
            if expected_revision!=inv['revision']:raise ValidationError('This draft changed. Reload and review before issuing.')
            d=inv['data']
            if d.get('tax_invoice'):
                seller=d['company'];buyer=d['contact']
                if not tax_confirmed:raise ValidationError('Confirm the full invoice amount is subject to VAT now before issuing the tax invoice.')
                if not self.party('companies',inv['company_id']).get('vat_registered'):raise ValidationError('Mark the issuing company as VAT-registered in My companies first.')
                if inv['currency']!='THB':raise ValidationError('Tax invoices must be in THB in this workflow.')
                if d['issue_date']!=dt.date.today().isoformat():raise ValidationError('Use today’s issue date for a tax invoice.')
                if not seller.get('address') or not seller.get('branch') or not re.fullmatch(r'\d{13}',seller.get('tax_id','')):raise ValidationError('Tax invoices need the seller’s address, branch and 13-digit tax ID.')
                if not buyer.get('address'):raise ValidationError('Tax invoices need the customer address.')
                if buyer.get('vat_registered') and (not buyer.get('branch') or not re.fullmatch(r'\d{13}',buyer.get('tax_id',''))):raise ValidationError('A VAT-registered customer needs a branch and 13-digit tax ID.')
            d['company'].update({k:self.party('companies',inv['company_id']).get(k,'') for k in DOCUMENT_COMPANY_FIELDS})
            number=self.reserve_number(self.party('companies',inv['company_id']),'INV',d['issue_date'],custom)
            inv['number']=number;inv['status']='issued'
            html=render_document(inv)
            pdf=self.pdf_renderer(html)
            self.db.execute("UPDATE invoices SET number=?,status='issued',html=?,pdf=?,issued=?,data=? WHERE id=?",(number,html,pdf,dt.datetime.now(dt.timezone.utc).isoformat(),json.dumps(d,ensure_ascii=False),id))
            self.event('issue',id,number)
            return self.invoice(id)

    def add_payment(self, id, data):
        with self.lock, self.db:
            self.db.execute('BEGIN IMMEDIATE')
            key=text(data.get('request_key'),'Payment request key',100,True)
            duplicate=self.db.execute('SELECT p.*,i.currency FROM payments p JOIN invoices i ON i.id=p.invoice_id WHERE p.request_key=?',(key,)).fetchone()
            if duplicate:
                if duplicate['invoice_id']!=id:raise ValidationError('Payment request key already used.')
                previous=json.loads(duplicate['data'])
                same=(amount_minor(data.get('amount'),duplicate['currency'])==duplicate['amount_minor'] and amount_minor(data.get('withholding') or '0',duplicate['currency'])==previous.get('withholding_minor',0) and date(data.get('date'),'Payment date')==previous['date'] and text(data.get('method'),'Payment method',120,True)==previous['method'] and text(data.get('reference'),'Payment reference',200)==previous.get('reference','') and (not data.get('number') or text(data['number'],'Receipt number',80)==duplicate['number']))
                same=same and (data.get('link_bank_id') or None)==previous.get('linked_bank_id') and (data.get('separate_payment_confirmed') is True)==previous.get('separate_payment_confirmed',False)
                if not same:raise ValidationError('This payment was already recorded with different details. Reopen the invoice to review its saved receipt before recording another payment.')
                return self.invoice(id)
            inv=self.invoice(id)
            if inv['kind']!='sale':raise ValidationError('Supplier payments belong in Expense journal. Payable payment documents are no longer available.')
            if inv['status']!='issued':raise ValidationError('Payments require an issued document.')
            amount=amount_minor(data.get('amount'),inv['currency'])
            withheld=amount_minor(data.get('withholding') or '0',inv['currency'])
            if not 0<amount or amount+withheld>inv['balance_minor']:raise ValidationError('Payment amount must be positive, and payment plus withholding cannot exceed the outstanding balance.')
            paid_date=date(data.get('date'),'Payment date')
            if paid_date<inv['data']['issue_date'] or paid_date>dt.date.today().isoformat():raise ValidationError('Payment date must be between the invoice date and today.')
            candidates=[b for b in self.bank_entries() if b['company_id']==inv['company_id'] and b['currency']==inv['currency'] and b['direction']=='in' and b['date']==paid_date and b['amount_minor']==amount and not b['matched_record']]
            bank=next((b for b in candidates if b['id']==data.get('link_bank_id')),None)
            if data.get('link_bank_id') and (not bank or bank['revision']!=data.get('bank_revision')):raise ValidationError('The selected bank transaction changed or does not match this payment. Reopen the receipt editor.')
            if not bank and data.get('separate_payment_confirmed') is not True and any(b['reviewed'] and b['cash_flow'] for b in candidates):raise ValidationError('A bank transaction already records this cash received. Link it below, or confirm this is a separate payment.')
            details=dict(date=paid_date,receipt_date=paid_date,withholding_minor=withheld,method=text(data.get('method'),'Payment method',120,True),reference=text(data.get('reference'),'Payment reference',200),confirmed=True,linked_bank_id=bank['id'] if bank else None,separate_payment_confirmed=data.get('separate_payment_confirmed') is True)
            number=self.reserve_number(inv['data']['company'],'RCT',paid_date,data.get('number',''))
            created=dt.datetime.now(dt.timezone.utc).isoformat()
            pid=self.db.execute('INSERT INTO payments(invoice_id,number,amount_minor,data,created,request_key) VALUES(?,?,?,?,?,?)',(id,number,amount,json.dumps(details,ensure_ascii=False),created,key)).lastrowid
            inv=self.invoice(id)
            payment=next(p for p in inv['payments'] if p['id']==pid)
            inv['data']['company'].update({k:self.party('companies',inv['company_id']).get(k,'') for k in DOCUMENT_COMPANY_FIELDS})
            html=render_document(inv,payment=payment)
            pdf=self.pdf_renderer(html)
            self.db.execute('UPDATE payments SET html=?,pdf=? WHERE id=?',(html,pdf,pid))
            if bank:self.save_bank_entry(dict(bank,amount=str(Decimal(bank['amount_minor'])/(10**CURRENCIES[bank['currency']])),matched_record='payment:'+str(pid),cash_flow=False,category='income' if bank['category']=='unclassified' else bank['category']))
            self.event('record_payment',id,f'{number}: payment {amount}, withholding {withheld} {inv["currency"]} minor units')
            return self.invoice(id)

    def void(self,id,reason):
        with self.lock,self.db:
            inv=self.invoice(id)
            if inv['data'].get('tax_invoice'):raise ValidationError('Tax invoice corrections require a credit note. Please consult your accountant; LocalBooks does not issue credit notes.')
            if inv['status']!='issued' or inv['paid_minor']:
                raise ValidationError('Only an issued document without active payments can be voided. Reverse payments first.')
            reason=text(reason,'Void reason',500,True)
            self.db.execute("UPDATE invoices SET status='void',void_reason=? WHERE id=?",(reason,id))
            self.event('void',id,reason)
        return self.invoice(id)

    def reverse_payment(self,id,pid,reason):
        with self.lock,self.db:
            p=next((p for p in self.invoice(id)['payments'] if p['id']==pid),None)
            if not p or p['reversed']:raise ValidationError('Active payment not found.')
            reason=text(reason,'Reversal reason',500,True)
            self.db.execute('UPDATE payments SET reversed=?,reverse_reason=? WHERE id=?',(dt.datetime.now(dt.timezone.utc).isoformat(),reason,pid))
            for bank in self.db.execute("SELECT id,data FROM bank_entries WHERE json_extract(data,'$.matched_record')=?",('payment:'+str(pid),)).fetchall():
                item=json.loads(bank['data']);item.update(matched_record='',reviewed=False,cash_flow=False)
                self.db.execute('UPDATE bank_entries SET data=?,revision=revision+1 WHERE id=?',(json.dumps(item,ensure_ascii=False),bank['id']))
                self.event('review_reversed_payment_link',bank['id'],p['number']+' reversed; bank transaction needs review.')
            self.event('reverse_payment',id,p['number']+': '+reason)
        return self.invoice(id)

    def attach(self,id,data):
        with self.lock,self.db:
            inv=self.invoice(id)
            pid=data.get('payment_id')
            if pid and not any(p['id']==pid for p in inv['payments']):raise ValidationError('Payment does not belong to this document.')
            category=data.get('category','general')
            if category not in ('general','withholding_certificate') or (category=='withholding_certificate' and not pid):raise ValidationError('Invalid attachment type.')
            try:content=base64.b64decode(data.get('content',''),validate=True)
            except Exception:raise ValidationError('Invalid attachment encoding.')
            if not 0<len(content)<=MAX_FILE:raise ValidationError('Attachments must be between 1 byte and 20 MB.')
            name=text(Path(str(data.get('name',''))).name,'File name',200,True)
            media='application/octet-stream'
            attachment_id=self.db.execute('INSERT INTO attachments(invoice_id,payment_id,name,media_type,content,sha256,created) VALUES(?,?,?,?,?,?,?)',(id,pid,name,media,content,hashlib.sha256(content).hexdigest(),dt.datetime.now(dt.timezone.utc).isoformat())).lastrowid
            if category=='withholding_certificate':self.db.execute("INSERT INTO metadata(key,value) VALUES(?,?)",(f'attachment_type:{attachment_id}',category))
            self.event('attach_payment_proof' if pid else 'attach_original',id,name)
        return self.invoice(id)

    def backup(self):
        with self.lock, tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'backup.sqlite3'
            target=sqlite3.connect(p)
            self.db.backup(target);target.close()
            return p.read_bytes()

    def restore(self,content,confirmation):
        if confirmation!='RESTORE':raise ValidationError('Type RESTORE to replace the current data with this backup.')
        if not content.startswith(b'SQLite format 3\0'):raise ValidationError('This is not a LocalBooks database backup.')
        with self.lock,tempfile.TemporaryDirectory(dir=self.directory) as tmp:
            p=Path(tmp)/'incoming.sqlite3';p.write_bytes(content)
            db=sqlite3.connect(f'file:{p}?mode=ro',uri=True)
            try:
                db.execute('PRAGMA trusted_schema=OFF')
                if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValidationError('Backup integrity check failed.')
                if db.execute("SELECT value FROM metadata WHERE key='format'").fetchone()!=('localbooks-v1',):raise ValidationError('Unsupported backup version.')
                expected={r[0]:r[1] for r in self.db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                actual={r[0]:r[1] for r in db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                optional={'journal_entries','journal_attachments','bank_statements','bank_entries','bank_attachments'} | compliance.TABLES
                if any(k not in expected or expected[k]!=v for k,v in actual.items()) or any(k not in actual for k in expected if k not in optional) or bool(set(actual)&{'bank_statements','bank_entries','bank_attachments'}) and not {'bank_statements','bank_entries','bank_attachments'}.issubset(actual) or db.execute("SELECT count(*) FROM sqlite_master WHERE type IN ('trigger','view')").fetchone()[0]:raise ValidationError('Backup schema does not match this app.')
                if db.execute('PRAGMA foreign_key_check').fetchall():raise ValidationError('Backup contains broken document links.')
                compliance.validate_backup(db,set(actual))
                company_ids={r[0] for r in db.execute('SELECT id FROM companies')}
                def valid_company_ids(item):
                    if 'company_ids' in item and (not isinstance(item['company_ids'],list) or not item['company_ids'] or any(type(id) is not int or id not in company_ids for id in item['company_ids'])):raise ValidationError('Backup contains invalid company assignments.')
                for raw, in db.execute('SELECT data FROM contacts'):valid_company_ids(json.loads(raw))
                catalog_row=db.execute("SELECT value FROM metadata WHERE key='catalog'").fetchone()
                if catalog_row:
                    catalog=json.loads(catalog_row[0])
                    if not isinstance(catalog,list):raise ValidationError('Backup contains an invalid catalog.')
                    ids=set()
                    for item in catalog:
                        if not isinstance(item,dict) or not isinstance(item.get('id'),str) or item['id'] in ids:raise ValidationError('Backup contains an invalid catalog item.')
                        valid_company_ids(item)
                        ids.add(item['id']);text(item.get('name'),'Item name',1200,True)
                        if item.get('currency') not in CURRENCIES:raise ValidationError('Backup contains an invalid item currency.')
                        decimal(item.get('price'),'Item price',CURRENCIES[item['currency']]);text(item.get('unit'),'Unit',40);text(item.get('code'),'Item code',80)
                if 'journal_entries' in actual:
                    for raw,checksum,raw_data in db.execute('SELECT content,sha256,data FROM journal_entries'):
                        if hashlib.sha256(raw).hexdigest()!=checksum:raise ValidationError('A journal document in this backup is damaged.')
                        item=json.loads(raw_data)
                        if not isinstance(item,dict):raise ValidationError('Backup contains an invalid journal entry.')
                        text(item.get('supplier'),'Supplier',200,True);date(item.get('date'),'Document date')
                        supplier_id=item.get('supplier_id')
                        if supplier_id is not None and (type(supplier_id) is not int or not db.execute('SELECT 1 FROM contacts WHERE id=?',(supplier_id,)).fetchone()):raise ValidationError('Backup contains a broken supplier link.')
                        for key,limit in [('tax_id',80),('address',1200),('email',200),('phone',80),('branch',100)]:text(item.get('supplier_'+key),'Supplier '+key,limit)
                        if item.get('currency') not in CURRENCIES or type(item.get('amount_minor')) is not int or item['amount_minor']<=0 or item.get('document_type') not in ('invoice','receipt','other') or item.get('payment_status') not in ('unknown','unpaid','paid'):raise ValidationError('Backup contains an invalid journal entry.')
                        if 'payment_date' in item or 'paid_amount_minor' in item:
                            paid_date=date(item.get('payment_date'),'Payment date')
                            if item['payment_status']!='paid' or paid_date>dt.date.today().isoformat() or type(item.get('paid_amount_minor')) is not int or not 0<=item['paid_amount_minor']<=item['amount_minor']:raise ValidationError('Backup contains invalid expense payment details.')
                if 'journal_attachments' in actual:
                    for raw,checksum,media in db.execute('SELECT content,sha256,media_type FROM journal_attachments'):
                        if not 0<len(raw)<=MAX_FILE or hashlib.sha256(raw).hexdigest()!=checksum or {'pdf':'application/pdf','png':'image/png','jpg':'image/jpeg'}[file_type(raw)]!=media:raise ValidationError('An expense supporting document in this backup is damaged.')
                if 'journal_entries' in actual:
                    expense_data={r[0]:(r[1],json.loads(r[2])) for r in db.execute('SELECT id,company_id,data FROM journal_entries')}
                    for id,(company,item) in expense_data.items():
                        target=item.get('duplicate_of')
                        if target is not None:
                            if type(target) is not int or target not in expense_data or target==id:raise ValidationError('Backup contains an invalid duplicate expense link.')
                            owner,parent=expense_data[target]
                            if parent.get('duplicate_of') or owner!=company or parent['currency']!=item['currency'] or parent['amount_minor']!=item['amount_minor'] or not reconciliation.same_supplier(parent,item):raise ValidationError('Backup contains incompatible duplicate expenses.')
                if 'bank_statements' in actual:
                    for company,account,currency,raw,checksum,media in db.execute('SELECT company_id,account,currency,content,sha256,media_type FROM bank_statements'):
                        if currency not in CURRENCIES or not re.fullmatch(r'\d{6,34}',account) or not 0<len(raw)<=MAX_FILE or hashlib.sha256(raw).hexdigest()!=checksum:raise ValidationError('Backup contains an invalid bank statement.')
                        if media not in ('text/csv','application/pdf','image/png','image/jpeg'):raise ValidationError('Backup contains an invalid bank statement file type.')
                        if media!='text/csv' and {'pdf':'application/pdf','png':'image/png','jpg':'image/jpeg'}[file_type(raw)]!=media:raise ValidationError('Backup bank statement file type does not match its contents.')
                    for raw,checksum,media in db.execute('SELECT content,sha256,media_type FROM bank_attachments'):
                        if not 0<len(raw)<=MAX_FILE or hashlib.sha256(raw).hexdigest()!=checksum:raise ValidationError('A bank supporting document in this backup is damaged.')
                        if {'pdf':'application/pdf','png':'image/png','jpg':'image/jpeg'}[file_type(raw)]!=media:raise ValidationError('Backup supporting document file type does not match its contents.')
                    for raw,currency,original in db.execute('SELECT e.data,s.currency,e.original_data FROM bank_entries e JOIN bank_statements s ON s.id=e.statement_id'):
                        item=json.loads(raw);origin=json.loads(original)
                        if not isinstance(origin,dict) or not isinstance(item,dict):raise ValidationError('Backup contains an invalid bank transaction.')
                        date(item.get('date'),'Bank transaction date');text(item.get('description'),'Bank description',1000,True)
                        if item.get('direction') not in ('in','out') or type(item.get('amount_minor')) is not int or item['amount_minor']<=0 or type(item.get('reviewed')) is not bool or type(item.get('cash_flow')) is not bool:raise ValidationError('Backup contains invalid bank transaction amounts or status.')
                        if item.get('category') not in ('unclassified','income','expense','transfer','loan','capital','tax','refund','personal','other') or (item['reviewed'] and item['category']=='unclassified'):raise ValidationError('Backup contains an invalid bank classification.')
                        if item.get('balance_minor') is not None and type(item['balance_minor']) is not int:raise ValidationError('Backup contains an invalid statement balance.')
                        if item['cash_flow'] and (not item['reviewed'] or item.get('matched_record')):raise ValidationError('Backup contains a bank transaction counted twice.')
                if 'bank_entries' in actual and 'journal_entries' in actual:
                    totals={}
                    for company,currency,raw in db.execute('SELECT s.company_id,s.currency,e.data FROM bank_entries e JOIN bank_statements s ON s.id=e.statement_id'):
                        item=json.loads(raw);match=re.fullmatch(r'expense:(\d+)',item.get('matched_record',''))
                        if not match:continue
                        id=int(match[1]);record=expense_data.get(id)
                        if not record or record[0]!=company or record[1]['currency']!=currency or record[1].get('duplicate_of') or item['direction']!='out':raise ValidationError('Backup contains an invalid expense-bank link.')
                        totals[id]=totals.get(id,0)+item['amount_minor']
                    if any(amount>reconciliation.payment_limit(expense_data[id][1]) for id,amount in totals.items()):raise ValidationError('Backup bank payments exceed their linked expense cash total.')
                for row in db.execute('SELECT content,sha256 FROM attachments'):
                    if hashlib.sha256(row[0]).hexdigest()!=row[1]:raise ValidationError('An attachment in this backup is damaged.')
                for table in ('companies','contacts','invoices','payments'):
                    for row in db.execute(f'SELECT data FROM {table}'):
                        data=json.loads(row[0])
                        if not isinstance(data,dict):raise ValidationError('Backup contains invalid records.')
                        if table in ('companies','contacts') and not isinstance(data.get('name'),str):raise ValidationError('Backup contains an invalid contact.')
                        if table=='contacts' and data.get('role','customer') not in ('customer','supplier','both'):raise ValidationError('Backup contains an invalid contact type.')
                for row in db.execute('SELECT id,data,currency,total_minor,kind FROM invoices'):
                    data=json.loads(row[1]);_,subtotal=calculate(data.get('lines'),row[2]);rate,vat=calculate_vat(subtotal,data.get('vat_rate'));total=subtotal+vat
                    if data.get('vat_rate') not in (None,'') and (data.get('vat_minor')!=vat or data.get('subtotal_minor')!=subtotal):raise ValidationError('Backup contains inconsistent VAT amounts.')
                    if type(data.get('tax_invoice',False)) is not bool or (data.get('tax_invoice') and (rate=='' or row[2]!='THB' or row[4]!='sale')):raise ValidationError('Backup contains invalid tax invoice details.')
                    if total!=row[3] or not isinstance(data.get('company'),dict) or not isinstance(data.get('contact'),dict):raise ValidationError('Backup contains inconsistent invoice totals or details.')
                    active=0
                    for amount,payment_data in db.execute('SELECT amount_minor,data FROM payments WHERE invoice_id=? AND reversed IS NULL',(row[0],)):
                        payment_data=json.loads(payment_data);withheld=payment_data.get('withholding_minor',0)
                        if type(withheld) is not int or withheld<0:raise ValidationError('Backup contains invalid withholding.')
                        active+=amount+withheld
                    if active>total:raise ValidationError('Backup payments exceed an invoice total.')
                for row in db.execute('SELECT data FROM payments'):
                    data=json.loads(row[0]);date(data.get('date'),'Backup payment date')
                    text(data.get('method'),'Backup payment method',120,True)
                old_identity=self.db.execute("SELECT value FROM metadata WHERE key='database_id'").fetchone()
                new_identity=db.execute("SELECT value FROM metadata WHERE key='database_id'").fetchone()
                same_identity=bool(old_identity and new_identity and old_identity[0]==new_identity[0])
                old_codes={r['id']:json.loads(r['data']).get('code') for r in self.db.execute('SELECT id,data FROM companies')}
                new_codes={json.loads(r[1]).get('code'):r[0] for r in db.execute('SELECT id,data FROM companies')}
                new_ids={r[0] for r in db.execute('SELECT id FROM companies')}
                reserved=[]
                for row in self.db.execute('SELECT company_id,number FROM numbers'):
                    company=row['company_id'] if same_identity and row['company_id'] in new_ids else new_codes.get(old_codes.get(row['company_id']))
                    if company:reserved.append((company,row['number']))
            except (sqlite3.Error,ValueError,TypeError):raise ValidationError('The backup is invalid or incompatible.')
            finally:db.close()
            snapshots=self.directory/'safety-copies';snapshots.mkdir(exist_ok=True,mode=0o700)
            before=snapshots/f'Before-restore-{dt.datetime.now().strftime("%Y%m%d-%H%M%S")}-{secrets.token_hex(3)}.sqlite3'
            before.write_bytes(self.backup());os.chmod(before,0o600)
            self.db.close()
            try:os.replace(p,self.path)
            finally:self.db=self.connect()
            self.db.executescript(SCHEMA);self.db.commit()
            os.chmod(self.path,0o600)
            with self.db:
                self.db.executemany('INSERT OR IGNORE INTO numbers VALUES(?,?)',reserved)
                self.event('restore',None,'Previous data saved at '+str(before)+'; existing document numbers remain reserved for matching companies.')
            self.clean_bank_descriptions()
            return dict(safety_copy=str(before))


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass

    def send(self,status,body,media='application/json',filename=None):
        if not isinstance(body,bytes):body=(json.dumps(body,ensure_ascii=False) if media=='application/json' else body).encode()
        self.send_response(status)
        self.send_header('Content-Type',media)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        policy="default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; font-src 'self' data:; frame-src 'self' blob:; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'self'; object-src 'self'; base-uri 'none'; form-action 'self'"
        if self.path.startswith('/document/') and media.startswith('text/html'):
            policy="sandbox allow-scripts allow-modals; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; font-src data:; img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'"
        self.send_header('Content-Security-Policy',policy)
        if filename:self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+urllib.parse.quote(filename,safe=''))
        self.end_headers();self.wfile.write(body)

    def allowed(self):
        if self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}':
            self.send(403,{'error':'Use the local launcher.'});return False
        c=cookies.SimpleCookie()
        try:c.load(self.headers.get('Cookie',''))
        except cookies.CookieError:pass
        if 'localbooks' not in c or not secrets.compare_digest(c['localbooks'].value,self.server.token):
            self.send(403,{'error':'Open LocalBooks using its Mac launcher.'});return False
        return True

    def do_GET(self):
        u=urllib.parse.urlsplit(self.path)
        if u.path=='/' and urllib.parse.parse_qs(u.query).get('key',[''])[0]==self.server.token and self.headers.get('Host')==f'127.0.0.1:{self.server.server_port}':
            self.send_response(303);self.send_header('Location','/');self.send_header('Set-Cookie',f'localbooks={self.server.token}; HttpOnly; SameSite=Strict; Path=/');self.send_header('Cache-Control','no-store');self.end_headers();return
        if not self.allowed():return
        try:
            store=self.server.store
            if u.path=='/api/dev-version' and self.server.dev:
                files=sorted((ROOT/'static').rglob('*'))
                version=hashlib.sha256(self.server.backend_version.encode())
                for file in files:
                    if file.is_file():
                        version.update(str(file.relative_to(ROOT)).encode());version.update(file.read_bytes())
                return self.send(200,{'version':version.hexdigest()})
            if u.path=='/api/state':return self.send(200,store.state())
            if u.path=='/api/backup':return self.send(200,store.backup(),'application/vnd.sqlite3',f'LocalBooks-{dt.date.today()}.sqlite3')
            if u.path in ('/api/compliance','/api/compliance/export'):
                params=urllib.parse.parse_qs(u.query)
                company=compliance.integer(params.get('company',[''])[0],1,2147483647)
                year=compliance.integer(params.get('year',[''])[0],2000,2100)
                if u.path.endswith('/export'):return self.send(200,compliance.export_pack(store,company,year),'application/zip',f'LocalBooks-accountant-{company}-{year}.zip')
                return self.send(200,compliance.workspace(store,company,year))
            if u.path.startswith('/compliance-file/'):
                with store.lock:row=store.db.execute('SELECT name,content,media_type FROM compliance_files WHERE id=?',(int(u.path.split('/')[-1]),)).fetchone()
                if not row:raise ValidationError('Compliance document not found.')
                return self.send(200,row['content'],row['media_type'],row['name'])

            if u.path.startswith('/api/bank/scan/'):
                key=u.path.split('/')[-1]
                with self.server.scan_lock:job=self.server.scan_jobs.get(key);result={k:v for k,v in job.items() if k not in ('cancel',)} if job else None
                if result is None:raise ValidationError('Statement scan not found. Please upload it again.')
                return self.send(200,result)
            if u.path=='/api/bank/export':
                params=urllib.parse.parse_qs(u.query);return self.send(200,store.export_bank(params.get('company',[''])[0],params.get('year',[''])[0]),'text/csv; charset=utf-8','LocalBooks-bank-journal.csv')
            if u.path.startswith(('/bank-statement/','/bank-support/')):
                table='bank_statements' if u.path.startswith('/bank-statement/') else 'bank_attachments'
                with store.lock:row=store.db.execute(f'SELECT name,content,media_type FROM {table} WHERE id=?',(int(u.path.split('/')[-1]),)).fetchone()
                if not row:raise ValidationError('Bank document not found.')
                preview=urllib.parse.parse_qs(u.query).get('preview')==['1']
                if preview and row['media_type']=='text/csv':return self.send(200,csv_text(row['content']),'text/plain; charset=utf-8')
                return self.send(200,row['content'],row['media_type'],None if preview else row['name'])
            if u.path.startswith('/journal-support/'):
                with store.lock:row=store.db.execute('SELECT name,content,media_type FROM journal_attachments WHERE id=?',(int(u.path.split('/')[-1]),)).fetchone()
                if not row:raise ValidationError('Expense supporting document not found.')
                preview=urllib.parse.parse_qs(u.query).get('preview')==['1']
                return self.send(200,row['content'],row['media_type'],None if preview else row['name'])
            if u.path.startswith('/journal-file/'):
                with store.lock:row=store.db.execute('SELECT name,content,media_type FROM journal_entries WHERE id=?',(int(u.path.split('/')[-1]),)).fetchone()
                if not row:raise ValidationError('Journal document not found.')
                preview=urllib.parse.parse_qs(u.query).get('preview')==['1']
                return self.send(200,row['content'],row['media_type'],None if preview else row['name'])
            if u.path.startswith('/attachment/'):
                with store.lock:row=store.db.execute('SELECT name,content,media_type FROM attachments WHERE id=?',(int(u.path.split('/')[-1]),)).fetchone()
                if not row:raise ValidationError('Attachment not found.')
                return self.send(200,row['content'],row['media_type'],row['name'])
            if u.path.startswith('/document/'):
                _,_,kind,id,format=u.path.split('/')
                if kind not in ('invoice','payment') or format not in ('html','pdf'):raise ValidationError('Invalid document URL.')
                with store.lock:
                    table='invoices' if kind=='invoice' else 'payments'
                    row=store.db.execute(f'SELECT * FROM {table} WHERE id=?',(int(id),)).fetchone()
                    if not row:raise ValidationError('Document not found.')
                    content=row[format]
                    if content is None:
                        inv=store.invoice(row['invoice_id'] if kind=='payment' else int(id))
                        if inv['kind']!='sale':raise ValidationError('Payable generation is no longer available. Upload supplier documents through Expense journal.')
                        if kind=='payment':raise ValidationError('The saved receipt is unavailable.')
                        inv['data']['company'].update({k:store.party('companies',inv['company_id']).get(k,'') for k in DOCUMENT_COMPANY_FIELDS})
                        content=render_document(inv,draft=True)
                        if format=='pdf':content=store.pdf_renderer(content)
                return self.send(200,content,'application/pdf' if format=='pdf' else 'text/html; charset=utf-8',(row['number'] or 'Draft')+'.pdf' if format=='pdf' else None)
            p=ROOT/'static'/('index.html' if u.path=='/' else u.path.removeprefix('/static/'))
            if (u.path=='/' or u.path.startswith('/static/')) and p.resolve().is_relative_to((ROOT/'static').resolve()) and p.is_file():
                content=p.read_bytes()
                if u.path=='/' and self.server.dev:content=content.replace(b'</body>',b'<script src="/static/dev-reload.js"></script></body>')
                return self.send(200,content,mimetypes.guess_type(p)[0] or 'application/octet-stream')
            self.send(404,{'error':'Not found.'})
        except (ValidationError,ValueError) as e:self.send(400,{'error':str(e)})
        except Exception as e:
            print(type(e).__name__,str(e),flush=True);self.send(500,{'error':'Unable to complete this action. See the local app log.'})

    def do_POST(self):
        if not self.allowed():return
        if self.headers.get('Origin')!=f'http://127.0.0.1:{self.server.server_port}':return self.send(403,{'error':'Request origin rejected.'})
        try:
            n=int(self.headers.get('Content-Length','0'))
            if urllib.parse.urlsplit(self.path).path=='/api/restore' and self.headers.get_content_type()=='application/vnd.sqlite3':
                if n<=0:raise ValidationError('Choose a LocalBooks backup.')
                content=self.rfile.read(n)
                if len(content)!=n:raise ValidationError('The backup upload was incomplete. Try again.')
                return self.send(200,self.server.store.restore(content,self.headers.get('X-LocalBooks-Confirmation','')))
            if not 0<n<=150*1024*1024:raise ValidationError('Request is too large or empty.')
            data=json.loads(self.rfile.read(n))
            s=self.server.store;p=urllib.parse.urlsplit(self.path).path
            if p=='/api/company':result=s.save_party('companies',data)
            elif p=='/api/contact':result=s.save_party('contacts',data)
            elif p=='/api/compliance/profile':result=compliance.save_profile(s,data)
            elif p=='/api/compliance/record':result=compliance.save_record(s,data)
            elif p=='/api/compliance/attach':result=compliance.attach(s,data)
            elif p=='/api/scan':
                try:raw=base64.b64decode(data.get('content',''),validate=True)
                except (ValueError,TypeError):raise ValidationError('Invalid file encoding.')
                if not 0<len(raw)<=MAX_FILE:raise ValidationError('Files must be between 1 byte and 20 MB.')
                with s.lock:
                    companies=[dict(id=r['id'],**json.loads(r['data'])) for r in s.db.execute('SELECT id,data FROM companies')]
                    contacts=s.contacts('supplier')
                try:result=scan_document(raw,companies=companies,contacts=contacts,preview=data.get('preview') is True)
                except ScanError as error:raise ValidationError(str(error))
                result['sha256']=hashlib.sha256(raw).hexdigest()
                with s.lock:
                    s.document_scans[result['sha256']]={k:v for k,v in result.items() if k not in ('text','previews')}
                    while len(s.document_scans)>32:s.document_scans.pop(next(iter(s.document_scans)))
            elif p=='/api/bank/scan':
                try:raw=base64.b64decode(data.get('content',''),validate=True)
                except (ValueError,TypeError):raise ValidationError('Invalid file encoding.')
                if not 0<len(raw)<=MAX_FILE:raise ValidationError('Choose a statement up to 20 MB.')
                with self.server.scan_lock:
                    if any(j['status']=='scanning' for j in self.server.scan_jobs.values()):raise ValidationError('A statement is already being scanned. Finish or cancel that scan first.')
                    self.server.scan_jobs=dict(list((k,j) for k,j in self.server.scan_jobs.items() if time.time()-j['created']<3600)[-2:])
                    key=secrets.token_urlsafe(20);job=dict(status='scanning',page=0,pages=0,created=time.time(),cancel=False);self.server.scan_jobs[key]=job
                def work():
                    def progress(page,pages):
                        with self.server.scan_lock:
                            if job['cancel']:raise ScanError('Scan cancelled.')
                            job.update(page=page,pages=pages)
                    try:
                        scan=scan_statement(raw,data.get('name',''),progress=progress);scan['sha256']=hashlib.sha256(raw).hexdigest()
                        with self.server.scan_lock:job.update(status='cancelled' if job['cancel'] else 'complete',result=scan if not job['cancel'] else None)
                    except Exception as error:
                        with self.server.scan_lock:job.update(status='cancelled' if job['cancel'] else 'error',error=str(error) if isinstance(error,(ScanError,ValueError)) else 'Could not scan this statement. Try CSV or a clearer file.')
                threading.Thread(target=work,daemon=True).start();result=dict(job_id=key)
            elif p=='/api/bank/scan/cancel':
                with self.server.scan_lock:
                    job=self.server.scan_jobs.get(data.get('job_id'))
                    if job:job['cancel']=True
                result=dict(ok=True)
            elif p=='/api/bank/check':result=s.check_bank_rows(data)
            elif p=='/api/bank/import':result=s.import_bank(data)
            elif p=='/api/bank/entry':result=s.save_bank_entry(data)
            elif p=='/api/bank/attach':result=s.attach_bank_document(data)
            elif p=='/api/document/review':result=s.review_attachment(data)
            elif p=='/api/journal':result=s.save_journal(data)
            elif p=='/api/journal/attach':result=s.attach_journal_document(data)
            elif p=='/api/journal/duplicate':result=s.mark_expense_duplicate(data)
            elif p=='/api/catalog':result=s.save_catalog_item(data)
            elif p=='/api/catalog/delete':result=s.delete_catalog_item(data.get('id'),data.get('company_id'))
            elif p=='/api/draft':result=s.save_draft(data)
            elif p=='/api/invoice/duplicate':result=s.duplicate_invoice(int(data['id']))
            elif p=='/api/issue':result=s.issue(int(data['id']),data.get('number',''),data.get('revision'),data.get('tax_confirmed') is True)
            elif p=='/api/payment':result=s.add_payment(int(data['id']),data)
            elif p=='/api/void':result=s.void(int(data['id']),data.get('reason'))
            elif p=='/api/reverse':result=s.reverse_payment(int(data['id']),int(data['payment_id']),data.get('reason'))
            elif p=='/api/attach':result=s.attach(int(data['id']),data)
            elif p=='/api/restore':
                try:content=base64.b64decode(data.get('content',''),validate=True)
                except Exception:raise ValidationError('Invalid backup file.')
                result=s.restore(content,data.get('confirmation'))
            elif p=='/api/shutdown':
                self.send(200,{'ok':True});threading.Thread(target=self.server.shutdown,daemon=True).start();return
            else:raise ValidationError('Unknown action.')
            self.send(200,result)
        except (ValidationError,KeyError,ValueError,TypeError) as e:self.send(400,{'error':str(e)})
        except Exception as e:
            print(type(e).__name__,str(e),flush=True);self.send(500,{'error':'Action was not saved. '+str(e)[:300]})


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',default=str(ROOT/'data'));parser.add_argument('--port',type=int,default=8765);parser.add_argument('--open',action='store_true');parser.add_argument('--dev',action='store_true');args=parser.parse_args()
    os.umask(0o077)
    d=Path(args.data);d.mkdir(parents=True,exist_ok=True)
    lock=open(d/'server.lock','a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        if args.open:
            try:webbrowser.open(json.loads((d/'runtime.json').read_text())['url'])
            except Exception:pass
        return
    store=Store(d)
    try:server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    except OSError:server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    server.scan_jobs={};server.scan_lock=threading.Lock()
    server.store=store;server.dev=args.dev;server.token=os.environ.get('LOCALBOOKS_DEV_TOKEN') if args.dev else secrets.token_urlsafe(32)
    if not server.token:server.token=secrets.token_urlsafe(32)
    server.backend_version=hashlib.sha256(b''.join(p.read_bytes() for p in sorted([*ROOT.glob('*.py'), ROOT/'pdf.mjs']))).hexdigest() if args.dev else ''
    url=f'http://127.0.0.1:{server.server_port}/?key={server.token}'
    (d/'runtime.json').write_text(json.dumps({'url':url,'pid':os.getpid()}))
    print(f'LocalBooks listening on 127.0.0.1:{server.server_port}',flush=True)
    if args.open:threading.Timer(.4,lambda:webbrowser.open(url)).start()
    try:server.serve_forever()
    finally:server.server_close();store.db.close();(d/'runtime.json').unlink(missing_ok=True)


if __name__=='__main__':main()

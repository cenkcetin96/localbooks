"""Expense evidence and payment reconciliation; source documents remain immutable."""
import datetime as dt
import unicodedata


def identity(value):
    return ''.join(c for c in unicodedata.normalize('NFKC',str(value or '')).casefold() if unicodedata.category(c)[0] in 'LMN')


def same_supplier(a,b):
    ta=identity(a.get('supplier_tax_id'));tb=identity(b.get('supplier_tax_id'))
    if ta and tb:return ta==tb
    if a.get('supplier_id') and b.get('supplier_id') and a['supplier_id']==b['supplier_id']:return True
    return bool(identity(a.get('supplier'))) and identity(a.get('supplier'))==identity(b.get('supplier'))


def duplicate_candidates(entries,candidate,exclude=None):
    out=[]
    for e in entries:
        if e['id']==exclude or e.get('duplicate_of') or e['company_id']!=candidate.get('company_id'):continue
        shared_hash=candidate.get('sha256') and candidate['sha256'] in {d.get('sha256') for d in e.get('documents',[])}
        same= e.get('currency')==candidate.get('currency') and same_supplier(e,candidate)
        number=identity(candidate.get('document_number'))
        exact_number=same and number and number==identity(e.get('document_number'))
        close=False
        if same and e.get('amount_minor')==candidate.get('amount_minor'):
            try:close=abs((dt.date.fromisoformat(e['date'])-dt.date.fromisoformat(candidate.get('date',''))).days)<=7
            except (ValueError,TypeError):pass
        if shared_hash or exact_number or close:out.append(dict(id=e['id'],reason='This file is already attached' if shared_hash else 'Same supplier and document number' if exact_number else 'Same supplier and amount within 7 days',exact=bool(shared_hash or exact_number)))
    return sorted(out,key=lambda r:not r['exact'])


def manual_cash(e):
    return e.get('paid_amount_minor',0) if e.get('payment_status')=='paid' and e.get('payment_date') else 0


def payment_limit(e):
    return e['paid_amount_minor'] if e.get('payment_status')=='paid' and e.get('paid_amount_minor',0)>0 else e['amount_minor']

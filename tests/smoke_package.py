"""Exercise an already-launched isolated native package; never uses real data."""
import base64
import csv
import datetime as dt
import io
import json
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile

def main():
    root=Path(sys.argv[1] if len(sys.argv)>1 else '/tmp/LocalBooks-final-package-qa')
    runtime=json.loads((root/'data/runtime.json').read_text())
    u=urllib.parse.urlsplit(runtime['url']);base=f'{u.scheme}://{u.netloc}'
    key=urllib.parse.parse_qs(u.query)['key'][0];checks=[]
    def request(path,data=None,auth=True,origin=None,host=None,expected=200):
        headers={'Cookie':'localbooks='+key} if auth else {}
        if data is not None:headers.update({'Content-Type':'application/json','Origin':origin or base})
        if host:headers['Host']=host
        req=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
        try:r=urllib.request.urlopen(req,timeout=40)
        except urllib.error.HTTPError as error:r=error
        body=r.read();assert r.status==expected,(path,r.status,body[:200])
        return (json.loads(body) if r.headers.get_content_type()=='application/json' else body),r.headers
    request('/api/state',auth=False,expected=403);checks.append('Unauthenticated read rejected')
    request('/api/state',host='untrusted.example',expected=403);checks.append('Foreign Host rejected')
    request('/api/company',{'name':'No','code':'NO'},origin='https://untrusted.example',expected=403);checks.append('Foreign Origin rejected')
    request('/static/../app.py',expected=404);checks.append('Static path traversal rejected')
    c,_=request('/api/company',{'name':'Package QA company','code':'QA'})
    sample = Path(__file__).parent / 'fixtures/thai-invoices/03-image-only.pdf'
    encoded = base64.b64encode(sample.read_bytes()).decode()
    scan,_ = request('/api/scan', dict(content=encoded))
    assert scan['suggested_supplier'] == 'บริษัท ตัวอย่างสยาม จำกัด'
    assert scan['suggested_date'] == '2026-11-23' and scan['suggested_vat_amount'] == '35.00'
    expense,_ = request('/api/journal', dict(
        company_id=c['id'], supplier=scan['suggested_supplier'], supplier_tax_id=scan['suggested_supplier_tax_id'],
        date=scan['suggested_date'], document_number=scan['suggested_number'], amount=scan['suggested_amount'],
        currency=scan['suggested_currency'], vat_amount=scan['suggested_vat_amount'], document_type='invoice',
        payment_status='unpaid', name=sample.name, content=encoded))
    bill = dict(id=expense['id'], supplier=scan['suggested_supplier'], gross_minor=53500,
                saved_vat_minor=3500, currency='THB', due_date='2026-12-23')
    args = dict(company_id=c['id'], account='5299100', tax='QA VAT 7%', bills=[bill])
    xero,_ = request('/api/journal/vendor-bills', dict(args, target='xero'))
    assert xero.startswith(b'\xef\xbb\xbf')
    row, = csv.DictReader(io.StringIO(xero.decode('utf-8-sig')))
    assert row['ContactName'] == scan['suggested_supplier'] and row['UnitAmount'] == '500.00' and row['TaxAmount'] == '35.00'
    odoo,_ = request('/api/journal/vendor-bills', dict(args, target='odoo'))
    row, = csv.DictReader(io.StringIO(odoo.decode()))
    assert row['Partner'] == scan['suggested_supplier'] and row['Invoice Lines/Unit Price'] == '500.00'
    checks.append('Packaged image-only Thai invoice OCR, persisted VAT and both vendor-bill exports')
    contact,_=request('/api/contact',{'name':'Package QA customer'})
    data=dict(company_id=c['id'],contact_id=contact['id'],kind='sale',currency='THB',issue_date=dt.date.today().isoformat(),lines=[dict(description='Native package check — ทดสอบ',quantity='0.5',unit_price='0.01')],contact_details={'name':'Inline package customer'})
    invoice,_=request('/api/draft',data);assert invoice['total_minor']==1;assert invoice['data']['contact']['name']=='Inline package customer';checks.append('Packaged inline draft validation and half-up rounding')
    invoice,_=request('/api/issue',dict(id=invoice['id'],revision=invoice['revision']));checks.append('Packaged Chrome PDF generation and numbering')
    invoice,_=request('/api/payment',dict(id=invoice['id'],amount='0.01',date=dt.date.today().isoformat(),method='Test only',request_key='package-smoke'))
    assert invoice['payment_status']=='paid';checks.append('Packaged receipt generation and full payment')
    receipt = invoice['payments'][0]
    receipt_number = f"QA-RE-{data['issue_date'][:7]}-00001"
    assert receipt['number'] == receipt_number
    pdf, headers = request(f"/document/payment/{receipt['id']}/pdf")
    assert pdf.startswith(b'%PDF') and f'{receipt_number}.pdf' in headers['Content-Disposition']
    html,headers=request('/document/invoice/'+str(invoice['id'])+'/html');assert 'sandbox allow-scripts' in headers['Content-Security-Policy'];checks.append('Saved PDF retrieval and sandboxed HTML preview')
    invoice,_=request('/api/attach',dict(id=invoice['id'],payment_id=invoice['payments'][0]['id'],name='proof.txt',content=base64.b64encode(b'package proof').decode()))
    attached,_=request('/attachment/'+str(invoice['attachments'][0]['id']));assert attached==b'package proof';checks.append('Packaged linked attachment round trip')
    company_id, invoice_id = c['id'], invoice['id']
    work, _ = request(f'/api/accounting?company={company_id}&kind=invoice&id={invoice_id}')
    saved, _ = request('/api/accounting/entry', dict(work['automatic'], company_id=company_id, source_kind='invoice', source_id=invoice_id, revision=work['revision']))
    assert saved['saved'] and sum(line['debit_minor'] for line in saved['entry']['lines']) == 1
    assert sum(line['credit_minor'] for line in saved['entry']['lines']) == 1
    mappings, _ = request(f'/api/accounting/thai?company={company_id}')
    assert 'pnd2' in mappings['forms'] and len(mappings['accounts']) == 39
    for script in ('accounting.js', 'thai-reporting.js'):
        body, _ = request(f'/static/{script}')
        assert body
    pack, _ = request(f"/api/compliance/export?company={company_id}&year={data['issue_date'][:4]}")
    with zipfile.ZipFile(io.BytesIO(pack)) as archive:
        assert archive.read(f"receipts/{receipt['id']}/{receipt_number}.pdf") == pdf
        assert {'accounting-entries.csv', 'thai-reporting-mappings.csv', 'dbd-mapped-movements.csv'} <= set(archive.namelist())
    checks.append('Packaged accounting, Thai reporting mappings and accountant export')
    backup,_=request('/api/backup');assert backup.startswith(b'SQLite format 3\0')
    request('/api/restore',dict(content=base64.b64encode(b'bad').decode(),confirmation='RESTORE'),expected=400)
    restored,_=request('/api/restore',dict(content=base64.b64encode(backup).decode(),confirmation='RESTORE'));assert Path(restored['safety_copy']).exists();checks.append('Packaged backup validation, restore, and safety copy')
    state,_=request('/api/state');assert len(state['invoices'])==1;assert state['invoices'][0]['payment_status']=='paid'
    out=Path(__file__).resolve().parents[1]/'output/verification/package-smoke.json'
    out.write_text(json.dumps({'passed':len(checks),'checks':checks,'support_directory':str(root),'host':u.netloc},indent=2))
    print(json.dumps({'passed':len(checks),'checks':checks},indent=2))
    request('/api/shutdown',{})

if __name__=='__main__':main()

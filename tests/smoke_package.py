"""Exercise an already-launched isolated native package; never uses real data."""
import base64
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request

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
    contact,_=request('/api/contact',{'name':'Package QA customer'})
    data=dict(company_id=c['id'],contact_id=contact['id'],kind='sale',currency='THB',issue_date=dt.date.today().isoformat(),lines=[dict(description='Native package check — ทดสอบ',quantity='0.5',unit_price='0.01')],contact_details={'name':'Inline package customer'})
    invoice,_=request('/api/draft',data);assert invoice['total_minor']==1;assert invoice['data']['contact']['name']=='Inline package customer';checks.append('Packaged inline draft validation and half-up rounding')
    invoice,_=request('/api/issue',dict(id=invoice['id'],revision=invoice['revision']));checks.append('Packaged Chrome PDF generation and numbering')
    invoice,_=request('/api/payment',dict(id=invoice['id'],amount='0.01',date=dt.date.today().isoformat(),method='Test only',request_key='package-smoke'))
    assert invoice['payment_status']=='paid';checks.append('Packaged receipt generation and full payment')
    pdf,_=request('/document/payment/'+str(invoice['payments'][0]['id'])+'/pdf');assert pdf.startswith(b'%PDF')
    html,headers=request('/document/invoice/'+str(invoice['id'])+'/html');assert 'sandbox allow-scripts' in headers['Content-Security-Policy'];checks.append('Saved PDF retrieval and sandboxed HTML preview')
    invoice,_=request('/api/attach',dict(id=invoice['id'],payment_id=invoice['payments'][0]['id'],name='proof.txt',content=base64.b64encode(b'package proof').decode()))
    attached,_=request('/attachment/'+str(invoice['attachments'][0]['id']));assert attached==b'package proof';checks.append('Packaged linked attachment round trip')
    backup,_=request('/api/backup');assert backup.startswith(b'SQLite format 3\0')
    request('/api/restore',dict(content=base64.b64encode(b'bad').decode(),confirmation='RESTORE'),expected=400)
    restored,_=request('/api/restore',dict(content=base64.b64encode(backup).decode(),confirmation='RESTORE'));assert Path(restored['safety_copy']).exists();checks.append('Packaged backup validation, restore, and safety copy')
    state,_=request('/api/state');assert len(state['invoices'])==1;assert state['invoices'][0]['payment_status']=='paid'
    out=Path(__file__).resolve().parents[1]/'output/verification/package-smoke.json'
    out.write_text(json.dumps({'passed':len(checks),'checks':checks,'support_directory':str(root),'host':u.netloc},indent=2))
    print(json.dumps({'passed':len(checks),'checks':checks},indent=2))
    request('/api/shutdown',{})

if __name__=='__main__':main()

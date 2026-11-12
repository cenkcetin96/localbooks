"""Generate local visual fixtures; never inserts sample data into the real database."""
import copy
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from documents import render_document,make_pdf

out=Path(__file__).resolve().parents[1]/'output/verification'
out.mkdir(parents=True,exist_ok=True)
issuer=dict(name='Example Company Co., Ltd.',address='1256 Example Street\nWatthana, Bangkok 10110\nThailand',email='billing@example.test',tax_label='Tax ID',tax_id='DEMO ONLY',logo_text='EC')
customer=dict(name='Example Customer Co., Ltd.',address='42 Example Road, Khlong Toei\nWatthana\nBangkok 10110\nThailand',email='customer@example.test',tax_label='Tax ID',tax_id='DEMO ONLY')
line=dict(description='Consulting services (per project)',service='Oct 5–Nov 5, 2026',quantity='1',unit_price='6541.12',total_minor=654112)
payment=dict(id=1,number='DEMO-REC-2026-0001',amount_minor=654112,data=dict(date='2026-10-08',method='Bank transfer'),reversed=None)
inv=dict(id=1,kind='sale',status='issued',number='DEMO-INV-2026-0001',currency='THB',total_minor=654112,paid_minor=654112,balance_minor=0,payments=[payment],data=dict(company=issuer,contact=customer,lines=[line],currency='THB',issue_date='2026-10-08',due_date='2026-10-22'))
for name,p in [('receipt-example',payment),('invoice-example',None)]:
    html=render_document(inv,p);(out/(name+'.html')).write_text(html);(out/(name+'.pdf')).write_bytes(make_pdf(html));print(name,flush=True)
long=copy.deepcopy(inv);long['data']['company']['name']='บริษัท ตัวอย่าง จำกัด';long['data']['contact']['name']='ลูกค้าตัวอย่าง';long['data']['lines']=[dict(line,description=f'Item {i+1:02}: บริการออกแบบ — design and production services, including review, preparation, and local delivery of the agreed project.',service='October 2026') for i in range(30)];long['total_minor']=654112*30
html=render_document(long);(out/'multipage-thai.html').write_text(html);(out/'multipage-thai.pdf').write_bytes(make_pdf(html));print('multipage-thai',flush=True)

# Exercise full-width history pagination and the oversized summary fallback.
many=copy.deepcopy(inv)
many['payments']=[dict(payment,id=i+1,number=f'HISTORY-{i+1:03}',amount_minor=10000,data=dict(date='2026-10-08',method='Bank transfer',reference=f'Transfer reference {i+1:03}')) for i in range(35)]
closing=copy.deepcopy(inv)
closing['data'].update(payment_instructions='\n'.join(f'Bank line {i+1:02}' for i in range(20)),notes='\n'.join(f'Note line {i+1:02}' for i in range(45)))
large=copy.deepcopy(inv)
large['data']['lines']=[dict(line,quantity='1',unit_price='9999999999.99',total_minor=999999999999)]
large['total_minor']=999999999999
for name,sample,p,required in [
    ('long-closing',closing,None,['Bank line 20','Note line 45','Authorized by','Received by']),
    ('large-amount',large,None,['9,999,999,999.99']),
]:
    html=render_document(sample,p);(out/(name+'.html')).write_text(html)
    target=out/(name+'.pdf');target.write_bytes(make_pdf(html))
    import subprocess
    from documents import shutil_which
    extracted=subprocess.check_output([shutil_which('pdftotext'),'-layout',str(target),'-'],text=True)
    for value in required:assert value in extracted,(name,value)
    print(name+' content preserved',flush=True)

# Short receipts stay on one page; longer receipts preserve every item and payment.
receipt=copy.deepcopy(inv)
receipt['data']['company'].update(bank_name='Example Bank',bank_account_name='Example Company',bank_account_number='001-02-0034',bank_branch='Bangkok')
receipt['data']['lines']=[dict(line,description=f'Receipt item {i+1:02}',service='November 2026',unit_price='100.00',total_minor=10000) for i in range(8)]
receipt['total_minor']=80000
receipt['payments']=[dict(payment,amount_minor=77600,data=dict(date='2026-10-08',method='Bank transfer',reference='Current transfer',withholding_minor=2400))]
html=render_document(receipt,receipt['payments'][0]);target=out/'single-page-receipt.pdf';target.write_bytes(make_pdf(html))
info=subprocess.check_output([shutil_which('pdfinfo'),str(target)],text=True)
assert any(row.split()==['Pages:','1'] for row in info.splitlines()),info
extracted=subprocess.check_output([shutil_which('pdftotext'),str(target),'-'],text=True)
for value in ['Receipt item 08','001-02-0034','Withholding tax','24.00','Authorized by','Received by','Page 1 of 1']:
    assert value in extracted,value
target=out/'long-receipt.pdf';target.write_bytes(make_pdf(render_document(many,many['payments'][-1])))
info=subprocess.check_output([shutil_which('pdfinfo'),str(target)],text=True)
assert int(next(row.split(':',1)[1] for row in info.splitlines() if row.startswith('Pages:')))>1
extracted=subprocess.check_output([shutil_which('pdftotext'),str(target),'-'],text=True)
for value in ['HISTORY-001','HISTORY-035','Authorized by','Received by']:
    assert value in extracted,value
print('Short receipt one-page output and long receipt pagination preserve all content',flush=True)

"""Shared invoice and receipt layouts with self-contained, paginated exports."""
import base64
import datetime as dt
from decimal import Decimal
from html import escape
import json
from pathlib import Path
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parent
SYMBOLS={'THB':'THB ','USD':'$','EUR':'€','GBP':'£','SGD':'S$','AUD':'A$','CAD':'C$','JPY':'¥','KWD':'KWD '}
PLACES={'JPY':0,'KWD':3}
from local_tools import tool, browser
CHROME=browser()

def e(value):return escape(str(value or ''),quote=True)
def money(minor,currency):
    p=PLACES.get(currency,2)
    return SYMBOLS.get(currency,currency+' ')+format(Decimal(minor)/(10**p),f',.{p}f')
def pretty_date(value):
    d=dt.date.fromisoformat(value)
    return f'{d:%B} {d.day}, {d.year}'
def party(p):
    content=f'<b>{e(p["name"])}</b>'
    if p.get('legal_name'):content+='<div>'+e(p['legal_name'])+'</div>'
    if p.get('address'):content+='<div>'+e(p['address']).replace('\n','<br>')+'</div>'
    if p.get('branch'):content+=f'<div>{e(p["branch"])}</div>'
    if p.get('email'):content+=f'<div>{e(p["email"])}</div>'
    phone=p.get('contact_phone') or p.get('phone') or ''
    if p.get('contact_name'):
        details=e(p['contact_name'])+(' · '+e(phone) if phone else '')
        content+=f'<div class="party-contact">Contact / ผู้ติดต่อ: {details}</div>'
    elif phone:content+=f'<div>{e(phone)}</div>'
    if p.get('tax_id'):content+=f'<div class="doc-tax">{e(p.get("tax_label") or "Tax ID / เลขประจำตัวผู้เสียภาษี")} {e(p["tax_id"])}</div>'
    return content

def signature_party(p):
    person=f'<span class="signature-person">{e(p["contact_name"])}</span>' if p.get('contact_name') else ''
    return person+f'<span class="signature-party">{e(p["name"])}</span>'

def bank_account(p):
    fields=[('Bank', 'bank_name'),('Account name', 'bank_account_name'),('Account number', 'bank_account_number'),('Branch', 'bank_branch'),('SWIFT/BIC', 'bank_swift')]
    rows=''.join(f'<div><span>{label}: </span>{e(p[key])}</div>' for label,key in fields if p.get(key))
    logo=''
    if p.get('bank_swift','').upper().startswith('KASITHBK') or 'kasikorn' in p.get('bank_name','').lower() or 'กสิกร' in p.get('bank_name',''):
        data=base64.b64encode((ROOT/'static/banks/kasikorn.svg').read_bytes()).decode()
        logo=f'<img class="bank-logo" alt="Kasikorn Bank logo" src="data:image/svg+xml;base64,{data}">'
    return '<section class="paper-extra paper-bank">'+logo+'<b>Bank account / บัญชีธนาคาร</b>'+rows+'</section>' if rows else ''

def render_document(inv,payment=None,draft=False):
    if inv['kind']!='sale':raise ValueError('Only sales invoices and receipts can be generated. Upload supplier documents through Expense journal.')
    d=inv['data'];currency=inv['currency']
    title='Receipt / ใบเสร็จรับเงิน' if payment else ('Tax invoice / ใบกำกับภาษี' if d.get('tax_invoice') else 'Invoice / ใบแจ้งหนี้')
    number=inv.get('number') or 'DRAFT — not issued'
    company=d['company'];contact=d['contact']
    issuer=company
    billed=contact
    paid=sum(p['amount_minor']+p['data'].get('withholding_minor',0) for p in inv.get('payments',[]) if not p.get('reversed'))
    vat_rate=d.get('vat_rate','')
    line_vat=e(vat_rate)+'%' if vat_rate!='' else '—'
    lines=[]
    for item in d['lines']:
        price=int(Decimal(item['unit_price'])*(10**PLACES.get(currency,2)))
        attributes=[]
        if item.get('code'):attributes.append(e(item['code']))
        if item.get('unit'):attributes.append('Unit: '+e(item['unit']))
        detail='<div class="line-detail">'+' · '.join(attributes)+'</div>' if attributes else ''
        if item.get('service'):detail+=f'<div class="line-detail">{e(item["service"])}</div>'
        lines.append(f'<tr><td>{e(item["description"]).replace(chr(10),"<br>")}{detail}</td><td>{e(item["quantity"])}</td><td>{money(price,currency)}</td><td>{line_vat}</td><td>{money(item["total_minor"],currency)}</td></tr>')
    invoice_label=('Tax invoice no. / เลขที่ใบกำกับภาษี' if d.get('tax_invoice') else 'Invoice no. / เลขที่ใบแจ้งหนี้')
    invoice_meta=f'<div><span>{invoice_label}</span><b>{e(number)}</b></div>'
    if payment:
        meta=f'<div><span>Receipt no. / เลขที่ใบเสร็จ</span><b>{e(payment["number"])}</b></div>'+invoice_meta
        meta+=f'<div><span>Issue date / วันที่ออก</span><span>{pretty_date(payment["data"]["date"])}</span></div><div><span>Payment date / วันที่ชำระ</span><span>{pretty_date(payment["data"]["date"])}</span></div>'
        headline_label='Payment received / รับชำระเงิน'
        headline_detail='Paid on '+pretty_date(payment['data']['date'])
        headline_amount=payment['amount_minor']
    else:
        meta=invoice_meta+f'<div><span>Issue date / วันที่ออก</span><span>{pretty_date(d["issue_date"])}</span></div><div><span>Due date / วันครบกำหนด</span><span>{pretty_date(d["due_date"]) if d.get("due_date") else "—"}</span></div>'
        if d.get('reference'):meta+=f'<div><span>Reference / อ้างอิง</span><span>{e(d["reference"])}</span></div>'
        headline_label='Amount due / ยอดชำระ'
        headline_detail='Due by '+pretty_date(d['due_date']) if d.get('due_date') else ''
        headline_amount=inv['total_minor']
    headline=f'<div><span class="summary-label">{headline_label}</span><span class="summary-detail">{e(headline_detail)}</span></div><strong class="summary-amount">{money(headline_amount,currency)}</strong>'
    subtotal=d.get('subtotal_minor',inv['total_minor'])
    totalrows=f'<div><span>Subtotal</span><span>{money(subtotal,currency)}</span></div>'
    if vat_rate!='':totalrows+=f'<div><span>VAT {e(vat_rate)}% / ภาษีมูลค่าเพิ่ม</span><span>{money(d.get("vat_minor",0),currency)}</span></div>'
    totalrows+=f'<div><span>Total</span><span>{money(inv["total_minor"],currency)}</span></div>'
    if payment:
        withheld=payment['data'].get('withholding_minor',0)
        settled=payment['amount_minor']+withheld
        totalrows+=f'<div><span>Payment received</span><span>{money(payment["amount_minor"],currency)}</span></div><div><span>Withholding tax / หัก ณ ที่จ่าย</span><span>{money(withheld,currency)}</span></div><div class="total-emphasis"><b>Amount settled (this receipt)</b><b>{money(settled,currency)}</b></div>'
        if settled!=inv['total_minor'] or len(inv.get('payments',[]))>1:
            totalrows+=f'<div><span>Settled to date</span><span>{money(paid,currency)}</span></div><div><b>Remaining balance</b><b>{money(inv["total_minor"]-paid,currency)}</b></div>'
    else:totalrows+=f'<div class="total-emphasis"><b>Amount due</b><b>{money(inv["total_minor"],currency)}</b></div>'
    extra=bank_account(company)
    if not payment:
        if d.get('payment_instructions'):extra+='<section class="paper-extra"><b>Payment details / รายละเอียดการชำระเงิน</b><div>'+e(d['payment_instructions']).replace(chr(10),'<br>')+'</div></section>'
        if d.get('notes'):extra+='<section class="paper-extra"><b>Notes / หมายเหตุ</b><div>'+e(d['notes']).replace(chr(10),'<br>')+'</div></section>'
    history=''
    if payment:
        rows=''
        for p in inv.get('payments',[]):
            if p.get('reversed'):continue
            rows+=f'<tr><td data-label="Method">{e(p["data"]["method"])}</td><td data-label="Date">{pretty_date(p["data"]["date"])}</td><td data-label="Payment">{money(p["amount_minor"],currency)}</td><td data-label="Receipt">{e(p["number"])}</td></tr>'
        history=f'<section class="paper-history"><h2>Payment history</h2><table class="paper-items paper-payments"><colgroup><col style="width:30%"><col style="width:23%"><col style="width:21%"><col style="width:26%"></colgroup><thead><tr><th>Payment method</th><th>Date</th><th>Amount paid</th><th>Receipt number</th></tr></thead><tbody>{rows}</tbody></table></section>'
    signature=company.get('signature','')
    signature_image=f'<img src="{e(signature)}" alt="Company signature">' if signature.startswith(('data:image/png;base64,','data:image/jpeg;base64,')) else ''
    signed_date=pretty_date(payment['data']['date'] if payment else d['issue_date'])
    signatures='<section class="paper-signatures"><div><div>'+signature_image+'</div><b>Authorized by / ผู้อนุมัติ (ผู้ขาย)</b>'+signature_party(company)+'<span class="signature-date">Date / วันที่: '+signed_date+'</span></div><div><div></div><b>Received by / ผู้รับเอกสาร (ลูกค้า)</b>'+signature_party(contact)+'<span class="signature-date">Date / วันที่: '+signed_date+'</span></div></section>'
    logohtml=f'<div class="paper-logo">{e(issuer.get("logo_text") or "")}</div>'
    if issuer.get('logo') and issuer['logo'].startswith(('data:image/png;base64,','data:image/jpeg;base64,')):
        logohtml=f'<img class="paper-logo image" alt="Company logo" src="{e(issuer["logo"])}">'
    heading=e(title).replace(' / ','<span class="title-thai">',1)+('</span>' if ' / ' in title else '')
    identity=title.split(' / ')[0]+' · '+(payment['number'] if payment else number)
    shared_css=(ROOT/'static/document.css').read_text()
    summary='<section class="paper-totals">'+totalrows+'</section>'
    if not payment and extra:summary='<div class="paper-summary"><div>'+extra+'</div>'+summary+'</div>'
    if payment:
        summary='<div class="paper-summary"><div>'+extra+'</div>'+summary+'</div>'
    html='''<!doctype html><html lang="en"><head><meta charset="utf-8"><title>'''+e(identity)+'''</title><style>'''+shared_css+'''
body{margin:0;background:#f1f1f1}.sheet{height:841.89pt;overflow:hidden;box-shadow:0 3pt 18pt #20202014}.content{display:flow-root}.source{display:none}
@page{size:A4;margin:0}@media print{body{background:white}.sheet{margin:0;box-shadow:none;break-after:page}.sheet:last-child{break-after:auto}}
</style></head><body><div id="source" class="source"><div class="dochead"><header class="paper-header"><h1>'''+heading+'''</h1>'''+logohtml+'''</header><div class="paper-metadata">'''+meta+'''</div><div class="paper-parties"><div><span class="party-label">'''+'From / ผู้ขาย'+'''</span>'''+party(issuer)+'''</div><div><span class="party-label">'''+'Bill to / ลูกค้า'+'''</span>'''+party(billed)+'''</div></div></div><div class="paper-headline">'''+headline+'''</div><table class="paper-items"><colgroup><col style="width:40%"><col style="width:8%"><col style="width:19%"><col style="width:11%"><col style="width:22%"></colgroup><thead><tr><th>Description / รายการ</th><th>Qty</th><th>Unit price</th><th>VAT %</th><th>Amount before VAT</th></tr></thead><tbody>'''+''.join(lines)+'''</tbody></table>'''+summary+history+signatures+'''</div><script>
const documentTitle='''+json.dumps(identity).replace('<','\\u003c')+''';
const isDraft='''+('true' if draft else 'false')+''';
const singlePageReceipt='''+('true' if payment else 'false')+''';
async function paginate(){await document.fonts.ready;await Promise.all([...document.images].map(img=>img.decode().catch(()=>{})));let pages=[],content;
function page(first=false){let sheet=document.createElement('section');sheet.className='paper sheet'+(singlePageReceipt?' paper-receipt':'');sheet.innerHTML='<div class="content"></div><footer class="paper-footer"><span></span><span></span></footer>'+(isDraft?'<div class="draft">DRAFT — not issued</div>':'');document.body.append(sheet);pages.push(sheet);content=sheet.querySelector('.content');sheet.querySelector('.paper-footer').firstElementChild.textContent=documentTitle;if(!first){let h=document.createElement('div');h.className='continued';h.textContent=documentTitle+' (continued)';content.append(h)}return content}
function fits(){return content.getBoundingClientRect().bottom<=pages.at(-1).querySelector('.paper-footer').getBoundingClientRect().top-16}
page(true);const source=document.querySelector('#source'),signatures=source.querySelector('.paper-signatures'),beforeSignatures=signatures?.previousElementSibling;
if(singlePageReceipt){
 content.append(...source.children);
 if(!fits())pages[0].classList.add('receipt-tight');
 for(const scale of [1,.98,.96,.94,.92,.90,.88]){
  content.style.width=(100/scale)+'%';content.style.transformOrigin='top left';content.style.transform=`scale(${scale})`;
  if(fits()){source.remove();pages[0].querySelector('.paper-footer').lastElementChild.textContent='Page 1 of 1';document.documentElement.dataset.ready='true';return}
 }
 // Long invoices and payment histories must still be payable. Reuse the normal pagination.
 source.append(...content.children);content.removeAttribute('style');pages[0].classList.remove('receipt-tight');
}
function fitsWithSignatures(){if(!signatures)return fits();const copy=signatures.cloneNode(true);content.append(copy);const result=fits();copy.remove();return result}
function place(block){
 if(block.tagName==='TABLE'||block.classList.contains('paper-history')){
  let table=block.tagName==='TABLE'?block:block.querySelector('table');let rows=[...table.querySelectorAll('tbody tr')];let heading=block.querySelector('h2');let container,clone,body;
  function tablePart(){container=heading?document.createElement('section'):null;if(container){container.className='paper-history';container.append(heading.cloneNode(true));content.append(container)}clone=table.cloneNode(true);clone.querySelector('tbody').replaceChildren();(container||content).append(clone);body=clone.querySelector('tbody')}
  tablePart();
  for(const row of rows){body.append(row);if(!fits()||(block===beforeSignatures&&row===rows.at(-1)&&!fitsWithSignatures())){row.remove();if(!body.children.length)(container||clone).remove();page();tablePart();body.append(row);if(!fits())throw new Error('One row is too tall. Shorten its description.')}}
 }else{content.append(block);if(!fits()||(block===beforeSignatures&&!fitsWithSignatures())){block.remove();page();content.append(block);if(!fits()){if(block.classList.contains('paper-summary')){block.remove();for(const child of [...block.children]){if(child.className)place(child);else for(const nested of [...child.children])place(nested)}}else throw new Error('A section is too long for one page. Shorten its text.')}}}
}
for(const block of [...source.children])place(block);
source.remove();pages.forEach((p,i)=>{const footer=p.querySelector('.paper-footer');footer.firstElementChild.textContent=documentTitle;footer.lastElementChild.textContent=`Page ${i+1} of ${pages.length}`});document.documentElement.dataset.ready='true';
}
paginate().catch(error=>{document.body.dataset.error=error.message;document.body.innerHTML='<p style="padding:30px">Document could not fit: '+error.message+'</p>';document.documentElement.dataset.ready='error'});
</script></body></html>'''
    return html


def make_pdf(html):
    if not Path(CHROME).exists():raise RuntimeError('Google Chrome is required for PDF export on this Mac.')
    with tempfile.TemporaryDirectory(prefix='localbooks-pdf-') as directory:
        tmp=Path(directory);source=tmp/'document.html';output=tmp/'document.pdf';source.write_text(html)
        cmd=[tool('node'),str(ROOT/'pdf.mjs'),str(source),str(output),str(tmp/'profile')]
        import os
        proc=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45,env=dict(os.environ, LOCALBOOKS_CHROME=CHROME))
        if proc.returncode!=0 or not output.exists() or not output.read_bytes().startswith(b'%PDF'):
            error=proc.stderr.decode(errors='replace').strip()[:200]
            raise RuntimeError('PDF generation failed. Nothing was issued. '+error)
        # Layout errors are rejected before any immutable document is committed.
        if shutil_which('pdftotext'):
            result=subprocess.run([shutil_which('pdftotext'),str(output),'-'],capture_output=True,text=True)
            if 'Document could not fit:' in result.stdout:raise RuntimeError(result.stdout.strip())
        return output.read_bytes()

def shutil_which(name):
    path=tool(name)
    return path if Path(path).is_file() else None

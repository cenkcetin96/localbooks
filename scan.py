"""Local text extraction for supplier documents; all suggestions require review."""
import datetime as dt
import base64
from decimal import Decimal, InvalidOperation
import re
from pathlib import Path
import subprocess
import tempfile
import unicodedata

ROOT=Path(__file__).resolve().parent
from local_tools import tool

class ScanError(ValueError):pass

def file_type(content):
    if content.startswith(b'%PDF-'):return 'pdf'
    if content.startswith(b'\x89PNG\r\n\x1a\n'):return 'png'
    if content.startswith(b'\xff\xd8\xff'):return 'jpg'
    raise ScanError('Choose a PDF, PNG or JPEG file.')

def run(args,timeout=60):
    try:
        p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,check=True)
        return p.stdout.decode('utf-8',errors='replace')
    except (OSError,subprocess.SubprocessError):raise ScanError('Could not scan this file. Check that it opens and is not password protected.')

def suggest_supplier_tax_id(clean):
    # Stay inside the labelled seller block; never use the buyer's tax ID.
    seller=False
    for line in clean.splitlines():
        if re.search(r'(?:customer|bill\s*to|sold\s*to|buyer|ลูกค้า|ผู้ซื้อ)\s*[:：]',line,re.I):break
        if re.search(r'(?:seller|supplier|ผู.?ขาย|ผู้จำหน่าย)\s*[:：]',line,re.I):seller=True
        if seller:
            label=re.search(r'(?:tax\s*(?:id|no\.?|number)|เลข(?:ที่|ประจำตัวผู้เสีย)ภาษี)\s*[:：]?',line,re.I)
            if label:
                found=re.search(r'(?<![0-9])([0-9](?:[ -]*[0-9]){12})(?![0-9])',line[label.end():])
                if found:return re.sub(r'[^0-9]','',found.group(1))
    return ''

def extract_pages(content, *, max_pages=10, progress=None):
    kind=file_type(content)
    with tempfile.TemporaryDirectory(prefix='localbooks-scan-') as directory:
        root=Path(directory);source=root/('source.'+kind);source.write_bytes(content)
        if kind=='pdf':
            info=run([tool('pdfinfo'),str(source)])
            pages=re.search(r'^Pages:\s*(\d+)',info,re.M)
            if not pages or not 1<=int(pages.group(1))<=max_pages:raise ScanError(f'PDFs must contain between 1 and {max_pages} pages.')
            texts=[]
            for page in range(1,int(pages.group(1))+1):
                if progress:progress(page,int(pages.group(1)))
                text=run([tool('pdftotext'),'-f',str(page),'-l',str(page),'-layout',str(source),'-'])
                visible=re.sub(r'\s','',text)
                private_glyphs=len(re.findall(r'[\ue000-\uf8ff\ufffd]',text))
                if len(visible)<40 or private_glyphs>len(visible)*.1:
                    image=root/'page.png'
                    run([tool('pdftoppm'),'-f',str(page),'-l',str(page),'-singlefile','-png','-r','200',str(source),str(root/'page')],timeout=90)
                    text=run([tool('tesseract'),str(image),'stdout','--tessdata-dir',str(ROOT/'ocr/tessdata'),'-l','tha+eng'],timeout=90)
                texts.append(text)
            return texts
        else:
            if progress:progress(1,1)
            extracted=run([tool('tesseract'),str(source),'stdout','--tessdata-dir',str(ROOT/'ocr/tessdata'),'-l','tha+eng'],timeout=90)
            return [extracted]


def scan_document(content, *, companies=(), contacts=(), preview=False):
    extracted='\n\n'.join(extract_pages(content))[:100000].strip()
    if not extracted:raise ScanError('No text could be read. Try a clearer file.')
    result=parse_document_text(extracted,companies=companies,contacts=contacts)
    if preview and file_type(content)=='pdf':
        try:
            with tempfile.TemporaryDirectory(prefix='localbooks-preview-') as directory:
                root=Path(directory);source=root/'source.pdf';source.write_bytes(content)
                run([tool('pdftoppm'),'-f','1','-l','10','-scale-to','1100','-png',str(source),str(root/'page')],timeout=90)
                result['previews']=[];total=0
                for page in sorted(root.glob('page-*.png'),key=lambda p:int(p.stem.split('-')[-1])):
                    raw=page.read_bytes();total+=len(raw)
                    if total>8*1024*1024:break
                    result['previews'].append('data:image/png;base64,'+base64.b64encode(raw).decode())
        except ScanError:pass
    return result

BUYER=r'(?:bill\s*to\b|sold\s*to\b|customer\b|buyer\b|ลูกค[้่๊๋]?า|ผู[้่๊๋]?ซื้อ)'
SELLER=r'(?:seller\b|supplier\b|issued\s*by\b|from\b|ผู[้่๊๋]?ขาย|ผู[้่๊๋]?จำหน่าย)'
TAX=r'(?:tax\s*(?:id|no\.?|number)|(?:TH\s+)?VAT(?:\s*(?:id|no\.?|number))?|เลข(?:ที่|ที|ประจำตัวผู้เสีย)ภาษี(?:อากร)?)'
DATE=r'(?:issue\s*date|invoice\s*date|document\s*date|receipt\s*date|date\s*issued|วันที่(?:ออก(?:เอกสาร)?|เอกสาร|ใบเสร็จ)?|วันที(?:ออก)?|\bdate\b)'
NUMBER=r'(?:\b(?:invoice|receipt|document|doc\.?)(?:\s*(?:number|no\.?|#))|เลขที่(?:เอกสาร|ใบเสร็จ(?:รับเงิน)?|ใบกำกับภาษี|ใบแจ้งหนี้)?|เลขที(?:เอกสาร)?|\bno\.)'


def normalize_text(value):
    value=unicodedata.normalize('NFKC',value).replace('\u0e4d\u0e32','\u0e33').replace('ำา','ำ')
    value=re.sub(r'[\ue000-\uf8ff]','',value)
    return value.translate(str.maketrans('๐๑๒๓๔๕๖๗๘๙','0123456789')).expandtabs(4)


def record_key(value):
    return ''.join(c for c in unicodedata.normalize('NFKC',value).casefold() if unicodedata.category(c)[0] in 'LMN')


def party_blocks(lines):
    end=next((i for i,line in enumerate(lines) if re.search(r'description|รายละเอียด|คำอธิบาย',line,re.I) and re.search(r'qty|quantity|จำนวน',line,re.I)),min(len(lines),45))
    header=lines[:end]
    for i,line in enumerate(header):
        buyer=re.search(BUYER,line,re.I)
        if not buyer:continue
        if buyer.start()>15:
            # Side-by-side parties: retain the seller's left column after "Bill to".
            column=buyer.start()
            seller=[row[:column].rstrip() for row in header]
            billed=[row[column:].strip() for row in header[i:]]
            return seller,billed
        return header[:i],header[i:]
    return header,[]


def tax_id(block):
    for i,line in enumerate(block):
        label=re.search(TAX,line,re.I)
        if not label:continue
        candidates=[line[label.end():]]
        if not candidates[0].strip() and i+1<len(block):candidates.append(block[i+1])
        for candidate in candidates:
            found=re.search(r'(?<!\d)(\d(?:[ -]*\d){12})(?!\d)',candidate)
            if found:return re.sub(r'\D','',found.group(1))
    return ''


def labelled_values(lines,pattern):
    for i,line in enumerate(lines):
        found=re.search(pattern,line,re.I)
        if not found:continue
        tail=line[found.end():].strip(' :：#\t')
        if tail:
            yield line,tail
        if i+1<len(lines) and (not tail or not re.search(r'\d',tail)):
            cells=list(re.finditer(r'\S(?:.*?\S)?(?=\s{2,}|$)',line))
            cell=next((j for j,c in enumerate(cells) if c.start()<=found.start()<c.end()),0)
            start=cells[cell].start() if cells else 0
            end=cells[cell+1].start() if cell+1<len(cells) else None
            yield line,lines[i+1][start:end].strip()


def parse_date(value):
    thai=bool(re.search(r'[ก-๙]',value))
    def make(year,month,day):
        year=int(year)
        if year<100:year=(2500+year-543) if thai and year>=50 else 2000+year
        if year>=2400:year-=543
        try:return dt.date(year,int(month),int(day)).isoformat()
        except ValueError:return ''
    iso=re.search(r'(?<!\d)(\d{4})[/-](\d{1,2})[/-](\d{1,2})(?!\d)',value)
    if iso:return make(*iso.groups())
    numeric=re.search(r'(?<!\d)(\d{1,2})[/-](\d{1,2})[/-](\d{4}|\d{2})(?!\d)',value)
    if numeric:
        day,month,year=numeric.groups();return make(year,month,day)
    months=['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec']
    english=re.search(r'\b([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})\b',value)
    if english and english[1][:3].lower() in months:return make(english[3],months.index(english[1][:3].lower())+1,english[2])
    english=re.search(r'\b(\d{1,2})\s+([A-Za-z]+)\.?\s+(\d{4})\b',value)
    if english and english[2][:3].lower() in months:return make(english[3],months.index(english[2][:3].lower())+1,english[1])
    thai_months=[('มกราคม','ม.ค.'),('กุมภาพันธ์','ก.พ.'),('มีนาคม','มี.ค.'),('เมษายน','เม.ย.'),('พฤษภาคม','พ.ค.'),('มิถุนายน','มิ.ย.'),('กรกฎาคม','ก.ค.'),('สิงหาคม','ส.ค.'),('กันยายน','ก.ย.'),('ตุลาคม','ต.ค.'),('พฤศจิกายน','พ.ย.'),('ธันวาคม','ธ.ค.')]
    for month,names in enumerate(thai_months,1):
        match=re.search(r'(\d{1,2})\s*(?:'+ '|'.join(map(re.escape,names))+r')\s*(?:พ\.ศ\.\s*)?(\d{4}|\d{2})(?!\d)',value)
        if match:return make(match[2],month,match[1])
    return ''


def money_value(value,currency):
    if parse_date(value) and not re.search(r'[฿$€£]|\d+\.\d{2,3}(?!\d)',value):return ''
    places={'JPY':0,'KWD':3}.get(currency,2)
    numbers=re.findall(r'(?<![\w.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,3})?(?![\d.%])',value)
    for raw in reversed(numbers):
        try:
            number=Decimal(raw.replace(',',''))
            if 0<=number<=Decimal('9999999999.99') and number==number.quantize(Decimal(10)**-places):return f'{number:.{places}f}'
        except InvalidOperation:pass
    return ''


def parse_document_text(extracted, *, companies=(), contacts=()):
    clean=normalize_text(extracted);lines=[line.rstrip() for line in clean.splitlines() if line.strip()]
    seller,buyer=party_blocks(lines)
    supplier='';name_index=None
    # The legal issuer header wins over an employee labelled "seller".
    legal=r'บริษัท|ห้างหุ้นส่วน|\b(?:Co\.?\s*,?\s*Ltd\.?|Limited|LLC|Inc\.?|Corporation)\b'
    for i,row in enumerate(seller):
        cell=re.split(r'\s{2,}',row.strip())[0]
        if re.search(legal,cell,re.I):
            supplier=re.sub(r'^'+SELLER+r'\s*[:：]\s*','',cell,flags=re.I);name_index=i;break
    if not supplier:
        for line,value in labelled_values(seller,SELLER+r'\s*[:：]?'):
            value=re.split(r'\s{2,}',value)[0]
            if value and not re.fullmatch(r'/?\s*'+SELLER,value,re.I):supplier=value;break
    if not supplier:
        # A short unlabelled brand above its address is common on card receipts.
        for i,row in enumerate(seller[:12]):
            cell=re.split(r'\s{2,}',row.strip())[0]
            if i+1<len(seller) and re.match(r'\s*\d+\s+\w',seller[i+1]) and re.search(r'[A-Za-zก-๙]',cell) and not re.search(NUMBER+'|'+DATE+r'|receipt|invoice|ใบเสร็จ|ใบแจ้งหนี้',cell,re.I):supplier=cell;name_index=i;break
    branch=re.search(r'สำนักงานใหญ่|head\s*office|สาขา\s*[\w-]+',supplier,re.I)
    supplier_branch=branch.group(0) if branch else ''
    supplier=re.split(r'\s*\((?:สำนักงานใหญ่|head\s*office|สาขา)',supplier,flags=re.I)[0].strip()[:200]
    supplier_tax=tax_id(seller) if supplier else ''
    buyer_tax=tax_id(buyer)
    currency=''
    codes=set(c.upper() for c in re.findall(r'\b(THB|USD|EUR|GBP|SGD|AUD|CAD|JPY|KWD)\b',clean,re.I))
    for pattern,code in [(r'฿|\bbaht\b|บาท','THB'),(r'US\$','USD'),(r'€','EUR'),(r'£','GBP'),(r'S\$','SGD'),(r'A\$','AUD'),(r'C\$','CAD')]:
        if re.search(pattern,clean,re.I):codes.add(code)
    if len(codes)==1:currency=codes.pop()
    receipt=bool(re.search(r'\breceipt\b|ใบเสร็จ', '\n'.join(lines[:12]),re.I))
    doc_type='receipt' if receipt else 'invoice' if re.search(r'\binvoice\b|ใบแจ้งหนี้|ใบกำกับภาษี','\n'.join(lines[:12]),re.I) else 'other'
    dates=[]
    for line,value in labelled_values(lines,DATE):
        label=re.search(DATE,line,re.I)
        if re.search(r'(?:due|payment)\s*$',line[:label.start()],re.I) or re.match(r'\s*(?:paid|ครบกำหนด|ชำระ)',line[label.end():],re.I):continue
        parsed=parse_date(value)
        if parsed:dates.append((3 if re.search(r'issue|invoice|document|วันที่ออก|วันที่เอกสาร',line,re.I) else 1,parsed))
    payment_dates=[parse_date(value) for _,value in labelled_values(lines,r'date\s*paid|payment\s*date|paid\s*on|วันที่ชำระ')]
    payment_date=next((value for value in payment_dates if value),'')
    document_date=max(dates,key=lambda row:row[0])[1] if dates else payment_date if receipt else ''
    numbers=[]
    for line,value in labelled_values(lines,NUMBER):
        if re.search(r'บัญชี|account|ใบอนุญาต',line,re.I):continue
        token=re.match(r'([A-Za-z0-9][A-Za-z0-9_./-]{1,79})(?![A-Za-z0-9])',value)
        if token and re.search(r'\d',token[1]) and not re.fullmatch(r'\d{1,4}[/-]\d{1,2}[/-]\d{2,4}',token[1]):
            preferred=bool(re.search(r'receipt|ใบเสร็จ',line,re.I)) if receipt else bool(re.search(r'invoice|ใบแจ้งหนี้|ใบกำกับภาษี',line,re.I))
            numbers.append((2 if preferred else 1,token[1]))
    number=max(numbers,key=lambda row:row[0])[1] if numbers else ''
    amounts=[];paid_amount=''
    gross=r'grand\s*total|total\s*amount|amount\s*due|^\s*total\b|จำนวนเงิน(?:รวม)?ทั้งสิ้น|รวมทั้งสิ้น|ยอดรวมสุทธิ|รวมเป็นเงิน|จำนวนเงินรวม'
    for line,value in labelled_values(lines,gross):
        if re.search(r'subtotal|before\s*(?:tax|VAT)|total\s*(?:paid|tax|VAT)|ก่อนภาษี',line,re.I):continue
        amount=money_value(value,currency)
        if amount:
            rank=3 if re.search(r'grand\s*total|total\s*amount|จำนวนเงิน(?:รวม)?ทั้งสิ้น|รวมทั้งสิ้น|ยอดรวมสุทธิ',line,re.I) else 2 if re.search(r'^\s*total\b|รวมเป็นเงิน',line,re.I) else 1
            amounts.append((rank,amount))
    for _,value in labelled_values(lines,r'amount\s*paid|total\s*paid|payment\s*received|จำนวนเงินที่ชำระ'):
        paid_amount=money_value(value,currency)
        if paid_amount:break
    amount=max(amounts,key=lambda row:row[0])[1] if amounts else paid_amount
    withheld=[money_value(value,currency) for _,value in labelled_values(lines,r'withholding(?:\s*tax)?|WHT\b|หัก\s*ณ\s*ที[่]?จ[่]?าย')]
    positive_withheld=[Decimal(value) for value in withheld if value and Decimal(value)>0]
    payment_uncertain=bool(positive_withheld and (not amount or not paid_amount or Decimal(amount)-Decimal(paid_amount) not in positive_withheld))
    if payment_uncertain:paid_amount=''
    address=[]
    if name_index is not None:
        for row in seller[name_index+1:name_index+7]:
            cell=re.split(r'\s{2,}',row.strip())[0]
            if re.search(TAX+r'|โทร|phone|\btel\b|@',cell,re.I):break
            if re.search(r'^\d|street|road|bangkok|thailand|united states|ตำบล|อำเภอ|แขวง|เขต|จังหวัด|san francisco',cell,re.I):address.append(cell)
    seller_text='\n'.join(seller)
    email=re.search(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',seller_text)
    phone=re.search(r'(?:โทร\.?|phone|tel\.?)\s*[:：]?\s*(\+?\d[\d ()-]{6,25}\d)',seller_text,re.I)
    description=[]
    in_items=False
    for row in lines:
        if re.search(r'description|รายละเอียด|คำอธิบาย',row,re.I) and re.search(r'qty|quantity|จำนวน',row,re.I):in_items=True;continue
        if not in_items:continue
        if re.search(gross+r'|subtotal|หมายเหตุ|payment\s*history',row,re.I):break
        cells=re.split(r'\s{2,}',row.strip())
        if re.match(r'รวม|มูลค|ยอด|ภาษี|จำนวนเงิน',cells[0]):break
        first=re.sub(r'^\d+[.)]?\s+','',cells[0])
        if re.fullmatch(r'\d+[.)]?',first) and len(cells)>1:first=cells[1]
        if first and re.search(r'[A-Za-zก-๙]',first) and not parse_date(first) and (re.match(r'^\d+[.)]?\s',row.strip()) or len(cells)>1 and money_value(cells[-1],currency)):description.append(first)
        if len(description)==3:break
    if not supplier_branch:
        branch=re.search(r'สำนักงานใหญ่|head\s*office',seller_text,re.I)
        if branch:supplier_branch=branch.group(0)
    result=dict(text=extracted,suggested_date=document_date,suggested_amount=amount,suggested_number=number,suggested_supplier=supplier,suggested_supplier_tax_id=supplier_tax,suggested_supplier_address='\n'.join(address)[:1200],suggested_supplier_email=email.group(0) if email else '',suggested_supplier_phone=phone[1].strip() if phone else '',suggested_supplier_branch=supplier_branch,suggested_currency=currency,suggested_document_type=doc_type,suggested_description='; '.join(description)[:1000],suggested_payment_date=payment_date,suggested_paid_amount=paid_amount,payment_amount_uncertain=payment_uncertain)
    matches=[p for p in contacts if supplier_tax and record_key(p.get('tax_id',''))==record_key(supplier_tax)]
    if not matches and supplier:
        matches=[p for p in contacts if record_key(supplier) in {record_key(p.get('name','')),record_key(p.get('legal_name',''))} and (not supplier_tax or not p.get('tax_id') or record_key(p['tax_id'])==record_key(supplier_tax))]
    if len(matches)==1:result['suggested_supplier_id']=matches[0]['id']
    matches=[p for p in companies if buyer_tax and record_key(p.get('tax_id',''))==record_key(buyer_tax)]
    if not matches and buyer:
        buyer_text=record_key('\n'.join(buyer))
        matches=[p for p in companies if any(len(record_key(p.get(key,'')))>=5 and record_key(p[key]) in buyer_text for key in ('name','legal_name'))]
    if len(matches)==1:
        result['suggested_company_id']=matches[0]['id'];result['suggested_party_role']='supplier'
    return result

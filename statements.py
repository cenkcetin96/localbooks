"""Local, review-first KBank statement extraction. No tax treatment is inferred."""
import csv
from decimal import Decimal, InvalidOperation
import hashlib
import io
import re
from scan import extract_pages,normalize_text,parse_date,ScanError

MAX_ROWS=20000
MONEY=re.compile(r'(?<![\w/.:])[-+]?\d[\d,]*\.\d{2,3}(?![\d.:/])')
DATE=re.compile(r'^\s*(\d{1,2}[/-]\d{1,2}[/-](?:\d{4}|\d{2})|\d{4}-\d{2}-\d{2})(?=\s|$)')
LABELS={
 'date':r'วันที่|\bdate\b|transaction date',
 'description':r'รายละเอียด|\bdescription\b|\bdetails\b|รายการ',
 'debit':r'withdrawal|\bdebit\b|เงินถอน|ถอนเงิน|ยอดถอน',
 'credit':r'\bdeposit\b|\bcredit\b|เงินฝาก|ฝากเงิน|ยอดฝาก',
 'balance':r'(?:outstanding\s+)?balance|ยอดคงเหลือ|คงเหลือ',
 'reference':r'reference|\bref\b|เลขที่อ้างอิง|อ้างอิง|cheque no',
 'amount':r'^amount$|จำนวนเงิน',
 'direction':r'^type$|^direction$|ประเภทรายการ',
 'time':r'\btime\b|เวลา',
}
ACCOUNT_OPEN=r'เปิดบัญชี|account\s+(?:opening|opened)|open(?:ing)?\s+account'
OPEN=r'opening balance|balance brought forward|brought forward|ยอดยกมา|ยอดเงินยกมา|balance b/f|ยอดคงเหลือต้นงวด'
CLOSE=r'closing balance|ยอดยกไป|ยอดคงเหลือปลายงวด'
SUMMARY=r'รวม(?:เงิน|รายการ|ยอด)|total\s+(?:withdrawal|deposit|debit|credit|transaction)|page\s+\d|หน้า\s+\d|หมายเหตุ|คำอธิบาย'

# Match bank publishing/contact boilerplate precisely; keep branch continuations,
# payees, transaction references and any user notes on either side of it.
BANK_FOOTER=re.compile(
    r'\bKBPDF\s*\([^)]{1,100}\)\s*(?:\(\d{2}[-/]\d{2,4}\))?'
    r'|(?:สอบถามข้อมูลเพิ่มเติม\s*)?(?:(?:บุคคลธรรมดา|นิติบุคคล)\s*)?'
    r'K[ -]*(?:BIZ[ -]*)?Contact\s*Center\s*(?:\+66|0)[\d ()-]{7,20}'
    r'|ออกโดย\s+K\s*BIZ\b|\bIssued\s+by\s+K\s*BIZ\b'
    r'|(?:https?://)?(?:www\.)?kasikornbank\.com(?:/[^\s]*)?',re.I)

def clean_bank_description(value, *, preserve_columns=False):
    value=str(value or '')
    cleaned=BANK_FOOTER.sub(lambda match:' '*len(match[0]) if preserve_columns else '',value)
    if preserve_columns or cleaned==value:return cleaned
    return re.sub(r'[ \t]{2,}',' ',cleaned).strip()


def number(value):
    raw=str(value).strip().replace(',','').replace('บาท','').replace('฿','').strip()
    if raw in ('','-','—'):return ''
    try:
        n=Decimal(raw)
        if not n.is_finite() or abs(n)>Decimal('999999999999') or n.as_tuple().exponent < -3:return ''
        return format(n,'f')
    except InvalidOperation:return ''

def statement_date(raw):
    raw=normalize_text(raw).strip()
    found=re.fullmatch(r'(\d{1,2})[/-](\d{1,2})[/-](\d{2})',raw)
    if found and int(found[3])>=40:raw=f'{found[1]}/{found[2]}/{2500+int(found[3])}'
    return parse_date(raw)

def direction_hint(value):
    credit=bool(re.search(r'โอนเข้า|รับโอน|รับเงิน|เงินเข้า|\bdeposit\b|\bcredit\b|^CR$|^C$',value,re.I))
    debit=bool(re.search(r'โอนออก|ถอน|ชำระ|หักบัญชี|ค่าธรรมเนียม|\bwithdrawal\b|\bdebit\b|\bpayment\b|^DR$|^D$',value,re.I))
    return ('in' if credit else 'out') if credit!=debit else ''

def row_values(date,description,*,debit='',credit='',amount='',direction='',balance='',reference='',time='',row_key='',page=1,source_text=''):
    debit=number(debit);credit=number(credit);amount=number(amount);balance=number(balance)
    errors=[]
    if debit and Decimal(debit)<0:errors.append('Negative withdrawal needs review')
    if credit and Decimal(credit)<0:errors.append('Negative deposit needs review')
    if debit and Decimal(debit)!=0 and credit and Decimal(credit)!=0:errors.append('Both withdrawal and deposit have amounts');direction='';amount=''
    elif debit and Decimal(debit)>0:direction='out';amount=debit
    elif credit and Decimal(credit)>0:direction='in';amount=credit
    if amount and Decimal(amount)<0:direction='out';amount=str(-Decimal(amount))
    if not date:errors.append('Check transaction date')
    if not direction:errors.append('Choose money in or out')
    if not amount or Decimal(amount)<=0:errors.append('Check transaction amount')
    return dict(date=date,description=clean_bank_description(description).strip()[:1000],direction=direction,amount=amount,balance=balance,reference=reference.strip()[:200],time=time.strip()[:30],row_key=row_key,page=page,source_text=source_text[:2500],errors=errors)

def metadata(text):
    account=re.search(r'(?:account\s*(?:no\.?|number)|เลขที่บัญชี|เลขบัญชี)\s*[:：]?\s*([\dXx*][\dXx* -]{7,30})',text,re.I)
    currency=re.search(r'\b(THB|USD|EUR|GBP|SGD|AUD|CAD|JPY|KWD)\b',text)
    if not currency and re.search(r'บาท|baht',text,re.I):currency=re.search('THB','THB')
    return dict(account=account[1].strip() if account else '',currency=currency[1] if currency and currency.lastindex else 'THB' if currency else '')

def csv_text(content):
    decoded=None
    encodings=['utf-16'] if content.startswith((b'\xff\xfe',b'\xfe\xff')) else ['utf-8-sig','cp874']
    for encoding in encodings:
        try:decoded=content.decode(encoding);break
        except UnicodeError:continue
    if decoded is None:raise ScanError('Could not read this CSV. Export as UTF-8 CSV from K BIZ.')
    if '\x00' in decoded:raise ScanError('This is not a readable CSV statement.')
    return decoded

def parse_csv(content):
    decoded=csv_text(content)
    try:delimiter=csv.Sniffer().sniff(decoded[:16000],delimiters=',;\t').delimiter
    except csv.Error:delimiter=','
    reader=csv.reader(io.StringIO(decoded),delimiter=delimiter);header=None;mapping={}
    for i,cells in enumerate(reader):
        if i>=80:break
        mapping={key:next((j for j,c in enumerate(cells) if re.search(pattern,normalize_text(c),re.I)),None) for key,pattern in LABELS.items()}
        if mapping['date'] is not None and any(mapping[k] is not None for k in ['debit','credit','amount']):header=i;break
    if header is None:raise ScanError('No transaction columns found. Use a K BIZ CSV with Date, Description, Withdrawal/Deposit or Amount columns.')
    out=[];warnings=[];opening='';closing=''
    def get(cells,key):
        j=mapping.get(key);return cells[j].strip() if j is not None and j<len(cells) else ''
    for i,cells in enumerate(reader,header+2):
        if not any(c.strip() for c in cells):continue
        source=' | '.join(cells);values=MONEY.findall(source)
        if re.search(ACCOUNT_OPEN,source,re.I) and all(Decimal(number(value))==0 for value in values):continue
        if re.search(OPEN,source,re.I):
            if values:opening=number(values[-1])
            continue
        if re.search(CLOSE,source,re.I):
            if values:closing=number(values[-1])
            continue
        if re.search(SUMMARY,source,re.I):continue
        raw_date=get(cells,'date');parsed=statement_date(raw_date)
        if not parsed and not re.search(r'\d',raw_date):continue
        row=row_values(parsed,get(cells,'description'),debit=get(cells,'debit'),credit=get(cells,'credit'),amount=get(cells,'amount'),direction=direction_hint(get(cells,'direction')),balance=get(cells,'balance'),reference=get(cells,'reference'),time=get(cells,'time'),row_key=f'csv-{i}',source_text=source)
        out.append(row)
        if len(out)>MAX_ROWS:raise ScanError(f'This statement has more than {MAX_ROWS:,} rows. Split it into smaller periods.')
    return finish(out,warnings,metadata(decoded),opening,closing,1)

def parse_pages(pages):
    rows=[];warnings=[];opening='';closing='';previous=None;columns={}
    for page,text in enumerate(pages,1):
        last_on_page=False
        for line_no,raw in enumerate(normalize_text(text).splitlines(),1):
            line=clean_bank_description(raw.rstrip(),preserve_columns=True)
            if not line.strip():continue
            values=list(MONEY.finditer(line))
            if re.search(ACCOUNT_OPEN,line,re.I) and all(Decimal(number(value[0]))==0 for value in values):continue
            if re.search(OPEN,line,re.I):
                if values:
                    opening=opening or number(values[-1][0])
                continue
            if re.search(CLOSE,line,re.I):
                if values:closing=number(values[-1][0])
                continue
            if re.search(SUMMARY,line,re.I):continue
            found={key:re.search(LABELS[key],line,re.I) for key in ['debit','credit','balance']}
            if found['balance'] and (found['debit'] or found['credit']):
                columns={key:(m.start()+m.end())/2 for key,m in found.items() if m}
                if re.search(r'withdrawal\s*/\s*deposit|ถอน.{0,8}/.{0,8}ฝาก',line,re.I):columns={k:v for k,v in columns.items() if k=='balance'}
                continue
            match=DATE.match(line)
            if not match:
                if rows and last_on_page and line.strip() and not re.search(r'account|statement|เลขที่บัญชี|บัญชี|วันที่|date|description|balance|ธนาคาร|kasikorn',line,re.I) and not values and not re.match(r'^\s*\d+\s*$',line):
                    rows[-1]['description']=(rows[-1]['description']+' '+line.strip())[:1000]
                continue
            day=statement_date(match[1]);tail=line[match.end():];time=re.search(r'\b\d{1,2}:\d{2}(?::\d{2})?\b',tail)
            values=[m for m in values if m.start()>=match.end()]
            amounts={};ambiguous=False
            if columns and len(values)>=2:
                for m in values:
                    key=min(columns,key=lambda key:abs((m.start()+m.end())/2-columns[key]))
                    if key in amounts:ambiguous=True
                    amounts[key]=m[0]
            balance=number(amounts.get('balance','')) if not ambiguous else ''
            debit=amounts.get('debit','') if not ambiguous else '';credit=amounts.get('credit','') if not ambiguous else ''
            amount='';direction=''
            # A balance change can resolve collapsed OCR columns without guessing from "transfer".
            if not balance and len(values)>=2:balance=number(values[-1][0])
            if len(values)>=2:amount=number(values[-2][0])
            elif len(values)==1:amount=number(values[0][0])
            if not direction:direction=direction_hint(tail)
            description=tail
            for m in reversed(values):description=description[:m.start()-match.end()]+description[m.end()-match.end():]
            description=re.sub(r'^\s*\d{1,2}:\d{2}(?::\d{2})?\s*','',description).strip()
            reference=re.search(r'(?:\bref(?:erence)?\.?|อ้างอิง)\s*[:：]?\s*([A-Za-z0-9/-]{4,})',tail,re.I)
            row=row_values(day,re.sub(r'\s{2,}',' ',description),debit=debit,credit=credit,amount=amount,direction=direction,balance=balance,reference=reference[1] if reference else '',time=time[0] if time else '',row_key=f'p{page}-l{line_no}',page=page,source_text=raw.rstrip())
            rows.append(row);last_on_page=True
    empty=[str(i) for i in range(1,len(pages)+1) if not any(r['page']==i for r in rows)]
    if empty:warnings.append('No dated transactions detected on page(s) '+', '.join(empty)+'. Check these pages against the original; add missing rows if needed.')
    return finish(rows,warnings,metadata('\n'.join(pages)),opening,closing,len(pages))

def finish(rows,warnings,meta,opening,closing,pages):
    if not rows:raise ScanError('No dated transactions could be read. Try the K BIZ CSV export or a clearer statement.')
    if len(rows)>MAX_ROWS:raise ScanError(f'This statement has more than {MAX_ROWS:,} rows. Split it into smaller periods.')
    dated=[r for r in rows if r['date']]
    descending=len(dated)>1 and (dated[0]['date']+' '+dated[0].get('time',''))>(dated[-1]['date']+' '+dated[-1].get('time',''))
    ordered=list(reversed(rows)) if descending else rows
    previous=Decimal(opening) if opening else None
    for row in ordered:
        if row['balance'] and row['amount'] and previous is not None:
            delta=Decimal(row['balance'])-previous;amount=Decimal(row['amount'])
            if not row['direction'] and abs(delta)==amount and delta:
                row['direction']='in' if delta>0 else 'out'
                row['errors']=[error for error in row['errors'] if error!='Choose money in or out']
            elif row['direction'] and delta!=(amount if row['direction']=='in' else -amount):
                row['errors'].append('Running balance does not match; check amount/direction');row['direction']=''
        if row['balance']:previous=Decimal(row['balance'])
    if any(row['errors'] for row in rows):warnings.append('Some rows need correction. Uncertain rows are not selected automatically.')
    incoming=sum((Decimal(r['amount']) for r in rows if r['amount'] and r['direction']=='in'),Decimal(0))
    outgoing=sum((Decimal(r['amount']) for r in rows if r['amount'] and r['direction']=='out'),Decimal(0))
    reconciliation=None
    if opening and closing:reconciliation=Decimal(opening)+incoming-outgoing==Decimal(closing)
    if reconciliation is False:warnings.append('Opening balance + money in − money out does not equal closing balance. Review missing or misread rows.')
    return dict(transactions=rows,warnings=warnings,opening_balance=opening,closing_balance=closing,money_in=str(incoming),money_out=str(outgoing),balance_matches=reconciliation,pages=pages,**meta)

def scan_statement(content,name='',progress=None):
    if str(name).lower().endswith('.csv'):
        if progress:progress(1,1)
        return parse_csv(content)
    return parse_pages(extract_pages(content,max_pages=200,progress=progress))

def match_key(row):
    # Never silently merge similar same-day transfers without a running balance.
    if row.get('balance') in ('',None):return ''
    values=[row['date'],row['direction'],str(row['amount_minor']),str(row.get('balance_minor')),row.get('time',''),row.get('reference',''),re.sub(r'\s+',' ',clean_bank_description(row.get('description',''))).strip().casefold()]
    return hashlib.sha256('\0'.join(values).encode()).hexdigest()

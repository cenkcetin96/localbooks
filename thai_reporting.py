"""DBD published-line mappings and conditional Thai return references for review."""
import csv
import datetime as dt
import io
import json

SCHEMA = '''
CREATE TABLE IF NOT EXISTS thai_reporting_profiles(company_id INTEGER PRIMARY KEY REFERENCES companies(id), data TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1);
'''
TABLES = {'thai_reporting_profiles'}
VERSION = 'dbd2566-form2-v1'
VERIFIED = '2026-11-17'
SOURCES = {
    'dbd':dict(title='DBD notification B.E. 2566 · Form 2',url='https://www.dbd.go.th/storage/law/63ed8d68-5f29-46c3-94e8-669f0cea5438.pdf'),
    'dbd_notes':dict(title='DBD explanation · private limited companies',url='https://www.dbd.go.th/storage/law/0fafe207-f3e1-4173-bca7-47c32219a932.pdf'),
    'rd_forms':dict(title='Revenue Department return directory',url='https://www.rd.go.th/rdeservice/63813.html'),
    'wht_forms':dict(title='RD withholding forms and income categories',url='https://www.rd.go.th/7049-1.html'),
    'wht_certificate':dict(title='RD section 50 bis withholding certificate guidance',url='https://www.rd.go.th/63641.html'),
    'cit':dict(title='Revenue Code sections 65–76 · CIT and adjustments',url='https://www.rd.go.th/english/37764.html'),
    'vat':dict(title='RD VAT and foreign-service VAT guidance',url='https://www.rd.go.th/english/6043.html'),
    'foreign_vat':dict(title='RD foreign-service VAT ruling · P.P.36',url='https://www.rd.go.th/33475.html'),
    'sso':dict(title='SSO contribution return · S.P.S.1-10',url='https://www.sso.go.th/eservices/esv/txn1d05.do'),
}
FORMS = {
    'pp30':dict(label='P.P.30 · ภ.พ.30',purpose='Monthly VAT: reviewed tax-point output VAT, eligible input VAT and credits; registration and supply treatment must be checked.',source='vat'),
    'pp36':dict(label='P.P.36 · ภ.พ.36',purpose='VAT remittance in applicable cases, including foreign services used in Thailand. Check supplier/VAT arrangement and payment tax point.',source='foreign_vat'),
    'pnd1':dict(label='P.N.D.1 · ภ.ง.ด.1',purpose='Payroll/qualifying section 40(1)–(2) payment withholding; employee-level calculation and actual payments required.',source='wht_forms'),
    'pnd1k':dict(label='P.N.D.1 Kor · ภ.ง.ด.1ก',purpose='Annual payroll/qualifying employment income return; reconcile employee income and withholding records.',source='wht_forms'),
    'pnd2':dict(label='P.N.D.2 · ภ.ง.ด.2',purpose='Applicable section 40(3)–(4) payments to individual taxpayers, including interest, royalties and dividends; confirm payee residence and income category.',source='wht_forms'),
    'pnd2k':dict(label='P.N.D.2 Kor · ภ.ง.ด.2ก',purpose='Applicable annual summary of section 40(4) payments; review alongside P.N.D.2 records.',source='wht_forms'),
    'pnd3':dict(label='P.N.D.3 · ภ.ง.ด.3',purpose='Applicable withholding payments to individual taxpayers in the specified income categories, such as rent and services; choose from actual payment/payee facts.',source='wht_forms'),
    'pnd53':dict(label='P.N.D.53 · ภ.ง.ด.53',purpose='Applicable withholding payments to corporate taxpayers; recipient status and income type determine treatment.',source='wht_forms'),
    'pnd54':dict(label='P.N.D.54 · ภ.ง.ด.54',purpose='Applicable payments to foreign companies under section 70, or profit remittance under section 70 bis; review residence, business presence and treaties.',source='cit'),
    'pnd50':dict(label='P.N.D.50 · ภ.ง.ด.50',purpose='Annual corporate income tax: accounting result adjusted under tax law, with approved tax credits and supporting schedules.',source='cit'),
    'pnd51':dict(label='P.N.D.51 · ภ.ง.ด.51',purpose='Applicable half-year corporate income tax; use the proper estimate/basis and approved credits, not account movements alone.',source='cit'),
    'sso110':dict(label='S.P.S.1-10 · สปส.1-10',purpose='SSO employee/employer contribution return; this is a social-security form, not a Revenue Department tax return.',source='sso'),
    'wht_certificate':dict(label='50 Tawi · หนังสือรับรอง 50 ทวิ',purpose='Withholding certificate/e-WHT evidence: collect for customer WHT credits and prepare required supplier evidence. This is a tax document, not a return; the app does not issue the certificate.',source='wht_certificate'),
}

# Keys below are local references to published statement lines, not XBRL element IDs.
_CATEGORIES = [
    ('bs.1.1','1.1','Cash and cash equivalents','เงินสดและรายการเทียบเท่าเงินสด','asset',32),
    ('bs.1.2','1.2','Temporary investments','เงินลงทุนชั่วคราว','asset',32),
    ('bs.1.3','1.3','Trade and other current receivables','ลูกหนี้การค้าและลูกหนี้หมุนเวียนอื่น','asset',32),
    ('bs.1.6','1.6','Inventories','สินค้าคงเหลือ','asset',32),
    ('bs.1.7','1.7','Current income tax assets · TAS 12 election','สินทรัพย์ภาษีเงินได้ของงวดปัจจุบัน','asset',32),
    ('bs.1.9','1.9','Other current assets','สินทรัพย์หมุนเวียนอื่น','asset',32),
    ('bs.2.1','2.1','Pledged bank deposits','เงินฝากธนาคารที่มีภาระค้ำประกัน','asset',32),
    ('bs.2.6','2.6','Trade and other non-current receivables','ลูกหนี้การค้าและลูกหนี้ไม่หมุนเวียนอื่น','asset',32),
    ('bs.2.10','2.10','Property, plant and equipment','ที่ดิน อาคารและอุปกรณ์','asset',32),
    ('bs.2.14','2.14','Deferred tax assets · TAS 12 election','สินทรัพย์ภาษีเงินได้รอการตัดบัญชี','asset',33),
    ('bs.2.15','2.15','Other non-current assets','สินทรัพย์ไม่หมุนเวียนอื่น','asset',33),
    ('bs.3.1','3.1','Bank overdrafts and short-term bank borrowings','เงินเบิกเกินบัญชีและเงินกู้ยืมระยะสั้นจากสถาบันการเงิน','liability',33),
    ('bs.3.2','3.2','Trade and other current payables','เจ้าหนี้การค้าและเจ้าหนี้หมุนเวียนอื่น','liability',33),
    ('bs.3.4','3.4','Current portion of long-term liabilities','ส่วนของหนี้สินระยะยาวที่ถึงกำหนดชำระภายในหนึ่งปี','liability',33),
    ('bs.3.6','3.6','Short-term borrowings','เงินกู้ยืมระยะสั้น','liability',33),
    ('bs.3.7','3.7','Income tax payable','ภาษีเงินได้นิติบุคคลค้างจ่าย','liability',33),
    ('bs.3.10','3.10','Other current liabilities','หนี้สินหมุนเวียนอื่น','liability',33),
    ('bs.4.1','4.1','Long-term borrowings','เงินกู้ยืมระยะยาว','liability',33),
    ('bs.4.3','4.3','Trade and other non-current payables','เจ้าหนี้การค้าและเจ้าหนี้ไม่หมุนเวียนอื่น','liability',33),
    ('bs.4.5','4.5','Deferred tax liabilities · TAS 12 election','หนี้สินภาษีเงินได้รอการตัดบัญชี','liability',33),
    ('bs.4.8','4.8','Other non-current liabilities','หนี้สินไม่หมุนเวียนอื่น','liability',33),
    ('bs.5.1.2','5.1.2','Paid-up share capital','ทุนที่ชำระแล้ว','equity',34),
    ('bs.5.1.2.1','5.1.2.1','Paid-up preference shares','ทุนที่ชำระแล้ว - หุ้นบุริมสิทธิ','equity',34),
    ('bs.5.1.2.2','5.1.2.2','Paid-up ordinary shares','ทุนที่ชำระแล้ว - หุ้นสามัญ','equity',34),
    ('bs.5.4.1.1','5.4.1.1','Legal reserve','ทุนสำรองตามกฎหมาย','equity',34),
    ('bs.5.4.1.2','5.4.1.2','Other appropriated retained earnings','กำไรสะสมจัดสรรแล้ว - อื่น ๆ','equity',34),
    ('bs.5.4.2','5.4.2','Unappropriated retained earnings','กำไร (ขาดทุน) สะสม - ยังไม่ได้จัดสรร','equity',34),
    ('fn.1','1','Revenue from sales or services','รายได้จากการขายหรือการให้บริการ','income',46),
    ('fn.2','2','Other income','รายได้อื่น','income',46),
    ('fn.3','3','Cost of sales or services','ต้นทุนขายหรือต้นทุนการให้บริการ','expense',46),
    ('fn.4','4','Selling expenses','ค่าใช้จ่ายในการขาย','expense',46),
    ('fn.5','5','Administrative expenses','ค่าใช้จ่ายในการบริหาร','expense',46),
    ('fn.6','6','Other expenses','ค่าใช้จ่ายอื่น','expense',46),
    ('fn.9','9','Finance costs','ต้นทุนทางการเงิน','expense',46),
    ('fn.11','11','Income tax expense','ภาษีเงินได้','expense',46),
    ('nat.1','1','Revenue from sales or services','รายได้จากการขายหรือการให้บริการ','income',36),
    ('nat.2','2','Other income','รายได้อื่น','income',36),
    ('nat.3','3','Changes in finished goods and work in progress','การเปลี่ยนแปลงในสินค้าสำเร็จรูปและงานระหว่างทำ','expense',36),
    ('nat.5','5','Raw materials and consumables used','วัตถุดิบและวัสดุสิ้นเปลืองใช้ไป','expense',36),
    ('nat.6','6','Employee benefit expenses','ค่าใช้จ่ายผลประโยชน์พนักงาน','expense',36),
    ('nat.7','7','Depreciation and amortisation','ค่าเสื่อมราคาและค่าตัดจำหน่าย','expense',36),
    ('nat.9','9','Other expenses','ค่าใช้จ่ายอื่น','expense',36),
    ('nat.12','12','Finance costs','ต้นทุนทางการเงิน','expense',36),
    ('nat.14','14','Income tax expense','ภาษีเงินได้','expense',36),
]
CATEGORIES = {key:dict(id=key,item=item,label=en,thai=th,type=kind,normal='debit' if kind in ('asset','expense') else 'credit',statement='Balance sheet' if key.startswith('bs.') else 'P&L by function · Form 2 section 2.7' if key.startswith('fn.') else 'P&L by nature · Form 2 section 2.1',source_url=SOURCES['dbd']['url']+'#page='+str(page),source_page=page) for key,item,en,th,kind,page in _CATEGORIES}
CIT = ['pnd50','pnd51']
WHT = ['pnd3','pnd53','pnd54']
PURCHASE = ['pp30','pp36']
EXPENSE_REVIEW = 'Confirm business purpose, evidence, tax timing and any disallowed portion. A book expense is not automatically a tax deduction.'
PAYMENT_REVIEW = 'Choose WHT from payment date, income category, recipient legal type/residence and treaty status; do not infer a rate or form from the account alone.'
VAT_REVIEW = 'P.P.30 needs reviewed tax points and eligible tax invoices. P.P.36 is conditional on the foreign-service/VAT facts; the two forms must not be treated as automatic duplicates.'
FUNCTION_REVIEW = 'Allocate production/service costs, selling costs and administration to their actual functions; split mixed accounts where needed.'

# One default for each of the 39 starter accounts; blank categories require facts/splits.
# Entries: category, candidate return references, tax treatment, accountant review note.
DEFAULTS = {
    '1101000':('bs.1.1',[],'balance','Cash movements do not establish taxable revenue or deductible expense; use the linked transaction.'),
    '1102000':('bs.1.1',[],'balance','Confirm unrestricted demand deposits/cash equivalents. Review term deposits, pledged balances and overdrafts separately.'),
    '1201000':('bs.1.3',[],'balance','Confirm current trade receivables, impairment and operating cycle. The sales transaction, not the closing debtor balance, determines tax reporting.'),
    '1202000':('bs.1.3',CIT+['wht_certificate'],'credit','Customer WHT is a potential CIT credit supported by certificates/e-WHT evidence; it is not supplier WHT to remit. TAS 12 election changes presentation to item 1.7.'),
    '1203000':('bs.1.3',['pp30'],'vat','Review recoverable input VAT and carry-forward. Not-yet-due input VAT may belong in item 1.9. Exclude non-creditable VAT or capitalize/expense it appropriately.'),
    '1301000':('bs.1.6',CIT+PURCHASE,'balance','Inventory is an asset until recognized as cost; review stock counts, valuation, write-downs and eligible purchase VAT.'),
    '1401000':('bs.1.3',CIT+WHT+PURCHASE,'review','DBD includes current prepaid expenses in other receivables. Review the period benefited; prepayment is not an immediate tax deduction. '+PAYMENT_REVIEW),
    '1402000':('',[],'balance','Classify refundable deposits from the contract, nature and recovery date: current receivable/other current asset or non-current receivable/other asset. Do not guess from the account name.'),
    '1501000':('bs.2.10',CIT+PURCHASE,'capital','Capitalize qualifying equipment; review asset register, in-service date, tax depreciation and eligible input VAT. Purchase cost is not automatically a current deduction.'),
    '1591000':('bs.2.10',CIT,'depreciation','Contra-asset: subtract accumulated depreciation from PPE. Reconcile book depreciation with the tax asset schedule; do not deduct this balance again.'),
    '2101000':('bs.3.2',[],'balance','Review current supplier obligations. Supplier payments and underlying invoices determine WHT/VAT forms; the payable balance is not a return tax base.'),
    '2201000':('bs.3.2',CIT,'review','Review accrual period, business purpose and support. Supplier WHT generally needs actual payment review rather than the accrual balance.'),
    '2301000':('bs.3.2',['pnd1','pnd2','pnd3','pnd53','pnd54','wht_certificate'],'withholding','Reconcile withholding payable by actual return/payment period and payee. '+PAYMENT_REVIEW),
    '2302000':('bs.3.2',['pnd1','pnd1k'],'withholding','Reconcile payroll tax withheld to employee payroll records, monthly remittances and the annual employment return.'),
    '2303000':('bs.3.2',['sso110'],'social_security','Reconcile employee deductions and employer contributions to S.P.S.1-10; social security is separate from RD payroll tax.'),
    '2304000':('bs.3.7',CIT,'liability','Reconcile current CIT provision, half-year tax, approved credits and payments. This liability is not tax expense or a deductible tax base.'),
    '2341000':('bs.3.2',['pp30'],'vat','Reconcile output VAT and net VAT payable with the VAT control accounts and filed returns; do not count output VAT as sales income.'),
    '2401000':('',[],'balance','Split loans by lender and contractual maturity: bank short-term 3.1, other short-term 3.6, current portion of long-term 3.4, long-term 4.1. Principal is not an expense.'),
    '3101000':('bs.5.1.2',[],'balance','Use paid-up capital rather than registered capital; split preference and ordinary shares when required by the company records.'),
    '3201000':('bs.5.4.2',[],'balance','Review prior-year closing and profit appropriation. Legal reserve/other appropriated earnings need their own categories; do not treat retained earnings as current-year taxable sales.'),
    '4001000':('fn.1',CIT+['pp30'],'income','Review taxable/exempt/zero-rated supplies and tax-point periods. Exclude output VAT from sales revenue; reconcile book and taxable revenue.'),
    '4002000':('fn.1',CIT+['pp30'],'income','Contra-revenue: debit discounts reduce sales. A book discount does not itself authorize a VAT reduction; verify invoice/credit-note evidence.'),
    '4003000':('fn.1',CIT+['pp30'],'income','Review service revenue recognition, VAT registration, exemption/zero-rating and tax points; service receipts may differ from accrued revenue.'),
    '4004000':('fn.2',CIT+['pp30'],'income','Identify each income source and its CIT/VAT treatment. Do not assume all other income is VAT-taxable or fully taxable for CIT.'),
    '5001000':('',CIT+WHT+PURCHASE,'review','Classify the supplier cost before reporting; inventory/capital items must be reclassified. '+FUNCTION_REVIEW+' '+EXPENSE_REVIEW),
    '5002000':('fn.3',CIT,'review','Match recognized cost to sales/services and closing inventory; avoid deducting both purchases and cost of goods sold.'),
    '5101000':('fn.5',CIT+['pnd1','pnd1k'],'review',FUNCTION_REVIEW+' Reconcile payroll expense to employee-level income, payroll withholding and payment records.'),
    '5102000':('fn.5',CIT+['sso110'],'review',FUNCTION_REVIEW+' Reconcile employer contributions to SSO records; employee deductions are liabilities rather than employer expense.'),
    '5201000':('fn.5',CIT+WHT+PURCHASE,'review',FUNCTION_REVIEW+' Distinguish immovable-property rent from equipment/services for VAT. '+PAYMENT_REVIEW),
    '5202000':('fn.5',CIT+WHT+PURCHASE,'review',FUNCTION_REVIEW+' '+EXPENSE_REVIEW+' Check supplier-specific VAT/WHT treatment.'),
    '5203000':('fn.5',CIT+WHT+PURCHASE,'review',FUNCTION_REVIEW+' '+VAT_REVIEW+' '+PAYMENT_REVIEW),
    '5204000':('fn.5',CIT+WHT+PURCHASE,'review',FUNCTION_REVIEW+' Review reimbursements versus payments to transport/service suppliers and document any restricted input VAT.'),
    '5205000':('fn.5',CIT+WHT+PURCHASE,'review',EXPENSE_REVIEW+' '+PAYMENT_REVIEW+' '+VAT_REVIEW),
    '5206000':('fn.5',CIT,'review','Ordinary operating bank fees suggest administration; borrowing-related bank fees belong to finance costs or another treatment. Review fee type, evidence and any applicable VAT/WHT separately.'),
    '5207000':('fn.4',CIT+WHT+PURCHASE,'review','Marketing suggests selling expenses. '+PAYMENT_REVIEW+' '+VAT_REVIEW),
    '5301000':('fn.5',CIT,'depreciation',FUNCTION_REVIEW+' Reconcile book depreciation and tax allowances; only the required difference is a tax adjustment, not an automatic full add-back.'),
    '5401000':('fn.9',CIT+['pnd2','pnd2k','pnd53','pnd54'],'review','Review capitalization and deductibility. Individual interest payees may require P.N.D.2, corporate payees P.N.D.53, foreign payees P.N.D.54; confirm conditions and treaty relief.'),
    '5501000':('fn.11',CIT,'addback','Review accounting CIT expense as a tax add-back under section 65 ter (6); assess deferred/current components separately. Do not calculate statutory tax from this expense account.'),
    '9990000':('',[],'unresolved','Suspense has no final DBD reporting category. Identify and reclassify its transactions before final accounts or tax preparation.'),
}
TREATMENTS = {'balance':'Balance/control account','credit':'Potential CIT credit','vat':'VAT control','capital':'Capital asset review','income':'Revenue reconciliation','review':'Transaction-level tax review','depreciation':'Book/tax depreciation reconciliation','withholding':'Withholding control','liability':'CIT payable control','social_security':'Social-security control','addback':'CIT add-back review','unresolved':'Unresolved'}


def _text(value, label, limit=2000, required=False):
    if not isinstance(value,str) or len(value.strip())>limit or (required and not value.strip()):raise ValueError(f'{label}: enter valid text, up to {limit} characters.')
    return value.strip()


def profile_data(data):
    if not isinstance(data,dict):raise ValueError('Invalid Thai reporting profile.')
    if data.get('framework') not in ('unconfirmed','tfrs_npae','other') or data.get('presentation') not in ('function','nature') or data.get('income_tax_basis') not in ('npae','tas12'):raise ValueError('Choose the reporting framework, expense presentation and income-tax accounting basis.')
    if type(data.get('confirmed')) is not bool:raise ValueError('Invalid reporting basis confirmation.')
    confirmed=data['confirmed'];reviewer=_text(data.get('reviewed_by',''),'Reviewer',200,confirmed)
    if confirmed and data['framework']!='tfrs_npae':raise ValueError('This Form 2 template supports TFRS for NPAEs. Leave another framework unconfirmed for separate mapping.')
    return {**{k:data[k] for k in ('framework','presentation','income_tax_basis','confirmed')},'reviewed_by':reviewer,'note':_text(data.get('note',''),'Basis note')}


def profile(store, company):
    if type(company) is not int or company<=0:raise ValueError('Choose a saved company for Thai reporting.')
    store.party('companies',company)
    row=store.db.execute('SELECT * FROM thai_reporting_profiles WHERE company_id=?',(company,)).fetchone()
    out=json.loads(row['data']) if row else dict(framework='unconfirmed',presentation='function',income_tax_basis='npae',confirmed=False,reviewed_by='',note='Confirm the company uses TFRS for NPAEs and the selected expense presentation. The initial mapping is a Form 2 reference template.')
    return dict(company_id=company,revision=row['revision'] if row else 0,basis_id=VERSION+'-'+out['presentation']+'-'+out['income_tax_basis'],**out)


def save_profile(store, data):
    with store.transaction():
        old=profile(store,data.get('company_id'));out=profile_data(data)
        if data.get('revision')!=old['revision']:raise ValueError('The Thai reporting basis changed. Reload before saving.')
        store.db.execute('INSERT INTO thai_reporting_profiles VALUES(?,?,?) ON CONFLICT(company_id) DO UPDATE SET data=excluded.data,revision=excluded.revision',(old['company_id'],json.dumps(out,ensure_ascii=False),old['revision']+1))
        store.event('thai_reporting_profile',old['company_id'],'DBD Form 2 · '+out['presentation']+' · '+out['income_tax_basis'])
        return profile(store,old['company_id'])


def mapping_data(data):
    if not isinstance(data,dict):raise ValueError('Invalid Thai reporting mapping.')
    category=data.get('category','')
    if type(category) is not str or (category and category not in CATEGORIES):raise ValueError('Choose a published DBD category.')
    forms=data.get('tax_forms')
    if not isinstance(forms,list) or any(type(f) is not str or f not in FORMS for f in forms) or len(set(forms))!=len(forms):raise ValueError('Choose valid Thai return references.')
    if data.get('treatment') not in TREATMENTS or type(data.get('reviewed')) is not bool:raise ValueError('Choose the tax review treatment and mapping status.')
    out=dict(category=category,tax_forms=forms,treatment=data['treatment'],reviewed=data['reviewed'])
    for key,limit in [('basis_id',120),('account_name',200),('account_type',30),('reviewed_by',200),('reviewed_on',40),('note',2000)]:out[key]=_text(data.get(key,''),key,limit)
    if out['reviewed'] and (not category or not out['reviewed_by'] or not out['note'] or not out['basis_id']):raise ValueError('Reviewed mappings need a DBD category, reviewer, basis and review note.')
    return out


def suggested(account, p):
    category,forms,treatment,note=DEFAULTS.get(account['code'],('',[],'review','New/custom account: choose the appropriate DBD category and conditional return references.'))
    if p['presentation']=='nature' and category.startswith('fn.'):
        category={'fn.1':'nat.1','fn.2':'nat.2','fn.4':'nat.9','fn.5':'nat.9','fn.6':'nat.9','fn.9':'nat.12','fn.11':'nat.14'}.get(category,'')
        if account['code'] in ('5101000','5102000'):category='nat.6'
        if account['code']=='5301000':category='nat.7'
        if account['code']=='5002000':note='Nature presentation requires splitting purchased goods/materials, inventory changes and other cost components; do not map a single COGS total to one nature line.'
    if account['code']=='1202000' and p['income_tax_basis']=='tas12':category='bs.1.7'
    return dict(category=category,tax_forms=list(forms),treatment=treatment,note=note,reviewed=False,reviewed_by='',reviewed_on='',basis_id=p['basis_id'],account_name=account['name'],account_type=account['type'])


def resolved(account, p):
    saved=account.get('thai_reporting');m=dict(saved or suggested(account,p));category=CATEGORIES.get(m['category'])
    current=m.get('basis_id')==p['basis_id'] and m.get('account_name')==account['name'] and m.get('account_type')==account['type']
    compatible=not category or category['type']==account['type']
    selected_prefix='fn.' if p['presentation']=='function' else 'nat.'
    if category and category['id'].startswith(('fn.','nat.')) and not category['id'].startswith(selected_prefix):compatible=False
    if category and category['id'] in ('bs.1.7','bs.2.14','bs.4.5') and p['income_tax_basis']!='tas12':compatible=False
    reviewed=bool(m['reviewed'] and current and compatible and category and p['confirmed'])
    status='reviewed' if reviewed else 'unmapped' if not category else 'needs_review' if saved and (not current or not compatible or m['reviewed']) else 'suggested'
    return dict(m,status=status,usable=reviewed,current=current,compatible=compatible,category_detail=category,form_details=[dict(id=f,**FORMS[f]) for f in m['tax_forms']])


def workspace(store, company):
    import accounting
    with store.lock:
        p=profile(store,company);chart=accounting.accounts(store,company)
        rows=[dict(code=a['code'],name=a['name'],type=a['type'],active=a['active'],revision=a['revision'],mapping=resolved(a,p)) for a in chart]
        company_info=store.party('companies',company)
        return dict(company=dict(id=company,name=company_info['name'],vat_registered=bool(company_info.get('vat_registered'))),profile=p,accounts=rows,categories=list(CATEGORIES.values()),forms=FORMS,treatments=TREATMENTS,sources=SOURCES,verified_on=VERIFIED,reference_version=VERSION,counts={status:sum(a['active'] and a['mapping']['status']==status for a in rows) for status in ('reviewed','suggested','needs_review','unmapped')})


def save_mapping(store, data):
    import accounting
    with store.transaction():
        company=data.get('company_id');p=profile(store,company)
        account=next((a for a in accounting.accounts(store,company) if a['code']==data.get('code')),None)
        if not account:raise ValueError('Company account not found.')
        if data.get('profile_revision')!=p['revision']:raise ValueError('The Thai reporting basis changed. Reload before reviewing this mapping.')
        m=mapping_data({**data.get('mapping',{}),'basis_id':p['basis_id'],'account_name':account['name'],'account_type':account['type'],'reviewed_on':dt.datetime.now(dt.timezone.utc).isoformat()})
        candidate=resolved(dict(account,thai_reporting=m),p)
        if not candidate['compatible']:raise ValueError('The DBD category does not match the account type or selected reporting basis.')
        if m['reviewed'] and not p['confirmed']:raise ValueError('Confirm the company reporting basis before marking an account mapping reviewed.')
        accounting.save_account(store,dict(account,company_id=company,revision=data.get('revision'),thai_reporting=m))
        store.event('thai_reporting_mapping',company,account['code']+' · '+(m['category'] or 'unmapped'))
        return workspace(store,company)


def validate_backup(db, tables):
    if 'thai_reporting_profiles' in tables:
        for company,raw,revision in db.execute('SELECT * FROM thai_reporting_profiles'):
            profile_data(json.loads(raw))
            if type(revision) is not int or revision<1:raise ValueError('Invalid Thai reporting profile revision.')


def report(store, company, start, end):
    import accounting
    with store.lock:
        work=workspace(store,company);b=accounting.balances(store,company,start,end,'THB');chart={a['code']:a for a in work['accounts']};groups={};unmapped=[]
        supported=b['start']>='2024-01-01'
        for row in b['accounts']:
            a=chart[row['code']];m=a['mapping'];category=m['category_detail']
            if not category or not m['compatible'] or not m['current']:
                unmapped.append(dict(code=a['code'],name=a['name'],debit_minor=row['debit_minor'],credit_minor=row['credit_minor'],reason='Classification unresolved, changed or incompatible with the selected basis.'));continue
            g=groups.setdefault(category['id'],dict(category=category,reviewed_movement_minor=0,suggested_movement_minor=0,accounts=[]))
            value=row['debit_minor']-row['credit_minor']
            if category['normal']=='credit':value=-value
            key='reviewed_movement_minor' if m['usable'] and supported else 'suggested_movement_minor';g[key]+=value
            g['accounts'].append(dict(code=a['code'],name=a['name'],movement_minor=value,status=m['status'],tax_forms=m['tax_forms'],treatment=m['treatment']))
        return dict(company_id=company,company=work['company'],start=b['start'],end=b['end'],currency='THB',profile=work['profile'],period_supported=supported,groups=list(groups.values()),unmapped=unmapped,excluded_entries=b['excluded'],included_entries=b['included'],scope='Saved-entry THB movements only; no opening balances, FX conversion, tax liabilities or statutory financial statements are calculated.',sources=SOURCES,reference_version=VERSION)


def mappings_rows(work):
    rows=[['Account code','Account','Type','Active','DBD reference version','DBD statement','DBD published item','DBD category EN','DBD category TH','Mapping status','Conditional form references','Tax review treatment','Conditions / accountant note','Reviewer','Reviewed at','Company basis confirmed','DBD source','Company','Current VAT registered setting','Saved mapping basis','Current company basis']]
    for a in work['accounts']:
        m=a['mapping'];c=m['category_detail'] or {}
        rows.append([a['code'],a['name'],a['type'],a['active'],VERSION,c.get('statement',''),c.get('item',''),c.get('label','Unresolved'),c.get('thai',''),m['status'],'; '.join(FORMS[f]['label'] for f in m['tax_forms']),TREATMENTS[m['treatment']],m['note'],m['reviewed_by'],m['reviewed_on'],work['profile']['confirmed'],c.get('source_url',SOURCES['dbd']['url']),work['company']['name'],work['company']['vat_registered'],m['basis_id'],work['profile']['basis_id']])
    return rows


def report_rows(result):
    rows=[['DBD statement','Published item','Category','Currency','Reviewed movement_minor','Suggested movement_minor','Account codes','Scope']]
    for g in result['groups']:
        c=g['category'];rows.append([c['statement'],c['item'],c['label'],'THB',g['reviewed_movement_minor'],g['suggested_movement_minor'],';'.join(a['code'] for a in g['accounts']),result['scope']])
    for a in result['unmapped']:rows.append(['Unresolved','',a['code']+' · '+a['name'],'THB','','',a['code'],a['reason']])
    for e in result['excluded_entries']:rows.append(['Excluded source','','','THB','','',e['source_kind']+':'+str(e['source_id']),e['reason']])
    rows.append(['Scope','','','THB','','','',result['scope']])
    metadata=[result['company']['name'],result['start'],result['end'],result['profile']['confirmed'],result['period_supported'],result['profile']['basis_id'],result['included_entries'],len(result['excluded_entries'])]
    rows=[rows[0]+['Company','From','To','Company basis confirmed','Period supported','Company reporting basis','Included entries','Excluded entries']]+[row+metadata for row in rows[1:]]
    return rows


def csv_bytes(rows):
    output=io.StringIO();writer=csv.writer(output)
    for row in rows:writer.writerow([str(v) if isinstance(v,(int,float)) else "'"+str(v) if str(v).lstrip().startswith(('=','+','-','@')) else v for v in row])
    return ('\ufeff'+output.getvalue()).encode()

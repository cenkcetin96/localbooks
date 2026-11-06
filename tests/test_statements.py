import base64
import datetime as dt
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Store,ValidationError
from statements import parse_csv,parse_pages,scan_statement,clean_bank_description,match_key,ScanError

CSV=b'''Account No: 000-0-00000-0 THB
Date,Time,Description,Withdrawal,Deposit,Outstanding Balance,Reference
01/11/2026,10:30,Transfer in,,1000.00,2000.00,R001
02/11/2026,11:40,Office rent,500.00,,1500.00,R002
'''

def header():return f'{"Date":<14}{"Description":<30}{"Withdrawal":>15}{"Deposit":>15}{"Balance":>15}'
def row(day,desc,debit='',credit='',balance=''):return f'{day:<14}{desc:<30}{debit:>15}{credit:>15}{balance:>15}'

class StatementParserTests(unittest.TestCase):
    def test_kbiz_csv_and_thai_years(self):
        parsed=parse_csv(CSV)
        self.assertEqual(parsed['account'],'000-0-00000-0');self.assertEqual(parsed['currency'],'THB')
        self.assertEqual([(r['date'],r['direction'],r['amount']) for r in parsed['transactions']],[('2026-11-01','in','1000.00'),('2026-11-02','out','500.00')])
        thai='วันที่,รายละเอียด,ถอนเงิน,ฝากเงิน,คงเหลือ\n๑/๑๑/๒๕๖๙,รับโอน,,100.00,900.00\n'
        self.assertEqual(parse_csv(thai.encode())['transactions'][0]['date'],'2026-11-01')
        self.assertEqual(parse_csv(thai.replace('๑/๑๑/๒๕๖๙','01/11/69').encode('cp874'))['transactions'][0]['date'],'2026-11-01')
    def test_multi_page_columns_and_balance_reconciliation(self):
        pages=['Account No: 000-0-00000-0 THB\nOpening balance 1,000.00\n'+header()+'\n'+row('01/11/2026','Transfer Deposit','','2,000.00','3,000.00')+'\n'+row('02/11/2026','Rent','500.00','','2,500.00'),header()+'\n'+row('03/11/2026','Payment','100.00','','2,400.00')+'\nClosing balance 2,400.00']
        p=parse_pages(pages)
        self.assertEqual(len(p['transactions']),3);self.assertEqual(p['pages'],2)
        self.assertEqual([r['direction'] for r in p['transactions']],['in','out','out'])
        self.assertTrue(p['balance_matches']);self.assertEqual(p['transactions'][-1]['page'],2)
    def test_collapsed_ocr_uses_balance_and_flags_uncertainty(self):
        p=parse_pages(['ยอดยกมา 1,000.00\n01/11/2569 โอนเงิน 100.00 900.00\n02/11/2569 โอนเงิน 50.00 950.00\n03/11/2569 โอนเงิน 50.00 700.00'])
        self.assertEqual([r['direction'] for r in p['transactions']],['out','in',''])
        self.assertTrue(p['transactions'][-1]['errors'])
        q=parse_pages(['01/11/2026 Transfer 100.00 900.00'])
        self.assertEqual(q['transactions'][0]['direction'],'');self.assertTrue(q['warnings'])
    def test_descending_csv_and_overdraft_balances(self):
        p=parse_csv(b'Date,Description,Withdrawal,Deposit,Balance\n02/11/2026,Rent,200.00,,-100.00\n01/11/2026,Deposit,,100.00,100.00\n')
        self.assertEqual([r['direction'] for r in p['transactions']],['out','in'])
        self.assertFalse(any(r['errors'] for r in p['transactions']))

    def test_zero_value_account_opening_is_not_a_transaction(self):
        page='Opening balance 0.00\n16-06-25 12:44 เปิดบัญชี 0.00 สาขาตัวอย่าง รหัสอ้างอิง DEMO0001\n17-06-25 10:30 รับโอนเงิน 100.00 100.00'
        parsed=parse_pages([page])
        self.assertEqual(len(parsed['transactions']),1)
        self.assertEqual(parsed['transactions'][0]['amount'],'100.00')
        self.assertEqual(parsed['transactions'][0]['errors'],[])
        csv=b'Date,Description,Withdrawal,Deposit,Balance\n01/11/2026,Account opening,,,0.00\n02/11/2026,Deposit,,100.00,100.00\n'
        self.assertEqual(len(parse_csv(csv)['transactions']),1)
        paid=parse_pages(['01/11/2026 Account opening deposit 100.00 100.00'])
        self.assertEqual(len(paid['transactions']),1,'A real initial deposit must remain importable')

    def test_footer_lines_do_not_pollute_wrapped_details(self):
        source='Opening balance 0.00\n'+header()+'\n'+row('01/11/2026','Cash deposit','','100.00','100.00')+'   Ref DEMO0002\nKBPDF (FM702-CA_SA-V.1) (03-25)\nตัวอย่าง\nออกโดย K BIZ\nสอบถามข้อมูลเพิ่มเติม บุคคลธรรมดา K Contact Center 02-8888888 นิติบุคคล K-BIZ Contact Center 02-8888822\nwww.kasikornbank.com'
        parsed=parse_pages([source]);entry=parsed['transactions'][0]
        self.assertEqual(parsed['money_in'],'100.00')
        self.assertIn('ตัวอย่าง',entry['description']);self.assertIn('DEMO0002',entry['description'])
        for footer in ['KBPDF','Contact Center','ออกโดย','kasikornbank.com']:self.assertNotIn(footer,entry['description'])
        self.assertIn('Ref DEMO0002',entry['source_text'])

    def test_footer_cleanup_keeps_real_details_and_user_notes(self):
        raw='ฝากเงินสด CDM00000 เครื่อง 1 (000) KBPDF (FM702-CA_SA-V.1) (03-25) สอบถามข้อมูลเพิ่มเติม บุคคลธรรมดา K Contact Center 02-8888888 นิติบุคคล K-BIZ Contact Center 02-8888822 ออกโดย K BIZ User note: owner deposit'
        self.assertEqual(clean_bank_description(raw),'ฝากเงินสด CDM00000 เครื่อง 1 (000) User note: owner deposit')
        legitimate='Paid supplier 02-1234567 at Bangkok branch\nUser note: reference ABC123'
        self.assertEqual(clean_bank_description(legitimate),legitimate)
        inline='01/11/2026 Deposit 100.00 100.00 ออกโดย K BIZ'
        entry=parse_pages([inline])['transactions'][0]
        self.assertNotIn('ออกโดย',entry['description']);self.assertIn('ออกโดย',entry['source_text'])

    def test_invalid_rows_and_unsupported_files_are_not_silently_imported(self):
        p=parse_csv(b'Date,Description,Withdrawal,Deposit\n31/02/2026,Bad,10.00,20.00\n01/11/2026,No amount,,\n')
        self.assertEqual(len(p['transactions']),2);self.assertTrue(all(r['errors'] for r in p['transactions']))
        with self.assertRaises(ScanError):parse_csv(b'not,a,statement\n1,2,3')
        with patch('statements.extract_pages',return_value=[header()+'\n'+row('01/11/2026','Fee','10.00','','90.00')]) as extract:
            scan_statement(b'%PDF-test','bank.pdf');self.assertEqual(extract.call_args.kwargs['max_pages'],200)

class BankStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(self.tmp.name,pdf_renderer=lambda h:b'%PDF-test')
        self.company=self.s.save_party('companies',dict(name='A',code='A'));self.other=self.s.save_party('companies',dict(name='B',code='B'))
    def tearDown(self):self.s.db.close();self.tmp.cleanup()
    def payload(self,**extra):
        return dict(company_id=self.company['id'],account='000-0-00000-0',currency='THB',account_confirmed=True,name='statement.csv',content=base64.b64encode(CSV).decode(),rows=parse_csv(CSV)['transactions'],**extra)
    def test_import_deduplicate_edit_support_backup_and_old_restore(self):
        old=self.s.backup();result=self.s.import_bank(self.payload());self.assertEqual(result['imported'],2)
        entries=self.s.bank_entries();self.assertEqual(len(entries),2);self.assertTrue(all(not e['reviewed'] and not e['cash_flow'] for e in entries))
        self.assertEqual(self.s.import_bank(self.payload())['imported'],0)
        overlap=self.payload();overlap['content']=base64.b64encode(CSV+b'\n').decode();self.assertEqual(self.s.import_bank(overlap)['imported'],0)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM bank_statements').fetchone()[0],1)
        # The same account on a different business is kept separate.
        other=self.payload();other['company_id']=self.other['id'];self.assertEqual(self.s.import_bank(other)['imported'],2)
        e=entries[0];edit=dict(id=e['id'],revision=e['revision'],date=e['date'],direction=e['direction'],amount='500.00',balance='1500.00',description='Reviewed rent',counterparty='Landlord',category='expense',reviewed=True,cash_flow=True)
        saved=self.s.save_bank_entry(edit);self.assertTrue(saved['cash_flow']);self.assertEqual(saved['revision'],2)
        with self.assertRaises(ValidationError):self.s.save_bank_entry(edit)
        file=dict(id=e['id'],name='rent.pdf',content=base64.b64encode(b'%PDF-rent-original').decode())
        self.s.attach_bank_document(file);self.s.attach_bank_document(file)
        self.assertEqual(len(next(x for x in self.s.bank_entries() if x['id']==e['id'])['documents']),1)
        backup=self.s.backup();self.s.restore(backup,'RESTORE');self.assertEqual(len(self.s.bank_entries()),4)
        self.assertEqual(self.s.db.execute('SELECT content FROM bank_attachments').fetchone()[0],b'%PDF-rent-original')
        exported=self.s.export_bank(str(self.company['id']),'2026').decode('utf-8-sig');self.assertIn('Landlord',exported);self.assertIn('rent.pdf',exported)
        self.s.restore(old,'RESTORE');self.assertEqual(self.s.bank_entries(),[])
    def test_atomic_validation_possible_duplicates_and_matched_cash(self):
        bad=self.payload();bad['rows'][1]['date']='bad'
        with self.assertRaises(ValidationError):self.s.import_bank(bad)
        self.assertEqual(self.s.bank_entries(),[]);self.assertEqual(self.s.db.execute('SELECT count(*) FROM bank_statements').fetchone()[0],0)
        late=self.payload();late['rows'][1]['page']=201
        with self.assertRaises(ValidationError):self.s.import_bank(late)
        self.assertEqual(self.s.bank_entries(),[]);self.assertEqual(self.s.db.execute('SELECT count(*) FROM bank_statements').fetchone()[0],0)
        self.s.import_bank(self.payload());duplicate=self.payload();duplicate['content']=base64.b64encode(CSV+b'new').decode();duplicate['rows']=duplicate['rows'][:1];duplicate['rows'][0]['balance']=''
        self.assertEqual(self.s.check_bank_rows(duplicate)[0]['status'],'possible')
        self.assertEqual(self.s.import_bank(duplicate)['imported'],0)
        duplicate['rows'][0]['allow_possible_duplicate']=True;self.assertEqual(self.s.import_bank(duplicate)['imported'],1)
        e=self.s.bank_entries()[0]
        edit=dict(id=e['id'],revision=e['revision'],date=e['date'],direction=e['direction'],amount='1000',description='Transfer',category='unclassified',reviewed=True,cash_flow=True)
        with self.assertRaises(ValidationError):self.s.save_bank_entry(edit)
        edit.update(category='income',reviewed=False)
        with self.assertRaises(ValidationError):self.s.save_bank_entry(edit)
        edit.update(reviewed=True,matched_record='payment:999')
        with self.assertRaises(ValidationError):self.s.save_bank_entry(edit)
    def test_matching_existing_receipt_does_not_copy_payment(self):
        contact=self.s.save_party('contacts',dict(name='Customer'))
        draft=self.s.save_draft(dict(company_id=self.company['id'],contact_id=contact['id'],kind='sale',currency='THB',issue_date='2026-11-01',lines=[dict(description='Service',quantity='1',unit_price='1000')]))
        invoice=self.s.issue(draft['id'],expected_revision=draft['revision'])
        paid=self.s.add_payment(invoice['id'],dict(date='2026-11-01',amount='1000',method='Bank transfer',request_key='bank-match'))
        self.s.import_bank(self.payload());entry=next(e for e in self.s.bank_entries() if e['direction']=='in')
        edit=dict(id=entry['id'],revision=entry['revision'],date=entry['date'],direction='in',amount='1000',description=entry['description'],category='income',reviewed=True,cash_flow=False,matched_record='payment:'+str(paid['payments'][0]['id']))
        saved=self.s.save_bank_entry(edit);self.assertTrue(saved['reviewed']);self.assertFalse(saved['cash_flow'])
        self.assertEqual(len(self.s.invoice(invoice['id'])['payments']),1)
        edit.update(revision=saved['revision'],cash_flow=True)
        with self.assertRaises(ValidationError):self.s.save_bank_entry(edit)
        self.s.restore(self.s.backup(),'RESTORE')

    def test_identical_amounts_with_different_balances_and_csv_formula_safety(self):
        payload=self.payload();payload['rows']=[dict(date='2026-11-01',direction='in',amount='100',balance=str(n),description='=Not a formula',row_key=str(n),page=1) for n in [100,200]]
        self.assertEqual(self.s.import_bank(payload)['imported'],2)
        self.assertEqual(self.s.import_bank(payload)['imported'],0)
        self.assertIn("'=Not a formula",self.s.export_bank().decode('utf-8-sig'))

    def test_existing_footer_cleanup_is_backed_up_idempotent_and_preserves_evidence(self):
        result=self.s.import_bank(self.payload());id=result['ids'][0]
        self.s.attach_bank_document(dict(id=id,name='proof.pdf',content=base64.b64encode(b'%PDF-original-support').decode()))
        original=self.s.db.execute('SELECT * FROM bank_entries WHERE id=?',(id,)).fetchone();data=json.loads(original['data']);origin=json.loads(original['original_data'])
        data['description']='Transfer KBPDF (FM702-CA_SA-V.1) (03-25) note: keep me'
        data.update(counterparty='Known customer',notes='Manual review note',category='income',reviewed=True,cash_flow=True)
        origin['description']='Transfer KBPDF (FM702-CA_SA-V.1) (03-25)'
        raw_origin=json.dumps(origin,ensure_ascii=False)
        with self.s.db:self.s.db.execute('UPDATE bank_entries SET data=?,original_data=?,match_key=? WHERE id=?',(json.dumps(data,ensure_ascii=False),raw_origin,'legacy-footer-key',id))
        dirty_backup=self.s.backup();statement=tuple(self.s.db.execute('SELECT * FROM bank_statements').fetchone());support=tuple(self.s.db.execute('SELECT * FROM bank_attachments').fetchone())
        self.assertEqual(self.s.clean_bank_descriptions(),1)
        after=self.s.db.execute('SELECT * FROM bank_entries WHERE id=?',(id,)).fetchone();cleaned=json.loads(after['data'])
        self.assertEqual(cleaned['description'],'Transfer note: keep me')
        self.assertEqual({k:v for k,v in cleaned.items() if k!='description'},{k:v for k,v in data.items() if k!='description'})
        self.assertEqual(after['original_data'],raw_origin)
        self.assertEqual(after['revision'],original['revision']+1)
        self.assertEqual(after['match_key'],match_key(self.s.bank_row(origin,'THB')))
        self.assertEqual(tuple(self.s.db.execute('SELECT * FROM bank_statements').fetchone()),statement)
        self.assertEqual(tuple(self.s.db.execute('SELECT * FROM bank_attachments').fetchone()),support)
        copies=list((Path(self.tmp.name)/'safety-copies').glob('Before-bank-footer-cleanup-*'));self.assertEqual(len(copies),1)
        backup=sqlite3.connect(copies[0]);self.assertIn('KBPDF',backup.execute('SELECT data FROM bank_entries WHERE id=?',(id,)).fetchone()[0]);backup.close()
        self.assertEqual(self.s.clean_bank_descriptions(),0);self.assertEqual(self.s.db.execute('SELECT revision FROM bank_entries WHERE id=?',(id,)).fetchone()[0],after['revision'])
        # Older backups are repaired on restore without changing source evidence.
        self.s.restore(dirty_backup,'RESTORE');restored=self.s.db.execute('SELECT data,original_data FROM bank_entries WHERE id=?',(id,)).fetchone()
        self.assertEqual(json.loads(restored['data'])['description'],'Transfer note: keep me');self.assertEqual(restored['original_data'],raw_origin)

    def test_legacy_backup_without_new_tables_and_tampered_support(self):
        self.s.import_bank(self.payload());self.s.attach_bank_document(dict(id=self.s.bank_entries()[0]['id'],name='a.pdf',content=base64.b64encode(b'%PDF-proof').decode()))
        p=Path(self.tmp.name)/'tampered.sqlite3';p.write_bytes(self.s.backup())
        db=sqlite3.connect(p);db.execute("UPDATE bank_attachments SET content=x'1234'");db.commit();db.close()
        with self.assertRaises(ValidationError):self.s.restore(p.read_bytes(),'RESTORE')
        p.write_bytes(self.s.backup());db=sqlite3.connect(p)
        for table in ['bank_attachments','bank_entries','bank_statements']:db.execute('DROP TABLE '+table)
        db.commit();db.close();self.s.restore(p.read_bytes(),'RESTORE');self.assertEqual(self.s.bank_entries(),[])

if __name__=='__main__':unittest.main()

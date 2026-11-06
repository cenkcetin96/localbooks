"""Supplier reuse and creation when reviewing expense documents."""
import base64
import concurrent.futures
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Store,ValidationError,identity_key
from scan import suggest_supplier_tax_id

class JournalSupplierTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(self.tmp.name)
        self.company=self.s.save_party('companies',{'name':'My company','code':'MINE'})
    def tearDown(self):self.s.db.close();self.tmp.cleanup()
    def payload(self,**extra):
        return {'company_id':self.company['id'],'supplier':'Acme Co., Ltd.','date':'2026-11-01','document_type':'invoice','payment_status':'unknown','currency':'THB','amount':'125.00','name':'supplier.pdf','content':base64.b64encode(b'%PDF-1.4 test expense').decode(),**extra}
    def unique(self,n,**extra):return self.payload(content=base64.b64encode(b'%PDF-1.4 expense '+str(n).encode()).decode(),duplicate_confirmed=True,**extra)
    def test_new_supplier_reused_by_normalized_name_and_tax(self):
        first=self.s.save_journal(self.payload(supplier_tax_id='3333333333333',supplier_address='Saved address'))
        self.assertTrue(first['supplier_created'])
        self.assertEqual(len(self.s.state()['contacts']),1)
        second=self.s.save_journal(self.unique(2,supplier=' acme co ltd '))
        self.assertEqual(second['supplier_id'],first['supplier_id']);self.assertFalse(second['supplier_created'])
        third=self.s.save_journal(self.unique(3,supplier='OCR alias',supplier_tax_id='3-3333-33333-33-3',supplier_address='Different OCR address',supplier_phone='021234567'))
        self.assertEqual(third['supplier_id'],first['supplier_id'])
        supplier=self.s.party('contacts',first['supplier_id'])
        self.assertEqual(supplier['address'],'Saved address')
        self.assertEqual(supplier['phone'],'021234567')
        self.assertTrue(all(e['supplier']=='Acme Co., Ltd.' for e in self.s.journal()))
        self.assertEqual(self.s.journal()[-1]['supplier_tax_id'],'3333333333333')
        self.s.restore(self.s.backup(),'RESTORE')
        self.assertTrue(all(e['supplier_id']==first['supplier_id'] for e in self.s.journal()))
    def test_distinct_tax_ids_and_ambiguous_names_require_selection(self):
        one=self.s.save_party('contacts',{'role':'supplier','name':'Same name','tax_id':'1111111111111'})
        two=self.s.save_party('contacts',{'role':'supplier','name':'Same name','tax_id':'2222222222222'})
        with self.assertRaises(ValidationError):self.s.save_journal(self.payload(supplier='Same name'))
        result=self.s.save_journal(self.payload(supplier='Same name',supplier_id=str(two['id'])))
        self.assertEqual(result['supplier_id'],two['id'])
        with self.assertRaises(ValidationError):self.s.save_journal(self.unique(2,supplier='Same name',supplier_id=one['id'],supplier_tax_id=two['tax_id']))
        new=self.s.save_journal(self.unique(3,supplier='Same name',supplier_tax_id='3333333333333'))
        self.assertTrue(new['supplier_created']);self.assertEqual(len(self.s.state()['contacts']),3)
    def test_missing_tax_id_filled_and_legal_name_reused(self):
        saved=self.s.save_party('contacts',{'role':'supplier','name':'Saved English name','legal_name':'บริษัท ผู้ขาย จำกัด'})
        result=self.s.save_journal(self.payload(supplier='บริษัท ผู้ขาย จำกัด',supplier_tax_id='1234567890123'))
        self.assertEqual(result['supplier_id'],saved['id'])
        self.assertEqual(self.s.party('contacts',saved['id'])['tax_id'],'1234567890123')
        self.assertNotEqual(identity_key('กิ'),identity_key('ก'))
    def test_duplicates_invalid_documents_and_failed_save_create_no_supplier(self):
        self.s.save_journal(self.payload())
        with self.assertRaises(ValidationError):self.s.save_journal(self.payload(supplier='Do not create duplicate-file supplier'))
        with self.assertRaises(ValidationError):self.s.save_journal(self.unique(2,supplier='Invalid amount supplier',amount='0'))
        self.assertEqual(len(self.s.state()['contacts']),1)
        event=self.s.event
        def fail(action,*args):
            if action=='save_journal':raise RuntimeError('Simulated save failure')
            return event(action,*args)
        self.s.event=fail
        with self.assertRaises(RuntimeError):self.s.save_journal(self.unique(3,supplier='Rolled back supplier'))
        self.s.event=event
        self.assertEqual(len(self.s.state()['contacts']),1);self.assertEqual(len(self.s.journal()),1)
    def test_concurrent_uploads_reuse_one_supplier(self):
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            results=list(pool.map(lambda n:self.s.save_journal(self.unique(n,supplier_tax_id='1234567890123')),range(4)))
        self.assertEqual(len({r['supplier_id'] for r in results}),1)
        self.assertEqual(len(self.s.state()['contacts']),1)
        self.assertEqual(len(self.s.journal()),4)
    def test_customer_and_supplier_matching_stay_separate(self):
        customer=self.s.save_party('contacts',{'name':'Acme Co., Ltd.','tax_id':'1234567890123'})
        expense=self.s.save_journal(self.payload(supplier_tax_id='1234567890123'))
        self.assertNotEqual(customer['id'],expense['supplier_id'])
        self.assertEqual([c['id'] for c in self.s.contacts('customer')],[customer['id']])
        self.assertEqual([c['id'] for c in self.s.contacts('supplier')],[expense['supplier_id']])
        with self.assertRaises(ValidationError):self.s.save_journal(self.unique(2,supplier_id=customer['id']))
        draft={'company_id':self.company['id'],'contact_id':expense['supplier_id'],'kind':'sale','currency':'THB','issue_date':'2026-11-01','lines':[{'description':'Service','quantity':'1','unit_price':'100'}]}
        with self.assertRaises(ValidationError):self.s.save_draft(draft)
        self.s.save_party('contacts',{'id':expense['supplier_id'],'name':'Acme Co., Ltd.','role':'both'})
        self.s.save_draft(draft)
        self.s.save_party('contacts',{'id':expense['supplier_id'],'name':'Updated Acme'})
        self.assertEqual(self.s.party('contacts',expense['supplier_id'])['role'],'both')
        with self.assertRaises(ValidationError):self.s.save_party('contacts',{'name':'Invalid','role':'other'})

    def test_legacy_roles_follow_document_history_and_survive_restore(self):
        customer=self.s.save_party('contacts',{'name':'Legacy customer'})
        expense=self.s.save_journal(self.payload())
        supplier_id=expense['supplier_id']
        draft={'company_id':self.company['id'],'contact_id':customer['id'],'kind':'sale','currency':'THB','issue_date':'2026-11-01','lines':[{'description':'Service','quantity':'1','unit_price':'100'}]}
        self.s.save_draft(draft)
        with self.s.db:self.s.db.execute("UPDATE contacts SET data=json_remove(data,'$.role')")
        self.assertEqual(self.s.party('contacts',customer['id'])['role'],'customer')
        self.assertEqual(self.s.party('contacts',supplier_id)['role'],'supplier')
        with self.s.db:self.s.db.execute('UPDATE invoices SET contact_id=?',(supplier_id,))
        self.assertEqual(self.s.party('contacts',supplier_id)['role'],'both')
        self.s.restore(self.s.backup(),'RESTORE')
        self.assertEqual(self.s.party('contacts',supplier_id)['role'],'both')

    def test_confirmed_cash_paid_and_payment_date(self):
        result=self.s.save_journal(self.payload(payment_status='paid',payment_date='2026-11-03',paid_amount='121.25'))
        entry=self.s.journal()[0]
        self.assertEqual(entry['paid_amount_minor'],12125)
        self.assertEqual(entry['amount_minor'],12500)
        self.assertEqual(entry['payment_date'],'2026-11-03')
        self.s.restore(self.s.backup(),'RESTORE')
        self.assertEqual(self.s.journal()[0]['paid_amount_minor'],12125)
        self.s.save_journal(self.payload(id=result['id'],payment_status='unpaid',payment_date='2026-11-03',paid_amount='121.25'))
        self.assertNotIn('paid_amount_minor',self.s.journal()[0])
        legacy=self.s.save_journal(self.unique(2,payment_status='paid'))
        self.assertNotIn('payment_date',next(e for e in self.s.journal() if e['id']==legacy['id']))
        import datetime as dt
        future=(dt.date.today()+dt.timedelta(days=1)).isoformat()
        for extra in [dict(payment_date=future,paid_amount='100'),dict(payment_date='2026-11-03',paid_amount='126'),dict(payment_date='',paid_amount='100'),dict(payment_date='2026-11-03',paid_amount='-1')]:
            with self.assertRaises(ValidationError):self.s.save_journal(self.unique(3,payment_status='paid',**extra))
        self.assertEqual(len(self.s.journal()),2)

    def test_backup_rejects_invalid_expense_cash_amount(self):
        self.s.save_journal(self.payload(payment_status='paid',payment_date='2026-11-03',paid_amount='121.25'))
        import sqlite3
        path=Path(self.tmp.name)/'bad-cash.sqlite3';path.write_bytes(self.s.backup())
        db=sqlite3.connect(path);db.execute("UPDATE journal_entries SET data=json_set(data,'$.paid_amount_minor',999999)");db.commit();db.close()
        with self.assertRaises(ValidationError):self.s.restore(path.read_bytes(),'RESTORE')

    def test_backup_rejects_dangling_supplier_id(self):
        self.s.save_journal(self.payload())
        import sqlite3
        path=Path(self.tmp.name)/'tampered.sqlite3';path.write_bytes(self.s.backup())
        db=sqlite3.connect(path);db.execute('DELETE FROM contacts');db.commit();db.close()
        with self.assertRaises(ValidationError):self.s.restore(path.read_bytes(),'RESTORE')

class SupplierScanTests(unittest.TestCase):
    def test_seller_tax_id_not_customer_tax_id(self):
        source='Seller: Supplier Co\nTax ID: 3-3333-33333-33-3\nCustomer: My Company\nTax ID: 9999999999999'
        self.assertEqual(suggest_supplier_tax_id(source),'3333333333333')
        self.assertEqual(suggest_supplier_tax_id('Seller: Supplier\nCustomer: Buyer\nTax ID: 9999999999999'),'')
        self.assertEqual(suggest_supplier_tax_id('Tax ID: 9999999999999'),'')
        self.assertEqual(suggest_supplier_tax_id('ผู้ขาย: บริษัท ตัวอย่าง จำกัด\nเลขที่ภาษี: 3333333333333\nลูกค้า: ผู้ซื้อ\nเลขที่ภาษี: 9999999999999'),'3333333333333')

if __name__=='__main__':unittest.main()

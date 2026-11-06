import base64,json,sqlite3,tempfile,unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Store,ValidationError

class CompanyScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(self.tmp.name,pdf_renderer=lambda h:b'%PDF-test')
        self.a=self.s.save_party('companies',dict(name='EXAMPLE',code='EXAMPLE'));self.b=self.s.save_party('companies',dict(name='Example Services',code='CB'))
    def tearDown(self):self.s.db.close();self.tmp.cleanup()
    def draft(self,company,contact):
        return self.s.save_draft(dict(company_id=company,contact_id=contact,kind='sale',currency='THB',issue_date='2026-10-01',lines=[dict(description='Monthly support',code='S1',quantity='1',unit_price='100')]))
    def test_legacy_contact_and_catalog_ownership_follows_existing_records_without_writes(self):
        c=self.s.save_party('contacts',dict(name='Customer'))
        self.draft(self.b['id'],c['id'])
        item=self.s.save_catalog_item(dict(name='Monthly support',code='S1',price='100',currency='THB'))
        with self.s.db:
            self.s.db.execute("UPDATE metadata SET value=json_remove(value,'$[0].company_ids') WHERE key='catalog'")
        before=self.s.db.execute('SELECT data FROM contacts WHERE id=?',(c['id'],)).fetchone()[0]
        self.assertEqual(self.s.contacts(company_id=self.a['id']),[])
        self.assertEqual(self.s.contacts(company_id=self.b['id'])[0]['id'],c['id'])
        self.assertEqual(self.s.catalog()[0]['company_ids'],[self.b['id']])
        self.assertEqual(self.s.db.execute('SELECT data FROM contacts WHERE id=?',(c['id'],)).fetchone()[0],before)
    def test_new_customer_is_owned_by_selected_company_and_draft_cannot_use_another_company(self):
        c=self.s.save_party('contacts',dict(name='EXAMPLE customer',company_ids=[self.a['id']]))
        self.draft(self.a['id'],c['id'])
        with self.assertRaisesRegex(ValidationError,'this company'):self.draft(self.b['id'],c['id'])
        self.s.save_party('contacts',dict(id=c['id'],name='EXAMPLE customer',company_ids=[self.a['id'],self.b['id']]))
        self.draft(self.b['id'],c['id'])
    def test_supplier_matching_reuses_only_selected_company_or_explicitly_shared_contacts(self):
        def expense(company,tag):return self.s.save_journal(dict(company_id=company,supplier='Vendor',supplier_tax_id='1234567890123',date='2026-10-01',document_type='invoice',payment_status='unknown',currency='THB',amount='100',name='supplier.pdf',content=base64.b64encode(b'%PDF-'+tag.encode()).decode()))
        a=expense(self.a['id'],'a');b=expense(self.b['id'],'b')
        self.assertNotEqual(a['supplier_id'],b['supplier_id'])
        self.assertEqual(len(self.s.contacts('supplier',self.a['id'])),1);self.assertEqual(len(self.s.contacts('supplier',self.b['id'])),1)
    def test_shared_catalog_remove_does_not_delete_other_company_item(self):
        item=self.s.save_catalog_item(dict(name='Shared service',price='100',currency='THB',company_ids=[self.a['id'],self.b['id']]))
        self.s.delete_catalog_item(item['id'],self.a['id'])
        self.assertEqual(self.s.catalog()[0]['company_ids'],[self.b['id']])
        with self.assertRaises(ValidationError):self.s.delete_catalog_item(item['id'],self.a['id'])
        self.s.restore(self.s.backup(),'RESTORE');self.assertEqual(self.s.catalog()[0]['company_ids'],[self.b['id']])
    def test_invalid_assignments_and_tampered_backup_are_rejected(self):
        with self.assertRaises(ValidationError):self.s.save_party('contacts',dict(name='Wrong',company_ids=[999]))
        with self.assertRaises(ValidationError):self.s.save_catalog_item(dict(name='Wrong',price='1',currency='THB',company_ids=[999]))
        c=self.s.save_party('contacts',dict(name='Customer',company_ids=[self.a['id']]))
        path=Path(self.tmp.name)/'bad.sqlite3';path.write_bytes(self.s.backup());db=sqlite3.connect(path);db.execute("UPDATE contacts SET data=json_set(data,'$.company_ids',json('[999]'))");db.commit();db.close()
        with self.assertRaises(ValidationError):self.s.restore(path.read_bytes(),'RESTORE')

if __name__=='__main__':unittest.main()

import base64
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Store,ValidationError
import accounting as a
import compliance


class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(self.tmp.name,pdf_renderer=lambda html:b'%PDF-test')
        self.c=self.s.save_party('companies',dict(name='Company A',code='A',vat_registered=True))
        self.other=self.s.save_party('companies',dict(name='Company B',code='B'))
        self.contact=self.s.save_party('contacts',dict(name='Customer'))
        self.n=0

    def tearDown(self):self.s.db.close();self.tmp.cleanup()

    def invoice(self,**extra):
        inv=self.s.save_draft(dict(company_id=self.c['id'],contact_id=self.contact['id'],kind='sale',currency='THB',issue_date='2026-10-01',lines=[dict(description='Goods',quantity='1',unit_price='3825')],vat_rate='7',**extra))
        return self.s.issue(inv['id'],expected_revision=inv['revision'])

    def work(self,kind,id,company=None):return a.workspace(self.s,kind,id,company or self.c['id'])

    def save(self,work,**extra):
        return a.save_entry(self.s,dict(work['entry'],company_id=work['source']['company_id'],source_kind=work['source']['kind'],source_id=work['source']['id'],revision=work['revision'],source_fingerprint=work['source']['fingerprint'],**extra))

    def expense(self,**extra):
        self.n+=1
        data=dict(company_id=self.c['id'],supplier='Supplier',date='2026-10-01',document_type='invoice',document_number='SUP-'+str(self.n),payment_status='unpaid',currency='THB',amount='1070',name='expense.pdf',content=base64.b64encode(b'%PDF-expense'+str(self.n).encode()).decode(),duplicate_confirmed=True)
        data.update(extra)
        return self.s.save_journal(data)['id']

    def bank(self,**extra):
        self.n+=1
        row=dict(date='2026-10-02',direction='out',amount='1070',balance='',description='Supplier payment',row_key='r'+str(self.n));row.update(extra)
        return self.s.import_bank(dict(company_id=self.c['id'],account='1111222233',currency='THB',account_confirmed=True,name='statement.csv',content=base64.b64encode(b'Date,Amount\n'+str(self.n).encode()).decode(),rows=[row]))['ids'][0]

    def test_reference_codes_are_company_scoped_and_revision_checked(self):
        codes={x['code'] for x in a.accounts(self.s,self.c['id'])}
        self.assertTrue({'1201000','4002000','2341000','4001000'}<=codes)
        saved=a.save_account(self.s,dict(company_id=self.c['id'],code='4001000',name='Product sales',type='income',active=True,revision=0))
        self.assertEqual(saved['revision'],1)
        self.assertEqual(next(x for x in a.accounts(self.s,self.other['id']) if x['code']=='4001000')['name'],'Sales')
        with self.assertRaisesRegex(ValueError,'another window'):a.save_account(self.s,dict(saved,company_id=self.c['id'],revision=0))
        with self.assertRaises(ValueError):a.save_account(self.s,dict(company_id=self.c['id'],code='<bad>',name='Bad',type='income',active=True,revision=0))

    def test_auto_invoice_exact_vat_and_no_document_mutation(self):
        inv=self.invoice();original=tuple(self.s.db.execute('SELECT * FROM invoices WHERE id=?',(inv['id'],)).fetchone())
        work=self.work('invoice',inv['id']);self.assertFalse(work['saved'])
        lines=work['entry']['lines'];self.assertEqual(lines,[a.line('1201000',409275),a.line('4001000',credit=382500),a.line('2341000',credit=26775)])
        # Client-side AUTO amounts cannot override source amounts.
        result=self.save(work,lines=[a.line('1201000',1),a.line('4001000',credit=1)])
        self.assertEqual(result['entry']['lines'],lines);self.assertEqual(result['revision'],1)
        self.assertEqual(tuple(self.s.db.execute('SELECT * FROM invoices WHERE id=?',(inv['id'],)).fetchone()),original)

    def test_manual_reference_discount_and_cost_center(self):
        inv=self.invoice();work=self.work('invoice',inv['id'])
        rows=[a.line('1201000',378579),a.line('4002000',28688),a.line('2341000',credit=24767),a.line('4001000',credit=382500)]
        rows[0]['cost_center']='Bangkok'
        result=self.save(work,mode='manual',lines=rows,note='Reviewed discount')
        self.assertEqual(result['entry']['lines'],rows);self.assertEqual(result['entry']['note'],'Reviewed discount')
        with self.assertRaisesRegex(ValueError,'balance'):self.save(result,mode='manual',lines=[a.line('1201000',100),a.line('4001000',credit=99)])
        with self.assertRaisesRegex(ValueError,'one side'):self.save(result,mode='manual',lines=[a.line('1201000',100,100),a.line('4001000',credit=100)])
        with self.assertRaisesRegex(ValueError,'integer'):self.save(result,mode='manual',lines=[a.line('1201000',1.5),a.line('4001000',credit=1.5)])

    def test_unknown_and_inactive_accounts_are_rejected(self):
        work=self.work('invoice',self.invoice()['id'])
        with self.assertRaisesRegex(ValueError,'saved account'):self.save(work,mode='manual',lines=[a.line('OTHER',100),a.line('4001000',credit=100)])
        a.save_account(self.s,dict(company_id=self.c['id'],code='4001000',name='Sales',type='income',active=False,revision=0))
        with self.assertRaisesRegex(ValueError,'saved account'):self.save(work)
        result=self.save(work,mode='manual',lines=[a.line('1201000',100),a.line('9990000',credit=100)])
        self.assertTrue(result['saved'])

    def test_draft_void_and_reversed_receipt_preserve_history(self):
        inv=self.invoice();self.save(self.work('invoice',inv['id']))
        self.s.void(inv['id'],'Correction')
        work=self.work('invoice',inv['id']);self.assertTrue(work['stale']);self.assertTrue(work['source']['blocked'])
        with self.assertRaises(ValueError):self.save(work)
        draft=self.s.duplicate_invoice(inv['id']);work=self.work('invoice',draft['id'])
        with self.assertRaises(ValueError):self.save(work)
        inv=self.invoice();inv=self.s.add_payment(inv['id'],dict(amount='100',withholding='3',date='2026-10-02',method='Bank transfer',request_key='wht'))
        pid=inv['payments'][0]['id'];work=self.work('payment',pid)
        self.assertEqual(work['entry']['lines'],[a.line('1102000',10000),a.line('1201000',credit=10300),a.line('1202000',300)])
        self.save(work);self.s.reverse_payment(inv['id'],pid,'Correction')
        work=self.work('payment',pid);self.assertTrue(work['source']['blocked']);self.assertTrue(work['stale'])

    def test_expense_uses_payable_and_bank_settles_it(self):
        eid=self.expense();bid=self.bank();work=self.work('expense',eid)
        self.assertEqual(work['entry']['lines'],[a.line('5001000',107000),a.line('2101000',credit=107000)])
        self.save(work)
        bank=next(b for b in self.s.bank_entries() if b['id']==bid)
        self.s.save_bank_entry(dict(bank,amount='1070',category='expense',reviewed=True,matched_record='expense:'+str(eid)))
        work=self.work('bank',bid);self.assertEqual(work['entry']['lines'],[a.line('2101000',107000),a.line('1102000',credit=107000)])
        self.save(work)
        with self.assertRaisesRegex(ValidationError,'original company'):self.s.save_journal(dict(id=eid,company_id=self.other['id'],supplier='Supplier',date='2026-10-01',document_type='invoice',payment_status='unpaid',currency='THB',amount='1070'))

    def test_unmatched_bank_suspense_and_receipt_link_avoids_duplicate(self):
        bid=self.bank(direction='in',amount='100');work=self.work('bank',bid)
        self.assertEqual(work['entry']['lines'],[a.line('1102000',10000),a.line('9990000',credit=10000)])
        self.save(work)
        inv=self.invoice();bank=next(b for b in self.s.bank_entries() if b['id']==bid)
        inv=self.s.add_payment(inv['id'],dict(amount='100',date='2026-10-02',method='Bank transfer',request_key='linked',link_bank_id=bid,bank_revision=bank['revision']))
        work=self.work('bank',bid);self.assertTrue(work['source']['blocked']);self.assertTrue(work['stale'])
        self.assertEqual(work['source']['related']['id'],inv['payments'][0]['id'])
        with self.assertRaises(ValueError):self.save(work)

    def test_source_change_stale_save_and_currency_change(self):
        eid=self.expense();old=self.work('expense',eid);saved=self.save(old)
        self.s.save_journal(dict(id=eid,company_id=self.c['id'],supplier='Supplier',date='2026-10-02',document_type='invoice',payment_status='unpaid',currency='JPY',amount='100'))
        with self.assertRaisesRegex(ValueError,'source transaction changed'):self.save(saved)
        work=self.work('expense',eid);self.assertTrue(work['stale']);self.assertEqual(work['entry']['currency'],'THB')
        with self.assertRaisesRegex(ValueError,'source currency changed'):self.save(work)
        work['entry']=work['automatic'];result=self.save(work);self.assertEqual(result['entry']['currency'],'JPY')
        with self.assertRaisesRegex(ValueError,'another window'):self.save(work)

    def test_cross_company_and_unsupported_source_rejected(self):
        inv=self.invoice()
        with self.assertRaisesRegex(ValueError,'another company'):self.work('invoice',inv['id'],self.other['id'])
        with self.assertRaises(ValueError):self.work('bad',inv['id'])

    def test_backup_restore_and_company_export(self):
        inv=self.invoice();saved=self.save(self.work('invoice',inv['id']))
        backup=self.s.backup();self.s.restore(backup,'RESTORE')
        self.assertEqual(self.work('invoice',inv['id'])['entry'],saved['entry'])
        pack=zipfile.ZipFile(io.BytesIO(compliance.export_pack(self.s,self.c['id'],2026)))
        entries=json.loads(pack.read('accounting-entries.json'));self.assertEqual(len(entries['entries']),1)
        self.assertIn('4001000',pack.read('accounting-entries.csv').decode());self.assertIn('chart-of-accounts.csv',pack.namelist())
        pack=zipfile.ZipFile(io.BytesIO(compliance.export_pack(self.s,self.other['id'],2026)))
        self.assertEqual(json.loads(pack.read('accounting-entries.json'))['entries'],[])
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bad.sqlite3';path.write_bytes(backup);db=sqlite3.connect(path)
            db.execute("UPDATE accounting_entries SET data=json_set(data,'$.lines[0].debit_minor',1)");db.commit();db.close()
            with self.assertRaises(ValidationError):self.s.restore(path.read_bytes(),'RESTORE')
            path.write_bytes(backup);db=sqlite3.connect(path)
            for table in a.TABLES:db.execute('DROP TABLE '+table)
            db.commit();db.close();self.s.restore(path.read_bytes(),'RESTORE')
            self.assertFalse(self.work('invoice',inv['id'])['saved'])

    def test_automatic_mappings_are_scoped_and_flag_saved_entries(self):
        work=self.work('invoice',self.invoice()['id']);saved=self.save(work)
        a.save_account(self.s,dict(company_id=self.c['id'],code='4100',name='Consulting revenue',type='income',active=True,revision=0))
        mapping=a.mappings(self.s,self.c['id']);mapping['roles']['sales']='4100'
        a.save_mappings(self.s,dict(company_id=self.c['id'],revision=0,roles=mapping['roles']))
        work=self.work('invoice',work['source']['id']);self.assertTrue(work['stale'])
        self.assertEqual(work['automatic']['lines'][1]['code'],'4100')
        self.assertEqual(work['entry']['lines'],saved['entry']['lines'])
        self.assertEqual(a.mappings(self.s,self.other['id'])['roles']['sales'],'4001000')
        with self.assertRaisesRegex(ValueError,'another window'):a.save_mappings(self.s,dict(company_id=self.c['id'],revision=0,roles=mapping['roles']))
        mapping['roles']['sales']='missing'
        with self.assertRaisesRegex(ValueError,'active company account'):a.save_mappings(self.s,dict(company_id=self.c['id'],revision=1,roles=mapping['roles']))

    def test_balances_net_receivable_and_exclude_stale_entries(self):
        inv=self.invoice();self.save(self.work('invoice',inv['id']))
        inv=self.s.add_payment(inv['id'],dict(amount='100',withholding='3',date='2026-10-02',method='Bank transfer',request_key='balances'))
        pid=inv['payments'][0]['id'];self.save(self.work('payment',pid))
        report=a.balances(self.s,self.c['id'],'2026-10-01','2026-10-31','THB')
        self.assertEqual(report['included'],2);self.assertTrue(report['balanced'])
        ar=next(r for r in report['accounts'] if r['code']=='1201000')
        self.assertEqual(ar['net_debit_minor'],398975)
        self.assertEqual(report['totals']['debit_minor'],419575)
        self.assertEqual(a.balances(self.s,self.c['id'],'2026-10-01','2026-10-31','USD')['included'],0)
        self.assertEqual(a.balances(self.s,self.other['id'],'2026-10-01','2026-10-31','THB')['included'],0)
        self.s.reverse_payment(inv['id'],pid,'Wrong payment')
        report=a.balances(self.s,self.c['id'],'2026-10-01','2026-10-31','THB')
        self.assertEqual(report['included'],1);self.assertEqual(len(report['excluded']),1)
        self.assertEqual(report['excluded'][0]['href'],'#document/'+str(inv['id']))
        with self.assertRaises(ValueError):a.balances(self.s,self.c['id'],'2026-11-01','2026-10-01','THB')

    def test_balance_csv_protects_account_names_and_keeps_exact_units(self):
        a.save_account(self.s,dict(company_id=self.c['id'],code='4001000',name='=1+1',type='income',active=True,revision=0))
        self.save(self.work('invoice',self.invoice()['id']))
        report=a.balances(self.s,self.c['id'],'2026-01-01','2026-12-31','THB')
        csv=a.balances_csv(report).decode('utf-8-sig')
        self.assertIn("'=1+1",csv);self.assertIn('382500',csv);self.assertIn('TOTAL',csv)

    def test_mapping_and_custom_codes_survive_backup(self):
        a.save_account(self.s,dict(company_id=self.c['id'],code='B-01',name='Operating bank',type='asset',active=True,revision=0))
        m=a.mappings(self.s,self.c['id']);m['roles']['bank']='B-01'
        a.save_mappings(self.s,dict(company_id=self.c['id'],revision=0,roles=m['roles']))
        self.s.restore(self.s.backup(),'RESTORE')
        self.assertEqual(a.mappings(self.s,self.c['id'])['roles']['bank'],'B-01')


if __name__=='__main__':unittest.main()

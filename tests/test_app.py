import base64
import concurrent.futures
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Store,ValidationError,calculate,amount_minor

def fake_pdf(html):return b'%PDF-TEST\n'+hashlib.sha256(html.encode()).hexdigest().encode()

class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(self.tmp.name,pdf_renderer=fake_pdf)
        self.company=self.s.save_party('companies',dict(name='Demo Company',code='DEMO'))
        self.contact=self.s.save_party('contacts',dict(name='Customer / supplier'))
    def tearDown(self):self.s.db.close();self.tmp.cleanup()
    def draft(self,**overrides):
        d=dict(company_id=self.company['id'],contact_id=self.contact['id'],kind='sale',currency='THB',issue_date=dt.date.today().isoformat(),due_date='',lines=[dict(description='Service',quantity='1',unit_price='100.00')]);d.update(overrides);return self.s.save_draft(d)
    def issued(self,**overrides):
        d=self.draft(**overrides);return self.s.issue(d['id'],expected_revision=d['revision'])
    def payment(self,inv,amount,key='one',**extra):
        return self.s.add_payment(inv['id'],dict(amount=amount,method='Bank transfer',request_key=key,**{'date':dt.date.today().isoformat(),**extra}))
    def test_duplicate_paid_invoice_is_independent_editable_draft(self):
        today=dt.date.today();old_date=(today-dt.timedelta(days=30)).isoformat()
        self.s.save_party('companies',dict(id=self.company['id'],name='Demo Company',code='DEMO',vat_registered=True))
        lines=[dict(description='Monthly support',quantity='2',unit_price='100.00',code='SUPPORT',unit='month',service='October 2026')]
        source=self.issued(issue_date=old_date,due_date=(today-dt.timedelta(days=16)).isoformat(),lines=lines,vat_rate='7',reference='Project 12',notes='Original notes',payment_instructions='Bank transfer',contact_details={'name':'Invoice customer','contact_name':'Customer Person'})
        source=self.payment(source,'208',withholding='6')
        source=self.s.attach(source['id'],dict(name='proof.pdf',content=base64.b64encode(b'%PDF-proof').decode(),payment_id=source['payments'][0]['id']))
        original=tuple(self.s.db.execute('SELECT * FROM invoices WHERE id=?',(source['id'],)).fetchone())
        numbers=self.s.db.execute('SELECT count(*) FROM numbers').fetchone()[0]
        self.s.save_party('contacts',dict(id=self.contact['id'],name='Changed reusable contact'))
        copy=self.s.duplicate_invoice(source['id'])
        self.assertNotEqual(copy['id'],source['id']);self.assertEqual(copy['status'],'draft');self.assertIsNone(copy['number'])
        self.assertEqual(copy['payments'],[]);self.assertEqual(copy['attachments'],[])
        self.assertEqual(copy['paid_minor'],0);self.assertEqual(copy['balance_minor'],21400)
        self.assertEqual(copy['company_id'],source['company_id']);self.assertEqual(copy['contact_id'],source['contact_id'])
        self.assertEqual(copy['data']['issue_date'],today.isoformat())
        self.assertEqual(copy['data']['due_date'],(today+dt.timedelta(days=14)).isoformat())
        for key in ('lines','vat_rate','reference','notes','payment_instructions','contact'):
            self.assertEqual(copy['data'][key],source['data'][key],key)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM numbers').fetchone()[0],numbers)
        row=self.s.db.execute('SELECT html,pdf FROM invoices WHERE id=?',(copy['id'],)).fetchone();self.assertEqual(tuple(row),(None,None))
        edited=self.s.save_draft(dict(id=copy['id'],revision=copy['revision'],company_id=copy['company_id'],contact_id=copy['contact_id'],kind='sale',currency='THB',issue_date=today.isoformat(),lines=[dict(description='New work',quantity='1',unit_price='300')],vat_rate='7'))
        self.assertEqual(edited['total_minor'],32100)
        issued=self.s.issue(edited['id'],expected_revision=edited['revision'])
        self.assertNotEqual(issued['number'],source['number'])
        self.assertEqual(tuple(self.s.db.execute('SELECT * FROM invoices WHERE id=?',(source['id'],)).fetchone()),original)
        self.assertEqual(self.s.invoice(source['id'])['balance_minor'],0)
        self.assertEqual(self.s.party('contacts',self.contact['id'])['name'],'Changed reusable contact')

    def test_duplicate_tax_invoice_and_draft_and_void_but_not_payable(self):
        self.s.save_party('companies',dict(id=self.company['id'],name='Demo Company',code='DEMO',vat_registered=True,address='Bangkok',branch='Head office',tax_id='1234567890123'))
        source=self.draft(vat_rate='7',tax_invoice=True,contact_details={'address':'Buyer address'})
        source=self.s.issue(source['id'],expected_revision=source['revision'],tax_confirmed=True)
        copy=self.s.duplicate_invoice(source['id'])
        self.assertTrue(copy['data']['tax_invoice']);self.assertEqual(copy['data']['vat_rate'],'7')
        self.assertEqual(copy['data']['due_date'],'')
        self.assertEqual(self.s.duplicate_invoice(copy['id'])['status'],'draft')
        ordinary=self.issued();self.s.void(ordinary['id'],'Test void')
        self.assertEqual(self.s.duplicate_invoice(ordinary['id'])['status'],'draft')
        with self.s.db:self.s.db.execute("UPDATE invoices SET kind='purchase' WHERE id=?",(ordinary['id'],))
        count=self.s.db.execute('SELECT count(*) FROM invoices').fetchone()[0]
        with self.assertRaises(ValidationError):self.s.duplicate_invoice(ordinary['id'])
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM invoices').fetchone()[0],count)

    def test_company_bank_account_documents_and_snapshots(self):
        bank=dict(bank_name='Example Bank',bank_account_name='Demo Company',bank_account_number='001-02-003456',bank_branch='Bangkok',bank_swift='KASITHBKXXX')
        draft=self.draft()
        self.s.save_party('companies',dict(id=self.company['id'],name='Demo Company',code='DEMO',**bank))
        invoice=self.s.issue(draft['id'],expected_revision=draft['revision'])
        original=self.s.db.execute('SELECT html FROM invoices WHERE id=?',(invoice['id'],)).fetchone()[0]
        self.assertIn('001-02-003456',original)
        self.assertIn('Example Bank',original)
        self.assertIn('KASITHBKXXX',original)
        with self.assertRaises(ValidationError):
            self.s.save_party('companies',dict(name='Invalid bank',code='INVALID',**{**bank,'bank_swift':'BAD'}))
        other=self.s.save_party('companies',dict(name='Other company',code='OTHER'))
        self.assertEqual(other['bank_account_number'],'')
        with self.assertRaises(ValidationError):
            self.s.save_party('companies',dict(name='Incomplete',code='BAD',bank_name='Only a bank'))
        # Older clients preserve bank fields when saving other company details.
        self.s.save_party('companies',dict(id=self.company['id'],name='Demo Company',code='DEMO'))
        self.assertEqual(self.s.party('companies',self.company['id'])['bank_account_number'],'001-02-003456')
        changed={**bank,'bank_account_number':'000-999-888'}
        self.s.save_party('companies',dict(id=self.company['id'],name='Demo Company',code='DEMO',**changed))
        paid=self.payment(invoice,'100')
        receipt=self.s.db.execute('SELECT html FROM payments WHERE id=?',(paid['payments'][0]['id'],)).fetchone()[0]
        self.assertIn('000-999-888',receipt)
        self.assertNotIn('001-02-003456',receipt)
        self.assertEqual(self.s.db.execute('SELECT html FROM invoices WHERE id=?',(invoice['id'],)).fetchone()[0],original)
        backup=self.s.backup();self.s.restore(backup,'RESTORE')
        self.assertEqual(self.s.party('companies',self.company['id'])['bank_account_number'],'000-999-888')
        self.s.save_party('companies',dict(id=self.company['id'],name='Demo Company',code='DEMO',**{k:'' for k in bank}))
        self.assertEqual(self.s.party('companies',self.company['id'])['bank_account_number'],'')

    def test_receipt_issue_date_matches_selected_payment_date(self):
        today=dt.date.today()
        invoice=self.issued(issue_date=(today-dt.timedelta(days=2)).isoformat())
        paid_date=(today-dt.timedelta(days=1)).isoformat()
        paid=self.payment(invoice,'100',date=paid_date,receipt_date=today.isoformat())
        payment=paid['payments'][0]
        self.assertEqual(payment['data']['date'],paid_date)
        self.assertEqual(payment['data']['receipt_date'],paid_date)
        from documents import pretty_date
        html=self.s.db.execute('SELECT html FROM payments WHERE id=?',(payment['id'],)).fetchone()[0]
        self.assertIn(pretty_date(paid_date),html)
        self.assertNotIn(pretty_date(today.isoformat()),html)

    def test_catalog_reuse_and_backup(self):
        item=self.s.save_catalog_item({'name':'Design service','currency':'THB','price':'125.50'})
        self.assertEqual(self.s.state()['catalog'][0]['price'],'125.50')
        backup=self.s.backup()
        self.s.save_catalog_item({**item,'name':'Updated service'})
        self.assertEqual(self.s.state()['catalog'][0]['name'],'Updated service')
        with self.assertRaises(ValidationError):self.s.save_catalog_item({'name':'Bad','currency':'JPY','price':'1.50'})
        self.s.delete_catalog_item(item['id'])
        self.assertEqual(self.s.state()['catalog'],[])
        self.s.restore(backup,'RESTORE')
        self.assertEqual(self.s.state()['catalog'][0]['name'],'Design service')

    def test_decimal_rounding_and_currencies(self):
        items,total=calculate([dict(description='A',quantity='0.5',unit_price='0.01'),dict(description='B',quantity='3',unit_price='0.10')],'THB')
        self.assertEqual([x['total_minor'] for x in items],[1,30]);self.assertEqual(total,31)
        self.assertEqual(calculate([dict(description='A',quantity='1.5',unit_price='1')],'JPY')[1],2)
        self.assertEqual(calculate([dict(description='A',quantity='1.5',unit_price='1.001')],'KWD')[1],1502)
        self.assertEqual(amount_minor('0.29','THB'),29)
        for bad in ['NaN','Infinity','-1','1.001']:
            with self.assertRaises(ValidationError):amount_minor(bad,'THB')
    def test_partial_full_reversal_and_duplicate_request(self):
        i=self.issued();i=self.payment(i,'40.00');self.assertEqual((i['paid_minor'],i['balance_minor'],i['payment_status']),(4000,6000,'partial'))
        i=self.payment(i,'40.00');self.assertEqual(len(i['payments']),1)
        with self.assertRaises(ValidationError):self.payment(i,'60.01','over')
        with self.assertRaises(ValidationError):self.payment(i,'0','zero')
        i=self.payment(i,'60','two');self.assertEqual(i['payment_status'],'paid')
        p=i['payments'][0];pdf=self.s.db.execute('SELECT pdf FROM payments WHERE id=?',(p['id'],)).fetchone()[0]
        i=self.s.reverse_payment(i['id'],p['id'],'Entered wrong amount');self.assertEqual(i['balance_minor'],4000)
        self.assertEqual(self.s.db.execute('SELECT pdf FROM payments WHERE id=?',(p['id'],)).fetchone()[0],pdf)
        with self.assertRaises(ValidationError):self.s.reverse_payment(i['id'],p['id'],'twice')
    def test_journal_original_edit_and_backup_compatibility(self):
        raw=b'%PDF-1.4 test file';payload={'company_id':self.company['id'],'supplier':'Supplier','date':'2026-10-08','document_number':'SUP-1','description':'Service','document_type':'invoice','payment_status':'unknown','currency':'THB','amount':'16500.00','name':'supplier.pdf','content':base64.b64encode(raw).decode()}
        saved=self.s.save_journal(payload);entry=self.s.state()['journal'][0]
        self.assertEqual((entry['amount_minor'],entry['payment_status']),(1650000,'unknown'))
        self.assertEqual(self.s.db.execute('SELECT content FROM journal_entries WHERE id=?',(saved['id'],)).fetchone()[0],raw)
        with self.assertRaises(ValidationError):self.s.save_journal(payload)
        self.s.save_journal({**payload,'id':saved['id'],'supplier':'Corrected supplier','amount':'16005.00'})
        self.assertEqual(self.s.state()['journal'][0]['supplier'],'Corrected supplier')
        backup=self.s.backup();self.s.restore(backup,'RESTORE')
        self.assertEqual(self.s.state()['journal'][0]['amount_minor'],1600500)
        path=Path(self.tmp.name)/'damaged-journal.sqlite3';path.write_bytes(backup)
        db=sqlite3.connect(path);db.execute("UPDATE journal_entries SET content=x'00'");db.commit();db.close()
        with self.assertRaises(ValidationError):self.s.restore(path.read_bytes(),'RESTORE')
        path.write_bytes(backup);db=sqlite3.connect(path);db.execute('DROP TABLE journal_entries');db.commit();db.close()
        self.s.restore(path.read_bytes(),'RESTORE');self.assertEqual(self.s.state()['journal'],[])

    def test_signature_upload_and_snapshots(self):
        signature='data:image/png;base64,'+base64.b64encode(b'\x89PNG\r\n\x1a\nexample').decode()
        draft=self.draft()
        updated=self.s.save_party('companies',{'id':self.company['id'],'name':'Demo Company','code':'DEMO','signature':signature})
        self.assertEqual(updated['signature'],signature)
        invoice=self.s.issue(draft['id'],expected_revision=draft['revision'])
        invoice_html=self.s.db.execute('SELECT html FROM invoices WHERE id=?',(invoice['id'],)).fetchone()[0]
        self.assertIn(signature,invoice_html)
        with self.assertRaises(ValidationError):self.s.save_party('companies',{'id':self.company['id'],'name':'Demo Company','code':'DEMO','signature':'data:image/png;base64,YWJj'})
        self.s.save_party('companies',{'id':self.company['id'],'name':'Demo Company','code':'DEMO','signature':''})
        paid=self.payment(invoice,'100')
        receipt_html=self.s.db.execute('SELECT html FROM payments WHERE id=?',(paid['payments'][0]['id'],)).fetchone()[0]
        self.assertNotIn(signature,receipt_html)
        self.assertEqual(self.s.db.execute('SELECT html FROM invoices WHERE id=?',(invoice['id'],)).fetchone()[0],invoice_html)
        backup=self.s.backup();self.s.restore(backup,'RESTORE')
        self.assertEqual(self.s.party('companies',self.company['id'])['signature'],'')

    def test_withholding_partial_reversal_and_restore(self):
        invoice=self.issued(lines=[dict(description='Consulting',quantity='1',unit_price='16500')])
        with self.assertRaises(ValidationError):self.payment(invoice,'16005','excess',withholding='496')
        with self.assertRaises(ValidationError):self.payment(invoice,'16005','negative',withholding='-1')
        partial=self.payment(invoice,'8000','first',withholding='250')
        self.assertEqual((partial['cash_paid_minor'],partial['withheld_minor'],partial['balance_minor']),(800000,25000,825000))
        paid=self.payment(partial,'8005','second',withholding='245')
        self.assertEqual((paid['cash_paid_minor'],paid['withheld_minor'],paid['paid_minor'],paid['balance_minor']),(1600500,49500,1650000,0))
        receipt=paid['payments'][-1]
        html=self.s.db.execute('SELECT html FROM payments WHERE id=?',(receipt['id'],)).fetchone()[0]
        self.assertIn('Withholding tax',html);self.assertIn('฿245.00',html);self.assertIn('฿8,005.00',html)
        backup=self.s.backup()
        reversed_invoice=self.s.reverse_payment(paid['id'],receipt['id'],'Correction')
        self.assertEqual(reversed_invoice['balance_minor'],825000)
        self.assertEqual(self.s.db.execute('SELECT html FROM payments WHERE id=?',(receipt['id'],)).fetchone()[0],html)
        self.s.restore(backup,'RESTORE')
        self.assertEqual(self.s.invoice(paid['id'])['balance_minor'],0)
        path=Path(self.tmp.name)/'bad-withholding.sqlite3';path.write_bytes(backup)
        db=sqlite3.connect(path);db.execute("UPDATE payments SET data=json_set(data,'$.withholding_minor',999999999) WHERE id=?",(receipt['id'],));db.commit();db.close()
        with self.assertRaises(ValidationError):self.s.restore(path.read_bytes(),'RESTORE')

    def test_numbering_collision_void_and_company_sequences(self):
        a=self.draft();a=self.s.issue(a['id'],'CUSTOM-1',a['revision']);b=self.draft()
        with self.assertRaises(ValidationError):self.s.issue(b['id'],'CUSTOM-1',b['revision'])
        self.assertEqual(self.s.invoice(b['id'])['status'],'draft')
        self.s.void(a['id'],'Cancelled')
        with self.assertRaises(ValidationError):self.s.issue(b['id'],'CUSTOM-1',b['revision'])
        b=self.s.issue(b['id'],expected_revision=b['revision']);self.assertTrue(b['number'].endswith('0001'))
        c=self.issued();self.assertTrue(c['number'].endswith('0002'))
        company2=self.s.save_party('companies',dict(name='Second company',code='SECOND'))
        second=self.issued(company_id=company2['id']);self.assertTrue(second['number'].endswith('0001'))
    def test_concurrent_issuance_and_double_issue(self):
        drafts=[self.draft() for _ in range(8)]
        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            results=list(pool.map(lambda i:self.s.issue(i['id'],expected_revision=i['revision']),drafts))
        self.assertEqual(len({x['number'] for x in results}),8)
        with self.assertRaises(ValidationError):self.s.issue(results[0]['id'],expected_revision=1)
    def test_immutable_documents_and_stale_drafts(self):
        d=self.draft();payload={**d['data'],'company_id':d['company_id'],'contact_id':d['contact_id'],'kind':'sale','id':d['id'],'revision':d['revision']}
        d2=self.s.save_draft(payload)
        with self.assertRaises(ValidationError):self.s.save_draft(payload)
        with self.assertRaises(ValidationError):self.s.issue(d['id'],expected_revision=d['revision'])
        i=self.s.issue(d2['id'],expected_revision=d2['revision']);pdf=self.s.db.execute('SELECT pdf FROM invoices WHERE id=?',(i['id'],)).fetchone()[0]
        self.s.save_party('companies',dict(id=self.company['id'],name='Changed',code='DEMO'))
        self.assertEqual(self.s.invoice(i['id'])['data']['company']['name'],'Demo Company')
        self.assertEqual(self.s.db.execute('SELECT pdf FROM invoices WHERE id=?',(i['id'],)).fetchone()[0],pdf)
        with self.assertRaises(ValidationError):self.s.save_draft({**payload,'revision':d2['revision']})
    def test_pdf_failure_rolls_back_number_and_payment(self):
        d=self.draft();normal=self.s.pdf_renderer
        def fail(html):raise RuntimeError('renderer unavailable')
        self.s.pdf_renderer=fail
        with self.assertRaises(RuntimeError):self.s.issue(d['id'],expected_revision=d['revision'])
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM numbers').fetchone()[0],0)
        self.assertEqual(self.s.invoice(d['id'])['status'],'draft')
        self.s.pdf_renderer=normal;i=self.s.issue(d['id'],expected_revision=d['revision']);self.s.pdf_renderer=fail
        with self.assertRaises(RuntimeError):self.payment(i,'10')
        self.assertEqual(self.s.invoice(i['id'])['paid_minor'],0)
    def test_persistence_attachment_links_and_no_inferred_payment(self):
        i=self.issued();content=b'Original local PDF\x00bytes'
        i=self.s.attach(i['id'],dict(name='original.pdf',content=base64.b64encode(content).decode()))
        self.assertEqual(i['paid_minor'],0)
        i=self.payment(i,'25');pid=i['payments'][0]['id']
        i=self.s.attach(i['id'],dict(payment_id=pid,name='proof.pdf',content=base64.b64encode(content).decode()))
        a=i['attachments'][-1];self.assertEqual(a['payment_id'],pid)
        self.assertEqual(self.s.db.execute('SELECT content FROM attachments WHERE id=?',(a['id'],)).fetchone()[0],content)
        self.s.db.close();self.s=Store(self.tmp.name,pdf_renderer=fake_pdf);i=self.s.invoice(i['id'])
        self.assertEqual(i['balance_minor'],7500);self.assertEqual(len(i['attachments']),2)
        with self.assertRaises(ValidationError):self.s.attach(i['id'],dict(payment_id=999,name='bad',content='YQ=='))
    def test_payable_generation_removed_and_legacy_snapshots_retained(self):
        from documents import render_document
        with self.assertRaisesRegex(ValidationError,'Expense journal'):
            self.draft(kind='purchase',supplier_number='SUP-10')
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM invoices').fetchone()[0],0)
        draft=self.draft()
        with self.s.db:
            self.s.db.execute("UPDATE invoices SET kind='purchase' WHERE id=?",(draft['id'],))
        legacy=self.s.invoice(draft['id'])
        with self.assertRaisesRegex(ValidationError,'Expense journal'):
            self.s.issue(legacy['id'],expected_revision=legacy['revision'])
        with self.assertRaisesRegex(ValueError,'Expense journal'):
            render_document(legacy,draft=True)
        self.assertEqual(self.s.invoice(legacy['id'])['status'],'draft')
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM numbers').fetchone()[0],0)
        # Existing supplier snapshots remain byte-for-byte intact in backups.
        old=self.issued();old=self.payment(old,'25',key='legacy')
        pid=old['payments'][0]['id']
        saved_invoice=tuple(self.s.db.execute('SELECT html,pdf FROM invoices WHERE id=?',(old['id'],)).fetchone())
        saved_payment=tuple(self.s.db.execute('SELECT html,pdf FROM payments WHERE id=?',(pid,)).fetchone())
        with self.s.db:self.s.db.execute("UPDATE invoices SET kind='purchase' WHERE id=?",(old['id'],))
        self.s.pdf_renderer=lambda html:self.fail('Retired payables must never render')
        with self.assertRaisesRegex(ValidationError,'Expense journal'):
            self.payment(old,'10',key='new-payment')
        self.assertEqual(len(self.s.invoice(old['id'])['payments']),1)
        self.s.restore(self.s.backup(),'RESTORE')
        self.assertEqual(self.s.invoice(old['id'])['kind'],'purchase')
        self.assertEqual(self.s.invoice(old['id'])['balance_minor'],7500)
        self.assertEqual(tuple(self.s.db.execute('SELECT html,pdf FROM invoices WHERE id=?',(old['id'],)).fetchone()),saved_invoice)
        self.assertEqual(tuple(self.s.db.execute('SELECT html,pdf FROM payments WHERE id=?',(pid,)).fetchone()),saved_payment)

    def test_vat_invoice_and_tax_invoice_safeguards(self):
        with self.assertRaises(ValidationError):self.draft(vat_rate='7')
        company=self.s.save_party('companies',{'id':self.company['id'],'name':'VAT Company','code':'DEMO','vat_registered':True,'address':'Bangkok','branch':'Head office / 00000','tax_id':'1234567890123'})
        contact=self.s.save_party('contacts',{'id':self.contact['id'],'name':'VAT Customer','address':'Bangkok','vat_registered':True,'branch':'Head office / 00000','tax_id':'9876543210987'})
        invoice=self.draft(company_id=company['id'],contact_id=contact['id'],vat_rate='7',tax_invoice=True,lines=[{'description':'Work','quantity':'1','unit_price':'100.01'}])
        self.assertEqual((invoice['data']['subtotal_minor'],invoice['data']['vat_minor'],invoice['total_minor']),(10001,700,10701))
        with self.assertRaises(ValidationError):self.s.issue(invoice['id'],expected_revision=invoice['revision'])
        invoice=self.s.issue(invoice['id'],expected_revision=invoice['revision'],tax_confirmed=True)
        html=self.s.db.execute('SELECT html FROM invoices WHERE id=?',(invoice['id'],)).fetchone()[0]
        self.assertIn('ใบกำกับภาษี',html);self.assertIn('VAT 7%',html);self.assertIn('฿7.00',html)
        self.assertIn('<th>VAT %</th><th>Amount before VAT</th>',html)
        self.assertIn('<td>7%</td><td>฿100.01</td>',html)
        with self.assertRaises(ValidationError):self.s.void(invoice['id'],'Correction')
        receipt=self.payment(invoice,'107.01')
        html=self.s.db.execute('SELECT html FROM payments WHERE id=?',(receipt['payments'][0]['id'],)).fetchone()[0]
        self.assertIn('VAT 7%',html);self.assertEqual(receipt['balance_minor'],0)
        backup=self.s.backup();self.s.restore(backup,'RESTORE');self.assertEqual(self.s.invoice(invoice['id'])['total_minor'],10701)
        for bad in ('101','-1','1.001'):
            with self.assertRaises(ValidationError):self.draft(company_id=company['id'],vat_rate=bad)
        missing=self.draft(company_id=company['id'],contact_id=self.contact['id'],vat_rate='7',tax_invoice=True,contact_details={'address':''})
        with self.assertRaises(ValidationError):self.s.issue(missing['id'],expected_revision=missing['revision'],tax_confirmed=True)

    def test_withholding_certificate_linked_and_restored(self):
        invoice=self.issued();invoice=self.payment(invoice,'97','tax',withholding='3')
        payment_id=invoice['payments'][0]['id']
        with self.assertRaises(ValidationError):self.s.attach(invoice['id'],dict(name='bad.pdf',content='YQ==',category='withholding_certificate'))
        invoice=self.s.attach(invoice['id'],dict(payment_id=payment_id,name='certificate.pdf',content='YQ==',category='withholding_certificate'))
        attachment=invoice['attachments'][0]
        self.assertEqual((attachment['category'],attachment['payment_id']),('withholding_certificate',payment_id))
        backup=self.s.backup();self.s.restore(backup,'RESTORE')
        self.assertEqual(self.s.invoice(invoice['id'])['attachments'][0]['category'],'withholding_certificate')

    def test_backup_restore_and_safety_copy(self):
        i=self.issued();self.payment(i,'25');self.s.attach(i['id'],dict(name='original.txt',content='aGVsbG8='));backup=self.s.backup()
        self.s.save_party('contacts',dict(name='Created after backup'));self.assertEqual(len(self.s.state()['contacts']),2)
        result=self.s.restore(backup,'RESTORE');self.assertTrue(Path(result['safety_copy']).is_file());self.assertEqual(len(self.s.state()['contacts']),1)
        restored=self.s.invoice(i['id']);self.assertEqual(restored['balance_minor'],7500);self.assertEqual(len(restored['attachments']),1)
        old=sqlite3.connect(result['safety_copy']);self.assertEqual(old.execute('SELECT count(*) FROM contacts').fetchone()[0],2);old.close()
    def test_backup_rejects_invalid_and_tampered_content(self):
        i=self.issued();self.s.attach(i['id'],dict(name='one.txt',content='YQ=='));before=self.s.backup()
        for b,confirm in [(b'not a database','RESTORE'),(before,'no')]:
            with self.assertRaises(ValidationError):self.s.restore(b,confirm)
        tamper=Path(self.tmp.name)/'tamper.sqlite3';tamper.write_bytes(before);db=sqlite3.connect(tamper);db.execute("UPDATE attachments SET content=x'ff'");db.commit();db.close()
        with self.assertRaises(ValidationError):self.s.restore(tamper.read_bytes(),'RESTORE')
        tamper.write_bytes(before);db=sqlite3.connect(tamper);db.execute('CREATE VIEW malicious AS SELECT * FROM invoices');db.commit();db.close()
        with self.assertRaises(ValidationError):self.s.restore(tamper.read_bytes(),'RESTORE')
        self.assertEqual(self.s.invoice(i['id'])['number'],i['number'])
    def test_receipt_records_specific_payment_without_future_rewrite(self):
        i=self.issued();i=self.payment(i,'20',reference='BANK-REF-123');p=i['payments'][0]
        old=self.s.db.execute('SELECT html FROM payments WHERE id=?',(p['id'],)).fetchone()[0]
        self.assertIn('Remaining balance',old);self.assertIn('฿80.00',old)
        self.assertNotIn('BANK-REF-123',old)
        self.assertEqual(p['data']['reference'],'BANK-REF-123')
        self.payment(i,'80','two')
        self.assertEqual(self.s.db.execute('SELECT html FROM payments WHERE id=?',(p['id'],)).fetchone()[0],old)
    def test_restore_older_backup_never_reuses_issued_numbers(self):
        one=self.issued();backup=self.s.backup();two=self.issued()
        self.s.restore(backup,'RESTORE');three=self.issued()
        self.assertNotEqual(three['number'],two['number']);self.assertTrue(three['number'].endswith('0003'))
        d=self.draft()
        with self.assertRaises(ValidationError):self.s.issue(d['id'],two['number'],d['revision'])
    def test_restore_rejects_inconsistent_financial_records(self):
        self.issued();p=Path(self.tmp.name)/'bad-totals.sqlite3';p.write_bytes(self.s.backup())
        db=sqlite3.connect(p);db.execute('UPDATE invoices SET total_minor=1');db.commit();db.close()
        with self.assertRaises(ValidationError):self.s.restore(p.read_bytes(),'RESTORE')
    def test_contact_details_render_and_remain_snapshotted(self):
        company=self.s.save_party('companies',{'id':self.company['id'],'name':'Demo Company','code':'DEMO','email':'sales@example.test','contact_name':'Seller Person','contact_phone':'0123456789'})
        contact=self.s.save_party('contacts',{'id':self.contact['id'],'name':'Customer','email':'buyer@example.test','phone':'021111111','contact_name':'Buyer Person','contact_phone':'0987654321'})
        invoice=self.issued(company_id=company['id'],contact_id=contact['id'])
        html=self.s.db.execute('SELECT html FROM invoices WHERE id=?',(invoice['id'],)).fetchone()[0]
        for value in ('sales@example.test','buyer@example.test','Seller Person','Buyer Person','0123456789','0987654321'):self.assertIn(value,html)
        self.assertNotIn('021111111',html)
        self.assertEqual(html.count('0987654321'),1)
        self.assertIn('<span class="signature-person">Buyer Person</span>',html)
        self.assertIn('<span class="signature-party">Customer</span>',html)
        from documents import pretty_date
        self.assertIn('Date / วันที่: '+pretty_date(invoice['data']['issue_date']),html)
        paid=self.payment(invoice,'100')
        receipt=self.s.db.execute('SELECT html FROM payments WHERE id=?',(paid['payments'][0]['id'],)).fetchone()[0]
        for value in ('sales@example.test','buyer@example.test','Seller Person','Buyer Person','0123456789','0987654321'):self.assertIn(value,receipt)
        self.assertIn('<span class="signature-person">Seller Person</span>',receipt)
        self.assertNotIn('Date / วันที่: __________________',receipt)
        self.s.save_party('contacts',{'id':contact['id'],'name':'Customer','contact_name':'Changed'})
        self.assertEqual(self.s.db.execute('SELECT html FROM invoices WHERE id=?',(invoice['id'],)).fetchone()[0],html)

    def test_document_inline_details_are_validated_and_snapshotted(self):
        i=self.draft(contact_details={'name':'Invoice-specific customer','address':'New address'},company_details={'name':'Document trading name'})
        self.assertEqual(i['data']['contact']['name'],'Invoice-specific customer')
        self.assertEqual(self.s.party('contacts',self.contact['id'])['name'],'Customer / supplier')
        issued=self.s.issue(i['id'],expected_revision=i['revision'])
        self.assertEqual(issued['data']['company']['name'],'Document trading name')
        with self.assertRaises(ValidationError):self.draft(contact_details={'name':''})

if __name__=='__main__':unittest.main(verbosity=2)

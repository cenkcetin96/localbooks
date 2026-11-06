import base64,json,sqlite3,tempfile,unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Store,ValidationError
import reconciliation,compliance

class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(self.tmp.name)
        self.c=self.s.save_party('companies',dict(name='A',code='A'));self.other=self.s.save_party('companies',dict(name='B',code='B'))
        self.n=0
    def tearDown(self):self.s.db.close();self.tmp.cleanup()
    def payload(self,**extra):
        self.n+=1
        p=dict(company_id=self.c['id'],supplier='Vendor Co Ltd',supplier_tax_id='1234567890123',date='2026-10-01',document_number='INV-'+str(self.n),document_type='invoice',payment_status='unknown',currency='THB',amount='1000',name='invoice.pdf',content=base64.b64encode(b'%PDF-expense-'+str(self.n).encode()).decode(),duplicate_confirmed=True)
        p.update(extra);return p
    def expense(self,**extra):return self.s.save_journal(self.payload(**extra))['id']
    def bank(self,amount='1000',day='2026-10-02',**extra):
        self.n+=1;r=dict(date=day,direction='out',amount=amount,balance='',description='Vendor payment',row_key='row-'+str(self.n))
        body=dict(company_id=self.c['id'],account='1111222233',currency='THB',account_confirmed=True,name='statement.csv',content=base64.b64encode(b'Date,Amount\n'+str(self.n).encode()).decode(),rows=[r])
        body.update(extra)
        return self.s.import_bank(body)['ids'][0]
    def save_bank(self,id,**extra):
        b=next(b for b in self.s.bank_entries() if b['id']==id)
        p=dict(id=id,revision=b['revision'],date=b['date'],direction=b['direction'],amount=str(b['amount_minor']/100),balance=b['balance'],description=b['description'],category='expense',reviewed=True,cash_flow=False,matched_record=b.get('matched_record',''))
        p.update(extra);return self.s.save_bank_entry(p)
    def get(self,id):return next(e for e in self.s.journal() if e['id']==id)
    def test_unpaid_expense_links_partial_payments_on_different_dates(self):
        e=self.expense();b1=self.bank('400','2026-10-03');b2=self.bank('600','2026-10-20')
        self.save_bank(b1,matched_record='expense:'+str(e));self.save_bank(b2,matched_record='expense:'+str(e))
        row=self.get(e);self.assertEqual(row['linked_cash_minor'],100000);self.assertEqual(row['unmatched_cash_minor'],0)
        self.assertEqual(len(row['linked_banks']),2)
        self.assertTrue(all(b['linked_expense_id']==e for b in self.s.bank_entries()))
        b3=self.bank('1','2026-10-21')
        with self.assertRaises(ValidationError):self.save_bank(b3,matched_record='expense:'+str(e))
        self.assertEqual(self.get(e)['bank_total_minor'],100000)
    def test_manual_cash_is_replaced_not_added_and_unreviewed_links_wait(self):
        e=self.expense(payment_status='paid',payment_date='2026-10-02',paid_amount='1000')
        b1=self.bank('400','2026-10-03');b2=self.bank('600','2026-10-20')
        self.save_bank(b1,matched_record='expense:'+str(e));self.save_bank(b2,matched_record='expense:'+str(e),reviewed=False)
        self.assertEqual(self.get(e)['unmatched_cash_minor'],60000)
        self.save_bank(b2,matched_record='expense:'+str(e));self.assertEqual(self.get(e)['unmatched_cash_minor'],0)
    def test_link_rejects_other_company_currency_incoming_and_overpayment(self):
        e=self.expense();b=self.bank(company_id=self.other['id'])
        with self.assertRaises(ValidationError):self.save_bank(b,matched_record='expense:'+str(e))
        b=self.bank('1001')
        with self.assertRaises(ValidationError):self.save_bank(b,matched_record='expense:'+str(e))
        b=self.bank('100')
        with self.assertRaises(ValidationError):self.save_bank(b,matched_record='expense:'+str(e),direction='in')
        b=self.bank('100',currency='USD')
        with self.assertRaises(ValidationError):self.save_bank(b,matched_record='expense:'+str(e))
    def test_duplicate_warning_precedes_supplier_mutation_and_attachments_do_not_create_expenses(self):
        first=self.payload();first['duplicate_confirmed']=False;id=self.s.save_journal(first)['id']
        another=self.payload();another.update(document_number=first['document_number'],duplicate_confirmed=False)
        with self.assertRaisesRegex(ValidationError,'similar expense'):self.s.save_journal(another)
        self.assertEqual(len(self.s.journal()),1);self.assertEqual(len(self.s.contacts('supplier')),1)
        self.s.attach_journal_document(dict(id=id,company_id=self.c['id'],name='receipt.pdf',content=another['content']))
        self.assertEqual(len(self.get(id)['documents']),2);self.assertEqual(len(self.s.journal()),1)
        self.s.attach_journal_document(dict(id=id,name='receipt.pdf',content=another['content']));self.assertEqual(len(self.get(id)['documents']),2)
        another.update(document_number='DIFFERENT',date='2020-01-01',duplicate_confirmed=True)
        with self.assertRaisesRegex(ValidationError,'already attached'):self.s.save_journal(another)
    def test_shared_documents_work_from_either_side(self):
        e=self.expense();b=self.bank();self.s.attach_bank_document(dict(id=b,name='transfer.pdf',content=base64.b64encode(b'%PDF-transfer').decode()))
        self.save_bank(b,matched_record='expense:'+str(e))
        self.s.attach_journal_document(dict(id=e,name='receipt.pdf',content=base64.b64encode(b'%PDF-receipt').decode()))
        bank=next(x for x in self.s.bank_entries() if x['id']==b)
        self.assertEqual({d['name'] for d in bank['shared_documents']},{'invoice.pdf','receipt.pdf','transfer.pdf'})
        self.assertEqual({d['name'] for d in self.get(e)['documents']},{'invoice.pdf','receipt.pdf','transfer.pdf'})
        self.assertEqual(len(bank['documents']),1,'Own attachment metadata remains export-compatible')
    def test_duplicate_documents_keep_one_expense_and_move_bank_links(self):
        primary=self.expense(payment_status='paid',payment_date='2026-10-02',paid_amount='1000')
        duplicate=self.expense();b1=self.bank('400','2026-10-03');b2=self.bank('600','2026-10-04')
        self.save_bank(b1,matched_record='expense:'+str(primary));self.save_bank(b2,matched_record='expense:'+str(duplicate))
        original_files=[tuple(r) for r in self.s.db.execute('SELECT id,content,sha256 FROM journal_entries')]
        self.s.mark_expense_duplicate(dict(id=duplicate,target_id=primary))
        self.assertEqual(self.get(duplicate)['duplicate_of'],primary)
        self.assertEqual(len(self.get(primary)['linked_banks']),2);self.assertEqual(len(self.get(primary)['documents']),2)
        self.assertEqual(self.get(primary)['unmatched_cash_minor'],0)
        self.assertEqual([tuple(r) for r in self.s.db.execute('SELECT id,content,sha256 FROM journal_entries')],original_files)
        self.s.restore(self.s.backup(),'RESTORE')
        self.s.mark_expense_duplicate(dict(id=duplicate,restore=True));self.assertNotIn('duplicate_of',self.get(duplicate))
        self.assertEqual(len(self.get(primary)['linked_banks']),2,'Restoring a separate expense does not silently move bank evidence')
    def test_paid_record_must_be_primary_and_cycles_cross_company_forbidden(self):
        unpaid=self.expense();paid=self.expense(payment_status='paid',payment_date='2026-10-02',paid_amount='1000')
        with self.assertRaises(ValidationError):self.s.mark_expense_duplicate(dict(id=paid,target_id=unpaid))
        with self.assertRaises(ValidationError):self.s.mark_expense_duplicate(dict(id=paid,target_id=paid))
        other=self.expense(company_id=self.other['id'])
        with self.assertRaises(ValidationError):self.s.mark_expense_duplicate(dict(id=unpaid,target_id=other))
    def test_cash_collision_is_blocked_in_both_creation_orders(self):
        e=self.expense(payment_status='paid',payment_date='2026-10-02',paid_amount='1000');b=self.bank()
        with self.assertRaisesRegex(ValidationError,'already records'):self.save_bank(b,cash_flow=True)
        self.save_bank(b,cash_flow=True,separate_payment_confirmed=True)
        new=self.payload();new.update(payment_status='paid',payment_date='2026-10-02',paid_amount='1000')
        with self.assertRaisesRegex(ValidationError,'already records'):self.s.save_journal(new)
        new['link_bank_id']=b;new['duplicate_confirmed']=True
        linked=self.s.save_journal(new)['id'];self.assertEqual(self.get(linked)['unmatched_cash_minor'],0)
        self.assertEqual(next(x for x in self.s.bank_entries() if x['id']==b)['linked_expense_id'],linked)
    def test_linked_expense_edits_preserve_cash_limits_and_company(self):
        p=self.payload();id=self.s.save_journal(p)['id'];b=self.bank('600');self.save_bank(b,matched_record='expense:'+str(id))
        p.update(id=id,amount='500')
        with self.assertRaises(ValidationError):self.s.save_journal(p)
        p.update(amount='1000',company_id=self.other['id'])
        with self.assertRaises(ValidationError):self.s.save_journal(p)
    def test_primary_edits_cannot_break_duplicate_identity(self):
        p=self.payload();primary=self.s.save_journal(p)['id'];duplicate=self.expense()
        self.s.mark_expense_duplicate(dict(id=duplicate,target_id=primary))
        for changed in [dict(amount='999'),dict(company_id=self.other['id']),dict(currency='USD'),dict(supplier='Other supplier',supplier_tax_id='4564564564564')]:
            edit={**p,'id':primary,**changed}
            with self.assertRaisesRegex(ValidationError,'Restore duplicate'):self.s.save_journal(edit)
        self.s.restore(self.s.backup(),'RESTORE')
    def review(self,bank,**extra):
        b=next(b for b in self.s.bank_entries() if b['id']==bank)
        payload=dict(bank_id=bank,revision=b['revision'],request_key='request-'+str(self.n)+'-1234567890',file=dict(name='review.pdf',content=base64.b64encode(b'%PDF-reviewed-'+str(self.n).encode()).decode()),document=dict(supplier='Vendor Co Ltd',supplier_tax_id='1234567890123',date='2026-10-01',document_number='DOC-'+str(self.n),amount='1000',currency='THB',document_type='invoice'),action='evidence',category='expense',counterparty='Vendor Co Ltd',reviewed=True)
        payload.update(extra);return payload
    def test_attachment_create_is_atomic_retry_safe_and_counts_one_payment(self):
        bank=self.bank('400');p=self.review(bank,action='create_expense')
        r=self.s.review_attachment(p);self.assertEqual(self.s.review_attachment(p),r)
        self.assertEqual(len(self.s.journal()),1);self.assertEqual(len(self.s.contacts('supplier')),1)
        e=self.get(r['expense_id']);self.assertEqual(e['linked_cash_minor'],40000);self.assertEqual(e['unmatched_cash_minor'],0)
        self.assertEqual(len(e['documents']),1);self.assertEqual(e['documents'][0]['review']['document_number'],p['document']['document_number'])
        b=next(b for b in self.s.bank_entries() if b['id']==bank);self.assertFalse(b['cash_flow']);self.assertEqual(b['revision'],2)
        p['counterparty']='Different'
        with self.assertRaisesRegex(ValidationError,'different details'):self.s.review_attachment(p)
    def test_attachment_failed_link_rolls_back_expense_supplier_and_file(self):
        bank=self.bank('1100');p=self.review(bank,action='create_expense')
        with self.assertRaises(ValidationError):self.s.review_attachment(p)
        self.assertEqual(len(self.s.journal()),0);self.assertEqual(len(self.s.contacts('supplier')),0)
        b=self.s.bank_entries()[0];self.assertEqual(b['revision'],1);self.assertEqual(len(b['documents']),0)
        self.assertFalse(self.s.db.execute("SELECT 1 FROM metadata WHERE key LIKE 'attachment_request:%'").fetchone())
    def test_attachment_links_existing_and_rejects_exact_duplicate_creation(self):
        e=self.expense(document_number='DOC-EXISTING');bank=self.bank('400');p=self.review(bank,action='create_expense')
        p['document']['document_number']='DOC-EXISTING';p['duplicate_confirmed']=True
        with self.assertRaisesRegex(ValidationError,'already belongs'):self.s.review_attachment(p)
        p.update(action='link',matched_record='expense:'+str(e));self.s.review_attachment(p)
        self.assertEqual(len(self.s.journal()),1);self.assertEqual(len(self.get(e)['documents']),2)
    def test_attachment_incoming_is_evidence_and_never_creates_invoice(self):
        bank=self.bank();p=self.review(bank,action='create_expense')
        self.save_bank(bank,direction='in',category='income',reviewed=False)
        p['revision']=2
        with self.assertRaisesRegex(ValidationError,'Only money out'):self.s.review_attachment(p)
        p.update(action='evidence',category='income',cash_flow=True);self.s.review_attachment(p)
        self.assertEqual(len(self.s.journal()),0);self.assertEqual(len(self.s.state()['invoices']),0)
        b=self.s.bank_entries()[0];self.assertTrue(b['cash_flow']);self.assertTrue(b['reviewed']);self.assertEqual(len(b['documents']),1)
        self.assertEqual(b['documents'][0]['review']['supplier'],'Vendor Co Ltd')
    def test_attachment_can_link_incoming_to_existing_receipt_without_second_payment(self):
        self.s.pdf_renderer=lambda html:b'%PDF-TEST'
        customer=self.s.save_party('contacts',dict(name='Customer'))
        draft=self.s.save_draft(dict(company_id=self.c['id'],contact_id=customer['id'],kind='sale',currency='THB',issue_date='2026-10-01',lines=[dict(description='Service',quantity='1',unit_price='1000')]))
        inv=self.s.issue(draft['id'],expected_revision=draft['revision'])
        inv=self.s.add_payment(inv['id'],dict(amount='1000',method='Bank transfer',date='2026-10-02',request_key='receipt-test'))
        bank=self.bank();self.save_bank(bank,direction='in',category='income',reviewed=False)
        p=self.review(bank,action='link',matched_record='payment:'+str(inv['payments'][0]['id']),category='income')
        self.s.review_attachment(p);self.s.review_attachment(p)
        state=self.s.state();self.assertEqual(len(state['invoices']),1);self.assertEqual(len(state['invoices'][0]['payments']),1)
        self.assertFalse(state['bank_entries'][0]['cash_flow']);self.assertEqual(len(state['bank_entries'][0]['documents']),1)
    def test_attachment_wrong_document_company_is_blocked_before_classification(self):
        from unittest.mock import patch
        bank=self.bank();p=self.review(bank,category='unclassified',reviewed=True)
        with patch('app.scan_document',return_value={'suggested_company_id':self.other['id']}):
            with self.assertRaisesRegex(ValidationError,'document belongs to B, not A'):self.s.review_attachment(p)
        self.assertEqual(len(self.s.journal()),0);self.assertEqual(self.s.bank_entries()[0]['revision'],1)
        self.assertEqual(self.s.bank_entries()[0]['documents'],[])
        p.update(reviewed=False)
        with patch('app.scan_document',return_value={'suggested_company_id':self.other['id']}):
            with self.assertRaisesRegex(ValidationError,'document belongs to B'):self.s.review_attachment(p)
    def test_attachment_company_uses_server_scan_not_edited_fields(self):
        import hashlib
        bank=self.bank();p=self.review(bank)
        digest=hashlib.sha256(base64.b64decode(p['file']['content'])).hexdigest()
        self.s.document_scans[digest]={'suggested_company_id':self.other['id']}
        p['document']['company_id']=self.c['id'];p['document']['supplier']='Changed text'
        with self.assertRaisesRegex(ValidationError,'document belongs to B'):self.s.review_attachment(p)
        self.assertEqual(self.s.bank_entries()[0]['documents'],[])
    def test_unrelated_attachment_saves_separately_for_detected_company_without_bank_changes(self):
        import hashlib
        bank=self.bank();p=self.review(bank,action='separate_expense',company_id=self.other['id'])
        digest=hashlib.sha256(base64.b64decode(p['file']['content'])).hexdigest()
        self.s.document_scans[digest]={'suggested_company_id':self.other['id']}
        before=tuple(self.s.db.execute('SELECT * FROM bank_entries WHERE id=?',(bank,)).fetchone())
        result=self.s.review_attachment(p);self.assertTrue(result['separate']);self.assertIsNone(result['bank_id']);self.assertEqual(result['matched_record'],'')
        e=self.get(result['expense_id']);self.assertEqual(e['company_id'],self.other['id']);self.assertEqual(e['linked_banks'],[]);self.assertEqual(e['payment_status'],'unknown')
        self.assertEqual(e['documents'][0]['review']['document_number'],p['document']['document_number'])
        self.assertEqual(tuple(self.s.db.execute('SELECT * FROM bank_entries WHERE id=?',(bank,)).fetchone()),before)
        self.assertEqual(self.s.review_attachment(p),result);self.assertEqual(len(self.s.journal()),1)
        p.update(request_key='different-request-123456',existing_expense_id=e['id'])
        result=self.s.review_attachment(p);self.assertTrue(result['reused']);self.assertEqual(len(self.s.journal()),1);self.assertEqual(len(self.get(e['id'])['documents']),1)
    def test_separate_expense_wrong_company_or_existing_record_is_rejected_atomically(self):
        import hashlib
        bank=self.bank();p=self.review(bank,action='separate_expense',company_id=self.c['id'])
        digest=hashlib.sha256(base64.b64decode(p['file']['content'])).hexdigest();self.s.document_scans[digest]={'suggested_company_id':self.other['id']}
        with self.assertRaisesRegex(ValidationError,'document belongs to B'):self.s.review_attachment(p)
        p['company_id']=self.other['id'];p['existing_expense_id']=999
        with self.assertRaisesRegex(ValidationError,'suggested existing'):self.s.review_attachment(p)
        self.assertEqual(len(self.s.journal()),0);self.assertEqual(len(self.s.contacts('supplier')),0)
    def test_separate_expense_exact_duplicate_must_be_reused_even_after_bank_changes(self):
        e=self.expense(document_number='EXISTING');bank=self.bank();p=self.review(bank,action='separate_expense',company_id=self.c['id'])
        p['document']['document_number']='EXISTING';p['duplicate_confirmed']=True
        with self.assertRaisesRegex(ValidationError,'already saved'):self.s.review_attachment(p)
        self.save_bank(bank,category='capital',reviewed=False)
        p['existing_expense_id']=e
        self.s.review_attachment(p)
        self.assertEqual(len(self.s.journal()),1);self.assertEqual(len(self.get(e)['documents']),2)
        self.assertEqual(self.s.bank_entries()[0]['revision'],2);self.assertEqual(self.s.bank_entries()[0]['matched_record'],'')
    def test_attachment_stale_revision_cross_company_and_invalid_file_leave_no_changes(self):
        e=self.expense(company_id=self.other['id']);bank=self.bank();p=self.review(bank,action='link',matched_record='expense:'+str(e))
        with self.assertRaises(ValidationError):self.s.review_attachment(p)
        p.update(action='evidence',revision=99)
        with self.assertRaisesRegex(ValidationError,'changed'):self.s.review_attachment(p)
        p.update(revision=1,file=dict(name='bad.pdf',content=base64.b64encode(b'not a document').decode()))
        with self.assertRaises(ValidationError):self.s.review_attachment(p)
        self.assertEqual(self.s.bank_entries()[0]['revision'],1)
    def test_attachment_expense_only_saves_details_and_original_once(self):
        e=self.expense();p=self.review(self.bank());p.pop('bank_id');p['expense_id']=e
        self.s.review_attachment(p);self.s.review_attachment(p)
        self.assertEqual(len(self.s.journal()),1);self.assertEqual(len(self.get(e)['documents']),2)
        self.s.restore(self.s.backup(),'RESTORE');self.assertEqual(len(self.get(e)['documents']),2)
        import io,zipfile
        with zipfile.ZipFile(io.BytesIO(compliance.export_pack(self.s,self.c['id'],2026))) as pack:
            text=pack.read('document-reviews.csv').decode('utf-8-sig')
            self.assertIn(p['document']['document_number'],text)

    def test_backup_rejects_dangling_links_and_support_tampering(self):
        e=self.expense();b=self.bank();self.save_bank(b,matched_record='expense:'+str(e))
        self.s.attach_journal_document(dict(id=e,name='proof.pdf',content=base64.b64encode(b'%PDF-proof').decode()))
        path=Path(self.tmp.name)/'tamper.sqlite3';path.write_bytes(self.s.backup());db=sqlite3.connect(path)
        db.execute("UPDATE bank_entries SET data=json_set(data,'$.matched_record','expense:99999')");db.commit();db.close()
        with self.assertRaises(ValidationError):self.s.restore(path.read_bytes(),'RESTORE')
        path.write_bytes(self.s.backup());db=sqlite3.connect(path);db.execute("UPDATE journal_attachments SET content=x'1234'");db.commit();db.close()
        with self.assertRaises(ValidationError):self.s.restore(path.read_bytes(),'RESTORE')
    def test_old_backup_and_accountant_export_include_expense_evidence(self):
        e=self.expense();self.s.attach_journal_document(dict(id=e,name='receipt.pdf',content=base64.b64encode(b'%PDF-receipt').decode()))
        import io,zipfile
        pack=compliance.export_pack(self.s,self.c['id'],2026)
        with zipfile.ZipFile(io.BytesIO(pack)) as z:self.assertTrue(any(n.startswith('expense-documents/') for n in z.namelist()))
        path=Path(self.tmp.name)/'old.sqlite3';path.write_bytes(self.s.backup());db=sqlite3.connect(path);db.execute('DROP TABLE journal_attachments');db.commit();db.close()
        self.s.restore(path.read_bytes(),'RESTORE');self.assertEqual(len(self.s.journal()),1)

if __name__=='__main__':unittest.main()

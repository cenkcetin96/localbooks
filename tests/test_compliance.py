import base64
import datetime as dt
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Store, ValidationError
import compliance as c


class ComplianceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(self.tmp.name, pdf_renderer=lambda h:b'%PDF-test')
        self.trading = self.s.save_party('companies', dict(name='Example Trading',code='EXAMPLE'))
        self.services = self.s.save_party('companies', dict(name='Example Services',code='CB',vat_registered=True))
        self.customer = self.s.save_party('contacts', dict(name='Customer'))

    def tearDown(self):
        self.s.db.close();self.tmp.cleanup()

    def settings(self, company=None, **extra):
        company = company or self.services
        return c.save_profile(self.s, dict(c.profile(self.s,company['id']), **extra))

    def work(self, company=None, year=2026):
        return c.workspace(self.s, (company or self.services)['id'], year)

    def filing(self, key='pp30:2026-01', **extra):
        record = next(r for r in self.work()['items'] if r['key']==key)
        return c.save_record(self.s, dict(record, company_id=self.services['id'], year=2026, **extra))

    def calc(self, kind, **extra):
        return c.save_record(self.s, {**dict(company_id=self.services['id'],year=2026,kind=kind,notes='Reviewed against source report',revision=0),**extra})

    def test_company_defaults_and_unknown_year_end(self):
        f = self.work(self.trading,2024);b = self.work(year=2025)
        self.assertEqual(f['profile']['start_year'],dt.date.today().year)
        self.assertEqual(b['profile']['start_year'],dt.date.today().year)
        self.assertFalse(f['profile']['vat_registered']);self.assertEqual(f['profile']['employees'],0)
        self.assertTrue(b['profile']['vat_registered']);self.assertEqual(b['profile']['employees'],0)
        self.assertFalse(any(r['key'].startswith('pp30:') for r in f['items']))
        self.assertFalse(any(r['key'].startswith('pnd1:') for r in b['items']))
        self.assertTrue(all(r['status']=='unknown' for r in b['items']))
        self.assertFalse(next(r for r in b['items'] if r['key']=='pnd50')['base_due'])

    def test_year_end_editable_confirmed_period_and_baseline_dates(self):
        self.settings(year_end='12-31')
        work=self.work();self.assertTrue(work['window']['provisional'])
        tasks={r['key']:r for r in work['items']}
        self.assertEqual(tasks['pnd51']['base_due'],'2026-08-31')
        self.assertEqual(tasks['pnd50']['base_due'],'2027-05-30')
        self.assertEqual(tasks['agm']['base_due'],'2027-04-30')
        self.assertEqual(tasks['dbd']['base_due'],'')
        self.settings(year_end='03-31',year_end_confirmed=True)
        work=self.work();self.assertEqual(work['window']['start'],'2025-04-01')
        self.assertEqual(work['window']['end'],'2026-03-31');self.assertFalse(work['window']['provisional'])
        tasks={r['key']:r for r in work['items']}
        self.assertEqual(tasks['pnd51']['base_due'],'2025-11-30')
        # Confirmation is not a lock.
        self.settings(year_end='06-30',year_end_confirmed=False)
        self.assertTrue(self.work()['window']['provisional'])
        with self.assertRaises(ValueError):self.settings(year_end='02-29')
        with self.assertRaises(ValueError):self.settings(year_end='',year_end_confirmed=True)

    def test_filing_status_evidence_revisions_and_backup(self):
        with self.assertRaises(ValueError):self.filing(status='filed')
        saved=self.filing(status='filed',filed_on='2026-02-01',reference='RD-123',notes='Confirmed existing filing')
        old_revision=saved['revision']
        self.assertTrue(any(r['source']=='filing' for r in self.work()['missing']))
        c.attach(self.s,dict(record_id=saved['id'],category='filing_acceptance',name='acceptance.pdf',content=base64.b64encode(b'%PDF-1.4 acceptance').decode()))
        self.assertFalse(any(r['source']=='filing' for r in self.work()['missing']))
        updated=self.filing(status='paid',filed_on='2026-02-01',paid_on='2026-02-01',reference='RD-123')
        self.assertTrue(any('Payment receipt' in r['reason'] for r in self.work()['missing']))
        with self.assertRaises(ValueError):c.save_record(self.s,dict(updated,company_id=self.services['id'],year=2026,revision=old_revision))
        self.s.restore(self.s.backup(),'RESTORE')
        r=next(r for r in self.work()['items'] if r['key']==saved['key'])
        self.assertEqual(r['status'],'paid');self.assertEqual(len(r['files']),1)
        self.settings(vat_registered=False)
        self.assertTrue(any(r['key']==saved['key'] for r in self.work()['items']))

    def test_vat_wht_cit_decimal_calculations_and_review(self):
        vat=self.calc('vat',key='vat:2026-01',month='2026-01',output_vat='700.01',eligible_input_vat='100.00',carry_forward='200.00')
        self.assertEqual(vat['payable'],40001);self.assertEqual(vat['credit'],0)
        vat2=self.calc('vat',key='vat:2026-02',month='2026-02',output_vat='0',eligible_input_vat='1.01',carry_forward='0')
        self.assertEqual(vat2['payable'],0);self.assertEqual(vat2['credit'],101)
        wht=self.calc('wht',payment_date='2026-01-01',payee='Supplier',tax_id='1234567890123',income_category='Service confirmed',source='INV 12 / bank 2',form='pnd53',tax_base='0.50',rate='3',reviewed=True)
        self.assertEqual(wht['withheld'],2);self.assertTrue(wht['reviewed'])
        half=self.calc('cit',key='cit:half_year',stage='half_year',income='1000',expenses='1500',addbacks='600',deductions='50',losses='25',annual_tax='100.01',wht_credits='0.01',half_year_paid='999')
        self.assertEqual(half['adjusted_profit'],2500)
        self.assertEqual(half['tax_before_credits'],5001);self.assertEqual(half['payable'],5000)
        annual=self.calc('cit',key='cit:annual',stage='annual',annual_tax='100',wht_credits='30',half_year_paid='50')
        self.assertEqual(annual['payable'],2000)
        self.s.restore(self.s.backup(),'RESTORE')
        self.assertEqual(len([r for r in self.work()['items'] if r['kind'] in ('vat','wht','cit')]),5)
        for invalid in ('NaN','Infinity','-1','0.001'):
            with self.assertRaises(ValueError):c.money(invalid)
        with self.assertRaises(ValueError):self.calc('vat',key='vat:2025-01',month='2025-01')
        with self.assertRaises(ValueError):self.calc('vat',key='vat:2026-03',month='2026-03',notes='')

    def test_document_requests_and_company_year_isolation(self):
        req=c.save_record(self.s,dict(company_id=self.trading['id'],year=2024,kind='document',title='Missing opening statement',period='2024-01',status='requested',owner='Accountant'))
        self.assertEqual(len(self.work(self.trading,2024)['missing']),1)
        self.assertEqual(len(self.work(self.trading,2025)['missing']),0)
        self.assertEqual(len(self.work()['missing']),0)
        c.save_record(self.s,dict(req,company_id=self.trading['id'],year=2024,status='reviewed'))
        self.assertEqual(len(self.work(self.trading,2024)['missing']),0)
        with self.assertRaises(ValueError):c.save_record(self.s,dict(req,company_id=self.trading['id'],year=2024,status='not_applicable',notes=''))

    def test_filing_working_totals_exclusions_and_changed_year_end(self):
        self.filing('pnd50',verified_due='2027-05-31',owner='Accountant')
        one=self.calc('wht',payment_date='2026-11-01',payee='Supplier',tax_id='1234567890123',income_category='Service',source='INV1',form='pnd53',tax_base='100',rate='3')
        two=self.calc('wht',payment_date='2026-11-02',payee='Supplier',tax_id='1234567890123',income_category='Service',source='INV2',form='pnd53',tax_base='200',rate='3',reviewed=True)
        task=lambda:next(r for r in self.work()['items'] if r['key']=='pnd53:2026-11')
        self.assertEqual(task()['working_amount_minor'],900);self.assertFalse(task()['working_reviewed'])
        # Stored money is resubmitted as display amounts, exactly as the editor does.
        c.save_record(self.s,dict(one,company_id=self.services['id'],year=2026,tax_base='100.00',excluded=True))
        self.assertEqual(task()['working_amount_minor'],600);self.assertTrue(task()['working_reviewed'])
        self.settings(year_end='03-31')
        self.assertTrue(any(r['source']=='worksheet' for r in self.work()['missing']))
        annual=next(r for r in self.work()['items'] if r['key']=='pnd50')
        self.assertTrue(annual['period_review_required'])
        self.assertTrue(any(r['source']=='period' for r in self.work()['missing']))
        self.s.restore(self.s.backup(),'RESTORE')
        self.filing('pnd50',verified_due='2026-08-28',owner='Accountant')
        self.assertFalse(next(r for r in self.work()['items'] if r['key']=='pnd50').get('period_review_required'))

    def test_export_contains_originals_money_units_and_no_other_company(self):
        for company,name in [(self.services,'In scope'),(self.trading,'PRIVATE OTHER COMPANY')]:
            self.s.save_journal(dict(company_id=company['id'],supplier=name,date='2026-01-02',document_type='invoice',payment_status='unknown',currency='THB',amount='107.00',name='../../supplier.pdf',content=base64.b64encode(b'%PDF-1.4 '+name.encode()).decode()))
        r=self.calc('vat',key='vat:2026-01',month='2026-01',output_vat='7',eligible_input_vat='0',carry_forward='0')
        c.attach(self.s,dict(record_id=r['id'],category='supporting',name='../source.pdf',content=base64.b64encode(b'%PDF-1.4 source').decode()))
        req=c.save_record(self.s,dict(company_id=self.services['id'],year=2026,kind='document',title='=FORMULA()',period='2026',status='requested'))
        raw=c.export_pack(self.s,self.services['id'],2026)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            self.assertTrue({'README.txt','records.json','manifest.csv','summary.html','missing-documents.csv','calculation-worksheets.csv'}<=set(z.namelist()))
            self.assertTrue(any(p.startswith('expenses/') for p in z.namelist()))
            self.assertTrue(any(p.startswith('compliance/') for p in z.namelist()))
            self.assertTrue(all('..' not in p.split('/') and not p.startswith('/') for p in z.namelist()))
            self.assertNotIn(b'PRIVATE OTHER COMPANY',z.read('records.json'))
            self.assertIn(b"'=FORMULA()",z.read('missing-documents.csv'))
            self.assertIn(b'7.00,THB',z.read('calculation-worksheets.csv'))
            data=json.loads(z.read('records.json'));self.assertEqual(len(data['records']['journal']),1)
        # Provisional year boundaries do not silently move saved records.
        self.settings(year_end='03-31')
        self.assertTrue(any(x['key']==req['key'] for x in self.work()['items']))

    def test_old_backup_compatibility_and_tampered_calculation_rejected(self):
        self.calc('vat',key='vat:2026-01',month='2026-01',output_vat='7',eligible_input_vat='0',carry_forward='0')
        path=Path(self.tmp.name)/'tampered.sqlite3';path.write_bytes(self.s.backup())
        db=sqlite3.connect(path);db.execute("UPDATE compliance_records SET data=json_set(data,'$.payable',999999)");db.commit();db.close()
        with self.assertRaises(ValidationError):self.s.restore(path.read_bytes(),'RESTORE')
        path.write_bytes(self.s.backup());db=sqlite3.connect(path)
        for table in ('compliance_files','compliance_records','compliance_profiles'):db.execute('DROP TABLE '+table)
        db.commit();db.close()
        self.s.restore(path.read_bytes(),'RESTORE')
        self.assertFalse(any(r['kind']=='vat' for r in self.work()['items']))

    def test_missing_bank_support_and_withholding_certificate_checks(self):
        self.s.import_bank(dict(company_id=self.services['id'],account='1234567890',currency='THB',account_confirmed=True,name='statement.csv',content=base64.b64encode(b'date,amount\n').decode(),rows=[dict(row_key='1',date='2026-01-02',direction='out',amount='100',description='Supplier payment')]))
        missing=self.work()['missing'];self.assertTrue(any(r['source']=='bank' and 'Supporting document missing' in r['reason'] for r in missing))
        inv=self.s.save_draft(dict(company_id=self.services['id'],contact_id=self.customer['id'],kind='sale',currency='THB',issue_date='2026-01-01',lines=[dict(description='Service',quantity='1',unit_price='100')]))
        inv=self.s.issue(inv['id'],expected_revision=inv['revision'])
        inv=self.s.add_payment(inv['id'],dict(amount='97',withholding='3',method='Bank',date='2026-01-02',request_key='one',confirmed=True))
        pay=inv['payments'][0]
        self.assertTrue(any(r['source']=='payment' for r in self.work()['missing']))
        self.s.attach(inv['id'],dict(name='wht.pdf',category='withholding_certificate',payment_id=pay['id'],content=base64.b64encode(b'%PDF-1.4 wht').decode()))
        self.assertFalse(any(r['source']=='payment' for r in self.work()['missing']))


if __name__=='__main__':unittest.main()

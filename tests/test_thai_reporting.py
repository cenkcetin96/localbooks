import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import accounting as a
import compliance as c
import thai_reporting as t
from app import Store,ValidationError


class ThaiReportingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(self.tmp.name,pdf_renderer=lambda html:b'%PDF-test')
        self.company=self.s.save_party('companies',dict(name='Company A',code='A',vat_registered=True))
        self.other=self.s.save_party('companies',dict(name='Company B',code='B'))
        self.contact=self.s.save_party('contacts',dict(name='Customer'))

    def tearDown(self):self.s.db.close();self.tmp.cleanup()

    def profile(self,**extra):
        body=dict(t.profile(self.s,self.company['id']),framework='tfrs_npae',confirmed=True,reviewed_by='Accountant')
        body.update(extra);return t.save_profile(self.s,body)

    def row(self,code,company=None):return next(r for r in t.workspace(self.s,company or self.company['id'])['accounts'] if r['code']==code)

    def mapping(self,code,**extra):
        r=self.row(code);m=dict(r['mapping'],reviewed=True,reviewed_by='Accountant',note='Reviewed against company records and official DBD lines')
        m.update(extra)
        return t.save_mapping(self.s,dict(company_id=self.company['id'],code=code,revision=r['revision'],profile_revision=t.profile(self.s,self.company['id'])['revision'],mapping=m))

    def invoice(self,currency='THB',day='2026-10-01'):
        inv=self.s.save_draft(dict(company_id=self.company['id'],contact_id=self.contact['id'],kind='sale',currency=currency,issue_date=day,vat_rate='7' if currency=='THB' else '',lines=[dict(description='Goods',quantity='1',unit_price='100')]))
        return self.s.issue(inv['id'],expected_revision=inv['revision'])

    def entry(self,inv,lines=None):
        work=a.workspace(self.s,'invoice',inv['id'],self.company['id'])
        body=dict(work['entry'],company_id=self.company['id'],source_kind='invoice',source_id=inv['id'],revision=work['revision'])
        if lines:body.update(mode='manual',lines=lines)
        return a.save_entry(self.s,body)

    def test_all_39_starters_have_explicit_mapping_decisions_without_writes(self):
        self.assertEqual(set(t.DEFAULTS),{code for code,_,_ in a.DEFAULTS})
        work=t.workspace(self.s,self.company['id']);self.assertEqual(len(work['accounts']),39)
        self.assertFalse(work['profile']['confirmed']);self.assertEqual(work['counts']['reviewed'],0)
        self.assertEqual(work['counts']['unmapped'],4)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM accounting_accounts').fetchone()[0],0)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM thai_reporting_profiles').fetchone()[0],0)

    def test_verified_dbd_lines_and_conditional_return_references(self):
        for code in ['2101000','2201000','2301000','2302000','2303000','2341000']:
            self.assertEqual(self.row(code)['mapping']['category'],'bs.3.2')
        for code in ['1201000','1202000','1203000','1401000']:
            self.assertEqual(self.row(code)['mapping']['category'],'bs.1.3')
        self.assertEqual(self.row('2304000')['mapping']['category'],'bs.3.7')
        self.assertEqual(self.row('5501000')['mapping']['treatment'],'addback')
        self.assertIn('pnd2',self.row('5401000')['mapping']['tax_forms'])
        self.assertNotIn('pnd3',self.row('1202000')['mapping']['tax_forms'])
        self.assertEqual(self.row('1102000')['mapping']['tax_forms'],[])
        self.assertEqual(self.row('2303000')['mapping']['tax_forms'],['sso110'])

    def test_unconfirmed_basis_cannot_be_bypassed_when_reviewing(self):
        with self.assertRaisesRegex(ValueError,'Confirm the company'):self.mapping('4001000')
        raw=self.row('4001000')['mapping'];m=t.mapping_data(raw)
        m.update(reviewed=True,reviewed_by='Accountant',note='Evidence',reviewed_on='2026-11-17')
        with self.assertRaisesRegex(ValueError,'current confirmed'):a.save_account(self.s,dict(company_id=self.company['id'],code='4001000',name='Sales',type='income',active=True,revision=0,thai_reporting=m))
        with self.assertRaises(ValueError):self.profile(framework='other')
        with self.assertRaises(ValueError):t.profile(self.s,True)

    def test_review_company_isolation_and_account_name_change(self):
        self.profile();self.mapping('4001000')
        self.assertEqual(self.row('4001000')['mapping']['status'],'reviewed')
        self.assertEqual(self.row('4001000',self.other['id'])['mapping']['status'],'suggested')
        r=next(r for r in a.accounts(self.s,self.company['id']) if r['code']=='4001000')
        a.save_account(self.s,dict(company_id=self.company['id'],code=r['code'],name='Other business meaning',type='income',active=True,revision=r['revision']))
        self.assertFalse(self.row('4001000')['mapping']['usable'])
        self.assertEqual(self.row('4001000')['mapping']['status'],'needs_review')
        self.assertIn('thai_reporting',next(r for r in a.accounts(self.s,self.company['id']) if r['code']=='4001000'))

    def test_basis_changes_and_nature_cost_classification(self):
        self.profile();self.mapping('4001000');self.profile(presentation='nature')
        self.assertEqual(self.row('4001000')['mapping']['status'],'needs_review')
        self.assertFalse(self.row('4001000')['mapping']['compatible'])
        self.assertEqual(self.row('5101000')['mapping']['category'],'nat.6')
        self.assertEqual(self.row('5301000')['mapping']['category'],'nat.7')
        self.assertEqual(self.row('5002000')['mapping']['category'],'')
        self.profile(income_tax_basis='tas12')
        self.assertEqual(self.row('1202000')['mapping']['category'],'bs.1.7')
        self.assertEqual(self.row('1203000')['mapping']['category'],'bs.1.3')

    def test_invalid_category_forms_type_and_review_details_rejected(self):
        self.profile()
        for change in [dict(category='made-up'),dict(category='bs.1.1'),dict(tax_forms=['pnd999']),dict(tax_forms=['pnd50','pnd50']),dict(note=''),dict(reviewed_by='')]:
            with self.subTest(change=change),self.assertRaises(ValueError):self.mapping('4001000',**change)
        with self.assertRaises(ValueError):self.mapping('1202000',category='bs.1.7')
        with self.assertRaises(ValueError):self.mapping('9990000')

    def test_mapping_revisions_and_profile_revisions_reject_stale_updates(self):
        p=self.profile();r=self.row('4001000');self.mapping('4001000')
        data=dict(company_id=self.company['id'],code=r['code'],revision=r['revision'],profile_revision=p['revision'],mapping=r['mapping'])
        with self.assertRaisesRegex(ValueError,'another window'):t.save_mapping(self.s,data)
        r=self.row('4001000');data.update(revision=r['revision'],mapping=r['mapping']);self.profile(presentation='nature')
        with self.assertRaisesRegex(ValueError,'basis changed'):t.save_mapping(self.s,data)

    def test_contra_revenue_and_ppe_net_correctly_without_tax_double_count(self):
        self.profile()
        for code in ['1201000','4001000','4002000','1501000','1591000','5301000']:self.mapping(code)
        inv=self.invoice()
        rows=[a.line('1201000',10000),a.line('4002000',1000),a.line('4001000',credit=11000),a.line('1501000',20000),a.line('1201000',credit=20000),a.line('5301000',2000),a.line('1591000',credit=2000)]
        self.entry(inv,rows)
        result=t.report(self.s,self.company['id'],'2026-01-01','2026-12-31');groups={g['category']['id']:g for g in result['groups']}
        self.assertEqual(groups['fn.1']['reviewed_movement_minor'],10000)
        self.assertEqual(groups['bs.2.10']['reviewed_movement_minor'],18000)
        self.assertEqual(groups['fn.5']['reviewed_movement_minor'],2000)
        self.assertNotIn('tax_payable',result);self.assertNotIn('tax_totals',result)
        self.assertEqual(result['unmapped'],[])

    def test_unreviewed_and_non_thb_and_stale_sources_are_separate(self):
        inv=self.invoice();self.entry(inv);self.entry(self.invoice(currency='JPY'))
        result=t.report(self.s,self.company['id'],'2026-01-01','2026-12-31')
        self.assertEqual(result['included_entries'],1)
        self.assertTrue(all(g['reviewed_movement_minor']==0 for g in result['groups']))
        self.assertTrue(any(g['suggested_movement_minor'] for g in result['groups']))
        self.s.void(inv['id'],'Correction')
        result=t.report(self.s,self.company['id'],'2026-01-01','2026-12-31')
        self.assertEqual(result['groups'],[]);self.assertEqual(len(result['excluded_entries']),1)
        self.assertEqual(t.report(self.s,self.other['id'],'2026-01-01','2026-12-31')['groups'],[])

    def test_older_periods_cannot_be_presented_as_reviewed_under_2024_template(self):
        self.profile();self.mapping('4001000');self.entry(self.invoice(day='2023-10-01'))
        result=t.report(self.s,self.company['id'],'2023-01-01','2023-12-31')
        self.assertFalse(result['period_supported'])
        self.assertEqual(next(g for g in result['groups'] if g['category']['id']=='fn.1')['reviewed_movement_minor'],0)

    def test_backup_old_backup_and_reject_corrupt_mappings(self):
        self.profile();self.mapping('4001000');backup=self.s.backup();self.s.restore(backup,'RESTORE')
        self.assertEqual(self.row('4001000')['mapping']['status'],'reviewed')
        path=Path(self.tmp.name)/'tampered.sqlite3';path.write_bytes(backup);db=sqlite3.connect(path)
        db.execute("UPDATE accounting_accounts SET data=json_set(data,'$.thai_reporting.category','bogus')");db.commit();db.close()
        with self.assertRaises(ValidationError):self.s.restore(path.read_bytes(),'RESTORE')
        path.write_bytes(backup);db=sqlite3.connect(path);db.execute('DROP TABLE thai_reporting_profiles');db.commit();db.close()
        self.s.restore(path.read_bytes(),'RESTORE')
        self.assertFalse(t.profile(self.s,self.company['id'])['confirmed']);self.assertFalse(self.row('4001000')['mapping']['usable'])

    def test_accountant_pack_has_39_mappings_categories_forms_and_sources(self):
        self.profile();self.mapping('4001000');self.entry(self.invoice())
        archive=zipfile.ZipFile(io.BytesIO(c.export_pack(self.s,self.company['id'],2026)))
        work=json.loads(archive.read('thai-reporting-mappings.json'))
        self.assertEqual(len(work['accounts']),39)
        self.assertIn('DBD published item',archive.read('thai-reporting-mappings.csv').decode())
        self.assertIn('bs.3.2',{x['id'] for x in work['categories']})
        self.assertIn('pnd2',work['forms']);self.assertIn('dbd',work['sources'])
        self.assertTrue(json.loads(archive.read('dbd-mapped-movements.json'))['groups'])
        csv=archive.read('dbd-mapped-movements.csv').decode('utf-8-sig')
        self.assertIn('Company A',csv);self.assertIn('2026-01-01',csv);self.assertIn('Company basis confirmed',csv)

    def test_pnd2_worksheet_links_to_its_filing_and_exports(self):
        r=c.save_record(self.s,dict(company_id=self.company['id'],year=2026,kind='wht',payment_date='2026-10-02',payee='Individual lender',tax_id='1234567890123',income_category='Interest · reviewed',source='Loan payment',form='pnd2',tax_base='100.01',rate='15',notes='Reviewed rate and recipient status',reviewed=True,revision=0))
        self.assertEqual(r['withheld'],1500)
        work=c.workspace(self.s,self.company['id'],2026);filing=next(x for x in work['items'] if x['key']=='pnd2:2026-10')
        self.assertEqual(filing['working_amount_minor'],1500);self.assertTrue(filing['working_reviewed'])

    def test_csv_protects_names_and_notes_while_keeping_signed_numeric_values(self):
        raw=t.csv_bytes([['name','amount'],['=SUM(A1:A2)',-1200],[' @formula',0]]).decode('utf-8-sig')
        self.assertIn("'=SUM",raw);self.assertIn(',-1200',raw);self.assertIn("' @formula",raw)

    def test_mapping_review_does_not_change_invoices_entries_or_create_tax_returns(self):
        inv=self.invoice();saved=self.entry(inv)
        before=tuple(self.s.db.execute('SELECT * FROM invoices WHERE id=?',(inv['id'],)).fetchone())
        self.profile();self.mapping('4001000')
        work=a.workspace(self.s,'invoice',inv['id'],self.company['id'])
        self.assertFalse(work['stale']);self.assertEqual(work['entry'],saved['entry'])
        self.assertEqual(tuple(self.s.db.execute('SELECT * FROM invoices WHERE id=?',(inv['id'],)).fetchone()),before)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM compliance_records').fetchone()[0],0)


if __name__=='__main__':unittest.main()

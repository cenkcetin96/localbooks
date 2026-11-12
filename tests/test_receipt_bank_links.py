import base64
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Store, ValidationError


class ReceiptBankLinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name, pdf_renderer=lambda html: b'%PDF-TEST')
        self.company = self.store.save_party('companies', dict(name='Test company', code='TEST'))
        self.customer = self.store.save_party('contacts', dict(name='Customer'))
        draft = self.store.save_draft(dict(company_id=self.company['id'], contact_id=self.customer['id'], kind='sale', currency='THB', issue_date='2026-11-01', lines=[dict(description='Service', quantity='1', unit_price='100')]))
        self.invoice = self.store.issue(draft['id'], expected_revision=draft['revision'])

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def bank(self, amount='100', **extra):
        payload = dict(company_id=self.company['id'], account='12345678', currency='THB', account_confirmed=True, name='statement.csv', content=base64.b64encode(b'Date,Description,Deposit\n02/11/2026,Deposit,100\n').decode(), rows=[dict(row_key=amount, date='2026-11-02', direction='in', amount=amount, description='Customer deposit', balance='100')])
        payload.update(extra)
        result = self.store.import_bank(payload)
        return result['ids'][0]

    def review(self, bank_id, **extra):
        old = next(b for b in self.store.bank_entries() if b['id'] == bank_id)
        return self.store.save_bank_entry(dict(dict(old, amount=str(old['amount_minor']/100), category='income', reviewed=True, cash_flow=False), **extra))

    def payment(self, **extra):
        return self.store.add_payment(self.invoice['id'], dict(dict(amount='100', date='2026-11-02', method='Bank transfer', request_key='receipt-link'), **extra))

    def test_bank_first_requires_link_or_explicit_separate_payment(self):
        bank = self.bank()
        row = self.review(bank, cash_flow=True)
        with self.assertRaisesRegex(ValidationError, 'bank transaction already records'):
            self.payment()
        self.assertEqual(self.store.invoice(self.invoice['id'])['payments'], [])
        paid = self.payment(link_bank_id=bank, bank_revision=row['revision'])
        self.assertEqual(paid['balance_minor'], 0)
        linked = self.store.bank_entries()[0]
        self.assertEqual(linked['matched_record'], 'payment:'+str(paid['payments'][0]['id']))
        self.assertFalse(linked['cash_flow'])
        self.store.restore(self.store.backup(), 'RESTORE')
        self.assertEqual(self.store.bank_entries()[0]['matched_record'], linked['matched_record'])
        self.assertEqual(self.payment(link_bank_id=bank, bank_revision=row['revision'])['payments'], paid['payments'], 'Retry must not create a second receipt')

    def test_receipt_first_rejects_double_cash_but_allows_matching(self):
        paid = self.payment()
        bank = self.bank()
        with self.assertRaisesRegex(ValidationError, 'existing receipt'):
            self.review(bank, cash_flow=True)
        self.review(bank, matched_record='payment:'+str(paid['payments'][0]['id']))
        self.assertFalse(self.store.bank_entries()[0]['cash_flow'])

    def test_identical_but_distinct_payments_remain_possible(self):
        bank = self.bank()
        self.review(bank, cash_flow=True)
        self.payment(separate_payment_confirmed=True)
        self.assertTrue(self.store.bank_entries()[0]['cash_flow'])
        self.assertEqual(len(self.store.invoice(self.invoice['id'])['payments']), 1)
        # The opposite creation order also requires the explicit confirmation.
        self.review(bank, cash_flow=True, separate_payment_confirmed=True)

    def test_changed_retry_details_are_rejected_without_a_second_payment(self):
        paid = self.payment()
        for changes in [dict(amount='50'), dict(date='2026-11-03'), dict(method='Cash'), dict(reference='Changed reference'), dict(withholding='1'), dict(number='DIFFERENT'), dict(separate_payment_confirmed=True), dict(link_bank_id=999)]:
            with self.assertRaisesRegex(ValidationError, 'already recorded with different details'):
                self.payment(**changes)
        self.assertEqual(self.payment()['payments'], paid['payments'])
        self.assertEqual(len(self.store.invoice(self.invoice['id'])['payments']), 1)

    def test_cash_match_excludes_withholding(self):
        bank = self.bank('97')
        row = self.review(bank, cash_flow=True)
        paid = self.payment(amount='97', withholding='3', link_bank_id=bank, bank_revision=row['revision'])
        self.assertEqual(paid['balance_minor'], 0)
        self.assertEqual(paid['cash_paid_minor'], 9700)
        self.assertEqual(paid['withheld_minor'], 300)
        self.assertFalse(self.store.bank_entries()[0]['cash_flow'])

    def test_stale_or_incompatible_bank_link_creates_nothing(self):
        bank = self.bank()
        row = self.review(bank, cash_flow=True)
        for changes in [dict(bank_revision=1), dict(bank_revision=row['revision'], amount='99'), dict(bank_revision=row['revision'], date='2026-11-03')]:
            with self.assertRaisesRegex(ValidationError, 'selected bank transaction'):
                self.payment(link_bank_id=bank, **changes)
        self.assertEqual(self.store.invoice(self.invoice['id'])['payments'], [])
        self.assertTrue(self.store.bank_entries()[0]['cash_flow'])

    def test_link_cannot_cross_company_or_currency(self):
        other = self.store.save_party('companies', dict(name='Other company', code='OTHER'))
        wrong_company = self.bank(company_id=other['id'])
        wrong_currency = self.bank(currency='USD')
        for bank in [wrong_company, wrong_currency]:
            with self.assertRaisesRegex(ValidationError, 'selected bank transaction'):
                self.payment(link_bank_id=bank, bank_revision=1)
        self.assertEqual(self.store.invoice(self.invoice['id'])['payments'], [])
        self.assertTrue(all(b['matched_record']=='' for b in self.store.bank_entries()))

    def test_pdf_failure_rolls_back_receipt_and_bank_changes(self):
        bank = self.bank()
        row = self.review(bank, cash_flow=True)
        def fail(_html):
            raise RuntimeError('PDF failed')
        self.store.pdf_renderer = fail
        with self.assertRaisesRegex(RuntimeError, 'PDF failed'):
            self.payment(link_bank_id=bank, bank_revision=row['revision'])
        self.assertEqual(self.store.invoice(self.invoice['id'])['payments'], [])
        self.assertTrue(self.store.bank_entries()[0]['cash_flow'])
        self.assertEqual(self.store.bank_entries()[0]['revision'], row['revision'])

    def test_reversal_requires_bank_review_and_preserves_originals(self):
        bank = self.bank()
        self.store.attach_bank_document(dict(id=bank, name='proof.pdf', content=base64.b64encode(b'%PDF-proof').decode()))
        paid = self.payment(link_bank_id=bank, bank_revision=1)
        receipt = paid['payments'][0]
        original = tuple(self.store.db.execute('SELECT html,pdf FROM payments WHERE id=?', (receipt['id'],)).fetchone())
        old = self.store.bank_entries()[0]
        reversed_invoice = self.store.reverse_payment(paid['id'], receipt['id'], 'Wrong receipt')
        after = self.store.bank_entries()[0]
        self.assertEqual(reversed_invoice['balance_minor'], 10000)
        self.assertEqual(after['matched_record'], '')
        self.assertFalse(after['reviewed'])
        self.assertFalse(after['cash_flow'])
        self.assertEqual(after['revision'], old['revision']+1)
        self.assertEqual(after['documents'], old['documents'])
        self.assertEqual(tuple(self.store.db.execute('SELECT html,pdf FROM payments WHERE id=?', (receipt['id'],)).fetchone()), original)
        self.store.restore(self.store.backup(), 'RESTORE')
        self.assertFalse(self.store.bank_entries()[0]['reviewed'])

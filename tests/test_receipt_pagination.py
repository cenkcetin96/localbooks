from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Store
from documents import shutil_which


class ReceiptPaginationTests(unittest.TestCase):
    def test_long_issued_invoice_can_be_paid_without_losing_receipt_content(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            try:
                company = store.save_party('companies', dict(name='Receipt test', code='RCPT'))
                customer = store.save_party('contacts', dict(name='Customer'))
                draft = store.save_draft(dict(
                    company_id=company['id'], contact_id=customer['id'], kind='sale',
                    currency='THB', issue_date='2026-11-01',
                    lines=[dict(description=f'Item {n+1:02}: Consulting, implementation and review services for the agreed project, including preparation and support.',
                                quantity='1', unit_price='100') for n in range(30)],
                ))
                invoice = store.issue(draft['id'], expected_revision=draft['revision'])
                paid = store.add_payment(invoice['id'], dict(
                    amount='3000', date='2026-11-12', method='Bank transfer', request_key='long-receipt',
                ))
                self.assertEqual(paid['balance_minor'], 0)
                self.assertEqual(len(paid['payments']), 1)
                pdf = store.db.execute('SELECT pdf FROM payments WHERE id=?', (paid['payments'][0]['id'],)).fetchone()[0]
                target = Path(directory) / 'receipt.pdf'
                target.write_bytes(pdf)
                info = subprocess.check_output([shutil_which('pdfinfo'), str(target)], text=True)
                pages = int(next(row.split(':', 1)[1] for row in info.splitlines() if row.startswith('Pages:')))
                self.assertGreater(pages, 1)
                text = subprocess.check_output([shutil_which('pdftotext'), str(target), '-'], text=True)
                for value in ['Item 01', 'Item 30', '3,000.00', 'Authorized by', 'Received by', f'Page {pages} of {pages}']:
                    self.assertIn(value, text)
            finally:
                store.db.close()

"""Synthetic PDF checks for the Thai scan, review, and vendor bill export."""
import base64
import csv
import io
from decimal import Decimal
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Store, ValidationError
from scan import scan_document

FIXTURES = Path(__file__).parent / 'fixtures/thai-invoices'
SAMPLES = (
    ('01-thai-columns.pdf', 'บริษัท ตัวอย่างอรุณ จำกัด', '1000.00', '70.00', '1070.00'),
    ('02-bilingual-stacked.pdf', 'บริษัท ตัวอย่างมะลิ จำกัด', '2450.00', '171.50', '2621.50'),
    ('03-image-only.pdf', 'บริษัท ตัวอย่างสยาม จำกัด', '500.00', '35.00', '535.00'),
)


@unittest.skipUnless(all(shutil.which(p) for p in ('pdfinfo', 'pdftotext', 'pdftoppm', 'tesseract')),
                     'Requires Poppler and Tesseract')
class ThaiVendorInvoiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.s.db.close)
        self.company = self.s.save_party('companies', dict(name='Synthetic buyer', code='QA',
                                                           tax_id='0000000000099'))

    def test_fixture_scans_review_persistence_and_both_vendor_bill_exports(self):
        saved = self.s.save_party('contacts', dict(name=SAMPLES[0][1], role='supplier', tax_id='0000000000001'))
        selections = []
        for i, (filename, name, net, vat, gross) in enumerate(SAMPLES, 1):
            with self.subTest(filename=filename):
                content = (FIXTURES / filename).read_bytes()
                scan = scan_document(content, companies=[self.company], contacts=[saved])
                self.assertEqual(scan['suggested_supplier'], name)
                self.assertEqual(scan['suggested_supplier_tax_id'], f'{i:013d}')
                self.assertEqual(scan['suggested_date'], '2026-11-23')
                self.assertEqual(scan['suggested_number'], f'QA-TH-{i:03d}')
                self.assertEqual(scan['suggested_currency'], 'THB')
                self.assertEqual(scan['suggested_amount'], gross)
                self.assertEqual(scan['suggested_vat_amount'], vat)
                if i == 1:
                    self.assertEqual(scan['suggested_supplier_id'], saved['id'])
                    self.assertEqual(scan['suggested_company_id'], self.company['id'])
                else:
                    self.assertNotIn('suggested_supplier_id', scan)
                result = self.s.save_journal(dict(
                    company_id=self.company['id'], supplier=name, supplier_tax_id=scan['suggested_supplier_tax_id'],
                    supplier_id=scan.get('suggested_supplier_id'), date=scan['suggested_date'],
                    document_number=scan['suggested_number'], currency='THB', document_type='invoice',
                    payment_status='unpaid', amount=gross, vat_amount=vat,
                    name=filename, content=base64.b64encode(content).decode()))
                selections.append(dict(id=result['id'], supplier=name, currency='THB',
                                       gross_minor=int(Decimal(gross)*100), saved_vat_minor=int(Decimal(vat)*100),
                                       due_date='2026-12-23'))

        self.s.restore(self.s.backup(), 'RESTORE')
        config = dict(company_id=self.company['id'], account='5299100', tax='QA VAT 7%', bills=selections)
        for format in ('odoo', 'xero'):
            with self.subTest(format=format):
                csv_bytes = self.s.export_vendor_bills(dict(config, target=format))
                rows = list(csv.DictReader(io.StringIO(csv_bytes.decode())))
                self.assertEqual(len(rows), 3)
                for i, (row, (_, supplier, net, vat, gross)) in enumerate(zip(rows, SAMPLES), 1):
                    if format == 'odoo':
                        self.assertEqual(row['Partner'], supplier)
                        self.assertEqual(row['Reference'], f'QA-TH-{i:03d}')
                        self.assertEqual(row['Invoice Bill Date'], '2026-11-23')
                        self.assertEqual(row['Invoice Lines/Unit Price'], net)
                        self.assertEqual(row['Invoice Lines/Taxes'], 'QA VAT 7%')
                    else:
                        self.assertEqual(row['ContactName'], supplier)
                        self.assertEqual(row['InvoiceNumber'], f'QA-TH-{i:03d}')
                        self.assertEqual(row['InvoiceDate'], '23/11/2026')
                        self.assertEqual(row['UnitAmount'], net)
                        self.assertEqual(row['TaxAmount'], vat)
                        self.assertEqual(Decimal(row['UnitAmount']) + Decimal(row['TaxAmount']), Decimal(gross))
                if EXPORT_DIRECTORY:
                    (EXPORT_DIRECTORY / f'{format}.csv').write_bytes(csv_bytes)

    def test_unreconciled_totals_and_ambiguous_date_or_contact_ask_for_review(self):
        filename = '04-needs-review.pdf'
        name = 'บริษัท ตัวอย่างซ้ำ จำกัด'
        scan = scan_document((FIXTURES / filename).read_bytes(), contacts=[dict(id=1, name=name), dict(id=2, name=name)])
        self.assertEqual(scan['suggested_supplier'], name)
        self.assertNotIn('suggested_supplier_id', scan)
        self.assertEqual(scan['suggested_date'], '')
        self.assertEqual(scan['suggested_vat_amount'], '')
        self.assertEqual(scan['suggested_amount'], '910.00')

    def test_manual_vat_zero_and_edits_remain_distinct_from_missing_vat(self):
        fixture = FIXTURES / SAMPLES[0][0]
        data = dict(company_id=self.company['id'], supplier=SAMPLES[0][1], date='2026-11-23',
                    document_number='QA-ZERO', currency='THB', document_type='invoice', payment_status='unpaid',
                    amount='1070.00', vat_amount='0', name=fixture.name,
                    content=base64.b64encode(fixture.read_bytes()).decode())
        saved = self.s.save_journal(data)
        self.assertEqual(self.s.journal()[0]['vat_minor'], 0)
        self.s.save_journal(dict(data, id=saved['id'], vat_amount='70.00'))
        self.assertEqual(self.s.journal()[0]['vat_minor'], 7000)
        with self.assertRaises(ValidationError):
            self.s.save_journal(dict(data, id=saved['id'], vat_amount='1070.00'))
        self.s.save_journal(dict(data, id=saved['id'], vat_amount=''))
        self.assertNotIn('vat_minor', self.s.journal()[0])


EXPORT_DIRECTORY = None
if __name__ == '__main__':
    if len(sys.argv) > 1:
        EXPORT_DIRECTORY = Path(sys.argv.pop())
        EXPORT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    unittest.main()

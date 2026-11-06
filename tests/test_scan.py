"""Regression checks for extracted document fields, independent of PDF/OCR engines."""
import unittest
from unittest.mock import patch
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scan import parse_document_text,parse_date,scan_document

COMPANIES=[dict(id=1,name='First Company',tax_id='1111111111111'),dict(id=2,name='Second Company',tax_id='2222222222222')]

THAI='''ใบเสร็จรับเงิน
บริษัท ผู้ขาย ตัวอย่าง จำากัด (สำานักงานใหญ่)                เลขที่                  RE2026100014
122/58 หมู่ 6 ตำาบลบางเพรียง                                วันที่                  16/10/2026
อำาเภอบางบอ่ จังหวัดสมุทรปราการ 10560                       ผู้ขาย                   Salesperson
เลขประจำาตัวผู้เสียภาษี 3333333333333
โทร. 098-245-1688
ลูกค้า
Second Company
เลขประจำาตัวผู้เสียภาษี 2222222222222
#   รายละเอียด                         จำนวน     ราคาต่อหน่วย       มูลค่า
1   Consultation                       100        80.00             8,000.00
                                       รวมเป็ นเงิน                 8,000.00 บาท
                                       มูลคา่ ที่ไมม่ ีภาษี          8,000.00 บาท
                                       จำนวนเงินรวมทั้งสิ้น          8,000.00 บาท
'''
STRIPE='''Receipt
Invoice number XSYRS54K-0006
Receipt number DEMO-REC-2026-0001
Date paid      October 8, 2026

Example AI OpCo, LLC                  Bill to
1455 3rd Street                       First Company
San Francisco, California 94158       Customer address
United States                         Thailand
billing@example.test                  buyer@example.test
TH VAT 3333333333333                  TH VAT 1111111111111

฿6,541.12 paid on October 8, 2026
Description                                 Qty       Unit price       Amount
AI Pro Subscription                         1         ฿6,541.12        ฿6,541.12
Oct 5-Nov 5, 2026
Subtotal                                                               ฿6,541.12
Total                                                                  ฿6,541.12
Amount paid                                                            ฿6,541.12
'''

class ScanFieldsTests(unittest.TestCase):
    def test_due_date_below_amount_label_is_not_read_as_money(self):
        result=parse_document_text('Invoice\nSupplier: Vendor Co Ltd\nAmount due / ยอดชำระ\nDue by November 20, 2026\nBank details                         Amount due                      ฿16,500.00')
        self.assertEqual(result['suggested_amount'],'16500.00')

    def test_thai_receipt_header_and_company(self):
        r=parse_document_text(THAI,companies=COMPANIES)
        self.assertEqual(r['suggested_supplier'],'บริษัท ผู้ขาย ตัวอย่าง จำกัด')
        self.assertEqual(r['suggested_supplier_tax_id'],'3333333333333')
        self.assertEqual(r['suggested_number'],'RE2026100014')
        self.assertEqual(r['suggested_date'],'2026-10-16')
        self.assertEqual(r['suggested_amount'],'8000.00')
        self.assertEqual(r['suggested_company_id'],2)
        self.assertEqual(r['suggested_party_role'],'supplier')
        self.assertEqual(r['suggested_document_type'],'receipt')
        self.assertEqual(r['suggested_currency'],'THB')
        self.assertEqual(r['suggested_description'],'Consultation')
        self.assertIn('122/58',r['suggested_supplier_address'])
        self.assertEqual(r['suggested_supplier_phone'],'098-245-1688')
        self.assertNotIn('payment_status',r)

    def test_side_by_side_receipt_and_buyer_tax_isolation(self):
        r=parse_document_text(STRIPE,companies=COMPANIES)
        self.assertEqual(r['suggested_supplier'],'Example AI OpCo, LLC')
        self.assertEqual(r['suggested_supplier_tax_id'],'3333333333333')
        self.assertEqual(r['suggested_supplier_email'],'billing@example.test')
        self.assertNotIn('Customer',r['suggested_supplier_address'])
        self.assertEqual(r['suggested_number'],'DEMO-REC-2026-0001')
        self.assertEqual(r['suggested_date'],'2026-10-08')
        self.assertEqual(r['suggested_payment_date'],'2026-10-08')
        self.assertEqual(r['suggested_amount'],'6541.12')
        self.assertEqual(r['suggested_paid_amount'],'6541.12')
        self.assertEqual(r['suggested_company_id'],1)
        self.assertEqual(r['suggested_description'],'AI Pro Subscription')

    def test_stacked_metadata_values(self):
        source='Invoice\nSupplier: Example Co., Ltd.\nInvoice number                 Issue date             Due date\nINV-2026-01                    November 1, 2026       November 20, 2026\nGrand total: USD 100.00'
        r=parse_document_text(source)
        self.assertEqual(r['suggested_number'],'INV-2026-01')
        self.assertEqual(r['suggested_date'],'2026-11-01')
        self.assertEqual(r['suggested_amount'],'100.00')
        self.assertEqual(r['suggested_currency'],'USD')

    def test_gross_total_kept_separate_from_net_payment(self):
        r=parse_document_text('Receipt\nSeller: Example Ltd.\nDate paid: 8 Oct 2026\nGrand total: THB 16,500.00\nWithholding tax: 495.00\nAmount paid: 16,005.00')
        self.assertEqual(r['suggested_amount'],'16500.00')
        self.assertEqual(r['suggested_paid_amount'],'16005.00')
        self.assertFalse(r['payment_amount_uncertain'])
        r=parse_document_text('Receipt\nGrand total: THB 16,500.00\nWithholding tax: 495.00\nAmount paid: 16,500.00')
        self.assertEqual(r['suggested_amount'],'16500.00')
        self.assertEqual(r['suggested_paid_amount'],'')
        self.assertTrue(r['payment_amount_uncertain'])
        r=parse_document_text('ใบเสร็จรับเงิน\nจำนวนเงินทั้งสิ้น 16,500.00 บาท\nจำนวนเงินที่ชำระ 16,500.00 บาท\nภาษีหัก ณ ที่จ\uf70aาย 495.00 บาท')
        self.assertEqual(r['suggested_paid_amount'],'')
        self.assertTrue(r['payment_amount_uncertain'])

    def test_known_supplier_tax_id_wins_and_missing_contact_fields_survive(self):
        contact=dict(id=9,name='Saved supplier alias',tax_id='3-3333-33333-33-3')
        r=parse_document_text(THAI,contacts=[contact])
        self.assertEqual(r['suggested_supplier_id'],9)
        self.assertIn('122/58',r['suggested_supplier_address'])
        self.assertNotIn('suggested_supplier_id',parse_document_text(THAI,contacts=[contact,{**contact,'id':10}]))

    def test_buyer_is_not_used_as_supplier(self):
        r=parse_document_text('Receipt\nBill to: First Company\nTax ID: 1111111111111\nTotal: THB 100.00',companies=COMPANIES)
        self.assertEqual(r['suggested_supplier'],'')
        self.assertEqual(r['suggested_supplier_tax_id'],'')
        self.assertEqual(r['suggested_company_id'],1)

    def test_conflicting_tax_id_does_not_match_supplier_by_name(self):
        r=parse_document_text(THAI,contacts=[dict(id=9,name='บริษัท ผู้ขาย ตัวอย่าง จำกัด',tax_id='9999999999999')])
        self.assertNotIn('suggested_supplier_id',r)

    def test_local_dates_and_due_date_not_used(self):
        for value in ['วันที่ ๑๖/๑๐/๒๕๖๙','วันที่ 16 ต.ค. 2569','Issue date 16 October 2026','Date Oct 16, 2026','วันที่ 2569-10-16']:
            self.assertEqual(parse_document_text('Invoice\n'+value)['suggested_date'],'2026-10-16',value)
        self.assertEqual(parse_document_text('Invoice\nDue date: 20/11/2026')['suggested_date'],'')
        self.assertEqual(parse_document_text('Invoice\nIssue date: 31/02/2026')['suggested_date'],'')
        self.assertEqual(parse_date('not a date'),'')

    def test_number_on_next_line_and_no_account_or_license_number(self):
        r=parse_document_text('Receipt\nReceipt number\nDEMO-REC-2026-0001\nAccount number 1234567890\nใบอนุญาตเลขที่ 1234/2569')
        self.assertEqual(r['suggested_number'],'DEMO-REC-2026-0001')
        self.assertEqual(parse_document_text('Invoice\nใบอนุญาตเลขที่ 1234/2569')['suggested_number'],'')

    def test_currency_and_precision(self):
        for total,currency,amount in [('Grand total JPY 8,000','JPY','8000'),('Grand total KWD 1.234','KWD','1.234'),('Total €123.45','EUR','123.45')]:
            r=parse_document_text('Invoice\n'+total)
            self.assertEqual(r['suggested_currency'],currency)
            self.assertEqual(r['suggested_amount'],amount)
        self.assertEqual(parse_document_text('Invoice\nTotal $100.00')['suggested_currency'],'')
        self.assertEqual(parse_document_text('Invoice\nTotal USD 100.00\nEquivalent ฿3,500.00')['suggested_currency'],'')
        self.assertEqual(parse_document_text('Receipt\nยอดรวมสุทธิ 8000บาท')['suggested_amount'],'8000.00')
        self.assertEqual(parse_document_text('Invoice\nSubtotal 100.00\nTotal tax 7.00')['suggested_amount'],'')

    def test_bad_text_layer_falls_back_to_ocr(self):
        calls=[]
        def fake_run(args,timeout=60):
            calls.append(args[0])
            if args[0].endswith('pdfinfo'):return 'Pages: 1'
            if args[0].endswith('pdftotext'):return '\uf701'*100
            if args[0].endswith('tesseract'):return THAI
            return ''
        with patch('scan.run',side_effect=fake_run):r=scan_document(b'%PDF-1.4 test',companies=COMPANIES)
        self.assertEqual(r['suggested_number'],'RE2026100014')
        self.assertTrue(any(p.endswith('pdftoppm') for p in calls))

if __name__=='__main__':unittest.main()

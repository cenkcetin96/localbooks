"""Run inside Odoo 19 shell with a disposable database and the fixture CSV."""
import csv
import io
from pathlib import Path

company = env.company  # Provided by odoo shell.
company.country_id = env.ref('base.th')
env['account.chart.template'].try_loading('th', company)
journal = env['account.journal'].search([('company_id', '=', company.id), ('type', '=', 'purchase')], limit=1)
assert journal and company.currency_id.name == 'THB'
account = env['account.account'].create(dict(name='QA fixture expense', code='5299100', account_type='expense',
                                            company_ids=[(6, 0, [company.id])]))
tax = env['account.tax'].create(dict(name='QA VAT 7%', company_id=company.id, amount=7,
                                    amount_type='percent', type_tax_use='purchase', price_include_override='tax_excluded'))

file = Path('/fixtures/odoo.csv').read_bytes()
rows = list(csv.DictReader(io.StringIO(file.decode())))
assert len(rows) == 3
for row in rows:
    env['res.partner'].create(dict(name=row['Partner'], supplier_rank=1))
wizard = env['base_import.import'].with_context(default_move_type='in_invoice', default_journal_id=journal.id).create({
    'res_model': 'account.move', 'file': file, 'file_name': 'odoo.csv', 'file_type': 'text/csv',
})
options = dict(encoding='utf-8', separator=',', quoting='"', has_headers=True, skip=0, date_format='%Y-%m-%d')
preview = wizard.parse_preview(options)
assert 'error' not in preview, preview
mapping = {'External ID': 'id', 'Partner': 'partner_id', 'Reference': 'ref',
           'Invoice Bill Date': 'invoice_date', 'Due Date': 'invoice_date_due', 'Currency': 'currency_id',
           'Invoice Lines/Label': 'invoice_line_ids/name', 'Invoice Lines/Account': 'invoice_line_ids/account_id',
           'Invoice Lines/Quantity': 'invoice_line_ids/quantity', 'Invoice Lines/Unit Price': 'invoice_line_ids/price_unit',
           'Invoice Lines/Taxes': 'invoice_line_ids/tax_ids'}
headers = preview['headers']
fields = [mapping[header] for header in headers]
result = wizard.execute_import(fields, headers, options)
assert not [message for message in result['messages'] if message['type'] == 'error'], result
bills = env['account.move'].browse(result['ids'])
assert len(bills) == 3
amounts = {'QA-TH-001': (1000, 70, 1070), 'QA-TH-002': (2450, 171.5, 2621.5), 'QA-TH-003': (500, 35, 535)}
for bill, row in zip(bills, rows):
    assert bill.state == 'draft' and bill.move_type == 'in_invoice'
    assert bill.partner_id.name == row['Partner'] and bill.ref == row['Reference']
    assert bill.invoice_date.isoformat() == '2026-11-23' and bill.invoice_date_due.isoformat() == '2026-12-23'
    assert bill.currency_id.name == 'THB'
    assert bill.invoice_line_ids.account_id == account and bill.invoice_line_ids.tax_ids == tax
    assert (bill.amount_untaxed, bill.amount_tax, bill.amount_total) == amounts[bill.ref]
    print(f'Odoo draft {bill.ref}: {bill.partner_id.name} · {bill.amount_untaxed} + {bill.amount_tax} = {bill.amount_total} THB')

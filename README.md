# LocalBooks

Local invoices, receipts, expenses and accountant handoff for Thai and expat SME owners and managers using a MacBook. Keep day-to-day paperwork and Thai business compliance preparation together on your Mac, with no subscription or cloud account.

**MIT licensed · Local storage · English interface · English/Thai documents and OCR**

Created by [Cenk Cetin](https://cenkcetin.com), with links to [FGC](https://fgc.co.th). A new installation starts with an empty database; all company, customer and bank details are entered by you.

## Download and start — no coding needed

[**Download LocalBooks for Mac**](https://github.com/cenkcetin96/localbooks/releases/latest)

For **Apple Silicon Macs (M1 or newer), macOS 15 Sequoia or later**. Check **Apple menu → About This Mac** if you are unsure. The download includes everything needed for PDF export and Thai/English scanning. No Terminal, Homebrew, Python or Chrome installation is needed.

1. On the download page, click **LocalBooks-1.1.0-macOS-AppleSilicon.dmg** under **Assets**.
2. Open the downloaded file and drag **LocalBooks** onto **Applications**.
3. Open **Applications → LocalBooks**.
4. If macOS blocks the app, choose **Done**, then **System Settings → Privacy & Security → Open Anyway**, and confirm **Open**. This community build is not Apple notarized. [Apple’s step-by-step instructions](https://support.apple.com/en-us/102445).
5. Add your company and a customer, then create your first invoice. Follow the short tour below for expenses and accountant handoff.

Your records stay on your Mac. The app opens with empty books on a new installation. If you already use LocalBooks, download a backup before updating; the app reuses your saved records. Intel Macs are not supported by this download.

## Start today, or make it your own

**Just want to run your business?** Download the app above and use it as-is. There is no subscription or cloud account.

**Want to vibe code your own workflow?** Fork the MIT-licensed source and open it in Codex, Claude Code, Cursor or your preferred AI coding tool. You can adjust forms, reports or workflows. Follow the [customization guide](DEVELOPMENT.md) and experiment with a separate demo profile before touching your live books.

## Five-minute onboarding tour

1. **My companies** — add your legal company name, tax and branch details, invoice code and optional logo. Add each business separately, then choose the active company in the sidebar.
2. **Customers & suppliers** — save a customer and the suppliers you work with. Add products or services you invoice regularly.
3. **Invoices** — create a draft, review amounts and VAT treatment, then issue and download its PDF. Record a payment to create its receipt; partial payments keep the remaining balance visible.
4. **Expenses & payables / Journal** — upload a supplier document or import a KBank statement. Review extracted Thai/English fields, attach evidence and link payments to avoid counting them twice.
5. **Compliance** — confirm your preparation year, VAT/payroll settings and financial year-end with your accountant. Track evidence and outstanding work, then export the company/year accountant pack.
6. **Backup & restore** — download a backup and keep a private copy on another drive. Backups include every company and their attachments.

**สำหรับเจ้าของและผู้จัดการ SME ในไทย:** จัดการใบแจ้งหนี้ ใบเสร็จ ค่าใช้จ่าย และเอกสารส่งนักบัญชีบน MacBook ข้อมูลอยู่ในเครื่อง โปรดตรวจสอบข้อมูลจาก OCR และให้นักบัญชียืนยันภาษีและกำหนดเวลายื่นแบบ

LocalBooks helps prepare records for review. It does not submit tax returns, run payroll, provide a double-entry ledger, or replace your accountant. Verify the applicability and current deadlines of every filing.

## License and privacy

Application code is released under the [MIT License](LICENSE). Bundled fonts and OCR models keep their own licenses; see [third-party notices](THIRD_PARTY_NOTICES.md).

This repository contains source, app packaging assets, OCR models and synthetic tests. It excludes databases, backups, statements, invoices, signatures, private screenshots, logs, local runtime files and prior local verification notes. `.gitignore` provides an additional guard for common data locations. Review files before committing; keep real records outside Git.

## Open the app

After installing, open **LocalBooks** from **Applications**. No Terminal command is needed. You can keep LocalBooks in the Dock for quick access. For this update, save any open draft, click **Quit LocalBooks** in the sidebar, then double-click the updated app. Existing records stay in place. Only one LocalBooks desktop instance runs for your records. Opening the app again returns to that instance and restores a minimized window. The launcher also reuses the existing local server. To load a newly rebuilt app, save any draft, quit LocalBooks, and reopen it. Click **Quit LocalBooks** in the sidebar to stop the local server and close the desktop app. Closing the window or using the Mac app menu leaves the local server running; reopening LocalBooks reconnects to it.

1. Add your company in **My companies**. Each company keeps its own numbering sequence. Enter your actual details and optionally a PNG/JPEG logo. The supplied sample issuer/customer is not preloaded.
2. Add customers or suppliers in **Customers & suppliers**.
3. Create an invoice draft, select its company/customer and currency, then edit directly on the document. Click the name, address, date, description, quantity, or price; totals update in place. Inline party edits apply to that document, leaving the reusable record unchanged. Save the draft. **Preview / print** opens the document. Use **File → Print** or ⌘P if printing on paper; use **Download PDF** for the fixed, saved PDF.
4. Review and **Issue** the document. Its details, number, and PDF are then preserved. A custom number may be entered; duplicates are rejected. Automatic numbers look like `DEMO-INV-2026-0001`.
5. **Record payment** opens a receipt editor. Enter the payment amount received, date, method, and any actual withholding tax on the document, then confirm they occurred. Payment plus withholding reduces the balance; attach the withholding certificate to the payment when available. The app does not determine or calculate the withholding rate. Partial payments retain the remaining balance. The receipt issue date matches the selected payment date, including payments recorded later; the audit timestamp still records when you saved it. Every recorded payment gets a separate receipt and saved PDF. No payments are sent by the app.
6. In **Expense journal**, use the company filter to keep each business’s records separate. Upload a supplier PDF, PNG, or JPEG. LocalBooks extracts text locally and suggests fields for you to verify; enter missing details, select the payment status yourself, and save the entry. On save, LocalBooks reuses a supplier from Customers & suppliers by tax ID, or by its normalized company name when no tax match exists. You can also choose a saved supplier manually. A new supplier is created when there is no match; reviewed tax ID, address, email, phone and branch details are saved with it. Existing supplier details are retained and only missing details are filled. Supplier creation and journal saving happen together, so failed or duplicate uploads do not create stray supplier records. The original file remains attached. Use View to open the saved PDF or image in a modal, Edit to update its reviewed fields, or Original to download it. Supplier documents are uploaded here; LocalBooks does not generate payables. Previously saved documents are retained in backups. This is an expense record, not a double-entry general ledger.

## Business overview

The app opens on **Overview** for the selected company: money in/out, net cash movement, outstanding invoices, top services/products and top customers. Select a year and currency; the current year runs through today, and past years cover January–December. Expand Monthly figures for exact values. Outstanding and overdue invoices show current saved balances across all years, after payments and withholding credits. Click outstanding invoices, a Needs attention count or a customer to open the matching records; the destination shows a removable overview filter. Needs attention flags unreviewed bank rows, bank rows missing supporting documents, and paid expenses missing payment details for the selected year. Review and missing-document counts can overlap. Payments are counted on their payment dates, with customer withholding excluded from cash received. Drafts, voided invoices and reversed payments are excluded. Sales use issued line totals before VAT and the invoice issue date; net movement is not profit.

For a paid expense, enter its **Date paid** and **Cash paid**, after any tax withheld. Older paid entries without these details are flagged for review and excluded from cash flow until confirmed. Unpaid and unspecified uploads do not create cash outflow. Legacy supplier-payable payments remain included. The overview covers saved records and is not a bank reconciliation or tax return.

See [the Thailand compliance plan](THAILAND_COMPLIANCE.md) for configuring company-specific compliance preparation and understanding its limits.

## Scan supplier documents

In Expense journal, choose **Upload document**, then **Detect from document** or pick the company yourself. Scanning fills the supplier, tax ID/contact details when readable, document type/number/date, currency, total and a short item description. English/Thai labels, Buddhist Era dates, Thai numerals, stacked fields and side-by-side supplier/customer blocks are supported. The buyer tax ID can select your company; supplier tax IDs match saved contacts. A company selected explicitly takes precedence. Ambiguous company/currency matches remain unselected for you to confirm.

Review the filled form and save. Payment status remains your choice. Receipt payment date and cash amount are suggested when clear, while conflicting withholding/payment figures leave cash paid for manual confirmation. Missing required fields are listed. Existing supplier details are reused, and readable missing contact details can be filled on save. Scanning and OCR stay on this Mac; poor scans or unfamiliar layouts may still require corrections. Text PDFs use direct extraction; image PDFs and broken text layers fall back to English/Thai OCR.

## Import KBank business statements

Open **Journal → Import bank statement**. Choose the company, enter the full account number, and upload a K BIZ CSV, PDF, scanned PDF, PNG or JPEG. Statements can contain up to **200 PDF pages / 20 MB / 20,000 rows per import**. Text extraction and Thai/English OCR run locally, with page progress and cancellation. Protected PDFs must be exported without a password first.

The review table shows each transaction's date, direction, amount, running balance, reference, source page and original extracted row. Correct uncertain fields, add a missing row when needed, and select the transactions to import. All pages are scanned; a page without recognized dated rows is flagged. Opening/closing and running balances are checked when readable. Always compare transaction counts and totals with the original, especially for scanned or unfamiliar layouts. Scanned text cannot guarantee a complete or accurate reconstruction of an arbitrary statement.

KBank's published business-statement formats distinguish withdrawals, deposits and balances, with detailed statements including counterparties. The importer supports those fields and K BIZ-style CSV exports; older layouts may need manual corrections. See [KBank business statement formats](https://www.kasikornbank.com/th/news/pages/Statement.aspx) and the [K-Cash Connect Plus manual](https://www.kasikornbank.com/th/Download/Level4_doc/EAZYMANUALKCashConnectPlus_EN.pdf).

Importing creates **Needs review** journal records. It does not generate invoices or receipts, create suppliers from vague bank descriptions, or infer VAT/withholding tax. The original statement is stored once and linked to its transactions. Click **View** for the statement, **Edit** to correct/classify the record, or **Documents** to attach and preview one or more supporting PDFs/images. Each transaction keeps its own document list, review notes, and original source row. Later edits do not change the uploaded statement or supporting files. Known KBank publishing codes, bank contact lines, websites and “issued by K BIZ” footer text are removed from transaction descriptions. Existing affected descriptions are repaired on startup and when restoring an older backup, with a safety copy created first. Original statement bytes and extracted source evidence stay unchanged.

Exact source rows cannot be imported twice. Matching rows with a running balance are recognized across overlapping files; uncertain same-date/same-amount matches are flagged for you to confirm. Genuine repeated transactions within the same statement remain separate. Companies and accounts have separate duplicate checks. Partial imports can be resumed by uploading the same statement again and selecting its remaining rows.

Imported transactions start **excluded from overview cash flow**. After reviewing/classifying a record, either match it to an existing invoice payment or paid expense, or explicitly include it as a separate cash movement. Matched records are excluded so the existing payment is counted once. Transfers, loans, capital and refunds have separate classifications; bank deposits do not become sales automatically. This is a reviewable journal, not a full double-entry ledger or automatic bank reconciliation.

Use the company/year/source/review filters and **Missing documents** to work through a backlog. **Export bank records** downloads a CSV for the selected company/year, including classifications, review status, cash-flow inclusion, matches, source statement/page and supporting filenames. This is an accountant handoff/review export, not a filed tax return. Thai tax treatment still needs invoices, certificates and other appropriate evidence; VAT input-tax evidence rules are in [Revenue Code Section 82/5](https://rd.go.th/english/37718.html).

Statements, transactions and supporting documents are included in the normal database backup. Restoring older backups remains supported. No bank login, bank connection or external upload is used. An unfinished review can be resumed during the current session from **Journal → Resume statement import**; closing/reloading an unfinished review loses unsaved edits, so save the import first or cancel it explicitly.

## Corrections and history

Open any saved sales invoice and click **Duplicate** to create an editable new draft. Company/customer details, items, currency, VAT, reference, notes and payment instructions are copied. The issue date becomes today and the due date keeps the original payment term. Review dates and service periods before issuing. The new invoice receives its own number when issued; payments, receipts, attachments and issued PDFs are not copied. The original stays unchanged.

Drafts are editable. Issued records cannot be edited or renumbered. Reverse an incorrect payment with a reason; its original receipt stays available and the app marks it reversed. An issued invoice with no active payments can be voided with a reason. Its number remains reserved. Create a new draft for the correction. A prior issued PDF or receipt never changes after later payments, contact edits, reversals, or voiding; its current status is shown in the app.

## Compliance and accountant handoff

Choose a company in the sidebar, open **Compliance**, select an accounting year, then use **Company settings** to set the first preparation year, current VAT/payroll settings, accountant and financial year-end. Year-end starts blank. New profiles start in the current year, with no employees and the VAT setting entered in My companies. Confirm the first preparation year, historical registrations and payroll before relying on the task list. Leave year-end unconfirmed until the accountant checks it. It remains editable after confirmation.

When year-end is blank, source records use a provisional calendar year and annual deadlines stay blank. A configured year-end selects the accounting period ending in the chosen year. Changing it retains saved work under its original year and flags calculation months outside the revised period; review both periods rather than assuming saved worksheets moved. Incorporation years, shortened periods and historical changes need accountant review.

- **Filings** tracks monthly VAT, supplier withholding, payroll/SSO and overseas-payment review, plus half-year/annual corporate tax, AGM, DBD and shareholder-list tasks. All begin Unknown. Update owner, verified deadline, filing/payment dates, declared/paid amounts and acceptance reference; attach filing acceptance and payment receipts separately. A missing receipt is flagged even if the status says filed/paid. No return is submitted by LocalBooks.
- **Missing documents** detects unreviewed bank records, unsupported unmatched bank movements, missing payment-linked customer WHT certificates and absent filing/payment evidence. Add your own document requests, assign owners, attach originals and mark them requested/received/reviewed. No detected gaps does not establish complete books.
- **Calculations** saves THB VAT working papers (output less eligible input less confirmed carry-forward), payment-level supplier WHT schedules (reviewed base × explicit rate, satang rounding) and corporate tax working papers. Corporate tax includes a profit bridge; the accountant enters annual tax before credits after applying the correct tax regime. Half-year uses half that annual tax; annual settlement deducts approved withholding credits and half-year tax paid. No default corporate/WHT rates, input-VAT eligibility, exchange rates or penalty calculations are inferred. Exclude mistaken supplier WHT rows from totals rather than deleting their history. Linked working-paper totals appear beside relevant filing tasks; they do not change declared/paid amounts.
- **Year-end close** tracks opening balances, complete bank statements, sales and supplier originals, WHT credits, fixed assets/inventory, adjustments, accountant review and signed audited accounts.

**Export accountant pack** downloads a ZIP for the selected company/year with complete source records as JSON, filing/close statuses, missing-document requests, calculation worksheets, supplier WHT schedule, VAT working papers, invoice/payment/expense/bank CSVs, saved invoice/receipt PDFs, expense originals, source bank statements, supporting files, a readable HTML summary and a SHA-256 manifest. Worksheet amounts use THB; source columns ending `_minor` use integer currency units (THB 100, JPY 1, KWD 1000 units per major unit). Original statements and related invoice/payment history can span outside the selected period for context, but another company's records are excluded. Drafts without saved PDFs are exported as records, not final documents. The pack is for accountant review, not an official RD Prep import file or audited accounts.

Baseline deadlines exclude holiday changes and online filing extensions. Check the official calendar and enter the verified deadline. DBD/shareholder-list deadlines depend on the actual AGM and are left for confirmation. Profiles, worksheets, tasks and evidence are included in normal database backups; older backups without compliance tables remain supported.

## Scan evidence directly from a transaction

In **Journal**, open **Documents** on a bank transaction and choose a PDF, PNG or JPEG. Scanning starts immediately. A single review screen shows the original pages, extracted fields, bank amount, classification and compatible existing expense/receipt links. Correct the fields, select an existing record, create a new supplier expense for money out, or keep the file as bank evidence. Choose **Save & mark reviewed**, or **Save for later** when details are incomplete. Classification is required to mark a bank transaction reviewed. Separate bank evidence enters overview cash flow only when explicitly included; linked payments use the existing count-once rules.

Uploads to an expense’s **Documents & payments** use the same scanner and add evidence to that expense. The saved financial details of an existing expense or invoice are not overwritten by attachment fields. Use **Review** beside a saved file to scan/review it again; reviewed fields are retained. PDF previews show up to ten scanned pages, with an 8 MB image preview limit; extracted text covers all scanned pages. Files remain limited to 20 MB and supplier-document PDFs to ten pages.

A document detected as belonging to another saved company is blocked from attachment to the original record. Choose **Save as separate expense** in the same screen: the current file and scanned fields are retained, the detected company is selected, and the new expense is saved without changing or linking the original bank row. Exact duplicates must reuse the suggested saved expense; the file is attached once. You can also use this action for an unrelated document belonging to the same company. Supplier documents attached to money-in rows and document dates later than bank dates are flagged for review; scanning does not establish that a file supports a bank movement. The existing-record option appears only when compatible records are available.

The document, reviewed fields and bank link save together. A failure rolls back the whole save; retrying a completed request reuses its result. Exact expense matches must be linked, while nearby supplier/amount matches require explicit confirmation before another expense can be created. Incoming funds never automatically generate an expense, invoice or receipt. The accountant pack includes **document-reviews.csv**, reviewed metadata and all saved originals.

## One expense, linked payments and evidence

Keep the supplier invoice as the main expense. In a bank transaction, use **Link an expense or existing receipt** to connect its payment. The expense can be unpaid when linked, and several bank transactions can cover partial payments on different dates. Company and currency must agree; combined bank payments cannot exceed recorded cash paid (when present) or the document total. One bank transaction links to one expense; splitting a bank payment across several expenses is not supported.

**Expenses & payables** lists supplier documents with payment status filters and **Documents & payments** for originals, receipts and bank evidence. **Journal** lists every bank movement, including linked ones, with direct links to its expense. From an expense, open a linked payment to return to Journal. Both pages use the same saved records and links; no payable document is generated. The older `#payables` URL opens Expenses & payables. Reviewed linked payments count in cash flow on their bank dates and replace the corresponding manual expense cash; only any remaining manual cash is added separately. Linking never infers VAT, withholding or deductibility.

When uploading another supplier document, a matching file, supplier/document number, or nearby supplier/amount raises a duplicate warning. Choose **Add file to this expense** to retain it as evidence without creating another expense. If two saved expense records refer to the same transaction, edit the duplicate and choose **Keep one expense**. Both originals remain saved; bank links move to the primary record and the duplicate is excluded from totals. Choose the paid record as primary when only one has manual payment details. The Duplicate documents filter on Expenses & payables allows restoring a record as a separate expense. Nothing is merged automatically; confirm separate expense/payment checkboxes only for genuinely different transactions.

## Company switcher

Select **Company** above **Quit LocalBooks** in the sidebar. The choice is remembered on this Mac and controls Overview, Invoices, Expenses & payables, Journal, Compliance, Customers, Suppliers, and Products & services. New invoices, expense uploads, contacts and services use the selected company. Page-level company filters are removed. Opening a saved record from another company changes the active company so the header context agrees with the record. Save or close an open editor before switching; finish or discard a pending bank import first.

Existing contacts and services follow their linked document company. Unused older records start under the first saved company. New records are assigned explicitly. **Used by companies** in a contact/service editor allows intentional reuse across company lists. A supplier saved for one company is not automatically reused for another unless shared. Removing a shared service from one company leaves its other company assignment intact. Historical invoice and receipt snapshots stay as issued.

**My companies** manages all profiles. **Backup & restore** covers the entire LocalBooks database, including every company; use the company-specific accountant pack for a company/year handover.

## Data and backups

The durable database is `~/Library/Application Support/LocalBooks/data/localbooks.sqlite3`, including attachments and saved PDF/HTML snapshots. The app installs its bundled runtime under `~/Library/Application Support/LocalBooks/app`. Moving or deleting the app bundle does not remove your data. Use **Backup & restore → Download backup** regularly and keep a copy on another drive.

Restore replaces the current records after validating the backup, and first saves the previous database under `~/Library/Application Support/LocalBooks/data/safety-copies/Before-restore-….sqlite3`. Numbers already issued on this Mac remain reserved for matching companies, even after restoring an older backup. No ZIP extraction or external file paths are trusted. Attachments are copied into the database, so moving the original file does not break them. Files can be downloaded again from their document. Maximum attachment size: 20 MB each. In-app restore limit: 100 MB; backup export has no app size limit.

For a larger backup, quit the app and use the optional offline helper:

```sh
cd "/path/to/LocalBooks"
/opt/homebrew/bin/python3.11 maintenance.py restore "/path/to/backup.sqlite3" --data "$HOME/Library/Application Support/LocalBooks/data" --confirm RESTORE
```

Backups are not encrypted by the app. Keep them private; macOS disk encryption and your chosen storage location determine protection at rest. There is no cloud sync, external document upload, analytics, external integration, invoice sending, or automatic payment feature.

## Template and amount rules

Invoices and receipts use a black-and-white A4 layout, consistent Arial/Thai typography, and English and Thai headings. The editor and exported documents share `static/document.css` for aligned metadata, parties, totals and signatures. Receipt exports are limited to one A4 page: compact spacing and modest scaling keep all fields together. If the content still cannot fit at a readable size, recording the payment is rejected before anything is saved; shorten the details and try again. Receipts place the bank account beside the totals and show payment history in a full-width table, with light gray metadata and bank panels shared by both invoice and receipt templates. They include company and customer details, tax IDs and branch labels when entered, item codes and units, and an optional service period. Invoices can also show a reference, payment instructions and notes. Receipts show the payment amount and withholding separately, the amount settled, and payment history. In My companies, save a bank name, account holder, account number, optional branch and SWIFT/BIC for each company. You can also enter a Thai/additional legal company name. These appear on draft previews, newly issued invoices and new receipts; account numbers keep their leading zeros. Existing issued snapshots are preserved. Company payment instructions are optional and may be changed for a draft. Issued PDFs remain unchanged.

Supported currencies: THB, USD, EUR, GBP, SGD, AUD, CAD, JPY, KWD. Amounts use decimal arithmetic: quantity × unit price is rounded half-up once per line, then integer minor units are summed. Payment amounts must use the currency precision (JPY 0, KWD 3, others 2 decimals). Currency totals are not mixed. No exchange rates are applied.

For VAT-registered sellers, you may enter an explicit VAT rate on an invoice. The VAT % column shows the entered invoice rate; the Amount before VAT column shows each tax-exclusive line total. Item prices exclude VAT; the app calculates VAT on the subtotal with half-up rounding. You may explicitly choose a full tax invoice only for THB, after confirming the full amount has reached the VAT tax point; the seller must have its address, 13-digit tax ID, and branch recorded. A VAT-registered customer also needs a 13-digit tax ID and branch. The app does not determine tax treatment or the VAT tax point, support tax invoices for partial tax points, or issue credit notes or withholding certificates. Invoices and receipts prefill both signature blocks with the company/customer names, contact people when recorded, and the document date. In My companies, optionally upload a PNG or JPEG signature to display on newly issued invoices and receipts; changing it never rewrites issued PDFs. This is an image, not a verified digital signature. Discount fields are not supported. Have your accountant review the documents for your circumstances. There is no payroll or general ledger.

## Development and maintenance

The downloadable app includes its own tools. Owners can install it from the release page without developer setup. To change the code, run from source or build a custom app, see [DEVELOPMENT.md](DEVELOPMENT.md).

Local logs are in `~/Library/Application Support/LocalBooks/data/app.log`. Keep logs and backups private. Download a backup before updating your app.

Automated checks:

```sh
/opt/homebrew/bin/python3.11 -m unittest discover -s tests -v
node tests/test_editor.mjs
node tests/test_overview.mjs
node tests/test_reconciliation.mjs
node tests/test_company_context.mjs
node tests/test_routes.mjs
node tests/test_compliance_ui.mjs
# Optional native launch/reopen check, using an isolated temporary profile:
/opt/homebrew/bin/python3.11 tests/smoke_desktop_instance.py
```

Generated verification outputs belong under ignored `output/`. Automated tests use isolated temporary databases. The public repository includes no business records.

## Live development

Save any open draft and quit the running app first. Start the source version with:

```sh
/opt/homebrew/bin/python3.11 dev.py
```

It uses the same local data, watches Python files and restarts the server after backend changes. Browser pages refresh when frontend or backend files change. If an editor or dialog is open, refresh waits until you save and leave it, so unsaved input is not discarded. Quit LocalBooks in the sidebar or press Ctrl-C in the terminal to stop development mode. The packaged app remains a separate, fixed version; rebuild it with `./build_app.sh` when you want to update that version.

Customers and suppliers have separate pages and document pickers. Set a contact’s Type to Customer, Supplier, or Both customer and supplier. Expense uploads create suppliers. Older contacts are classified from linked sales invoices and expense documents; unused older contacts default to customers. You can change the type in Edit details. Issued documents keep their original details.

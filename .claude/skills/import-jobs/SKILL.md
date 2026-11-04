---
name: import-jobs
description: Bring the owner's existing jobs, hours or materials into jobs.db from a spreadsheet or CSV export (Excel, Google Sheets, a field-service app's export). Use when they say "import", "bring in my jobs", or hand you a file.
---

# Importing a spreadsheet

1. **Look before you load.**
   - Read the file's header row and about 10 rows.
   - Tell the owner which columns you'll use for what: customer, job title, dates, quoted amount, hours by tech, materials and cost, billed amounts.
   - Ask about anything ambiguous, such as whether "Amount" means billed or cost.
2. **Back up** first: `python -m qbjobs backup`.
3. **Write a one-off script** in `scripts/import_<name>.py`. It uses the functions in `qbjobs/db.py` (`new_job`, `add_labor`, `add_material`, `add_bill_line`, `mark_done`), never raw inserts into money columns. Convert money with `db.cents()`.
   - **Customers:** match by name to existing customers (case-insensitive). List any new ones before creating them.
   - **Techs:** an unknown tech name stops the import. Ask for that tech's loaded cost.
   - **Jobs:** keep the source's job number with `number=` if it has one. Skip rows already imported (same number).
4. **Dry run.** First run the script so it only prints what it would create: how many jobs, customers and hours, and the total billed. The owner checks those totals against the spreadsheet.
5. **Load**, then run `python -m qbjobs report` and compare the totals to the spreadsheet again.
6. **Don't send old jobs to QuickBooks.** Jobs already invoiced in QuickBooks should be linked, not re-sent:
   - run `send --dry-run`
   - then a normal `send`, which finds the invoice by its number (DocNumber) and links it
   - if their old invoice numbers differ from the job numbers, ask before doing anything

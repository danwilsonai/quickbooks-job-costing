---
name: customize
description: Make a change the owner asks for - a new field (job type, crew, equipment, lead source), a new report or column, a change to the job board or the invoice. Use for any "can it also track...", "add a column", "change the report" request.
---

# Making a change safely

1. **Restate the request in one sentence** and say where it will show up: the job board, the report, or the invoice. Ask only what you can't decide yourself, such as which job types they use.
2. **Back up the data:** `python -m qbjobs backup`.
3. **Database.** For a new field, add an `ALTER TABLE ... ADD COLUMN` in `db.connect()`. Guard it so it runs only when the column is missing (check `PRAGMA table_info`). Existing jobs keep their data.
4. **Wire it through:**
   - the function in `db.py` that writes it
   - the CLI option in `cli.py`
   - the form and the display in `templates/`
   - the report in `report.py`, if they want it there (for example, margin by job type)
5. **QuickBooks.** If the change affects the invoice (`sync.invoice_payload`), show them a `send --dry-run` preview.
   - Never widen what this tool writes to QuickBooks (see CLAUDE.md rule 1).
   - Reading more from QuickBooks is fine.
6. **Tests:**
   - Add a test in `tests/` for anything with money or QuickBooks in it.
   - Run `python -m pytest -q`.
7. **Show them.** Restart `python -m qbjobs serve` and point at where the change appears. If it's a report change, run the report on their data.

Common ones:
- **Job type or trade on each job, and margin by type:** a column on `jobs`, a select on the new-job form, and a group-by in `report.py`.
- **Estimate vs actual hours:** a `quoted_hours` column, with the variance shown on the job page and the report.
- **Overhead per hour:** a setting plus a cost line in `job_costs`. Label it clearly, because it turns gross profit into net.
- **Import from their field app's export:** see the import-jobs skill.

# QuickBooks Job Costing

A free job record that sits next to QuickBooks Online. You build and change it with Claude Code by asking in plain English.

It does three things QuickBooks is bad at for a service business:

1. **One record per job.** The customer, what you quoted, every tech's hours, the materials, and what you're billing, all in one place.
2. **Job to invoice, once.** When a job is done, it becomes a QuickBooks invoice with no re-typing. If you run it twice, you still get one invoice.
3. **Job costing.** What each job actually made after labor and materials, with the ones under your target margin in red.

QuickBooks stays your books. This tool can create invoices and customers in QuickBooks, and that's all it can write. It never edits, voids or deletes anything, and it never touches journal entries, bills, payments or payroll. That limit is in the code (`ALLOWED_WRITES` in `qbjobs/qbo.py`), not just in this README.

![The job costing page](docs/report.png)

## Who it's for

Owners of service companies, roughly 5 to 50 people (HVAC, plumbing, electrical, and the like). You run QuickBooks Online, track jobs in spreadsheets or your head, and want to know which jobs make money before you spend $40K a year on an ERP.

It's a starting point, not a finished product. The idea is that you open it in Claude Code and have it reshaped to how your company works, for example: "add a job type and show me margin by type" or "import my jobs from this spreadsheet."

## What it costs

- **This repo:** free (MIT license).
- **Claude Code:** your own Claude plan. You pay Anthropic directly; nothing goes through me.
- **Intuit developer account:** free. It comes with a sandbox company, which is a practice QuickBooks company you can't hurt.

## Start here (about 15 minutes)

You need Python 3.10 or newer and [Claude Code](https://claude.com/claude-code).

```bash
git clone https://github.com/danwilsonai/quickbooks-job-costing.git
cd quickbooks-job-costing
claude
```

Then tell Claude: **"Set this up for me."** It follows `.claude/skills/setup/SKILL.md` and walks you through installing, loading a made-up HVAC company to play with, creating your free Intuit developer account, connecting the sandbox, and sending your first test invoice.

Prefer to do it yourself?

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m qbjobs demo        # a made-up HVAC company
.venv/bin/python -m qbjobs report      # job costing in the terminal
.venv/bin/python -m qbjobs serve       # the job board at http://localhost:5050
```

To connect QuickBooks, copy `.env.example` to `.env`, add your Intuit app's keys, and run `python -m qbjobs connect`.

## Before you point it at your real books

- **Start on the sandbox.** Run your own jobs through it for a week or two before switching `QBO_ENV` to `production`.
- **On live books:** every invoice needs a preview (`send --dry-run`) and an explicit `--confirm`. The job board shows LIVE BOOKS in red.
- **Your connection is a key to your books.** The sign-in file (`.qbo-tokens.json`) and your data (`jobs.db`) stay on your computer and are git-ignored. Back up `jobs.db` like any other business file.
- **Your numbers are yours.** Job costing is only as good as the loaded labor cost you enter for each tech (wage plus payroll taxes, workers' comp and benefits). Ask your bookkeeper if you're not sure.
- **No warranty.** This is not accounting or tax advice. Have your accountant look at anything that changes how you invoice.

## How it's built

Plain Python, one SQLite file, and a small Flask page that only listens on your own computer. No cloud service, no account with me.

| Folder | What's there |
|---|---|
| `qbjobs/` | the code: database, QuickBooks connection, invoice sync, job costing, commands, web page |
| `templates/` | the job board pages |
| `.claude/skills/` | step-by-step instructions Claude Code follows: setup, customize, import-jobs |
| `CLAUDE.md` | the rules Claude Code works under in this repo |
| `tests/` | tests that use a fake QuickBooks, so they run without an account |

## Who made this

I'm Dan Wilson. I build custom software and AI tools for owner-run companies at [Bedrock Process](https://bedrockprocess.com). I explain how this works, and when an ERP is and isn't worth it, on [YouTube](https://www.youtube.com/@bedrockprocess).

If you get stuck, open an issue. If you'd rather have it set up and shaped to your company for you, that's what [Bedrock Process](https://bedrockprocess.com) does.

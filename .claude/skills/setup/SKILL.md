---
name: setup
description: Walk the owner through first-time setup - install, the demo, an Intuit developer account, connecting the QuickBooks sandbox, and a first test invoice. Use when they say "set this up", "connect QuickBooks", or are starting fresh.
---

# First-time setup

Assume the owner has never used a terminal. Go one step at a time. After each step, tell them in plain words what happened and what's next. Never ask them to paste a secret into the chat: `python -m qbjobs keys` opens the settings file in their text editor and they paste keys there.

Before the first step, tell them: "I'll ask your permission before I run anything on your computer. Read what I'm about to do, and say yes if it makes sense."

1. **Install.**
   - On Windows, the Claude app's Code tab needs Git. If `git --version` fails, have them install Git for Windows from git-scm.com, then restart the Claude app.
   - Check that Python 3.10+ is installed (`python3 --version`, or `py --version` on Windows). If it isn't, tell them how to install it for their system (python.org; on Windows, tick "Add python.exe to PATH").
   - Make a virtual environment and install the requirements:
     - `python3 -m venv .venv`
     - then `.venv/bin/pip install -r requirements.txt` (on Windows: `.venv\Scripts\pip`)
   - Use that Python for every command after this.
2. **Try the demo.**
   - Run `python -m qbjobs demo`, then `python -m qbjobs report`.
   - Explain the columns using one real row. The mini-split job (J-1005) is the one under target: it was quoted at 18 hours and took 30.
   - Then run `python -m qbjobs shortcut`, which puts a "Job Board" icon on their desktop. Have them double-click it: the board opens in their browser. Explain that the black window it opens has to stay open while they use the board, and closing it stops the board.
3. **Intuit developer account (free).** Have them do this in their browser:
   - Sign up or sign in at developer.intuit.com with their own email.
   - Go to Dashboard, then create an app for QuickBooks Online and the Accounting scope. Intuit creates a sandbox company automatically.
   - Under the app's development keys, copy the Client ID and Client Secret.
   - In the same place, add the redirect URI `http://localhost:8000/callback`.
   - If Intuit's screens look different from this, describe what to look for (development or sandbox keys, redirect URIs) rather than guessing button names.
4. **Keys.**
   - Run `python -m qbjobs keys`. It creates `.env` and opens it in their text editor. They paste the Client ID and Secret after the `=` signs, save, and close it.
   - Leave `QBO_ENV=sandbox`.
5. **Connect.**
   - Run `python -m qbjobs connect`. A browser opens, they sign in, and they pick the sandbox company.
   - Run `python -m qbjobs status` to confirm the company name.
6. **First invoice in the sandbox.**
   - Run `python -m qbjobs items pull` and `python -m qbjobs items`, then help them pick the default item. A general "Services" item works: `python -m qbjobs set default_item ID`.
   - Run `python -m qbjobs customers push all`. This adds the demo customers to the sandbox.
   - Run `python -m qbjobs send J-1004 --dry-run` and read the preview with them.
   - Run `python -m qbjobs send J-1004`.
   - Have them open the sandbox company in QuickBooks and find invoice J-1004.
   - Run `python -m qbjobs refresh` and `python -m qbjobs report`. J-1004's revenue now comes from QuickBooks.
7. **Their own numbers.**
   - Ask for each tech's loaded hourly cost: wage plus payroll taxes, workers' comp and benefits. Add them with `techs add`.
   - Ask for their target margin: `set target_margin 0.40`.
   - **Sales tax.** Ask whether they charge sales tax on any of their invoices (often parts and equipment, depending on the state). If they do:
     - run `set sales_tax on`;
     - run `taxes`. If it says the company uses automated sales tax, nothing more. If it lists rates instead, ask which one their invoices use and `set sales_tax_rate ID` (without it QuickBooks charges $0 tax);
     - mark those lines "taxable" (the checkbox on the job page, or `--taxable`);
     - send a test job to the sandbox and have them check the tax on the invoice in QuickBooks with their bookkeeper before going live.
   - **Where the data lives.** All their jobs are in one file, `jobs.db`. Offer to keep it inside their Dropbox, OneDrive or Google Drive folder, so a dead laptop doesn't take their job history: set `JOBS_DB=<that folder>/jobs.db` in `.env`. Only one computer should run the board against that file at a time.
   - **It's one computer's tool.** Tell them plainly: techs can't log hours from their phones with this; someone in the office enters hours from timesheets. If they need a field app, that's a different product.
   - Delete the demo with `rm jobs.db`, after backing it up if they want to keep it. Then pull their sandbox customers, or import jobs (see import-jobs).

## Going live, only when they ask

- They need production keys from the same Intuit app. Intuit issues them only after the app's production settings are filled in, even for a private app only they use. Walk them through it in their developer dashboard:
  - HTTPS links to an end-user license agreement and a privacy policy, plus the app's host domain, launch URL and disconnect URL. They can use the generic pages at https://bedrockprocess.com/quickbooks-job-costing/, which lists exactly what goes in each field, or their own pages on a site they control.
  - The app assessment questionnaire. For a private app connecting only their own company, the honest answers are short: it's private, used by the owner's staff, it creates invoices and customers, and it stores the sign-in on their computer.
  - Don't invent answers about their company's compliance history; ask them.
- Intuit doesn't accept `localhost` as a redirect for production keys. Use Intuit's OAuth Playground redirect:
  - Add `https://developer.intuit.com/v2/OAuth2Playground/RedirectUrl` to the production redirect URIs.
  - Set it as `QBO_REDIRECT_URI` in `.env`.
  - Connect with `python -m qbjobs connect --paste`.
- Set `QBO_ENV=production`. The job board shows LIVE BOOKS in red.
- From then on, every invoice gets `send --dry-run` first, and `--confirm` only after the owner reads it.

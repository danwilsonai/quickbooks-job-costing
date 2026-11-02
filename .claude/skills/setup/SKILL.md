---
name: setup
description: Walk the owner through first-time setup - install, the demo, an Intuit developer account, connecting the QuickBooks sandbox, and a first test invoice. Use when they say "set this up", "connect QuickBooks", or are starting fresh.
---

# First-time setup

Go one step at a time. After each step, tell the owner what happened and what's next. Never ask them to paste a secret into the chat: they type keys into `.env` themselves.

1. **Install.**
   - Check that Python 3.10+ is installed (`python3 --version`). If it isn't, tell them how to install it for their system.
   - Make a virtual environment and install the requirements:
     - `python3 -m venv .venv`
     - then `.venv/bin/pip install -r requirements.txt` (on Windows: `.venv\Scripts\pip`)
   - Use that Python for every command after this.
2. **Try the demo.**
   - Run `python -m qbjobs demo`, then `python -m qbjobs report`.
   - Explain the columns using one real row. The mini-split job (J-1005) is the one under target: it was quoted at 18 hours and took 30.
   - Then run `python -m qbjobs serve` and have them open http://localhost:5050.
3. **Intuit developer account (free).** Have them do this in their browser:
   - Sign up or sign in at developer.intuit.com with their own email.
   - Go to Dashboard, then create an app for QuickBooks Online and the Accounting scope. Intuit creates a sandbox company automatically.
   - Under the app's development keys, copy the Client ID and Client Secret.
   - In the same place, add the redirect URI `http://localhost:8000/callback`.
   - If Intuit's screens look different from this, describe what to look for (development or sandbox keys, redirect URIs) rather than guessing button names.
4. **Keys.**
   - Copy `.env.example` to `.env`. They paste the Client ID and Secret into it themselves.
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
   - Delete the demo with `rm jobs.db`, after backing it up if they want to keep it. Then pull their sandbox customers, or import jobs (see import-jobs).

## Going live, only when they ask

- They need production keys from the same Intuit app. Intuit asks some questions about the app before it issues them.
- Intuit doesn't accept `localhost` as a redirect for production keys. Use Intuit's OAuth Playground redirect:
  - Add `https://developer.intuit.com/v2/OAuth2Playground/RedirectUrl` to the production redirect URIs.
  - Set it as `QBO_REDIRECT_URI` in `.env`.
  - Connect with `python -m qbjobs connect --paste`.
- Set `QBO_ENV=production`. The job board shows LIVE BOOKS in red.
- From then on, every invoice gets `send --dry-run` first, and `--confirm` only after the owner reads it.

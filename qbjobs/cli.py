"""Command line: python -m qbjobs <command>. Run python -m qbjobs -h for the list."""
import argparse
import http.server
import json
import sys
import threading
import urllib.parse
import webbrowser

from . import config, db, qbo, report, sync


def _conn():
    return db.connect()


def _client():
    return qbo.QBO()


def _print(obj):
    print(json.dumps(obj, indent=2, default=str))


# ---- setup ----------------------------------------------------------------------------------------------

def cmd_demo(a):
    """Load the made-up HVAC company from sample/demo.json."""
    conn = _conn()
    if conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] and not a.force:
        sys.exit("jobs.db already has jobs. Use --force to add the demo anyway, or delete jobs.db to start over.")
    data = json.loads((config.ROOT / "sample" / "demo.json").read_text())
    for k, v in data["settings"].items():
        db.set_setting(conn, k, v)
    techs = {}
    for t in data["techs"]:
        conn.execute("INSERT OR IGNORE INTO techs (name, cost_rate) VALUES (?, ?)", (t["name"], db.cents(t["cost_rate"])))
        techs[t["name"]] = conn.execute("SELECT id FROM techs WHERE name = ?", (t["name"],)).fetchone()["id"]
    custs = {}
    for c in data["customers"]:
        row = conn.execute("SELECT id FROM customers WHERE name = ?", (c["name"],)).fetchone()
        custs[c["name"]] = row["id"] if row else conn.execute(
            "INSERT INTO customers (name, email) VALUES (?, ?)", (c["name"], c.get("email"))).lastrowid
    conn.commit()
    for j in data["jobs"]:
        job = db.new_job(conn, custs[j["customer"]], j["title"], db.cents(j["quoted"]) if j.get("quoted") else None,
                         created_at=j.get("created_at"))
        if j.get("notes"):
            conn.execute("UPDATE jobs SET notes = ? WHERE id = ?", (j["notes"], job["id"]))
        for name, hours in j["labor"]:
            db.add_labor(conn, job["id"], techs[name], hours, j.get("completed_at") or j.get("created_at"))
        for desc, qty, cost in j["materials"]:
            db.add_material(conn, job["id"], desc, qty, db.cents(cost))
        for desc, qty, price in j["bill"]:
            db.add_bill_line(conn, job["id"], desc, qty, db.cents(price))
        if j.get("completed_at"):
            db.mark_done(conn, job["id"], j["completed_at"])
    print(f"Loaded {len(data['jobs'])} demo jobs. Try: python -m qbjobs report")


def cmd_connect(a):
    """Sign in to QuickBooks in your browser and save the connection."""
    state = qbo.new_state()
    url = qbo.auth_url(state)
    redirect = urllib.parse.urlparse(config.REDIRECT_URI)
    if a.paste:
        print("Open this link, sign in, pick your company, then paste the full address you land on:\n\n" + url + "\n")
        q = urllib.parse.parse_qs(urllib.parse.urlparse(input("Address: ").strip()).query)
        return _finish_connect(q, state)
    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path != redirect.path:
                self.send_response(404)
                self.end_headers()
                return
            result.update(urllib.parse.parse_qs(u.query))
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<p>Connected. You can close this tab and go back to the terminal.</p>")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer((redirect.hostname, redirect.port or 80), Handler)
    print(f"Opening QuickBooks sign-in ({config.QBO_ENV}). If no browser opens, use this link:\n\n{url}\n")
    threading.Timer(0.5, webbrowser.open, (url,)).start()
    while not result:
        server.handle_request()
    server.server_close()
    _finish_connect(result, state)


def _finish_connect(q, state):
    if q.get("error"):
        sys.exit(f"QuickBooks said: {q['error'][0]}")
    if q.get("state", [""])[0] != state:
        sys.exit("The sign-in response didn't match this request, so it was ignored. Run connect again.")
    qbo.exchange_code(q["code"][0], q["realmId"][0])
    print(f"Connected to {qbo.QBO().company_name()} ({config.QBO_ENV}).")


def cmd_disconnect(a):
    qbo.disconnect()
    print("Removed the saved QuickBooks connection from this computer.")


def cmd_status(a):
    conn = _conn()
    jobs = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}
    try:
        company = _client().company_name()
    except qbo.QBOError as e:
        company = f"not connected ({e})"
    _print({"environment": config.QBO_ENV, "quickbooks_company": company, "jobs": jobs,
            "default_item": db.get_setting(conn, "default_item"), "target_margin": db.get_setting(conn, "target_margin")})


def cmd_set(a):
    db.set_setting(_conn(), a.key, a.value)
    print(f"{a.key} = {a.value}")


# ---- customers, items, techs -------------------------------------------------------------------------

def cmd_customers(a):
    conn = _conn()
    if a.action == "pull":
        print(f"Pulled {sync.pull_customers(conn, _client())} customers from QuickBooks.")
    elif a.action == "push":
        client = _client()
        ids = [r["id"] for r in conn.execute("SELECT id FROM customers WHERE qbo_id IS NULL")] if a.id == "all" else [int(a.id)]
        for cid in ids:
            print(f"customer {cid} -> QuickBooks {sync.push_customer(conn, client, cid)}")
    elif a.action == "add":
        cid = conn.execute("INSERT INTO customers (name, email, phone) VALUES (?, ?, ?)", (a.name, a.email, a.phone)).lastrowid
        conn.commit()
        print(f"Added customer {cid}: {a.name} (not in QuickBooks until: customers push {cid})")
    else:
        for r in conn.execute("SELECT * FROM customers ORDER BY name"):
            print(f"{r['id']:>4}  {r['name']:<35} {'QuickBooks ' + r['qbo_id'] if r['qbo_id'] else 'local only'}")


def cmd_items(a):
    conn = _conn()
    if a.action == "pull":
        print(f"Pulled {sync.pull_items(conn, _client())} items from QuickBooks.")
    for r in conn.execute("SELECT * FROM items ORDER BY name"):
        print(f"{r['qbo_id']:>5}  {r['name']:<35} {r['type'] or '':<12} {db.dollars(r['unit_price'])}")


def cmd_techs(a):
    conn = _conn()
    if a.action == "add":
        conn.execute("INSERT INTO techs (name, cost_rate) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET cost_rate = excluded.cost_rate",
                     (a.name, db.cents(a.cost_rate)))
        conn.commit()
    for r in conn.execute("SELECT * FROM techs ORDER BY name"):
        print(f"{r['id']:>4}  {r['name']:<20} {db.dollars(r['cost_rate'])}/hour")


# ---- jobs ------------------------------------------------------------------------------------------------

def cmd_job(a):
    conn = _conn()
    if a.action == "new":
        job = db.new_job(conn, a.customer, a.title, db.cents(a.quoted) if a.quoted else None, a.address)
        print(f"Created {job['number']}")
    elif a.action == "list":
        for j in conn.execute("SELECT j.*, c.name customer FROM jobs j JOIN customers c ON c.id = j.customer_id ORDER BY j.id"):
            print(f"{j['number']:<8} {j['status']:<9} {j['customer']:<22} {j['title']}")
    elif a.action == "show":
        job = db.get_job(conn, a.job)
        out = dict(job)
        out["labor"] = [dict(r) for r in conn.execute("SELECT l.work_date, t.name tech, l.hours FROM labor l JOIN techs t ON t.id = l.tech_id WHERE job_id = ?", (job["id"],))]
        out["materials"] = [dict(r) for r in conn.execute("SELECT description, qty, unit_cost FROM materials WHERE job_id = ?", (job["id"],))]
        out["bill_lines"] = [dict(r) for r in conn.execute("SELECT description, qty, unit_price, qbo_item_id FROM bill_lines WHERE job_id = ?", (job["id"],))]
        out["costing"] = report.job_costs(conn, job)
        _print(out)
    elif a.action == "labor":
        tech = conn.execute("SELECT id FROM techs WHERE name = ? COLLATE NOCASE OR id = ?", (a.tech, a.tech)).fetchone()
        if not tech:
            sys.exit(f"No tech {a.tech!r}. Add one: python -m qbjobs techs add NAME COST_PER_HOUR")
        db.add_labor(conn, db.get_job(conn, a.job)["id"], tech["id"], a.hours, a.date)
        print("Logged.")
    elif a.action == "material":
        db.add_material(conn, db.get_job(conn, a.job)["id"], a.description, a.qty, db.cents(a.unit_cost))
        print("Added.")
    elif a.action == "bill":
        db.add_bill_line(conn, db.get_job(conn, a.job)["id"], a.description, a.qty, db.cents(a.unit_price), a.item)
        print("Added.")
    elif a.action == "done":
        db.mark_done(conn, db.get_job(conn, a.job)["id"], a.date)
        print("Marked done.")


def cmd_send(a):
    conn = _conn()
    refs = [r["number"] for r in conn.execute("SELECT number FROM jobs WHERE status = 'done' ORDER BY id")] if a.job == "all" else [a.job]
    client = None if a.dry_run else _client()
    failed = False
    for ref in refs:
        try:
            _print(sync.send_job(conn, client, ref, dry_run=a.dry_run, confirm=a.confirm))
        except (sync.SyncError, qbo.QBOError) as e:
            failed = True
            print(f"{ref}: {e}", file=sys.stderr)
    sys.exit(1 if failed else 0)


def cmd_refresh(a):
    _print(sync.refresh_invoices(_conn(), _client()))


def cmd_unlink(a):
    sync.unlink_invoice(_conn(), a.job)
    print(f"{a.job} is unlinked and can be sent again. Only do this if you deleted its invoice in QuickBooks.")


def cmd_report(a):
    rep = report.job_report(_conn(), a.status, a.since)
    print(report.as_csv(rep) if a.csv else report.as_text(rep))


def cmd_serve(a):
    from .web import create_app
    print(f"Open http://localhost:{a.port}  (Ctrl+C to stop)")
    create_app().run(host="127.0.0.1", port=a.port)


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m qbjobs", description="Job records and job costing around QuickBooks Online.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("demo", help="load a made-up HVAC company to try things out")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_demo)
    s = sub.add_parser("connect", help="sign in to QuickBooks")
    s.add_argument("--paste", action="store_true", help="paste the redirect address instead of catching it locally")
    s.set_defaults(fn=cmd_connect)
    sub.add_parser("disconnect", help="forget the saved QuickBooks sign-in").set_defaults(fn=cmd_disconnect)
    sub.add_parser("status", help="which company you're connected to, and job counts").set_defaults(fn=cmd_status)
    s = sub.add_parser("set", help="change a setting (default_item, target_margin, job_prefix, job_start)")
    s.add_argument("key")
    s.add_argument("value")
    s.set_defaults(fn=cmd_set)

    s = sub.add_parser("customers", help="list | pull | push ID|all | add NAME")
    s.add_argument("action", nargs="?", default="list", choices=["list", "pull", "push", "add"])
    s.add_argument("id", nargs="?", help="customer id, or 'all' (push)")
    s.add_argument("--name")
    s.add_argument("--email")
    s.add_argument("--phone")
    s.set_defaults(fn=cmd_customers)
    s = sub.add_parser("items", help="list | pull your QuickBooks products and services")
    s.add_argument("action", nargs="?", default="list", choices=["list", "pull"])
    s.set_defaults(fn=cmd_items)
    s = sub.add_parser("techs", help="list | add NAME COST_PER_HOUR")
    s.add_argument("action", nargs="?", default="list", choices=["list", "add"])
    s.add_argument("name", nargs="?")
    s.add_argument("cost_rate", nargs="?")
    s.set_defaults(fn=cmd_techs)

    s = sub.add_parser("job", help="new | list | show | labor | material | bill | done")
    jsub = s.add_subparsers(dest="action", required=True)
    j = jsub.add_parser("new")
    j.add_argument("--customer", type=int, required=True)
    j.add_argument("--title", required=True)
    j.add_argument("--quoted")
    j.add_argument("--address")
    jsub.add_parser("list")
    j = jsub.add_parser("show")
    j.add_argument("job")
    j = jsub.add_parser("labor")
    j.add_argument("job")
    j.add_argument("tech")
    j.add_argument("hours", type=float)
    j.add_argument("--date")
    j = jsub.add_parser("material")
    j.add_argument("job")
    j.add_argument("description")
    j.add_argument("qty", type=float)
    j.add_argument("unit_cost")
    j = jsub.add_parser("bill")
    j.add_argument("job")
    j.add_argument("description")
    j.add_argument("qty", type=float)
    j.add_argument("unit_price")
    j.add_argument("--item", help="QuickBooks item id (default: the default_item setting)")
    j = jsub.add_parser("done")
    j.add_argument("job")
    j.add_argument("--date")
    s.set_defaults(fn=cmd_job)

    s = sub.add_parser("send", help="turn a finished job (or 'all') into a QuickBooks invoice")
    s.add_argument("job")
    s.add_argument("--dry-run", action="store_true", help="show the invoice without sending it")
    s.add_argument("--confirm", action="store_true", help="required on your live books")
    s.set_defaults(fn=cmd_send)
    sub.add_parser("refresh", help="pull invoice totals and balances back from QuickBooks").set_defaults(fn=cmd_refresh)
    s = sub.add_parser("unlink", help="after deleting a job's invoice in QuickBooks, allow sending it again")
    s.add_argument("job")
    s.set_defaults(fn=cmd_unlink)
    s = sub.add_parser("report", help="job costing: revenue, labor, materials, profit, margin")
    s.add_argument("--status", choices=["open", "done", "invoiced"])
    s.add_argument("--since", help="YYYY-MM-DD")
    s.add_argument("--csv", action="store_true")
    s.set_defaults(fn=cmd_report)
    s = sub.add_parser("serve", help="open the job board in your browser")
    s.add_argument("--port", type=int, default=5050)
    s.set_defaults(fn=cmd_serve)

    a = p.parse_args(argv)
    try:
        a.fn(a)
    except (qbo.QBOError, sync.SyncError, LookupError, ValueError) as e:
        sys.exit(str(e))

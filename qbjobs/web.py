"""The job board in your browser: python -m qbjobs serve, then open http://localhost:5050.

It only listens on this computer (127.0.0.1). Every form carries a token so another website can't
submit to it behind your back.
"""
import secrets

from flask import Flask, Response, abort, flash, redirect, render_template, request, session, url_for

from . import config, db, qbo, report, sync


def create_app(db_path=None) -> Flask:
    app = Flask(__name__, template_folder=str(config.ROOT / "templates"))
    app.secret_key = secrets.token_hex(32)
    app.jinja_env.globals.update(dollars=db.dollars, pct=report._pct, env=config.QBO_ENV)

    def conn():
        return db.connect(db_path)

    @app.before_request
    def check_token():
        session.setdefault("csrf", secrets.token_urlsafe(24))
        if request.method == "POST" and request.form.get("csrf") != session["csrf"]:
            abort(400, "This form expired. Go back, reload the page and try again.")

    def client():
        try:
            return qbo.QBO()
        except qbo.QBOError as e:
            flash(str(e), "error")
            return None

    @app.get("/")
    def board():
        c = conn()
        jobs = c.execute("SELECT j.*, cu.name customer FROM jobs j JOIN customers cu ON cu.id = j.customer_id "
                         "ORDER BY j.id DESC").fetchall()
        costs = {j["id"]: report.job_costs(c, j) for j in jobs}
        customers = c.execute("SELECT * FROM customers ORDER BY name").fetchall()
        return render_template("board.html", jobs=jobs, costs=costs, customers=customers)

    @app.post("/jobs")
    def new_job():
        c = conn()
        quoted = request.form.get("quoted", "").strip()
        job = db.new_job(c, int(request.form["customer_id"]), request.form["title"].strip(),
                         db.cents(quoted) if quoted else None, request.form.get("address") or None)
        return redirect(url_for("job_page", number=job["number"]))

    @app.get("/job/<number>")
    def job_page(number):
        c = conn()
        try:
            job = db.get_job(c, number)
        except LookupError:
            abort(404)
        return render_template(
            "job.html", job=job,
            customer=c.execute("SELECT * FROM customers WHERE id = ?", (job["customer_id"],)).fetchone(),
            labor=c.execute("SELECT l.*, t.name tech FROM labor l JOIN techs t ON t.id = l.tech_id WHERE job_id = ? ORDER BY l.id", (job["id"],)).fetchall(),
            materials=c.execute("SELECT * FROM materials WHERE job_id = ? ORDER BY id", (job["id"],)).fetchall(),
            lines=c.execute("SELECT b.*, i.name item FROM bill_lines b LEFT JOIN items i ON i.qbo_id = b.qbo_item_id WHERE job_id = ? ORDER BY b.id", (job["id"],)).fetchall(),
            techs=c.execute("SELECT * FROM techs ORDER BY name").fetchall(),
            items=c.execute("SELECT * FROM items ORDER BY name").fetchall(),
            costing=report.job_costs(c, job),
            preview=sync.send_job(c, None, number, dry_run=True) if job["status"] == "done" and not job["qbo_invoice_id"] else None,
        )

    @app.post("/job/<number>/<what>")
    def job_action(number, what):
        c = conn()
        job = db.get_job(c, number)
        f = request.form
        try:
            if what == "labor":
                db.add_labor(c, job["id"], int(f["tech_id"]), float(f["hours"]), f.get("work_date") or None)
            elif what == "material":
                db.add_material(c, job["id"], f["description"].strip(), float(f["qty"]), db.cents(f["unit_cost"]))
            elif what == "bill":
                db.add_bill_line(c, job["id"], f["description"].strip(), float(f["qty"]), db.cents(f["unit_price"]),
                                 f.get("qbo_item_id") or None)
            elif what == "done":
                db.mark_done(c, job["id"])
            elif what == "send":
                q = client()
                if q:
                    res = sync.send_job(c, q, number, confirm=f.get("confirm") == "yes")
                    flash(f"QuickBooks: {res['action']} (invoice {res.get('invoice_id')})", "ok")
            elif what == "push-customer":
                q = client()
                if q:
                    sync.push_customer(c, q, job["customer_id"])
                    flash("Customer added to QuickBooks.", "ok")
            else:
                abort(404)
        except (ValueError, LookupError, sync.SyncError, qbo.QBOError) as e:
            flash(str(e), "error")
        return redirect(url_for("job_page", number=number))

    @app.get("/report")
    def report_page():
        rep = report.job_report(conn(), request.args.get("status") or None, request.args.get("since") or None)
        if request.args.get("csv"):
            return Response(report.as_csv(rep), mimetype="text/csv",
                            headers={"Content-Disposition": "attachment; filename=job-costing.csv"})
        return render_template("report.html", rep=rep)

    @app.get("/settings")
    def settings_page():
        c = conn()
        return render_template("settings.html",
                               techs=c.execute("SELECT * FROM techs ORDER BY name").fetchall(),
                               items=c.execute("SELECT * FROM items ORDER BY name").fetchall(),
                               customers=c.execute("SELECT * FROM customers ORDER BY name").fetchall(),
                               default_item=db.get_setting(c, "default_item"),
                               target=db.get_setting(c, "target_margin"))

    @app.post("/settings/<what>")
    def settings_action(what):
        c = conn()
        f = request.form
        try:
            if what == "tech":
                c.execute("INSERT INTO techs (name, cost_rate) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET cost_rate = excluded.cost_rate",
                          (f["name"].strip(), db.cents(f["cost_rate"])))
                c.commit()
            elif what == "customer":
                c.execute("INSERT INTO customers (name, email, phone) VALUES (?, ?, ?)",
                          (f["name"].strip(), f.get("email") or None, f.get("phone") or None))
                c.commit()
            elif what == "default-item":
                db.set_setting(c, "default_item", f["qbo_item_id"])
            elif what == "target":
                db.set_setting(c, "target_margin", str(float(f["target"]) / 100))
            elif what in ("pull-customers", "pull-items"):
                q = client()
                if q:
                    n = (sync.pull_customers if what == "pull-customers" else sync.pull_items)(c, q)
                    flash(f"Pulled {n} from QuickBooks.", "ok")
            elif what == "refresh":
                q = client()
                if q:
                    flash(f"Updated {len(sync.refresh_invoices(c, q))} invoices from QuickBooks.", "ok")
            else:
                abort(404)
        except (ValueError, LookupError, sync.SyncError, qbo.QBOError) as e:
            flash(str(e), "error")
        return redirect(url_for("settings_page"))

    return app

"""Job costing: what each job brought in, what it cost, and what was left.

Revenue is the invoice before sales tax (the QuickBooks subtotal once invoiced, the bill lines before that).
Cost is logged hours x each tech's loaded hourly cost, plus materials at what you paid.
Overhead (rent, trucks, the office) isn't spread across jobs here, so this is gross profit per job.
"""
import csv
import io

from . import db


def job_costs(conn, job) -> dict:
    labor_rows = conn.execute("SELECT hours, cost_rate FROM labor WHERE job_id = ?", (job["id"],)).fetchall()
    labor_cost = sum(db.line_amount(r["hours"], r["cost_rate"]) for r in labor_rows)
    mats = conn.execute("SELECT qty, unit_cost FROM materials WHERE job_id = ?", (job["id"],)).fetchall()
    materials = sum(db.line_amount(r["qty"], r["unit_cost"]) for r in mats)
    lines = conn.execute("SELECT qty, unit_price FROM bill_lines WHERE job_id = ?", (job["id"],)).fetchall()
    billed_local = sum(db.line_amount(r["qty"], r["unit_price"]) for r in lines)
    revenue = job["qbo_subtotal"] if job["qbo_subtotal"] is not None else billed_local
    cost = labor_cost + materials
    profit = revenue - cost
    return {
        "job": job["number"],
        "title": job["title"],
        "status": job["status"],
        "hours": round(sum(r["hours"] for r in labor_rows), 2),
        "quoted": job["quoted"],
        "revenue": revenue,
        "labor": labor_cost,
        "materials": materials,
        "cost": cost,
        "profit": profit,
        "margin": (profit / revenue) if revenue else None,
        "balance": job["qbo_balance"],
        "revenue_from": "QuickBooks" if job["qbo_subtotal"] is not None else "bill lines",
    }


def job_report(conn, status: str | None = None, since: str | None = None) -> dict:
    sql, args = "SELECT * FROM jobs WHERE 1=1", []
    if status:
        sql += " AND status = ?"
        args.append(status)
    if since:
        sql += " AND COALESCE(completed_at, created_at) >= ?"
        args.append(since)
    rows = [job_costs(conn, j) for j in conn.execute(sql + " ORDER BY id", args).fetchall()]
    totals = {k: sum(r[k] for r in rows) for k in ("revenue", "labor", "materials", "cost", "profit")}
    totals["hours"] = round(sum(r["hours"] for r in rows), 2)
    totals["margin"] = totals["profit"] / totals["revenue"] if totals["revenue"] else None
    target = db.get_setting(conn, "target_margin")
    target = float(target) if target else None
    for r in rows:
        r["below_target"] = target is not None and r["margin"] is not None and r["margin"] < target
    return {"rows": rows, "totals": totals, "target_margin": target}


def _pct(m) -> str:
    return "-" if m is None else f"{m * 100:.0f}%"


def as_text(rep: dict) -> str:
    head = f"{'Job':<8} {'Title':<30} {'Status':<9} {'Hours':>6} {'Revenue':>11} {'Labor':>10} {'Materials':>10} {'Profit':>11} {'Margin':>7}"
    out = [head, "-" * len(head)]
    for r in rep["rows"]:
        flag = "  <- below target" if r["below_target"] else ""
        out.append(f"{r['job']:<8} {r['title'][:30]:<30} {r['status']:<9} {r['hours']:>6} {db.dollars(r['revenue']):>11} "
                   f"{db.dollars(r['labor']):>10} {db.dollars(r['materials']):>10} {db.dollars(r['profit']):>11} "
                   f"{_pct(r['margin']):>7}{flag}")
    t = rep["totals"]
    out += ["-" * len(head),
            f"{'Total':<8} {'':<30} {'':<9} {t['hours']:>6} {db.dollars(t['revenue']):>11} {db.dollars(t['labor']):>10} "
            f"{db.dollars(t['materials']):>10} {db.dollars(t['profit']):>11} {_pct(t['margin']):>7}"]
    if rep["target_margin"] is not None:
        out.append(f"Target margin: {_pct(rep['target_margin'])}")
    return "\n".join(out)


def as_csv(rep: dict) -> str:
    buf = io.StringIO()
    cols = ["job", "title", "status", "hours", "quoted", "revenue", "labor", "materials", "cost", "profit", "margin", "balance"]
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rep["rows"]:
        w.writerow([r[c] / 100 if c in ("quoted", "revenue", "labor", "materials", "cost", "profit", "balance") and r[c] is not None
                    else (round(r[c], 4) if c == "margin" and r[c] is not None else r[c]) for c in cols])
    return buf.getvalue()

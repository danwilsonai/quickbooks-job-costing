"""Moving data between the job record and QuickBooks.

Into QuickBooks: a finished job becomes an invoice (once), and a new customer can be created.
Out of QuickBooks: customers, products/services, and each invoice's total and balance.
"""
import uuid

from . import db
from .qbo import QBO, quote


class SyncError(Exception):
    pass


def _money(c: int) -> float:
    return round(c / 100, 2)


# ---- pull ---------------------------------------------------------------------------------------------

def pull_customers(conn, qbo: QBO) -> int:
    """Copy QuickBooks customers in. Links a local customer with the same name instead of duplicating it."""
    n = 0
    for c in qbo.query("select * from Customer where Active = true"):
        name = c.get("DisplayName", "").strip()
        email = (c.get("PrimaryEmailAddr") or {}).get("Address")
        phone = (c.get("PrimaryPhone") or {}).get("FreeFormNumber")
        row = conn.execute("SELECT id FROM customers WHERE qbo_id = ?", (c["Id"],)).fetchone() or \
            conn.execute("SELECT id FROM customers WHERE qbo_id IS NULL AND name = ? COLLATE NOCASE", (name,)).fetchone()
        if row:
            conn.execute("UPDATE customers SET qbo_id = ?, name = ?, email = COALESCE(?, email), "
                         "phone = COALESCE(?, phone) WHERE id = ?", (c["Id"], name, email, phone, row["id"]))
        else:
            conn.execute("INSERT INTO customers (name, qbo_id, email, phone) VALUES (?, ?, ?, ?)",
                         (name, c["Id"], email, phone))
        n += 1
    conn.commit()
    return n


def pull_items(conn, qbo: QBO) -> int:
    rows = qbo.query("select * from Item where Active = true")
    for it in rows:
        if it.get("Type") == "Category":
            continue
        conn.execute("INSERT INTO items (qbo_id, name, type, unit_price) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT(qbo_id) DO UPDATE SET name = excluded.name, type = excluded.type, "
                     "unit_price = excluded.unit_price",
                     (it["Id"], it.get("FullyQualifiedName") or it["Name"], it.get("Type"),
                      db.cents(it.get("UnitPrice") or 0)))
    conn.commit()
    return len(rows)


def push_customer(conn, qbo: QBO, customer_id: int) -> str:
    """Create a local-only customer in QuickBooks (or link the one that already has that name)."""
    c = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    if not c:
        raise SyncError(f"No customer {customer_id}")
    if c["qbo_id"]:
        return c["qbo_id"]
    found = qbo.query(f"select * from Customer where DisplayName = {quote(c['name'])}")
    if found:
        qbo_id = found[0]["Id"]
    else:
        body = {"DisplayName": c["name"]}
        if c["email"]:
            body["PrimaryEmailAddr"] = {"Address": c["email"]}
        if c["phone"]:
            body["PrimaryPhone"] = {"FreeFormNumber": c["phone"]}
        qbo_id = qbo.create("customer", body, str(uuid.uuid5(uuid.NAMESPACE_URL, f"qbjobs/customer/{qbo.realm_id}/{c['id']}")))["Id"]
    conn.execute("UPDATE customers SET qbo_id = ? WHERE id = ?", (qbo_id, customer_id))
    conn.commit()
    return qbo_id


# ---- job -> invoice -----------------------------------------------------------------------------------

def invoice_payload(conn, job, strict: bool = True) -> dict:
    """The invoice QuickBooks will get, built from the job's bill lines.

    strict: raise SyncError if it isn't ready. Otherwise return it with a "_problems" list (for previews).
    """
    problems = []
    if job["status"] == "open":
        problems.append("the job isn't marked done")
    cust = conn.execute("SELECT * FROM customers WHERE id = ?", (job["customer_id"],)).fetchone()
    if not cust["qbo_id"]:
        problems.append(f"customer {cust['name']!r} isn't in QuickBooks yet (customers push {cust['id']})")
    lines = conn.execute("SELECT * FROM bill_lines WHERE job_id = ? ORDER BY id", (job["id"],)).fetchall()
    if not lines:
        problems.append("there's nothing to bill (no bill lines)")
    default_item = db.get_setting(conn, "default_item")
    # sales_tax on: every line says TAX or NON, and QuickBooks works out the tax from the customer's address
    # (US companies on automated sales tax). Off: QuickBooks uses each item's own taxable setting.
    sales_tax = (db.get_setting(conn, "sales_tax") or "off").lower() in ("on", "yes", "true", "1")
    out = []
    for ln in lines:
        item = ln["qbo_item_id"] or default_item
        if not item:
            problems.append(f"line {ln['description']!r} has no QuickBooks item and no default_item is set")
        detail = {"ItemRef": {"value": item}, "Qty": ln["qty"], "UnitPrice": _money(ln["unit_price"])}
        if sales_tax:
            detail["TaxCodeRef"] = {"value": "TAX" if ln["taxable"] else "NON"}
        out.append({
            "Amount": _money(db.line_amount(ln["qty"], ln["unit_price"])),
            "Description": ln["description"],
            "DetailType": "SalesItemLineDetail",
            "SalesItemLineDetail": detail,
        })
    if problems and strict:
        raise SyncError(f"{job['number']} can't be invoiced yet: " + "; ".join(problems))
    body = {
        "DocNumber": job["number"],
        "CustomerRef": {"value": cust["qbo_id"], "name": cust["name"]},
        "Line": out,
        "PrivateNote": f"From job {job['number']}: {job['title']}"[:4000],
    }
    if job["completed_at"]:
        body["TxnDate"] = job["completed_at"]
    if cust["email"]:
        body["BillEmail"] = {"Address": cust["email"]}
    if problems:
        body["_problems"] = problems
    return body


def _store_invoice(conn, job_id: int, inv: dict) -> None:
    subtotal = next((l["Amount"] for l in inv.get("Line", []) if l.get("DetailType") == "SubTotalLineDetail"), None)
    conn.execute("UPDATE jobs SET status = 'invoiced', qbo_invoice_id = ?, qbo_subtotal = ?, qbo_total = ?, "
                 "qbo_balance = ?, invoiced_at = COALESCE(invoiced_at, date('now')) WHERE id = ?",
                 (inv["Id"], db.cents(subtotal) if subtotal is not None else None,
                  db.cents(inv.get("TotalAmt", 0)), db.cents(inv.get("Balance", 0)), job_id))
    conn.commit()


def send_job(conn, qbo: QBO, ref, dry_run: bool = False, confirm: bool = False) -> dict:
    """Turn one finished job into a QuickBooks invoice, exactly once.

    Safe to run twice: a job that already has an invoice, here or in QuickBooks (same invoice number),
    is linked, never invoiced again. On your real books (production) nothing is sent without confirm=True.
    """
    job = db.get_job(conn, ref)
    if job["qbo_invoice_id"]:
        return {"job": job["number"], "action": "already invoiced", "invoice_id": job["qbo_invoice_id"]}
    if dry_run:
        body = invoice_payload(conn, job, strict=False)
        problems = body.pop("_problems", [])
        return {"job": job["number"], "action": "preview", "ready": not problems, "problems": problems, "invoice": body}
    body = invoice_payload(conn, job)
    existing = qbo.query(f"select * from Invoice where DocNumber = {quote(job['number'])}")
    if existing:
        _store_invoice(conn, job["id"], existing[0])
        return {"job": job["number"], "action": "linked existing invoice", "invoice_id": existing[0]["Id"]}
    if qbo.env == "production" and not confirm:
        raise SyncError(f"{job['number']}: this would create a real invoice on your live books. "
                        "Check the preview (--dry-run), then run again with --confirm.")
    request_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"qbjobs/invoice/{qbo.realm_id}/{job['number']}/{job['invoice_attempt']}"))
    inv = qbo.create("invoice", body, request_id)
    _store_invoice(conn, job["id"], inv)
    return {"job": job["number"], "action": "created", "invoice_id": inv["Id"], "total": inv.get("TotalAmt")}


def refresh_invoices(conn, qbo: QBO) -> list[dict]:
    """Pull each invoiced job's current total and balance (so you can see what's been paid)."""
    out = []
    for job in conn.execute("SELECT * FROM jobs WHERE qbo_invoice_id IS NOT NULL").fetchall():
        try:
            inv = qbo.read("invoice", job["qbo_invoice_id"])
        except Exception as e:  # deleted or voided in QuickBooks
            out.append({"job": job["number"], "error": str(e)})
            continue
        _store_invoice(conn, job["id"], inv)
        out.append({"job": job["number"], "total": inv.get("TotalAmt"), "balance": inv.get("Balance")})
    return out


def unlink_invoice(conn, ref) -> None:
    """For when you deleted the invoice in QuickBooks and want to send the job again."""
    job = db.get_job(conn, ref)
    conn.execute("UPDATE jobs SET status = 'done', qbo_invoice_id = NULL, qbo_subtotal = NULL, qbo_total = NULL, "
                 "qbo_balance = NULL, invoiced_at = NULL, invoice_attempt = invoice_attempt + 1 WHERE id = ?",
                 (job["id"],))
    conn.commit()

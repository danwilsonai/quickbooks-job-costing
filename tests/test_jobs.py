"""Run: python -m pytest -q

These use a fake QuickBooks, so they need no account. The real check is the sandbox walkthrough in README.
"""
import json

import pytest

from qbjobs import cli, db, qbo, report, sync


class FakeQBO:
    """Stands in for qbo.QBO: remembers what was created and answers the same queries."""

    def __init__(self, env="sandbox"):
        self.env, self.realm_id = env, "123"
        self.invoices, self.customers, self.request_ids = {}, {}, []

    def query(self, sql):
        if "from Invoice" in sql:
            num = sql.split("DocNumber = '")[1].rstrip("'")
            return [i for i in self.invoices.values() if i["DocNumber"] == num]
        if "from Customer where DisplayName" in sql:
            name = sql.split("DisplayName = '")[1].rstrip("'").replace("\\'", "'")
            return [c for c in self.customers.values() if c["DisplayName"] == name]
        if "from Customer" in sql:
            return list(self.customers.values())
        return []

    def create(self, entity, body, request_id):
        if entity not in qbo.ALLOWED_WRITES:
            raise qbo.WriteNotAllowed(entity)
        self.request_ids.append(request_id)
        if entity == "customer":
            rec = {"Id": str(100 + len(self.customers)), **body}
            self.customers[rec["Id"]] = rec
            return rec
        sub = sum(l["Amount"] for l in body["Line"])
        rec = {"Id": str(500 + len(self.invoices)), **body, "TotalAmt": round(sub * 1.07, 2), "Balance": round(sub * 1.07, 2),
               "Line": body["Line"] + [{"DetailType": "SubTotalLineDetail", "Amount": sub}]}
        self.invoices[rec["Id"]] = rec
        return rec

    def read(self, entity, entity_id):
        return self.invoices[entity_id]


@pytest.fixture
def conn(tmp_path, monkeypatch):
    c = db.connect(tmp_path / "t.db")
    monkeypatch.setattr(cli, "_conn", lambda: c)
    cli.main(["demo"])
    db.set_setting(c, "default_item", "1")
    return c


def test_money_rounds_to_the_cent():
    assert db.cents("1,234.565") == 123457
    assert db.cents(12) == 1200
    assert db.line_amount(5.5, 12500) == 68750
    assert db.dollars(-1999) == "-$19.99"


def test_report_math(conn):
    rep = report.job_report(conn)
    j5 = next(r for r in rep["rows"] if r["job"] == "J-1005")
    # 14h x $48 + 16h x $28 = $1,120 labor; $3,050 + $240 materials; $6,900 billed
    assert (j5["labor"], j5["materials"], j5["revenue"]) == (112000, 329000, 690000)
    assert j5["profit"] == 690000 - 112000 - 329000
    assert j5["below_target"]                     # 36% against the demo's 40% target
    assert rep["totals"]["revenue"] == sum(r["revenue"] for r in rep["rows"])


def test_cannot_invoice_until_ready(conn):
    fake = FakeQBO()
    with pytest.raises(sync.SyncError, match="isn't in QuickBooks yet"):
        sync.send_job(conn, fake, "J-1002")
    with pytest.raises(sync.SyncError, match="isn't marked done"):
        sync.send_job(conn, fake, "J-1007")
    assert fake.invoices == {}


def test_invoice_once_then_never_again(conn):
    fake = FakeQBO()
    sync.push_customer(conn, fake, db.get_job(conn, "J-1004")["customer_id"])
    first = sync.send_job(conn, fake, "J-1004")
    assert first["action"] == "created"
    inv = fake.invoices[first["invoice_id"]]
    assert inv["DocNumber"] == "J-1004" and [l["Amount"] for l in inv["Line"][:3]] == [129.0, 687.5, 290.0]
    assert sync.send_job(conn, fake, "J-1004")["action"] == "already invoiced"
    assert len(fake.invoices) == 1
    # revenue in the report is now the QuickBooks subtotal, before tax
    assert report.job_costs(conn, db.get_job(conn, "J-1004"))["revenue"] == 110650


def test_links_an_invoice_that_already_exists_in_quickbooks(conn):
    fake = FakeQBO()
    sync.push_customer(conn, fake, db.get_job(conn, "J-1003")["customer_id"])
    fake.invoices["900"] = {"Id": "900", "DocNumber": "J-1003", "TotalAmt": 1100, "Balance": 0, "Line": []}
    assert sync.send_job(conn, fake, "J-1003")["action"] == "linked existing invoice"
    assert len(fake.invoices) == 1


def test_live_books_need_confirm(conn):
    fake = FakeQBO(env="production")
    sync.push_customer(conn, fake, db.get_job(conn, "J-1006")["customer_id"])
    with pytest.raises(sync.SyncError, match="live books"):
        sync.send_job(conn, fake, "J-1006")
    assert sync.send_job(conn, fake, "J-1006", confirm=True)["action"] == "created"


def test_retry_uses_the_same_request_id(conn):
    fake = FakeQBO()
    sync.push_customer(conn, fake, db.get_job(conn, "J-1001")["customer_id"])
    sync.send_job(conn, fake, "J-1001")
    sync.unlink_invoice(conn, "J-1001")           # owner deleted it in QuickBooks
    fake.invoices.clear()
    sync.send_job(conn, fake, "J-1001")
    assert fake.request_ids[-1] != fake.request_ids[-2]   # a deliberate resend gets a new id


def test_only_invoices_and_customers_can_be_created(tmp_path, monkeypatch):
    tokens = tmp_path / "tok.json"
    tokens.write_text(json.dumps({"env": "sandbox", "realm_id": "1", "access_token": "x", "refresh_token": "y", "expires_at": 9e12}))
    monkeypatch.setattr(qbo.config, "QBO_ENV", "sandbox")
    client = qbo.QBO(tokens)
    for entity in ("journalentry", "bill", "payment", "account", "deposit"):
        with pytest.raises(qbo.WriteNotAllowed):
            client.create(entity, {}, "r")


def test_sandbox_tokens_refused_on_live_books(tmp_path, monkeypatch):
    tokens = tmp_path / "tok.json"
    tokens.write_text(json.dumps({"env": "sandbox", "realm_id": "1", "access_token": "x", "refresh_token": "y", "expires_at": 9e12}))
    monkeypatch.setattr(qbo.config, "QBO_ENV", "production")
    with pytest.raises(qbo.NotConnected):
        qbo.QBO(tokens)


def test_query_quoting():
    assert qbo.quote("St. Mark's") == "'St. Mark\\'s'"


def test_web_pages_render_and_forms_need_the_token(conn, tmp_path):
    from qbjobs.web import create_app
    app = create_app(tmp_path / "t.db")
    app.testing = True
    web = app.test_client()
    for path in ("/", "/job/J-1005", "/job/J-1002", "/report", "/settings", "/report?csv=1"):
        assert web.get(path).status_code == 200, path
    assert web.post("/job/J-1007/done").status_code == 400            # no token: refused
    with web.session_transaction() as s:
        token = s["csrf"]
    assert web.post("/job/J-1007/done", data={"csrf": token}).status_code == 302
    assert db.get_job(conn, "J-1007")["status"] == "done"

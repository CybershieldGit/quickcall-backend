"""
Billing regression: a call ended concurrently from several paths (media stream close, Exotel status
webhook, agent farewell) must deduct from the tenant wallet exactly once.
Runs against a throwaway SQLite file, so it needs no external database.
"""
import os
import sys
import threading
import time
from datetime import datetime, timedelta

from sqlalchemy import create_engine, event, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.telemetry import call_logger  # noqa: E402
from app.services.telemetry import summarizer  # noqa: E402

_SCHEMA = [
    """CREATE TABLE tenants (tenant_id TEXT PRIMARY KEY, reseller_id TEXT, rate_per_min REAL, wallet_balance REAL)""",
    """CREATE TABLE resellers (reseller_id TEXT PRIMARY KEY, wholesale_rate_per_min REAL, wallet_balance REAL)""",
    """CREATE TABLE calls (call_id TEXT PRIMARY KEY, tenant_id TEXT, start_time TIMESTAMP, end_time TIMESTAMP,
                           duration REAL, outcome_tag TEXT, transferred BOOLEAN)""",
    """CREATE TABLE billing_transactions (transaction_id TEXT PRIMARY KEY, call_id TEXT, tenant_id TEXT, reseller_id TEXT,
                           duration_seconds REAL, billable_minutes REAL, rate_per_min REAL, wholesale_rate_per_min REAL,
                           client_charged REAL, reseller_cost REAL, reseller_profit REAL, owner_net_profit REAL,
                           notes TEXT, timestamp TIMESTAMP)""",
]


def test_concurrent_call_end_bills_once(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'billing.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    with engine.begin() as conn:
        for stmt in _SCHEMA:
            conn.execute(text(stmt))
        conn.execute(text("INSERT INTO tenants VALUES ('t1', NULL, 5.0, 100.0)"))
        conn.execute(
            text("INSERT INTO calls (call_id, tenant_id, start_time) VALUES ('c1', 't1', :st)"),
            {"st": datetime.utcnow() - timedelta(minutes=2)},
        )

    # Widen the check-then-deduct race window so concurrent call-end paths reliably overlap
    @event.listens_for(engine, "after_cursor_execute")
    def _slow_settle_check(conn, cursor, statement, params, context, executemany):
        if "FROM billing_transactions WHERE call_id" in statement:
            time.sleep(0.3)

    jobs = []
    monkeypatch.setattr(call_logger, "sync_engine", engine)
    monkeypatch.setattr(call_logger, "submit_bg_db", lambda fn, *a, **kw: jobs.append(fn))
    monkeypatch.setattr(call_logger, "log_raw_operational_cost", lambda *a, **kw: None)
    monkeypatch.setattr(summarizer, "generate_executive_summary", lambda *a, **kw: None)

    call_logger.log_call_end("c1", "completed", False)
    call_logger.log_call_end("c1", "completed_graceful_exit", False)
    call_logger.log_call_end("c1", "completed", False)

    barrier = threading.Barrier(len(jobs))

    def run(fn):
        barrier.wait()
        fn()

    threads = [threading.Thread(target=run, args=(fn,)) for fn in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with engine.connect() as conn:
        tx_count = conn.execute(text("SELECT COUNT(*) FROM billing_transactions WHERE call_id = 'c1'")).scalar()
        charged = conn.execute(text("SELECT client_charged FROM billing_transactions WHERE call_id = 'c1'")).scalar()
        balance = conn.execute(text("SELECT wallet_balance FROM tenants WHERE tenant_id = 't1'")).scalar()

    assert tx_count == 1
    assert round(balance, 2) == round(100.0 - charged, 2)

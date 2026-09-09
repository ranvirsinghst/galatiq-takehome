import sqlite3
from decimal import Decimal

import pytest

from invoice_agent.database import SQLitePaymentStore
from invoice_agent.identity import payment_fingerprint, payment_identity
from invoice_agent.models import Critique, MockPaymentResult, Proposal, ReviewOutcome
from invoice_agent.payment import build_payment_request
from invoice_agent.validation import validate
from tests.unit.test_validation import candidate


class Events:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


def request(store, c=None):
    c = c or candidate()
    report = validate(
        c,
        store.lookup([line.item_name_normalized for line in c.items]),
        store.policy,
        store.lookup_price([line.item_name_normalized for line in c.items]),
    )
    review = ReviewOutcome(
        candidate_digest=report.candidate_digest,
        proposal=Proposal(
            decision="approved",
            reason_summary="Facts verified",
            finding_codes=[f.code for f in report.findings],
            checks={"unavailable_checks": "Not applicable"},
        ),
        critique=Critique(verdict="accept"),
        accepted=True,
    )
    return build_payment_request(c, report, review, store.run_id)


def success(vendor, amount):
    return MockPaymentResult(success=True, payment_id="p1")


def test_atomic_paid_duplicate_version_and_reopen(tmp_path):
    store = SQLitePaymentStore(tmp_path / "a.sqlite", "run")
    req = request(store)
    assert store.pay(req, success).status == "paid"
    assert store.snapshot().stock["WidgetA"] == 13
    assert store.snapshot().generation == 1
    assert store.pay(req, lambda *_: pytest.fail("duplicate called mock")).status == "already_paid"
    changed = candidate(
        total_usd=Decimal(30),
        source_amounts={"total": Decimal(30), "subtotal": Decimal(30)},
        items=[
            candidate()
            .items[0]
            .model_copy(
                update={
                    "source_unit_price": Decimal(15),
                    "unit_price_usd": Decimal(15),
                    "source_line_total": Decimal(30),
                }
            )
        ],
    )
    assert store.pay(request(store, changed), success).findings[0].code == "VERSION_CONFLICT"
    store.close()
    with sqlite3.connect(tmp_path / "a.sqlite") as db:
        assert db.execute("SELECT count(*) FROM payments").fetchone()[0] == 1
        assert db.execute("SELECT stock FROM inventory WHERE item='WidgetA'").fetchone()[0] == 13
    fresh = SQLitePaymentStore(tmp_path / "b.sqlite", "new")
    assert fresh.snapshot().stock["WidgetA"] == 15
    assert fresh.find_paid(req.identity) is None
    fresh.close()


@pytest.mark.parametrize(
    "stage", ["after_mock", "before_ledger_insert", "after_stock_update", "before_commit"]
)
def test_faults_rollback(tmp_path, stage):
    def fault(point):
        if point == stage:
            raise sqlite3.OperationalError("injected")

    events = Events()
    store = SQLitePaymentStore(tmp_path / "db", "run", fault_hook=fault, events=events)
    req = request(store)
    result = store.pay(req, success)
    assert result.status == "failed"
    assert store.snapshot().stock["WidgetA"] == 15
    assert store.snapshot().generation == 0
    assert store.find_paid(req.identity) is None
    assert not any(e.event == "payment_committed" for e in events.events)
    store.close()


@pytest.mark.parametrize("raises", [False, True])
def test_mock_failure_rollback(tmp_path, raises):
    def mock(*_):
        if raises:
            raise RuntimeError("bank simulation failed")
        return MockPaymentResult(success=False)

    store = SQLitePaymentStore(tmp_path / "db", "run")
    req = request(store)
    assert store.pay(req, mock).status == "failed"
    assert store.snapshot().stock["WidgetA"] == 15
    assert store.find_paid(req.identity) is None
    store.close()


def test_stale_snapshot_recheck_blocks_without_mock(tmp_path):
    store = SQLitePaymentStore(tmp_path / "db", "run")
    req = request(store)
    store.connection.execute("UPDATE inventory SET stock=1 WHERE item='WidgetA'")
    result = store.pay(req, lambda *_: pytest.fail("stock blocker called mock"))
    assert result.findings[0].code == "INSUFFICIENT_STOCK"
    assert store.find_paid(req.identity) is None
    store.close()


def test_tampered_request_rejected(tmp_path):
    store = SQLitePaymentStore(tmp_path / "db", "run")
    req = request(store).model_copy(update={"amount_usd": Decimal(1)})
    assert store.pay(req, lambda *_: pytest.fail("invalid request called mock")).findings
    store.close()


def test_equivalence_and_digest_distinction():
    c = candidate()
    other = c.model_copy(
        update={
            "source_id": "other",
            "payment_terms_raw": "Net 30",
            "source_amounts": {
                "total": Decimal("20.00"),
                "subtotal": Decimal("20.0"),
                "tax_amount": Decimal(0),
                "shipping": Decimal(0),
            },
        }
    )
    assert payment_fingerprint(c) == payment_fingerprint(other)
    from invoice_agent.models import candidate_digest

    assert candidate_digest(c) != candidate_digest(other)
    assert payment_identity(c) == payment_identity(
        c.model_copy(update={"vendor_normalized": " VENDOR ", "invoice_number_normalized": "INV 1"})
    )
    assert payment_fingerprint(c) != payment_fingerprint(
        c.model_copy(update={"items": c.items + c.items})
    )


def test_parameterized_inventory(tmp_path):
    store = SQLitePaymentStore(tmp_path / "db", "run")
    evil = "WidgetA'; DROP TABLE inventory; --"
    assert store.lookup([evil]).stock == {evil: None}
    assert store.snapshot().stock["WidgetA"] == 15
    store.close()


def test_generation_change_with_sufficient_stock_still_pays(tmp_path):
    events = Events()
    store = SQLitePaymentStore(tmp_path / "db", "run", events=events)
    req = request(store)
    store.connection.execute("UPDATE run_state SET generation=1")
    assert store.pay(req, success).status == "paid"
    assert any(e.event == "inventory_generation_changed" for e in events.events)
    store.close()


def test_unique_payment_id_collision_rolls_back_second_invoice(tmp_path):
    store = SQLitePaymentStore(tmp_path / "db", "run")
    assert store.pay(request(store), success).status == "paid"
    second = candidate(invoice_number_normalized="INV-2")
    assert store.pay(request(store, second), success).status == "failed"
    assert store.snapshot().stock["WidgetA"] == 13
    assert store.snapshot().generation == 1
    assert store.find_paid(payment_identity(second)) is None
    store.close()


def test_existing_file_never_reinitialized(tmp_path):
    from invoice_agent.errors import AgentError

    store = SQLitePaymentStore(tmp_path / "db", "run")
    assert store.pay(request(store), success).status == "paid"
    with pytest.raises(AgentError):
        SQLitePaymentStore(tmp_path / "db", "other")
    assert store.snapshot().stock["WidgetA"] == 13
    store.close()


def test_changed_terms_invalidate_preexisting_review(tmp_path):
    from pydantic import ValidationError

    store = SQLitePaymentStore(tmp_path / "db", "run")
    req = request(store)
    changed = req.candidate.model_copy(update={"payment_terms_raw": "Net 60", "net_days": 60})
    assert payment_fingerprint(changed) == req.fingerprint
    with pytest.raises(ValidationError):
        build_payment_request(changed, req.report, req.review, "run")
    store.close()


def test_missing_unknown_charges_do_not_equal_zero():
    c = candidate(
        items=[
            candidate()
            .items[0]
            .model_copy(update={"source_unit_price": None, "source_line_total": None})
        ],
        source_amounts={"total": Decimal(20)},
    )
    explicit = c.model_copy(
        update={
            "source_amounts": {
                "total": Decimal(20),
                "tax_amount": Decimal(0),
                "shipping": Decimal(0),
            }
        }
    )
    assert payment_fingerprint(c) != payment_fingerprint(explicit)


def test_fingerprint_reordering_and_distinct_prices():
    first = candidate().items[0]
    second = first.model_copy(
        update={
            "line_id": "2",
            "source_unit_price": Decimal(15),
            "unit_price_usd": Decimal(15),
            "source_line_total": Decimal(30),
        }
    )
    c = candidate(items=[first, second])
    assert payment_fingerprint(c) == payment_fingerprint(
        c.model_copy(update={"items": [second, first]})
    )
    assert payment_fingerprint(c) != payment_fingerprint(
        c.model_copy(update={"items": [first, first]})
    )


@pytest.mark.parametrize("operation", ["lookup", "snapshot", "find_paid"])
def test_closed_real_database_is_fatal_typed_failure(tmp_path, operation):
    from invoice_agent.errors import AgentError

    store = SQLitePaymentStore(tmp_path / "closed.db", "run")
    identity = payment_identity(candidate())
    store.close()
    with pytest.raises(AgentError) as failure:
        if operation == "lookup":
            store.lookup(["WidgetA"])
        elif operation == "snapshot":
            store.snapshot()
        else:
            store.find_paid(identity)
    assert failure.value.info.code == "STORAGE_ERROR" and failure.value.info.fatal


def test_actual_commit_failure_rolls_back_and_emits_no_success(tmp_path):
    events = Events()
    store = SQLitePaymentStore(tmp_path / "commit.db", "run", events=events)
    req = request(store)
    real = store.connection

    class CommitFailure:
        def __getattr__(self, key):
            return getattr(real, key)

        def commit(self):
            raise sqlite3.OperationalError("injected commit failure")

    store.connection = CommitFailure()
    result = store.pay(req, success)
    assert result.status == "failed"
    assert store.snapshot().stock["WidgetA"] == 15
    assert store.find_paid(req.identity) is None
    assert not any(e.event == "payment_committed" for e in events.events)
    store.close()


def test_unrecoverable_rollback_stops_shared_store(tmp_path):
    from invoice_agent.errors import AgentError

    store = SQLitePaymentStore(tmp_path / "rollback.db", "run")
    req = request(store)
    real = store.connection

    class RollbackFailure:
        def __getattr__(self, key):
            return getattr(real, key)

        def rollback(self):
            raise sqlite3.OperationalError("injected rollback failure")

    store.connection = RollbackFailure()
    try:
        with pytest.raises(AgentError) as failure:
            store.pay(req, lambda *_: MockPaymentResult(success=False))
        assert failure.value.info.code == "STORAGE_ERROR" and failure.value.info.fatal
    finally:
        real.rollback()
        real.close()

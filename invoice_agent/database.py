"""Fresh invocation SQLite state and atomic local mock-payment transactions."""

import sqlite3
from pathlib import Path

from pydantic import ValidationError

from .config import Policy
from .errors import AgentError
from .identity import payment_fingerprint, payment_identity
from .models import (
    CatalogEvidence,
    ErrorCode,
    ErrorInfo,
    FindingOrigin,
    InventorySnapshot,
    PaidRecord,
    PaymentOutcome,
    PaymentRequest,
    PaymentStatus,
    Severity,
    TraceEvent,
    ValidationFinding,
)
from .ports import NullEventSink
from .validation import validate


class SQLitePaymentStore:
    def __init__(
        self, path: Path, run_id: str, *, policy: Policy | None = None, events=None, fault_hook=None
    ):
        self.path, self.run_id = Path(path), run_id
        self.policy = policy or Policy()
        self.events = events or NullEventSink()
        self.fault_hook = fault_hook or (lambda stage: None)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation prevents accidental reuse/clearing of an earlier invocation.
        try:
            with self.path.open("xb"):
                pass
            self.connection = sqlite3.connect(self.path, isolation_level=None)
            self.connection.row_factory = sqlite3.Row
            sql = Path(__file__).with_name("sql")
            self.connection.executescript(
                (sql / "schema.sql").read_text() + (sql / "seed.sql").read_text()
            )
            self.connection.execute("INSERT INTO run_state(run_id) VALUES (?)", (run_id,))
        except (OSError, sqlite3.Error) as error:
            raise AgentError(
                ErrorCode.STORAGE_ERROR, "Cannot initialize fresh run database", fatal=True
            ) from error

    def _generation(self):
        return self.connection.execute(
            "SELECT generation FROM run_state WHERE run_id=?", (self.run_id,)
        ).fetchone()[0]

    def lookup(self, items: list[str]) -> InventorySnapshot:
        try:
            stock = {}
            for item in sorted(set(items)):
                row = self.connection.execute(
                    "SELECT stock FROM inventory WHERE item=?", (item,)
                ).fetchone()
                stock[item] = row[0] if row else None
            return InventorySnapshot(run_id=self.run_id, generation=self._generation(), stock=stock)
        except sqlite3.Error as error:
            raise AgentError(
                ErrorCode.STORAGE_ERROR, "Inventory lookup failed", fatal=True
            ) from error

    def lookup_price(self, items: list[str]) -> CatalogEvidence:
        try:
            prices = {}
            for item in sorted(set(items)):
                row = self.connection.execute(
                    "SELECT unit_price_usd FROM prices WHERE item=?", (item,)
                ).fetchone()
                prices[item] = row[0] if row else None
            return CatalogEvidence(run_id=self.run_id, prices=prices)
        except (sqlite3.Error, ValueError) as error:
            raise AgentError(
                ErrorCode.STORAGE_ERROR,
                "Catalog lookup failed: invalid or unavailable catalog data",
            ) from error

    def _verified_catalog(self, request):
        actual = self.lookup_price(list(request.report.catalog_evidence.prices))
        if actual != request.report.catalog_evidence:
            raise ValueError("Catalog evidence differs from current run catalog")
        return actual

    def snapshot(self) -> InventorySnapshot:
        try:
            return InventorySnapshot(
                run_id=self.run_id,
                generation=self._generation(),
                stock=dict(
                    self.connection.execute(
                        "SELECT item, stock FROM inventory ORDER BY item"
                    ).fetchall()
                ),
            )
        except sqlite3.Error as error:
            raise AgentError(
                ErrorCode.STORAGE_ERROR, "Inventory snapshot failed", fatal=True
            ) from error

    def find_paid(self, identity):
        try:
            row = self.connection.execute(
                "SELECT * FROM payments WHERE vendor=? AND invoice_number=?",
                (identity.vendor, identity.invoice_number),
            ).fetchone()
        except sqlite3.Error as error:
            raise AgentError(
                ErrorCode.STORAGE_ERROR, "Payment identity lookup failed", fatal=True
            ) from error
        return (
            PaidRecord(
                run_id=row["run_id"],
                identity=identity,
                fingerprint=row["fingerprint"],
                payment_id=row["payment_id"],
                amount_usd=row["amount_usd"],
                source_id=row["source_id"],
            )
            if row
            else None
        )

    def _rollback(self):
        try:
            if self.connection.in_transaction:
                self.connection.rollback()
        except sqlite3.Error as error:
            raise AgentError(
                ErrorCode.STORAGE_ERROR, "Run database cannot roll back safely", fatal=True
            ) from error

    def pay(self, request, mock):
        try:
            request = PaymentRequest.model_validate(request.model_dump())
            if (
                request.run_id != self.run_id
                or request.identity != payment_identity(request.candidate)
                or request.fingerprint != payment_fingerprint(request.candidate)
            ):
                raise ValueError("Payment identity binding mismatch")
            original = validate(
                request.candidate,
                request.report.stock_snapshot,
                self.policy,
                self._verified_catalog(request),
            )
            if (
                original.blockers
                or original != request.report
                or not original.complete
                or original.aggregate_quantities != request.aggregate_quantities
                or original.requires_high_value_review != request.report.requires_high_value_review
            ):
                raise ValueError("Payment validation evidence is not eligible")
        except (ValueError, ValidationError) as error:
            return PaymentOutcome(
                findings=[
                    ValidationFinding(
                        code="INVALID_PAYMENT_REQUEST",
                        severity=Severity.BLOCKER,
                        message=str(error),
                    )
                ]
            )
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            prior = self.find_paid(request.identity)
            if prior:
                self._rollback()
                if prior.fingerprint == request.fingerprint:
                    return PaymentOutcome(
                        status=PaymentStatus.ALREADY_PAID,
                        payment_id=prior.payment_id,
                        amount_usd=prior.amount_usd,
                    )
                return PaymentOutcome(
                    findings=[
                        ValidationFinding(
                            code="VERSION_CONFLICT",
                            severity=Severity.BLOCKER,
                            origin=FindingOrigin.IDENTITY,
                            message="A changed version of this invoice was already paid",
                        )
                    ]
                )
            current = self.lookup(list(request.aggregate_quantities))
            try:
                catalog = self._verified_catalog(request)
            except ValueError as error:
                self._rollback()
                return PaymentOutcome(
                    findings=[
                        ValidationFinding(
                            code="INVALID_PAYMENT_REQUEST",
                            severity=Severity.BLOCKER,
                            message=str(error),
                        )
                    ]
                )
            fresh = validate(request.candidate, current, self.policy, catalog)
            if fresh.blockers or not fresh.complete:
                self._rollback()
                return PaymentOutcome(findings=fresh.blockers)
            if current.generation != request.report.inventory_generation:
                self.events.emit(
                    TraceEvent(
                        run_id=self.run_id,
                        source_id=request.source_id,
                        stage="payment",
                        event="inventory_generation_changed",
                        payload={
                            "validated": request.report.inventory_generation,
                            "current": current.generation,
                        },
                    )
                )
            try:
                result = mock(request.candidate.vendor_normalized, request.amount_usd)
            except Exception:
                # This is the deliberately injectable simulation boundary.
                self._rollback()
                return PaymentOutcome(
                    status=PaymentStatus.FAILED,
                    error=ErrorInfo(
                        code=ErrorCode.PAYMENT_FAILED,
                        message="Mock payment raised an exception; transaction rolled back",
                    ),
                )
            if not result.success or not result.payment_id:
                self._rollback()
                return PaymentOutcome(
                    status=PaymentStatus.FAILED,
                    error=ErrorInfo(code=ErrorCode.PAYMENT_FAILED, message="Mock payment declined"),
                )
            self.fault_hook("after_mock")
            self.fault_hook("before_ledger_insert")
            self.connection.execute(
                "INSERT INTO payments(vendor, invoice_number, fingerprint, payment_id, amount_usd, source_id, run_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    request.identity.vendor,
                    request.identity.invoice_number,
                    request.fingerprint,
                    result.payment_id,
                    str(request.amount_usd),
                    request.source_id,
                    self.run_id,
                ),
            )
            for item, quantity in sorted(request.aggregate_quantities.items()):
                cursor = self.connection.execute(
                    "UPDATE inventory SET stock=stock-? WHERE item=? AND stock>=?",
                    (quantity, item, quantity),
                )
                if cursor.rowcount != 1:
                    raise sqlite3.IntegrityError("Concurrent stock change")
            self.fault_hook("after_stock_update")
            self.connection.execute(
                "UPDATE run_state SET generation=generation+1 WHERE run_id=?", (self.run_id,)
            )
            self.fault_hook("before_commit")
            self.connection.commit()
        except AgentError:
            self._rollback()
            raise
        except sqlite3.Error:
            self._rollback()
            code = ErrorCode.STORAGE_ERROR
            return PaymentOutcome(
                status=PaymentStatus.FAILED,
                error=ErrorInfo(
                    code=code, message="Local payment transaction failed and rolled back"
                ),
            )
        except BaseException:
            # Preserve programming errors for the outer diagnostic boundary, but release the transaction.
            self._rollback()
            raise
        self.events.emit(
            TraceEvent(
                run_id=self.run_id,
                source_id=request.source_id,
                stage="payment",
                event="payment_committed",
                payload={
                    "payment_id": result.payment_id,
                    "amount_usd": str(request.amount_usd),
                    "inventory_deltas": {
                        item: -q for item, q in request.aggregate_quantities.items()
                    },
                },
            )
        )
        return PaymentOutcome(
            status=PaymentStatus.PAID,
            payment_id=result.payment_id,
            amount_usd=request.amount_usd,
            inventory_deltas={item: -q for item, q in request.aggregate_quantities.items()},
        )

    def close(self):
        self.connection.close()

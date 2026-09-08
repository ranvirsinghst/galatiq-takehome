"""Evaluate actual ingestion/processing graphs against independent fixture expectations.

Offline mode uses reviewed extraction records and scripted tool/review responses;
it makes no claim about live provider quality. --live uses xAI instead.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from invoice_agent.config import Policy, load_settings  # noqa: E402
from invoice_agent.database import SQLitePaymentStore  # noqa: E402
from invoice_agent.output import EventCollector  # noqa: E402
from invoice_agent.runner import RunDependencies, run  # noqa: E402


def execute(paths, live=False):
    model: Any
    policy = Policy()
    events = EventCollector("evaluation")
    if live:
        from invoice_agent.llm import XAIClient

        settings = load_settings(ROOT / ".env")
        model = XAIClient(
            api_key=settings.api_key.get_secret_value(),
            model=settings.model,
            base_url=settings.base_url,
            timeout=settings.timeout_seconds,
            events=events,
            run_id=events.run_id,
        )
    else:
        from tests.fixture_model import FixtureLLM

        model = FixtureLLM()
    with tempfile.TemporaryDirectory(prefix="invoice-evaluation-") as directory:
        store = SQLitePaymentStore(
            Path(directory) / "run.sqlite3", events.run_id, policy=policy, events=events
        )
        try:
            return run(paths, RunDependencies(store, model, events, policy))
        finally:
            store.close()
            if live:
                model.close()


def evaluate(live=False, suite="all"):
    expectations = json.loads((ROOT / "tests/fixtures/expected/outcomes.json").read_text())
    cases = []
    if suite in ("all", "isolated"):
        for filename, expected in expectations.items():
            result = execute([ROOT / "data/invoices" / filename], live)
            actual = result.results[0]
            diffs = []
            if actual.status != "completed":
                diffs.append(
                    f"operational status: {actual.status}; code: {actual.error.code if actual.error else None}"
                )
            if actual.decision != expected["decision"]:
                diffs.append(f"decision: expected {expected['decision']}, got {actual.decision}")
            if str(actual.total_usd) != expected["total_usd"]:
                diffs.append(f"total_usd: expected {expected['total_usd']}, got {actual.total_usd}")
            codes = {f.code for f in actual.findings}
            for code in expected["codes"]:
                if code not in codes:
                    diffs.append(f"missing finding: {code}; observed: {sorted(codes)}")
            cases.append(
                {"suite": "isolated", "case": filename, "passed": not diffs, "diffs": diffs}
            )
    if suite in ("all", "stateful"):
        scenarios = [
            (
                "full_folder",
                sorted((ROOT / "data/invoices").glob("*")),
                2,
                0,
                "6890.00",
                {"WidgetA": 2, "WidgetB": 3, "GadgetX": 5, "FakeItem": 0},
            ),
            (
                "equivalent_formats",
                [ROOT / "data/invoices/invoice_1011.txt", ROOT / "data/invoices/invoice_1011.pdf"],
                1,
                1,
                "3000.00",
                {"WidgetA": 9, "WidgetB": 7, "GadgetX": 5, "FakeItem": 0},
            ),
            (
                "paid_revision",
                [
                    ROOT / "data/invoices/invoice_1004.json",
                    ROOT / "data/invoices/invoice_1004_revised.json",
                ],
                1,
                0,
                "1890.00",
                {"WidgetA": 12, "WidgetB": 8, "GadgetX": 5, "FakeItem": 0},
            ),
        ]
        for name, paths, payments, duplicates, amount, stock in scenarios:
            paths = [p for p in paths if p.suffix in {".json", ".csv", ".txt", ".xml", ".pdf"}]
            result = execute(paths, live)
            summary = result.summary
            diffs = []
            for field, expected in [
                ("new_payments", payments),
                ("duplicate_skips", duplicates),
                ("total_paid_usd", amount),
                ("final_inventory", stock),
                ("operational_errors", 0),
            ]:
                actual = getattr(summary, field)
                if field == "total_paid_usd":
                    actual = str(actual)
                if actual != expected:
                    diffs.append(f"{field}: expected {expected}, got {actual}")
            terminals = [e.sequence for e in result.trace if e.event == "ingest_terminal"]
            processing = [e.sequence for e in result.trace if e.event == "processing_started"]
            if len(terminals) != len(paths) or (processing and max(terminals) >= min(processing)):
                diffs.append("all-ingestion barrier violated")
            ordered = [
                r.invoice_date
                for r in result.results
                if r.processing_index is not None and r.invoice_date is not None
            ]
            if ordered != sorted(ordered):
                diffs.append("invoice date ordering violated")
            if name == "paid_revision" and not any(
                f.code == "VERSION_CONFLICT" for r in result.results for f in r.findings
            ):
                diffs.append("missing VERSION_CONFLICT")
            cases.append({"suite": "stateful", "case": name, "passed": not diffs, "diffs": diffs})
    return {
        "mode": "live_xai" if live else "offline_recorded_extraction_scripted_review",
        "passed": all(c["passed"] for c in cases),
        "cases": cases,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Use xAI; requires local .env or XAI_API_KEY"
    )
    parser.add_argument("--suite", choices=["all", "isolated", "stateful"], default="all")
    args = parser.parse_args(argv)
    result = evaluate(args.live, args.suite)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

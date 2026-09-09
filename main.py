"""Run the local invoice simulation; one invocation owns one fresh database."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
import webbrowser
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

from invoice_agent.config import Policy, load_settings
from invoice_agent.console import ConsoleReporter, clean_terminal
from invoice_agent.database import SQLitePaymentStore
from invoice_agent.errors import AgentError
from invoice_agent.llm import XAIClient
from invoice_agent.metrics import RunMetrics, compute_metrics
from invoice_agent.models import InvoiceResult, RunResult, RunSummary
from invoice_agent.output import EventCollector, write_invoice, write_summary
from invoice_agent.report import write_report
from invoice_agent.runner import RunDependencies, discover, run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--invoice_path", required=True, type=Path, help="Invoice file or folder (non-recursive)"
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Include structured events in result artifacts; detailed trace is always saved per run",
    )
    parser.add_argument(
        "--output_dir",
        type=Path,
        default=Path("runs"),
        help="Root for unique run databases and result artifacts",
    )
    parser.add_argument(
        "--json", action="store_true", help="Write JSONL results to stdout for scripts"
    )
    parser.add_argument(
        "--open-report",
        action="store_true",
        help="Open report.html in the default browser after the run",
    )
    args = parser.parse_args(argv)

    diagnostic_secrets: list[str] = []

    def diagnostic(code: str, message: str) -> None:
        payload = json.dumps({"error": code, "message": message}) if args.json else message
        print(clean_terminal(payload, diagnostic_secrets), file=sys.stderr, flush=True)

    try:
        paths, skipped = discover(args.invoice_path)
        settings = load_settings(Path(".env"))
    except (ValueError, ValidationError) as exc:
        message = (
            "Set XAI_API_KEY in local .env or environment; check model/timeout configuration."
            if isinstance(exc, ValidationError)
            else str(exc)
        )
        diagnostic("CONFIG_ERROR", message)
        return 2
    except OSError:
        diagnostic("SOURCE_IO_ERROR", "Cannot access invoice input path.")
        return 1
    run_id = uuid.uuid4().hex
    run_dir = args.output_dir / run_id
    store = None
    client = None
    trace_file = None
    events = None
    recorded: list[InvoiceResult] = []
    metrics_saved = False

    report_failed = False

    def save_html(result: RunResult) -> None:
        nonlocal report_failed
        path = run_dir / "report.html"
        try:
            write_report(result, path, secrets=diagnostic_secrets)
        except Exception:
            report_failed = True
            diagnostic(
                "REPORT_FAILED",
                f"Report could not be saved; results are in {run_dir}.",
            )
            return
        print(
            clean_terminal(f"Full report: {path}", diagnostic_secrets), file=sys.stderr, flush=True
        )
        if args.open_report:
            try:
                opened = webbrowser.open(path.resolve().as_uri())
            except Exception:
                opened = False
            if not opened:
                diagnostic(
                    "REPORT_OPEN_FAILED",
                    f"Browser could not open the report; open {path} manually.",
                )

    def save_metrics(metrics: RunMetrics) -> None:
        temporary = run_dir / "metrics.json.tmp"
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(metrics.model_dump_json(indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(run_dir / "metrics.json")

    try:
        run_dir.mkdir(parents=True)
        key = settings.api_key.get_secret_value()
        diagnostic_secrets.append(key)
        console = ConsoleReporter(paths, sys.stderr, secrets=[key])
        trace_file = (run_dir / "trace.jsonl").open("w", encoding="utf-8")
        events = EventCollector(
            run_id, secrets=[key], on_event=console.event, trace_stream=trace_file
        )
        policy = Policy(request_timeout_seconds=settings.timeout_seconds)
        store = SQLitePaymentStore(run_dir / "inventory.db", run_id, policy=policy, events=events)
        client = XAIClient(
            key,
            model=settings.model,
            base_url=settings.base_url,
            timeout=settings.timeout_seconds,
            retries=policy.transport_retries,
            events=events,
            run_id=run_id,
        )
        print(clean_terminal(f"Saving results to: {run_dir}", [key]), file=sys.stderr, flush=True)
        with (
            (run_dir / "results.jsonl").open("w", encoding="utf-8") as artifact,
            (run_dir / "audit.jsonl").open("w", encoding="utf-8") as audit,
        ):

            def persist(item: InvoiceResult) -> None:
                recorded.append(item)
                audit.write(item.model_dump_json(exclude_none=True) + "\n")
                audit.flush()
                os.fsync(audit.fileno())
                write_invoice(item, artifact, trace=args.trace)
                os.fsync(artifact.fileno())
                if args.json:
                    write_invoice(item, sys.stdout)
                else:
                    console.invoice(item, sys.stdout)

            result = run(
                paths, RunDependencies(store, client, events, policy, on_result=persist), skipped
            )
            assert result.summary.metrics is not None
            save_metrics(result.summary.metrics)
            write_summary(result, artifact, trace=args.trace)
            os.fsync(artifact.fileno())
            metrics_saved = True
            save_html(result)
            if args.json:
                write_summary(result, sys.stdout)
            else:
                console.summary(result, sys.stdout)
        return (
            1 if result.summary.operational_errors or result.summary.error or report_failed else 0
        )
    except KeyboardInterrupt:
        print(
            clean_terminal(
                f"Stopped early. Completed results and simulated payments are saved in {run_dir}.",
                [settings.api_key.get_secret_value()],
            ),
            file=sys.stderr,
        )
        return 130
    except (OSError, AgentError):
        diagnostic(
            "RUN_FAILED",
            (
                f"Processing completed. Results are saved in {run_dir}, but could not be displayed."
                if metrics_saved
                else f"Run stopped early. Completed results and simulated payments are saved in {run_dir}. Check available disk space and service settings."
            ),
        )
        return 1
    except Exception:
        diagnostic(
            "INTERNAL_ERROR",
            (
                f"Processing completed. Results are saved in {run_dir}, but could not be displayed."
                if metrics_saved
                else f"An unexpected problem stopped the run. Completed results and simulated payments are saved in {run_dir}."
            ),
        )
        return 1
    finally:
        if events is not None and not metrics_saved:
            partial = compute_metrics(
                recorded,
                events.events,
                len(paths),
                (time.monotonic() - events.start) * 1000,
                run_complete=False,
                run_error=True,
            )
            try:
                save_metrics(partial)
                print(
                    clean_terminal(
                        f"Partial run details saved to: {run_dir / 'metrics.json'}",
                        diagnostic_secrets,
                    ),
                    file=sys.stderr,
                    flush=True,
                )
            except OSError:
                pass  # HTML can still be writable when only metrics persistence fails.
            try:
                paid = [r for r in recorded if r.payment.status == "paid"]
                save_html(
                    RunResult(
                        results=recorded,
                        trace=events.events,
                        summary=RunSummary(
                            run_id=run_id,
                            discovered=len(paths),
                            metrics=partial,
                            completed=sum(r.status == "completed" for r in recorded),
                            approved=sum(r.decision == "approved" for r in recorded),
                            rejected=sum(r.decision == "rejected" for r in recorded),
                            operational_errors=sum(r.status == "error" for r in recorded),
                            duplicate_skips=sum(
                                r.payment.status == "already_paid" for r in recorded
                            ),
                            new_payments=len(paid),
                            total_paid_usd=sum(
                                (r.payment.amount_usd or Decimal(0) for r in paid), Decimal(0)
                            ),
                            skipped=skipped,
                        ),
                    )
                )
            except OSError:
                pass  # Preserve the original failure when diagnostics/storage are unavailable.
        if trace_file is not None:
            try:
                trace_file.close()
            except OSError:
                pass  # Trace persistence already reported as a run error by the collector.
        if client:
            client.close()
        if store:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())

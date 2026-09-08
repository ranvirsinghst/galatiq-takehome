"""Run the local invoice simulation; one invocation owns one fresh database."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

from pydantic import ValidationError

from invoice_agent.config import Policy, load_settings
from invoice_agent.database import SQLitePaymentStore
from invoice_agent.errors import AgentError
from invoice_agent.llm import XAIClient
from invoice_agent.models import InvoiceResult
from invoice_agent.output import EventCollector, write_invoice, write_summary
from invoice_agent.runner import RunDependencies, discover, run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--invoice_path", required=True, type=Path, help="Invoice file or folder (non-recursive)"
    )
    parser.add_argument(
        "--trace", action="store_true", help="Include detailed structured decision events"
    )
    parser.add_argument(
        "--output_dir",
        type=Path,
        default=Path("runs"),
        help="Root for unique run databases and result artifacts",
    )
    args = parser.parse_args(argv)
    try:
        paths, skipped = discover(args.invoice_path)
        settings = load_settings(Path(".env"))
    except (ValueError, ValidationError) as exc:
        message = (
            "Set XAI_API_KEY in local .env or environment; check model/timeout configuration."
            if isinstance(exc, ValidationError)
            else str(exc)
        )
        print(json.dumps({"error": "CONFIG_ERROR", "message": message}), file=sys.stderr)
        return 2
    except OSError:
        print(
            json.dumps(
                {"error": "SOURCE_IO_ERROR", "message": "Cannot access invoice input path."}
            ),
            file=sys.stderr,
        )
        return 1
    run_id = uuid.uuid4().hex
    run_dir = args.output_dir / run_id
    store = None
    client = None
    try:
        run_dir.mkdir(parents=True)
        key = settings.api_key.get_secret_value()
        events = EventCollector(run_id, secrets=[key])
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
        print(f"Run artifacts: {run_dir}", file=sys.stderr, flush=True)
        with (
            (run_dir / "results.jsonl").open("w", encoding="utf-8") as artifact,
            (run_dir / "audit.jsonl").open("w", encoding="utf-8") as audit,
        ):

            def persist(item: InvoiceResult) -> None:
                audit.write(item.model_dump_json(exclude_none=True) + "\n")
                audit.flush()
                os.fsync(audit.fileno())
                write_invoice(item, artifact, trace=args.trace)
                os.fsync(artifact.fileno())
                write_invoice(item, sys.stdout, trace=args.trace)

            result = run(
                paths, RunDependencies(store, client, events, policy, on_result=persist), skipped
            )
            write_summary(result, artifact, trace=args.trace)
            os.fsync(artifact.fileno())
            write_summary(result, sys.stdout, trace=args.trace)
        return 1 if result.summary.operational_errors or result.summary.error else 0
    except KeyboardInterrupt:
        print(
            f"Interrupted; partial outcomes and committed ledger remain in {run_dir}. No complete success summary emitted.",
            file=sys.stderr,
        )
        return 130
    except (OSError, AgentError):
        print(
            json.dumps(
                {
                    "error": "RUN_FAILED",
                    "message": "Run could not complete; check local storage and provider configuration.",
                }
            ),
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(
            json.dumps(
                {
                    "error": "INTERNAL_ERROR",
                    "message": "Unexpected run failure; no complete success claimed.",
                    "exception_type": type(exc).__name__,
                }
            ),
            file=sys.stderr,
        )
        return 1
    finally:
        if client:
            client.close()
        if store:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())

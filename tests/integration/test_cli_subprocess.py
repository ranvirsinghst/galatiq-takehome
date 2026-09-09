"""Whole CLI process through a local HTTP provider double, with real adapter and SQLite."""

import json
import os
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from invoice_agent.models import LLMRequest, LLMResponse, LLMToolCall
from tests.doubles import ScenarioLLM
from tests.integration.test_batch import invoice


@pytest.mark.parametrize("mode", ["stock", "price", "tool", "catalog"])
def test_whole_cli_process_through_http_and_committed_ledger(tmp_path, mode):
    model = ScenarioLLM()
    paths_seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            paths_seen.append(self.path)
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if payload.get("tools"):
                phase = "inventory"
            else:
                properties = payload["response_format"]["json_schema"]["schema"]["properties"]
                phase = "vp_critique" if "verdict" in properties else "vp_propose"
            if mode == "tool":
                response = LLMResponse(
                    tool_calls=[
                        LLMToolCall(
                            call_id="bad", name="unknown_tool", arguments={"items": ["WidgetA"]}
                        )
                    ]
                )
            else:
                response = model.complete(
                    LLMRequest(
                        phase=phase, messages=payload["messages"], tools=payload.get("tools", [])
                    )
                )
            if mode == "catalog":
                database = next((tmp_path / "runs").glob("*/inventory.db"))
                with sqlite3.connect(database) as db:
                    db.execute("UPDATE prices SET unit_price_usd='broken' WHERE item='WidgetA'")
            message = {"content": json.dumps(response.content)}
            if response.tool_calls:
                message = {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": c.call_id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                        }
                        for c in response.tool_calls
                    ],
                }
            body = json.dumps(
                {
                    "model": "local-provider-double",
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 20,
                        "total_tokens": 120,
                        "prompt_tokens_details": {"cached_tokens": 30},
                        "completion_tokens_details": {"reasoning_tokens": 2},
                        "cost_in_usd_ticks": 50000,
                    },
                    "choices": [
                        {
                            "finish_reason": "tool_calls" if response.tool_calls else "stop",
                            "message": message,
                        }
                    ],
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    inputs = tmp_path / "inputs"
    inputs.mkdir()
    if mode == "stock":
        invoice(inputs / "a-later.json", "INV-2", "2026-02-01", 8)
        invoice(inputs / "z-earlier.json", "INV-1", "2026-01-01", 10)
    else:
        source = invoice(inputs / "priced.json", "PRICE", "2026-01-01", 2)
        data = json.loads(source.read_text())
        price = 300 if mode == "price" else 250
        data["line_items"][0]["unit_price"] = price
        data["subtotal"] = data["total"] = price * 2
        source.write_text(json.dumps(data))
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = {
            **os.environ,
            "XAI_API_KEY": "local-http-test-sentinel",
            "XAI_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
        }
        result = subprocess.run(
            [
                sys.executable,
                "main.py",
                "--invoice_path",
                str(inputs),
                "--output_dir",
                str(tmp_path / "runs"),
                "--trace",
                "--json",
            ],
            cwd=Path(__file__).resolve().parents[2],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        json_run_dir = next((tmp_path / "runs").iterdir())
        json_call_count = len(paths_seen)
        if mode == "price":
            human = subprocess.run(
                [arg for arg in result.args if arg != "--json"],
                cwd=Path(__file__).resolve().parents[2],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert human.returncode == 0
            assert "VP review skipped" in human.stdout + human.stderr
            assert "$300.00/unit vs catalog $250.00" in human.stdout + human.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    if mode != "stock":
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        assert len(rows) == 2 and rows[-1]["type"] == "summary"
        run_dir = json_run_dir
        audit = json.loads((run_dir / "audit.jsonl").read_text())
        traces = [json.loads(line) for line in (run_dir / "trace.jsonl").read_text().splitlines()]
        metrics = json.loads((run_dir / "metrics.json").read_text())
        assert metrics == rows[-1]["metrics"]
        calls = json_call_count
        assert metrics["model_calls"] == calls
        assert metrics["total_tokens"] == calls * 120
        assert rows[-1]["new_payments"] == 0 and rows[-1]["final_inventory"]["WidgetA"] == 15
        with sqlite3.connect(run_dir / "inventory.db") as db:
            assert db.execute("SELECT count(*) FROM payments").fetchone()[0] == 0
            assert (
                db.execute("SELECT stock FROM inventory WHERE item='WidgetA'").fetchone()[0] == 15
            )
        assert not any(e["event"] in ("vp_propose", "payment_committed") for e in traces)
        if mode == "price":
            assert result.returncode == 0 and rows[0]["decision"] == "rejected"
            assert "PRICE_OVERCHARGE" in {f["code"] for f in rows[0]["findings"]}
            assert audit["validation"]["catalog_evidence"]["prices"] == {"WidgetA": "250.00"}
            assert any(
                e["event"] == "tool_result"
                and e["payload"].get("name") == "lookup_price"
                and e["payload"]["prices"] == {"WidgetA": "250.00"}
                for e in traces
            )
            assert "Catalog price lookup result" in (run_dir / "report.html").read_text()
            assert metrics["blocked_payment_exposure_usd"] == "600.00"
            assert metrics["rejection_rate"] == 1 and metrics["operational_error_rate"] == 0
        else:
            code = "STORAGE_ERROR" if mode == "catalog" else "TOOL_PROTOCOL_ERROR"
            assert result.returncode == 1 and rows[0]["error"]["code"] == code
            assert audit["error"]["code"] == code and rows[0].get("decision") is None
            assert metrics["operational_error_rate"] == 1 and metrics["rejection_rate"] == 0
            if mode == "catalog":
                assert any(
                    e["event"] == "tool_result"
                    and e["payload"].get("name") == "lookup_price"
                    and e["payload"]["error"]["code"] == "STORAGE_ERROR"
                    for e in traces
                )
        return
    assert result.returncode == 0, result.stderr
    assert "Reviewing oldest invoices first" in result.stderr
    assert "deterministic" not in result.stderr
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    assert [r["identity"]["invoice_number"] for r in rows[:-1]] == ["INV-1", "INV-2"]
    assert rows[-1]["new_payments"] == 1 and rows[-1]["rejected"] == 1
    assert rows[-1]["final_inventory"]["WidgetA"] == 5
    assert len(paths_seen) == 4 and set(paths_seen) == {"/v1/chat/completions"}
    run_dir = json_run_dir
    with sqlite3.connect(run_dir / "inventory.db") as db:
        assert db.execute("SELECT count(*) FROM payments").fetchone()[0] == 1
    audits = [json.loads(line) for line in (run_dir / "audit.jsonl").read_text().splitlines()]
    assert all(a["validation"] for a in audits)
    assert audits[0]["validation"]["catalog_evidence"]["prices"] == {"WidgetA": "250.00"}
    assert audits[0]["review"]["accepted"]
    assert audits[1].get("review") is None
    assert audits[1]["attempts"]["vp_calls"] == 0
    assert "local-http-test-sentinel" not in result.stdout + result.stderr

    assert all("trace" not in row for row in rows)
    assert "Trace:" not in result.stdout + result.stderr
    traces = [json.loads(line) for line in (run_dir / "trace.jsonl").read_text().splitlines()]
    usage = [e for e in traces if e["event"] == "model_usage"]
    assert len(usage) == 4
    assert usage[0]["payload"]["usage"]["prompt_tokens"] == 100
    metrics = json.loads((run_dir / "metrics.json").read_text())
    assert metrics == rows[-1]["metrics"]
    assert metrics["prompt_tokens"] == 400 and metrics["completion_tokens"] == 80
    assert metrics["total_tokens"] == 480 and metrics["cached_prompt_tokens"] == 120
    assert metrics["model_calls"] == metrics["transport_attempts"] == 4
    assert metrics["estimated_api_cost_usd"] == "0.000020"
    assert metrics["cost_complete"] and metrics["usage_complete"]
    assert metrics["operational_error_rate"] == 0 and metrics["rejection_rate"] == 0.5
    assert metrics["blocked_payment_exposure_usd"] == "80.00"
    assert set(metrics["agent_latency_ms"]) == {"ingestion", "validation", "approval", "payment"}
    assert set(metrics["model_latency_ms"]) == {"inventory", "vp_propose", "vp_critique"}
    assert metrics["wall_ms"] >= sum(metrics["agent_latency_ms"].values())

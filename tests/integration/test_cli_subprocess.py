"""Whole CLI process through a local HTTP provider double, with real adapter and SQLite."""

import json
import os
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from invoice_agent.models import LLMRequest
from tests.doubles import ScenarioLLM
from tests.integration.test_batch import invoice


def test_whole_cli_process_through_http_and_committed_ledger(tmp_path):
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
            response = model.complete(
                LLMRequest(
                    phase=phase, messages=payload["messages"], tools=payload.get("tools", [])
                )
            )
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
    invoice(inputs / "a-later.json", "INV-2", "2026-02-01", 8)
    invoice(inputs / "z-earlier.json", "INV-1", "2026-01-01", 10)
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
            ],
            cwd=Path(__file__).resolve().parents[2],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert result.returncode == 0, result.stderr
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    assert [r["identity"]["invoice_number"] for r in rows[:-1]] == ["INV-1", "INV-2"]
    assert rows[-1]["new_payments"] == 1 and rows[-1]["rejected"] == 1
    assert rows[-1]["final_inventory"]["WidgetA"] == 5
    assert len(paths_seen) == 6 and set(paths_seen) == {"/v1/chat/completions"}
    run_dir = next((tmp_path / "runs").iterdir())
    with sqlite3.connect(run_dir / "inventory.db") as db:
        assert db.execute("SELECT count(*) FROM payments").fetchone()[0] == 1
    audits = [json.loads(line) for line in (run_dir / "audit.jsonl").read_text().splitlines()]
    assert all(a["validation"] and a["review"] for a in audits)
    assert "local-http-test-sentinel" not in result.stdout + result.stderr

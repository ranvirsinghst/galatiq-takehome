"""The evaluator executes the same graphs, SQLite transactions, and fixture readers."""

import json
import subprocess
import sys
from pathlib import Path

from scripts import evaluate

ROOT = Path(__file__).resolve().parents[2]


def test_offline_evaluator_real_command():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/evaluate.py")],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["mode"] == "offline_recorded_extraction_scripted_review"
    assert payload["passed"] and len(payload["cases"]) == 23
    assert all(case["passed"] and not case["diffs"] for case in payload["cases"])


def test_evaluation_failure_is_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(
        evaluate,
        "evaluate",
        lambda live, suite: {
            "mode": "offline_recorded_extraction_scripted_review",
            "passed": False,
            "cases": [
                {"case": "synthetic", "passed": False, "diffs": ["expected approved, got rejected"]}
            ],
        },
    )
    assert evaluate.main(["--suite", "isolated"]) == 1
    assert "expected approved" in capsys.readouterr().out

"""Combined review preserves rule evidence and spends VP calls only on eligible invoices."""

import json
from pathlib import Path

import pytest

from invoice_agent.models import LLMResponse
from tests.doubles import ScenarioLLM
from tests.fixture_model import FixtureLLM
from tests.integration.test_batch import execute, invoice


def test_1002_rules_skip_vp_and_keep_warning_and_audit(tmp_path):
    llm = FixtureLLM()
    result = execute([Path("data/invoices/invoice_1002.txt")], tmp_path, llm=llm)
    item = result.results[0]
    assert item.decision == "rejected"
    assert item.payment.status == "not_paid"
    assert item.review is None
    assert item.validation is not None and item.validation.requires_high_value_review
    assert any(f.code == "INSUFFICIENT_STOCK" for f in item.findings)
    assert any(f.severity == "warning" and "due" in f.message.lower() for f in item.findings)
    assert item.attempts["vp_calls"] == item.attempts["vp_revisions"] == 0
    assert item.attempts["tool_rounds"] == 1
    assert not any(r.phase.startswith("vp_") for r in llm.requests)
    assert result.summary.new_payments == 0
    assert result.summary.final_inventory == {
        "WidgetA": 15,
        "WidgetB": 10,
        "GadgetX": 5,
        "FakeItem": 0,
    }
    rule_events = [e for e in result.trace if e.event == "review_rejected_by_rules"]
    assert len(rule_events) == 1
    assert "INSUFFICIENT_STOCK" in rule_events[0].payload["codes"]
    assert rule_events[0].payload["vp_skipped"]
    stages = [e.stage for e in result.trace if e.event == "agent_stage_finished"]
    assert "validation" in stages and "approval" not in stages and "review" not in stages


class HighValueVP(ScenarioLLM):
    def __init__(self, omit_checks=False, reject_critique=False):
        super().__init__()
        self.omit_checks = omit_checks
        self.reject_critique = reject_critique

    def complete(self, request):
        response = super().complete(request)
        if request.phase in ("vp_propose", "vp_revise") and self.omit_checks:
            return LLMResponse(content={**response.content, "checks": {}})
        if request.phase == "vp_critique" and self.reject_critique:
            return LLMResponse(
                content={
                    "verdict": "revise",
                    "reason_summary": "The proposal leaves the concern unexplained.",
                    "issues": ["Unresolved concern"],
                    "required_changes": ["Explain concern"],
                }
            )
        return response


@pytest.mark.parametrize(
    "omit_checks,reject_critique,paid",
    [(False, False, True), (True, False, False), (False, True, False)],
)
def test_eligible_high_value_retains_checklist_and_critique(
    tmp_path, omit_checks, reject_critique, paid
):
    path = invoice(tmp_path / "high.json", "HIGH", "2026-01-01", 1)
    data = json.loads(path.read_text())
    data["line_items"][0]["unit_price"] = 250
    data["subtotal"] = 250
    data["shipping"] = 11750
    data["total"] = 12000
    path.write_text(json.dumps(data))
    llm = HighValueVP(omit_checks, reject_critique)
    result = execute([path], tmp_path, llm=llm)
    item = result.results[0]
    assert item.validation.requires_high_value_review
    assert not item.validation.blockers
    assert item.review is not None
    assert result.summary.new_payments == int(paid)
    assert result.summary.final_inventory["WidgetA"] == 15 - int(paid)
    assert {"vp_propose", "vp_critique"} <= {r.phase for r in llm.requests}
    assert item.attempts["vp_calls"] == (2 if paid else 6)
    stages = [e.stage for e in result.trace if e.event == "agent_stage_finished"]
    assert stages.count("validation") == stages.count("approval") == 1
    assert "review" not in stages


def test_incomplete_report_fails_closed_before_vp(tmp_path, monkeypatch):
    import invoice_agent.graph as graph

    real_validate = graph.validate_with_tools

    def incomplete(*args):
        outcome = real_validate(*args)
        return outcome.model_copy(
            update={"report": outcome.report.model_copy(update={"complete": False})}
        )

    monkeypatch.setattr(graph, "validate_with_tools", incomplete)
    llm = ScenarioLLM()
    result = execute([invoice(tmp_path / "valid.json", "VALID", "2026-01-01")], tmp_path, llm=llm)
    item = result.results[0]
    assert item.decision == "rejected" and item.review is None
    assert item.validation.complete is False
    assert any(f.code == "VALIDATION_INCOMPLETE" for f in item.findings)
    assert result.summary.new_payments == 0
    assert result.summary.final_inventory["WidgetA"] == 15
    assert [r.phase for r in llm.requests] == ["inventory"]

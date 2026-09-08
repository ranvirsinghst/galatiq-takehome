"""Scripted model boundary; deterministic policy remains in real application modules."""

from __future__ import annotations

import json
from typing import Any

from invoice_agent.models import LLMRequest, LLMResponse, LLMToolCall


class ScenarioLLM:
    """Respond to known phases; extraction must be independently provided by tests."""

    def __init__(self, extraction: dict[str, Any] | None = None, hostile: bool = False):
        self.requests: list[LLMRequest] = []
        self.extraction = extraction
        self.hostile = hostile

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        payload = json.loads(request.messages[-1]["content"])
        if request.phase == "ingestion":
            if self.extraction is None:
                raise AssertionError("Test requires an explicit extraction fixture")
            data = dict(self.extraction)
            ids = [e["evidence_id"] for e in payload["evidence"]]
            data["field_evidence"] = {
                field: ids for field in ("vendor", "invoice_number", "date", "due_date", "total")
            }
            data["line_items"] = [
                {**line, "evidence_refs": ids} for line in data.get("line_items", [])
            ]
            return LLMResponse(content=data)
        if request.phase == "inventory":
            return LLMResponse(
                tool_calls=[
                    LLMToolCall(
                        call_id=f"lookup-{len(self.requests)}",
                        name="lookup_inventory",
                        arguments={"items": payload["normalized_items"]},
                    )
                ]
            )
        if request.phase in ("vp_propose", "vp_revise"):
            report = payload["validation"]
            blocked = any(f["severity"] == "blocker" for f in report["findings"])
            return LLMResponse(
                content={
                    "decision": "approved" if self.hostile or not blocked else "rejected",
                    "reason_summary": "Scripted approval for orchestration testing."
                    if not blocked
                    else "Scripted rejection citing supplied findings.",
                    "finding_codes": [f["code"] for f in report["findings"]],
                    "checks": {
                        k: "Scripted acknowledgment of supplied evidence."
                        for k in (
                            "arithmetic",
                            "aggregate_stock",
                            "data_completeness",
                            "suspicious_signals",
                            "unavailable_checks",
                        )
                    },
                    "high_value_review": report.get("requires_high_value_review", False),
                }
            )
        if request.phase == "vp_critique":
            return LLMResponse(content={"verdict": "accept", "issues": [], "required_changes": []})
        raise AssertionError(f"Unexpected model phase: {request.phase}")

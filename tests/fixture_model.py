"""Recorded, manually reviewed extraction data. Never imported by production modules."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from invoice_agent.models import LLMResponse
from invoice_agent.readers import read_source
from tests.doubles import ScenarioLLM


class FixtureLLM(ScenarioLLM):
    def __init__(self, invoice_dir: Path | None = None):
        super().__init__()
        root = Path(__file__).resolve().parents[1]
        invoice_dir = invoice_dir or root / "data/invoices"
        manifest = json.loads((root / "tests/fixtures/expected/extractions.json").read_text())
        self.extractions = {}
        for name, extraction in manifest.items():
            source = read_source(invoice_dir / name, "manifest")
            self.extractions[hashlib.sha256(source.raw_text.encode()).hexdigest()] = extraction

    def complete(self, request):
        if request.phase != "ingestion":
            return super().complete(request)
        self.requests.append(request)
        payload = json.loads(request.messages[-1]["content"])
        digest = hashlib.sha256(payload["source_text"].encode()).hexdigest()
        if digest not in self.extractions:
            raise AssertionError("No independently reviewed extraction for this source")
        source_id = payload["evidence"][0]["source_id"]
        extraction = json.loads(
            json.dumps(self.extractions[digest]).replace("{source_id}", source_id)
        )
        return LLMResponse(content=extraction)

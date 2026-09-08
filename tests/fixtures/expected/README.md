# Reviewed fixture expectations

`extractions.json` contains manually specified source facts for each supplied TXT/PDF file. Evidence references use `{source_id}` as a run-local placeholder. The records were checked against actual text extracted from the supplied PDF bytes, including the OCR-like tokens in 1012. These are test doubles, not cached live model outputs or a claim of live extraction quality.

`outcomes.json` records independently specified isolated-run decisions, USD totals, and minimum finding codes. Additional correct warnings/findings are allowed. An approved expected case must still complete the actual graph and mock-payment transaction. The expectations are never regenerated from observed application decisions.

`tests.fixture_model.FixtureLLM` selects reviewed records using hashes of actual extracted source text, replaces only evidence source IDs, and scripts inventory/VP responses at the LLM boundary. Production modules never import these records or branch on invoice identity.

Run `uv run python scripts/evaluate.py` for 20 isolated fixtures plus three stateful folder scenarios. `--suite isolated` and `--suite stateful` select suites. `--live` substitutes actual xAI requests and reports a distinct mode; it requires credentials. All runs create temporary fresh SQLite stores and execute the real ingestion barrier, date ordering, validation, reflection, and local mock payment operations.

The full-folder expected balance was calculated from invoice dates: 1001 pays first (WidgetA 10, WidgetB 5), then 1004 pays (WidgetA 3, WidgetB 2). Remaining stock is WidgetA 2, WidgetB 3, GadgetX 5, FakeItem 0; total paid is USD 6,890.00. Later demands exceed stock or have other blockers. The independent equivalent-format scenario pays 1011 once; the revision scenario pays original 1004 and rejects its changed version.

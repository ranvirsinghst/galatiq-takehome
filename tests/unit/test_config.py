from decimal import Decimal

import pytest
from pydantic import ValidationError

from invoice_agent.config import Policy, load_settings


def test_policy_defaults_and_invalid_overrides():
    policy = Policy()
    assert policy.fx_rates["EUR"] == Decimal("1.10")
    assert policy.review_revisions == 2
    with pytest.raises(ValidationError):
        Policy(transport_retries=-1)
    with pytest.raises(ValidationError):
        Policy(fx_rates={"USD": Decimal("1"), "EUR": Decimal("NaN")})
    with pytest.raises(TypeError):
        policy.fx_rates["EUR"] = Decimal("2")


def test_environment_override_is_explicit_and_does_not_mutate(monkeypatch, tmp_path):
    monkeypatch.setenv("XAI_MODEL", "untouched")
    path = tmp_path / ".env"
    path.write_text("XAI_API_KEY=local-secret\nXAI_MODEL=file-model\n")
    settings = load_settings(path, {"XAI_MODEL": "explicit-model"})
    assert settings.model == "explicit-model"
    assert settings.api_key.get_secret_value() == "local-secret"
    assert "local-secret" not in repr(settings)
    import os

    assert os.environ["XAI_MODEL"] == "untouched"
    with pytest.raises(ValidationError):
        load_settings(environ={})

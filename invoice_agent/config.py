"""Explicit configuration loading: library imports never inspect .env."""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class Policy(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", arbitrary_types_allowed=True, validate_default=True
    )
    fx_rates: Mapping[str, Decimal] = Field(
        default_factory=lambda: {"USD": Decimal("1.00"), "EUR": Decimal("1.10")}
    )
    high_value_threshold: Decimal = Field(default=Decimal("10000.00"), gt=0)
    price_tolerance_ratio: Decimal = Field(default=Decimal("0.10"), ge=0, lt=1, allow_inf_nan=False)
    arithmetic_tolerance: Decimal = Field(default=Decimal("0.01"), ge=0)
    item_aliases: Mapping[str, str] = Field(
        default_factory=lambda: {
            "widgeta": "WidgetA",
            "widgetb": "WidgetB",
            "gadgetx": "GadgetX",
            "fakeitem": "FakeItem",
        }
    )
    extraction_retries: int = Field(default=2, ge=0, le=10)
    review_revisions: int = Field(default=2, ge=0, le=10)
    tool_rounds: int = Field(default=2, ge=1, le=10)
    transport_retries: int = Field(default=2, ge=0, le=10)
    request_timeout_seconds: float = Field(default=30, gt=0, le=300)

    @field_validator("fx_rates")
    @classmethod
    def finite_rates(cls, rates: Mapping[str, Decimal]) -> Mapping[str, Decimal]:
        if rates.get("USD") != Decimal("1") or any(
            not r.is_finite() or r <= 0 for r in rates.values()
        ):
            raise ValueError("FX rates must be positive finite decimals and USD must equal one")
        return MappingProxyType(dict(rates))

    @field_validator("item_aliases")
    @classmethod
    def immutable_aliases(cls, aliases: Mapping[str, str]) -> Mapping[str, str]:
        return MappingProxyType(dict(aliases))


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    api_key: SecretStr
    model: str = Field(default="grok-4.3", min_length=1)
    base_url: str = "https://api.x.ai/v1"
    timeout_seconds: float = Field(default=30, gt=0, le=300)

    @field_validator("api_key")
    @classmethod
    def nonempty_key(cls, value: SecretStr) -> SecretStr:
        if (
            not value.get_secret_value().strip()
            or value.get_secret_value() == "replace-with-your-local-key"
        ):
            raise ValueError("XAI_API_KEY must be configured")
        return value


def load_settings(
    env_file: Path | None = None, environ: Mapping[str, str] | None = None
) -> Settings:
    """Merge explicit dotenv with environment, without mutating os.environ."""
    values = dict(dotenv_values(env_file)) if env_file is not None and env_file.exists() else {}
    values.update(dict(os.environ if environ is None else environ))
    return Settings.model_validate(
        {
            "api_key": values.get("XAI_API_KEY", ""),
            "model": values.get("XAI_MODEL") or "grok-4.3",
            "base_url": values.get("XAI_BASE_URL") or "https://api.x.ai/v1",
            "timeout_seconds": values.get("XAI_TIMEOUT_SECONDS") or 30,
        }
    )

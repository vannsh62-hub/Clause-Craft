"""Application settings.

`OPENAI_API_KEY` is required and validated at import time. A missing key must fail
here, loudly, rather than thirty seconds into a drafting run at the first model call.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, PostgresDsn, TypeAdapter, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DSN = TypeAdapter(PostgresDsn)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str = Field(min_length=1)

    database_url: str = "postgresql+psycopg://contract:contract@localhost:5433/contract_drafting"

    # The SDK's default model changes between releases. Always set `model=` explicitly.
    orchestrator_model: str = "gpt-4.1"
    drafting_model: str = "gpt-4.1"
    retrieval_model: str = "gpt-4.1-mini"
    judge_model: str = "gpt-4.1-mini"
    #: Structured extraction, not judgement. Per-agent model choice is the main lever
    #: keeping Phase A cost-neutral as it adds agents — benchmark before raising it.
    intent_model: str = "gpt-4.1-mini"

    #: The three understanding agents. Classification feeds every future capability and
    #: gets the strongest model; metadata is cheap extraction and does not. This spread is
    #: the lever that keeps Phase A's six model calls from costing six full-price calls.
    understanding_model: str = "gpt-4.1"
    metadata_model: str = "gpt-4.1-mini"
    classification_model: str = "gpt-4.1"

    #: Phase B planning. Transformation planning is the pivot of the system and gets the
    #: strongest model; deciding section order is easier and does not.
    draft_plan_model: str = "gpt-4.1-mini"
    transformation_model: str = "gpt-4.1"

    #: Reference-document analysis. Summarising, not drafting — but it must reliably
    #: paraphrase rather than copy, which is a reasoning task, so not the cheapest model.
    reference_model: str = "gpt-4.1"

    #: Below this, the Intent Agent must ask rather than proceed. A drafting tool that
    #: confidently produces the wrong contract type is worse than one that asks.
    intent_confidence_threshold: float = 0.7

    #: Contract types the service will attempt. Deliberately an allow-list: being wrong
    #: about an unsupported type costs a question, while being wrong about a supported one
    #: costs a contract nobody should sign.
    supported_contract_types: tuple[str, ...] = ("nda", "service", "sla", "msa", "dpa")

    # Hard stops. Enforced in code — a prompt asking the agent to stop is not a stop condition.
    max_turns: int = 40
    max_draft_attempts: int = 3

    #: How many times one contract may stop to ask the user before it must draft with what it
    #: has. The confidence check is enforced in code, so an agent that keeps reporting low
    #: confidence keeps suspending the run — the user answers, it asks again, forever. Telling
    #: it to stop in the prompt is not a stop condition; this is.
    max_ask_rounds: int = 3
    judge_pass_score: int = 90

    #: The budget, in tokens rather than currency: token counts come back exact from the API,
    #: prices are deployment-specific and change. Counts sub-agent spend too.
    max_total_tokens: int = 250_000

    #: Generated documents. Local disk in development; object storage in production.
    storage_dir: str = "storage/generated"

    # Contract text and prompt bodies are never emitted above DEBUG.
    log_level: str = "INFO"

    @field_validator("openai_api_key")
    @classmethod
    def _reject_placeholder(cls, v: str) -> str:
        if v.strip() in {"", "changeme", "your-key-here"}:
            raise ValueError("OPENAI_API_KEY is unset or a placeholder")
        return v

    @field_validator("database_url")
    @classmethod
    def _must_be_a_postgres_dsn(cls, v: str) -> str:
        _DSN.validate_python(v)
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# Import-time validation. Deliberate: see module docstring.
settings = get_settings()

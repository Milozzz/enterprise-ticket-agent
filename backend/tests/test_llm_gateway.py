from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import LLMUsageRecord
from app.llm.gateway import LLMCallContext, LLMGateway, ModelCandidate


class Classification(BaseModel):
    intent: str


class FakeModel:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.schema = None

    def bind_tools(self, tools):
        return self

    def with_structured_output(self, schema, include_raw=False):
        self.schema = schema
        self.include_raw = include_raw
        return self

    async def ainvoke(self, messages):
        if self.error:
            raise self.error
        raw = SimpleNamespace(
            content="ok",
            usage_metadata={"input_tokens": 12, "output_tokens": 4, "total_tokens": 16},
            response_metadata={},
        )
        if self.schema:
            return {"parsed": self.schema(intent="refund"), "raw": raw}
        return self.response or raw


@pytest.fixture
async def usage_session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'usage.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(LLMUsageRecord.__table__.create)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_gateway_fails_over_and_persists_usage(monkeypatch, usage_session_factory):
    from app.llm import gateway as gateway_module

    monkeypatch.setattr(gateway_module.settings, "google_api_key", "test-google")
    monkeypatch.setattr(gateway_module.settings, "openai_api_key", "test-openai")

    def factory(provider: str, model: str, temperature: float):
        if provider == "gemini":
            return FakeModel(error=RuntimeError("provider unavailable"))
        return FakeModel()

    gateway = LLMGateway(model_factory=factory, session_factory=usage_session_factory)
    gateway._routes = {
        "classify_intent": [
            ModelCandidate("gemini", "gemini-2.0-flash"),
            ModelCandidate("openai", "gpt-4o-mini"),
        ]
    }
    result = await gateway.ainvoke(
        "classify_intent",
        ["hello"],
        context=LLMCallContext(thread_id="thread-cost", trace_id="trace-cost"),
        schema=Classification,
        prompt_version="classifier-v2",
        prompt_variant="canary",
        prompt_rollout_bucket=7,
    )

    assert result.provider == "openai"
    assert result.fallback_index == 1
    assert result.output.intent == "refund"
    assert result.total_tokens == 16

    async with usage_session_factory() as session:
        records = (await session.execute(select(LLMUsageRecord).order_by(LLMUsageRecord.id))).scalars().all()
    assert [(record.provider, record.success) for record in records] == [("gemini", False), ("openai", True)]
    assert records[-1].thread_id == "thread-cost"
    assert records[-1].total_tokens == 16
    assert records[-1].prompt_version == "classifier-v2"
    assert records[-1].prompt_variant == "canary"
    assert records[-1].prompt_rollout_bucket == 7


@pytest.mark.asyncio
async def test_gateway_uses_provider_neutral_structured_output(monkeypatch, usage_session_factory):
    from app.llm import gateway as gateway_module

    monkeypatch.setattr(gateway_module.settings, "anthropic_api_key", "test-anthropic")
    gateway = LLMGateway(
        model_factory=lambda provider, model, temperature: FakeModel(),
        session_factory=usage_session_factory,
    )
    gateway._routes = {"policy": [ModelCandidate("anthropic", "claude-test")]}

    result = await gateway.ainvoke("policy", ["input"], schema=Classification)

    assert isinstance(result.output, Classification)
    assert result.output.intent == "refund"
    assert result.provider == "anthropic"

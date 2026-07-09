from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from typing import Any, Callable

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.database import AsyncSessionLocal
from app.db.models import LLMUsageRecord

logger = get_logger(__name__)
settings = get_settings()


@dataclass(frozen=True)
class ModelCandidate:
    provider: str
    model: str


@dataclass(frozen=True)
class LLMCallContext:
    thread_id: str = "unknown"
    trace_id: str | None = None
    tenant_id: str | None = None


@dataclass
class LLMCallResult:
    output: Any
    raw_message: Any
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: Decimal = field(default_factory=lambda: Decimal("0"))
    latency_ms: int = 0
    fallback_index: int = 0
    prompt_version: str | None = None
    prompt_variant: str | None = None
    prompt_rollout_bucket: int | None = None


class LLMProvidersExhausted(RuntimeError):
    pass


class LLMTenantBudgetExceeded(RuntimeError):
    """B8: 租户当日 token 用量超出配额，本次调用被拒绝（下游节点会走规则降级）。"""

    pass


DEFAULT_PRICE_CATALOG = {
    # Configurable estimates in USD per one million tokens. Keep production
    # prices in LLM_PRICE_CATALOG_JSON because providers can change pricing.
    "gemini:gemini-2.0-flash": {"input": "0.10", "output": "0.40"},
    "openai:gpt-4o-mini": {"input": "0.15", "output": "0.60"},
    "anthropic:claude-3-5-haiku-latest": {"input": "0.80", "output": "4.00"},
}


class GatewayRunnable:
    """Small Runnable-compatible facade used by existing LangGraph nodes."""

    def __init__(
        self,
        gateway: "LLMGateway",
        node_name: str,
        *,
        context: LLMCallContext | None = None,
        schema: Any = None,
        tools: list[Any] | None = None,
        temperature: float | None = None,
        timeout_seconds: float = 15.0,
        prompt_version: str | None = None,
        prompt_variant: str | None = None,
        prompt_rollout_bucket: int | None = None,
    ) -> None:
        self.gateway = gateway
        self.node_name = node_name
        self.context = context
        self.schema = schema
        self.tools = tools
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self.prompt_version = prompt_version
        self.prompt_variant = prompt_variant
        self.prompt_rollout_bucket = prompt_rollout_bucket

    async def ainvoke(self, messages: list[Any]) -> Any:
        result = await self.gateway.ainvoke(
            self.node_name,
            messages,
            context=self.context,
            schema=self.schema,
            tools=self.tools,
            temperature=self.temperature,
            timeout_seconds=self.timeout_seconds,
            prompt_version=self.prompt_version,
            prompt_variant=self.prompt_variant,
            prompt_rollout_bucket=self.prompt_rollout_bucket,
        )
        return result.output


class LLMGateway:
    def __init__(
        self,
        *,
        model_factory: Callable[[str, str, float], Any] | None = None,
        session_factory=AsyncSessionLocal,
    ) -> None:
        self._model_factory = model_factory or self._build_model
        self._session_factory = session_factory
        self._routes = self._load_routes()
        self._prices = self._load_prices()
        # B8: 每租户当日用量缓存 {tenant_id: (checked_at_monotonic, used_tokens)}，
        # 避免每次 LLM 调用都打一次 SUM 查询。
        self._budget_cache: dict[str, tuple[float, int]] = {}

    async def _tenant_tokens_used_today(self, tenant_id: str) -> int:
        cached = self._budget_cache.get(tenant_id)
        if cached and (time.monotonic() - cached[0]) < 60:
            return cached[1]
        from datetime import datetime, timezone

        from sqlalchemy import func, select

        day_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=None
        )
        async with self._session_factory() as session:
            used = await session.scalar(
                select(func.coalesce(func.sum(LLMUsageRecord.total_tokens), 0)).where(
                    LLMUsageRecord.tenant_id == tenant_id,
                    LLMUsageRecord.created_at >= day_start,
                )
            )
        used_int = int(used or 0)
        self._budget_cache[tenant_id] = (time.monotonic(), used_int)
        return used_int

    async def _enforce_tenant_budget(self, node_name: str, context: LLMCallContext | None) -> None:
        """B8: 每租户日 token 预算硬闸门。budget<=0 时关闭（默认关闭）。"""
        budget = int(getattr(settings, "llm_tenant_daily_token_budget", 0) or 0)
        if budget <= 0:
            return
        tenant_id = (context.tenant_id if context else None) or settings.default_tenant_id
        try:
            used = await self._tenant_tokens_used_today(tenant_id)
        except Exception as exc:  # 预算查询失败不阻断业务（fail-open，仅记录）
            logger.warning("llm_budget_check_failed", tenant_id=tenant_id, error=str(exc))
            return
        if used >= budget:
            logger.warning(
                "llm_tenant_budget_exceeded",
                tenant_id=tenant_id,
                node=node_name,
                used_tokens=used,
                budget_tokens=budget,
            )
            raise LLMTenantBudgetExceeded(
                f"Tenant {tenant_id} exceeded daily LLM token budget "
                f"({used}/{budget}); call to {node_name} rejected"
            )

    def runnable(
        self,
        node_name: str,
        *,
        context: LLMCallContext | None = None,
        schema: Any = None,
        tools: list[Any] | None = None,
        temperature: float | None = None,
        timeout_seconds: float = 15.0,
        prompt_version: str | None = None,
        prompt_variant: str | None = None,
        prompt_rollout_bucket: int | None = None,
    ) -> GatewayRunnable:
        return GatewayRunnable(
            self,
            node_name,
            context=context,
            schema=schema,
            tools=tools,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
            prompt_version=prompt_version,
            prompt_variant=prompt_variant,
            prompt_rollout_bucket=prompt_rollout_bucket,
        )

    async def ainvoke(
        self,
        node_name: str,
        messages: list[Any],
        *,
        context: LLMCallContext | None = None,
        schema: Any = None,
        tools: list[Any] | None = None,
        temperature: float | None = None,
        timeout_seconds: float = 15.0,
        prompt_version: str | None = None,
        prompt_variant: str | None = None,
        prompt_rollout_bucket: int | None = None,
    ) -> LLMCallResult:
        candidates = self.candidates_for(node_name)
        if not candidates:
            raise LLMProvidersExhausted(f"No configured LLM provider is available for {node_name}")

        # B8: 预算闸门在任何 provider 调用之前执行；超额直接拒绝，
        # 各节点已有的异常降级路径（规则 fallback + degraded 标注）自然接管。
        await self._enforce_tenant_budget(node_name, context)

        errors: list[str] = []
        for fallback_index, candidate in enumerate(candidates):
            started = time.perf_counter()
            try:
                model = self._model_factory(
                    candidate.provider,
                    candidate.model,
                    settings.model_temperature if temperature is None else temperature,
                )
                runnable = model
                if tools:
                    runnable = runnable.bind_tools(tools)
                if schema is not None:
                    runnable = runnable.with_structured_output(schema, include_raw=True)

                response = await asyncio.wait_for(
                    runnable.ainvoke(messages),
                    timeout=timeout_seconds,
                )
                raw_message = response
                output = response
                if schema is not None and isinstance(response, dict) and "parsed" in response:
                    output = response.get("parsed")
                    raw_message = response.get("raw")

                prompt_tokens, completion_tokens, total_tokens = self._extract_usage(raw_message)
                cost = self._estimate_cost(candidate, prompt_tokens, completion_tokens)
                latency_ms = max(0, int((time.perf_counter() - started) * 1000))
                result = LLMCallResult(
                    output=output,
                    raw_message=raw_message,
                    provider=candidate.provider,
                    model=candidate.model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    estimated_cost_usd=cost,
                    latency_ms=latency_ms,
                    fallback_index=fallback_index,
                    prompt_version=prompt_version,
                    prompt_variant=prompt_variant,
                    prompt_rollout_bucket=prompt_rollout_bucket,
                )
                await self._persist_usage(node_name, context, result, success=True)
                return result
            except Exception as exc:
                latency_ms = max(0, int((time.perf_counter() - started) * 1000))
                error = f"{candidate.provider}/{candidate.model}: {type(exc).__name__}: {exc}"
                errors.append(error)
                await self._persist_failure(
                    node_name,
                    context,
                    candidate,
                    fallback_index,
                    latency_ms,
                    exc,
                    prompt_version=prompt_version,
                    prompt_variant=prompt_variant,
                    prompt_rollout_bucket=prompt_rollout_bucket,
                )
                logger.warning(
                    "llm_provider_failed",
                    node=node_name,
                    provider=candidate.provider,
                    model=candidate.model,
                    fallback_index=fallback_index,
                    error=str(exc),
                )

        raise LLMProvidersExhausted("; ".join(errors))

    def candidates_for(self, node_name: str) -> list[ModelCandidate]:
        configured = self._routes.get(node_name) or self._routes.get("default")
        candidates = configured or self._default_candidates()
        return [candidate for candidate in candidates if self._provider_enabled(candidate.provider)]

    def _load_routes(self) -> dict[str, list[ModelCandidate]]:
        if not settings.llm_node_routes_json.strip():
            return {}
        try:
            payload = json.loads(settings.llm_node_routes_json)
            return {
                node: [
                    ModelCandidate(str(item["provider"]).lower(), str(item["model"]))
                    for item in candidates
                ]
                for node, candidates in payload.items()
            }
        except (TypeError, ValueError, KeyError) as exc:
            logger.warning("invalid_llm_node_routes", error=str(exc))
            return {}

    def _load_prices(self) -> dict[str, dict[str, str]]:
        prices = dict(DEFAULT_PRICE_CATALOG)
        if settings.llm_price_catalog_json.strip():
            try:
                prices.update(json.loads(settings.llm_price_catalog_json))
            except (TypeError, ValueError) as exc:
                logger.warning("invalid_llm_price_catalog", error=str(exc))
        return prices

    def _default_candidates(self) -> list[ModelCandidate]:
        models = {
            "gemini": settings.gemini_model,
            "openai": settings.openai_model,
            "anthropic": settings.anthropic_model,
        }
        ordered = [settings.llm_default_provider.lower(), "gemini", "openai", "anthropic"]
        seen: set[str] = set()
        candidates = []
        for provider in ordered:
            if provider in models and provider not in seen:
                seen.add(provider)
                candidates.append(ModelCandidate(provider, models[provider]))
        return candidates

    @staticmethod
    def _provider_enabled(provider: str) -> bool:
        return bool(
            {
                "gemini": settings.google_api_key,
                "openai": settings.openai_api_key,
                "anthropic": settings.anthropic_api_key,
            }.get(provider, "")
        )

    @staticmethod
    def _build_model(provider: str, model: str, temperature: float) -> Any:
        if provider == "gemini":
            from langchain_google_genai import ChatGoogleGenerativeAI

            return ChatGoogleGenerativeAI(
                model=model,
                google_api_key=settings.google_api_key,
                temperature=temperature,
            )
        if provider == "openai":
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(model=model, api_key=settings.openai_api_key, temperature=temperature)
        if provider == "anthropic":
            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(
                model=model,
                api_key=settings.anthropic_api_key,
                temperature=temperature,
            )
        raise ValueError(f"Unsupported LLM provider: {provider}")

    @staticmethod
    def _extract_usage(message: Any) -> tuple[int, int, int]:
        if message is None:
            return 0, 0, 0
        usage = getattr(message, "usage_metadata", None) or {}
        metadata = getattr(message, "response_metadata", None) or {}
        token_usage = metadata.get("token_usage") or metadata.get("usage") or {}
        prompt = int(
            usage.get("input_tokens")
            or token_usage.get("prompt_tokens")
            or token_usage.get("input_tokens")
            or 0
        )
        completion = int(
            usage.get("output_tokens")
            or token_usage.get("completion_tokens")
            or token_usage.get("output_tokens")
            or 0
        )
        total = int(usage.get("total_tokens") or token_usage.get("total_tokens") or prompt + completion)
        return prompt, completion, total

    def _estimate_cost(
        self,
        candidate: ModelCandidate,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> Decimal:
        price = self._prices.get(f"{candidate.provider}:{candidate.model}", {})
        input_cost = Decimal(str(price.get("input", "0"))) * Decimal(prompt_tokens) / Decimal(1_000_000)
        output_cost = Decimal(str(price.get("output", "0"))) * Decimal(completion_tokens) / Decimal(1_000_000)
        return (input_cost + output_cost).quantize(Decimal("0.00000001"))

    async def _persist_usage(
        self,
        node_name: str,
        context: LLMCallContext | None,
        result: LLMCallResult,
        *,
        success: bool,
        error_code: str | None = None,
    ) -> None:
        call_context = context or LLMCallContext()
        price = self._prices.get(f"{result.provider}:{result.model}", {})
        input_cost = (
            Decimal(str(price.get("input", "0")))
            * Decimal(result.prompt_tokens)
            / Decimal(1_000_000)
        ).quantize(Decimal("0.00000001"))
        output_cost = (
            Decimal(str(price.get("output", "0")))
            * Decimal(result.completion_tokens)
            / Decimal(1_000_000)
        ).quantize(Decimal("0.00000001"))
        try:
            async with self._session_factory() as session:
                session.add(
                    LLMUsageRecord(
                        tenant_id=call_context.tenant_id or settings.default_tenant_id,
                        thread_id=call_context.thread_id,
                        trace_id=call_context.trace_id,
                        node_name=node_name,
                        provider=result.provider,
                        model=result.model,
                        prompt_tokens=result.prompt_tokens,
                        completion_tokens=result.completion_tokens,
                        total_tokens=result.total_tokens,
                        input_cost_usd=input_cost,
                        output_cost_usd=output_cost,
                        total_cost_usd=result.estimated_cost_usd,
                        latency_ms=result.latency_ms,
                        success=success,
                        fallback_index=result.fallback_index,
                        error_code=error_code,
                        prompt_version=result.prompt_version,
                        prompt_variant=result.prompt_variant,
                        prompt_rollout_bucket=result.prompt_rollout_bucket,
                    )
                )
                await session.commit()
        except Exception as exc:
            logger.warning("llm_usage_persist_failed", node=node_name, error=str(exc))

    async def _persist_failure(
        self,
        node_name: str,
        context: LLMCallContext | None,
        candidate: ModelCandidate,
        fallback_index: int,
        latency_ms: int,
        error: Exception,
        prompt_version: str | None = None,
        prompt_variant: str | None = None,
        prompt_rollout_bucket: int | None = None,
    ) -> None:
        await self._persist_usage(
            node_name,
            context,
            LLMCallResult(
                output=None,
                raw_message=None,
                provider=candidate.provider,
                model=candidate.model,
                latency_ms=latency_ms,
                fallback_index=fallback_index,
                prompt_version=prompt_version,
                prompt_variant=prompt_variant,
                prompt_rollout_bucket=prompt_rollout_bucket,
            ),
            success=False,
            error_code=type(error).__name__[:100],
        )


@lru_cache
def get_llm_gateway() -> LLMGateway:
    return LLMGateway()

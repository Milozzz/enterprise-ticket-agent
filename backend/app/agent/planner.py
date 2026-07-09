"""A4: 工具自主规划层（plan-execute），架在 tool_gateway 治理边界之上。

定位：把"节点硬编码工具顺序"升级为"LLM 规划工具序列"，但**执行权仍完全
在 tool_gateway 手里**——每一步都过授权、input_schema 校验、熔断、审计，
LLM 只能在白名单工具内提议顺序与参数，不能绕过任何治理闸门。

安全设计：
- 默认关闭（AGENT_PLANNER_ENABLED=0），按场景配置 planner.enabled 双开关。
- 计划验证：工具必须在场景白名单内、参数过 input_schema 校验、步数上限。
- 执行策略：顺序执行、失败即停（不让 LLM 在失败后自由发挥）、逐步审计。
- LLM 不可用 / 计划无效 → 返回空结果，调用方降级回静态配置的工具序列。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from pydantic import BaseModel, Field

from app.agent.dependencies import get_agent_dependencies
from app.agent.tool_gateway import (
    TOOL_SPECS,
    ToolExecutionResult,
    execute_erp_connector_tool_async,
    execute_tool,
    gateway_context_from_state,
)
from app.agent.utils import get_state_val
from app.core.config import get_settings
from app.core.logging import get_logger
from app.llm.gateway import LLMCallContext

logger = get_logger(__name__)

# F4（agent×数据结合层）：可供规划器调用的 ERP 只读工具。这些工具走 tool_gateway
# 的 async 连接器路径（治理/审计/trace 贯穿全在），只读、低风险，是规划器扩面的
# 安全起点——让规划器能"查订单→查未清项→查凭证"串起多步只读诊断。
ERP_READONLY_TOOLS = {"erp_get_order", "erp_query_doctype"}


async def _execute_planned_tool(
    tool_name: str,
    args: Mapping[str, Any],
    *,
    context,
    handlers: Mapping[str, Any],
):
    """按工具类型分派执行：ERP 连接器工具走 async 治理路径，其余走本地 handler。"""
    if tool_name in ERP_READONLY_TOOLS:
        return await execute_erp_connector_tool_async(tool_name, dict(args), context=context)
    return execute_tool(
        tool_name, dict(args), context=context, handler=handlers[tool_name]
    )


class PlannedStep(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class ToolPlan(BaseModel):
    steps: list[PlannedStep] = Field(default_factory=list)
    goal_understanding: str = ""


class ReactDecision(BaseModel):
    """ReAct 单步决策：观察执行历史后决定下一步。"""

    action: str = "finish"  # "call_tool" | "finish"
    tool: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    final_summary: str = ""


@dataclass
class PlanExecution:
    plan: ToolPlan | None
    results: list[ToolExecutionResult] = field(default_factory=list)
    used_planner: bool = False
    degraded: bool = False
    aborted_reason: str | None = None
    mode: str = "plan_execute"
    react_trace: list[dict[str, Any]] = field(default_factory=list)

    @property
    def success(self) -> bool:
        # ReAct 允许"某步失败 → 观察 → 换路补救"，因此成功判据是
        # 正常收尾（无 aborted_reason）且产生过结果，而非每一步都成功。
        return bool(self.results) and self.aborted_reason is None


_PLANNER_PROMPT = """你是企业流程编排器。根据用户请求与已提取的槽位，从下列白名单工具中\
规划一个执行序列（1~{max_steps} 步）。

可用工具（只能使用这些，参数必须符合 schema）：
{catalog}

已提取槽位（可直接作为参数值使用）：
{slots}

要求：
- 只规划必要的步骤，能一步完成就不要多步
- args 的键必须严格来自该工具 schema 的 properties
- 不确定的参数留空字符串，不要编造
- goal_understanding 用一句中文复述你理解的目标
"""


def _tool_catalog(allowed_tools: set[str]) -> str:
    lines = []
    for name in sorted(allowed_tools):
        spec = TOOL_SPECS.get(name)
        if spec is None or not spec.enabled:
            continue
        schema = spec.input_schema or {}
        props = ", ".join(
            f"{key}: {value.get('type', 'any')}"
            for key, value in dict(schema.get("properties") or {}).items()
        )
        required = ", ".join(schema.get("required") or [])
        lines.append(
            f"- {name}: {spec.description}\n"
            f"  参数: {{{props}}}；必填: [{required}]；风险级别: {spec.risk_level}"
        )
    return "\n".join(lines)


def validate_plan(
    plan: ToolPlan,
    *,
    allowed_tools: set[str],
    max_steps: int,
) -> str | None:
    """返回校验失败原因；None 表示计划合法。"""
    if not plan.steps:
        return "计划为空"
    if len(plan.steps) > max_steps:
        return f"步数 {len(plan.steps)} 超过上限 {max_steps}"
    for index, step in enumerate(plan.steps):
        if step.tool not in allowed_tools:
            return f"步骤 {index + 1} 工具 '{step.tool}' 不在场景白名单内"
        spec = TOOL_SPECS.get(step.tool)
        if spec is None or not spec.enabled:
            return f"步骤 {index + 1} 工具 '{step.tool}' 未注册或已禁用"
        properties = set(dict((spec.input_schema or {}).get("properties") or {}))
        unknown = set(step.args) - properties
        if properties and unknown:
            return f"步骤 {index + 1} 含 schema 外参数: {sorted(unknown)}"
    return None


async def plan_tools(
    *,
    goal: str,
    slots: Mapping[str, Any],
    allowed_tools: set[str],
    state: Mapping[str, Any],
    max_steps: int,
) -> ToolPlan | None:
    """LLM 生成结构化计划；失败返回 None（调用方降级）。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    catalog = _tool_catalog(allowed_tools)
    if not catalog:
        return None
    slot_lines = "\n".join(f"- {k}: {v}" for k, v in dict(slots).items()) or "（无）"
    try:
        response = await get_agent_dependencies().llm.runnable(
            "tool_planner",
            context=LLMCallContext(
                thread_id=str(get_state_val(state, "thread_id", "unknown")),
                trace_id=str(get_state_val(state, "trace_id", "") or "") or None,
                tenant_id=str(get_state_val(state, "tenant_id", "") or "") or None,
            ),
            schema=ToolPlan,
            temperature=0.0,
            timeout_seconds=8.0,
        ).ainvoke([
            SystemMessage(
                content=_PLANNER_PROMPT.format(
                    max_steps=max_steps, catalog=catalog, slots=slot_lines
                )
            ),
            HumanMessage(content=goal),
        ])
        if isinstance(response, ToolPlan):
            return response
        if isinstance(response, dict):
            return ToolPlan(**response)
        return None
    except Exception as exc:
        logger.warning("tool_planner_llm_failed", error=str(exc))
        return None


_REACT_PROMPT = """你是企业流程编排器（ReAct 模式）。观察目标、槽位与已执行步骤的结果，\
决定下一步动作。

可用工具（只能使用这些，参数必须符合 schema）：
{catalog}

已提取槽位：
{slots}

已执行步骤及结果：
{history}

决策规则：
- 目标已达成 → action="finish"，并给出 final_summary（一句中文）
- 还需要调用工具 → action="call_tool"，给出 tool / args / reason
- 上一步失败时：先判断失败原因，能换参数或换工具补救则补救，不能则 finish 并说明
- args 的键必须严格来自该工具 schema 的 properties；不确定的值留空字符串
- 剩余可用步数：{remaining}。不要浪费步数。
"""


def _validate_single_step(
    tool: str,
    args: Mapping[str, Any],
    allowed_tools: set[str],
) -> str | None:
    if tool not in allowed_tools:
        return f"工具 '{tool}' 不在场景白名单内"
    spec = TOOL_SPECS.get(tool)
    if spec is None or not spec.enabled:
        return f"工具 '{tool}' 未注册或已禁用"
    properties = set(dict((spec.input_schema or {}).get("properties") or {}))
    unknown = set(args) - properties
    if properties and unknown:
        return f"含 schema 外参数: {sorted(unknown)}"
    return None


def _history_lines(react_trace: list[dict[str, Any]]) -> str:
    if not react_trace:
        return "（尚未执行任何步骤）"
    lines = []
    for index, entry in enumerate(react_trace):
        status = "成功" if entry.get("success") else f"失败: {entry.get('error') or '未知'}"
        data_excerpt = str(entry.get("data") or "")[:200]
        lines.append(
            f"{index + 1}. {entry.get('tool')} -> {status}"
            + (f"；结果摘要: {data_excerpt}" if data_excerpt else "")
        )
    return "\n".join(lines)


async def _react_decide(
    *,
    goal: str,
    slots: Mapping[str, Any],
    allowed_tools: set[str],
    react_trace: list[dict[str, Any]],
    remaining: int,
    state: Mapping[str, Any],
) -> ReactDecision | None:
    from langchain_core.messages import HumanMessage, SystemMessage

    catalog = _tool_catalog(allowed_tools)
    if not catalog:
        return None
    slot_lines = "\n".join(f"- {k}: {v}" for k, v in dict(slots).items()) or "（无）"
    try:
        response = await get_agent_dependencies().llm.runnable(
            "tool_planner_react",
            context=LLMCallContext(
                thread_id=str(get_state_val(state, "thread_id", "unknown")),
                trace_id=str(get_state_val(state, "trace_id", "") or "") or None,
                tenant_id=str(get_state_val(state, "tenant_id", "") or "") or None,
            ),
            schema=ReactDecision,
            temperature=0.0,
            timeout_seconds=8.0,
        ).ainvoke([
            SystemMessage(
                content=_REACT_PROMPT.format(
                    catalog=catalog,
                    slots=slot_lines,
                    history=_history_lines(react_trace),
                    remaining=remaining,
                )
            ),
            HumanMessage(content=goal),
        ])
        if isinstance(response, ReactDecision):
            return response
        if isinstance(response, dict):
            return ReactDecision(**response)
        return None
    except Exception as exc:
        logger.warning("react_decide_llm_failed", error=str(exc))
        return None


async def react_execute(
    state: Mapping[str, Any],
    *,
    scenario_id: str,
    goal: str,
    slots: Mapping[str, Any],
    allowed_tools: set[str],
    handlers: Mapping[str, Any],
    dry_run: bool = False,
) -> PlanExecution:
    """A4 完整形态：ReAct 步间重规划——执行一步、观察结果、再决定下一步。

    安全边界不变：白名单/schema/步数三重校验；新增连续失败上限，
    防止 LLM 在失败上打转。任何 LLM 决策失败：未产生结果则整体降级
    （调用方回静态序列），已产生结果则带 aborted_reason 终止。
    """
    settings = get_settings()
    max_steps = max(1, int(settings.agent_planner_max_steps))
    max_consecutive_failures = max(
        1, int(getattr(settings, "agent_planner_max_consecutive_failures", 2))
    )
    # ERP 只读工具无需本地 handler（走 async 连接器路径），单独并入可用集合
    effective_tools = (allowed_tools & set(handlers)) | (allowed_tools & ERP_READONLY_TOOLS)
    execution = PlanExecution(plan=None, mode="react")
    consecutive_failures = 0

    for step_no in range(max_steps):
        decision = await _react_decide(
            goal=goal,
            slots=slots,
            allowed_tools=effective_tools,
            react_trace=execution.react_trace,
            remaining=max_steps - step_no,
            state=state,
        )
        if decision is None:
            if not execution.results:
                return PlanExecution(plan=None, degraded=True, mode="react")
            execution.degraded = True
            execution.aborted_reason = "LLM 决策不可用，提前终止"
            break

        execution.used_planner = True
        if decision.action != "call_tool":
            logger.info(
                "react_finished",
                scenario=scenario_id,
                steps=len(execution.results),
                summary=decision.final_summary,
            )
            break

        invalid = _validate_single_step(decision.tool, decision.args, effective_tools)
        if invalid:
            execution.aborted_reason = f"步骤 {step_no + 1} 校验失败: {invalid}"
            logger.warning(
                "react_step_rejected", scenario=scenario_id, reason=invalid
            )
            break

        result = await _execute_planned_tool(
            decision.tool,
            dict(decision.args),
            context=gateway_context_from_state(
                state, actor_role="AGENT", scenario=scenario_id, dry_run=dry_run
            ),
            handlers=handlers,
        )
        execution.results.append(result)
        execution.react_trace.append(
            {
                "tool": decision.tool,
                "reason": decision.reason,
                "success": result.success,
                "error": result.error,
                "data": dict(result.data or {}) if result.success else None,
            }
        )
        if result.success:
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            logger.warning(
                "react_step_failed",
                scenario=scenario_id,
                step=step_no + 1,
                tool=decision.tool,
                error=result.error,
                consecutive_failures=consecutive_failures,
            )
            if consecutive_failures >= max_consecutive_failures:
                execution.aborted_reason = (
                    f"连续 {consecutive_failures} 步失败，终止执行"
                    f"（最后失败: {decision.tool}: {result.error}）"
                )
                break
    else:
        # 步数耗尽仍未 finish：不视为失败，但记录（LLM 未主动收尾）
        logger.info("react_steps_exhausted", scenario=scenario_id, steps=max_steps)

    return execution


async def plan_and_execute(
    state: Mapping[str, Any],
    *,
    scenario_id: str,
    goal: str,
    slots: Mapping[str, Any],
    allowed_tools: set[str],
    handlers: Mapping[str, Any],
    dry_run: bool = False,
) -> PlanExecution:
    """规划并执行工具序列。任何环节失败都不抛异常，让调用方走静态降级路径。

    模式由 AGENT_PLANNER_MODE 控制：
    - "react"（默认）：步间重规划——执行一步、观察结果、再决定下一步，失败可补救
    - "plan_execute"：一次规划、顺序执行、失败即停（更保守）
    两种模式下每步都经 execute_tool 完整治理（权限/schema 校验/熔断/幂等/审计）。
    """
    settings = get_settings()
    if not settings.agent_planner_enabled:
        return PlanExecution(plan=None)

    if str(getattr(settings, "agent_planner_mode", "react")).lower() == "react":
        return await react_execute(
            state,
            scenario_id=scenario_id,
            goal=goal,
            slots=slots,
            allowed_tools=allowed_tools,
            handlers=handlers,
            dry_run=dry_run,
        )

    max_steps = max(1, int(settings.agent_planner_max_steps))
    # 只允许"场景白名单 ∩ 已注册 handler"的工具
    # ERP 只读工具无需本地 handler（走 async 连接器路径），单独并入可用集合
    effective_tools = (allowed_tools & set(handlers)) | (allowed_tools & ERP_READONLY_TOOLS)
    plan = await plan_tools(
        goal=goal,
        slots=slots,
        allowed_tools=effective_tools,
        state=state,
        max_steps=max_steps,
    )
    if plan is None:
        return PlanExecution(plan=None, degraded=True)

    invalid_reason = validate_plan(plan, allowed_tools=effective_tools, max_steps=max_steps)
    if invalid_reason:
        logger.warning(
            "tool_plan_rejected", scenario=scenario_id, reason=invalid_reason
        )
        return PlanExecution(plan=plan, degraded=True, aborted_reason=invalid_reason)

    logger.info(
        "tool_plan_accepted",
        scenario=scenario_id,
        steps=[s.tool for s in plan.steps],
        goal_understanding=plan.goal_understanding,
    )

    execution = PlanExecution(plan=plan, used_planner=True)
    for index, step in enumerate(plan.steps):
        result = await _execute_planned_tool(
            step.tool,
            dict(step.args),
            context=gateway_context_from_state(
                state, actor_role="AGENT", scenario=scenario_id, dry_run=dry_run
            ),
            handlers=handlers,
        )
        execution.results.append(result)
        if not result.success:
            # 失败即停：不让 LLM 在失败后继续自由发挥
            execution.aborted_reason = (
                f"步骤 {index + 1}/{len(plan.steps)} ({step.tool}) 失败: {result.error}"
            )
            logger.warning(
                "tool_plan_aborted",
                scenario=scenario_id,
                step=index + 1,
                tool=step.tool,
                error=result.error,
            )
            break
    return execution

"""
意图识别节点
从用户输入中提取订单号、退款原因、用户意图
"""

import json
import re
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from app.agent.state import AgentState
from app.agent.utils import get_state_val
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

# 模块级单例：避免每次分类请求都重新创建 LLM 实例
# 使用结构化输出版本（response_mime_type=application/json）
_llm_structured: ChatGoogleGenerativeAI | None = None
_llm_fallback: ChatGoogleGenerativeAI | None = None


def _get_llm_structured() -> ChatGoogleGenerativeAI:
    """结构化 JSON 输出版本（Gemini 原生支持，100% 不返回非 JSON）"""
    global _llm_structured
    if _llm_structured is None:
        if not settings.google_api_key:
            raise ValueError("请配置 GOOGLE_API_KEY")
        _llm_structured = ChatGoogleGenerativeAI(
            model=settings.gemini_model,
            google_api_key=settings.google_api_key,
            temperature=settings.model_temperature,
            # Gemini 结构化输出：强制 100% 返回合法 JSON，无需正则提取
            generation_config={
                "response_mime_type": "application/json",
                "response_schema": {
                    "type": "object",
                    "properties": {
                        "intent": {
                            "type": "string",
                            "enum": ["refund", "query_order", "query_policy",
                                     "track_logistics", "escalate", "other"],
                        },
                        "order_id": {"type": "string"},
                        "reason": {
                            "type": "string",
                            "enum": ["damaged", "wrong_item", "not_received",
                                     "quality_issue", "other"],
                        },
                        "description": {"type": "string"},
                        "user_id": {"type": "string"},
                    },
                    "required": ["intent"],
                },
            },
        )
    return _llm_structured


def _get_llm_fallback() -> ChatGoogleGenerativeAI:
    """无结构化输出的降级版本（老模型兼容 / response_schema 不支持时）"""
    global _llm_fallback
    if _llm_fallback is None:
        if not settings.google_api_key:
            raise ValueError("请配置 GOOGLE_API_KEY")
        _llm_fallback = ChatGoogleGenerativeAI(
            model=settings.gemini_model,
            google_api_key=settings.google_api_key,
            temperature=settings.model_temperature,
        )
    return _llm_fallback

SYSTEM_PROMPT = """你是一个企业客服工单分类助手。
从用户输入中提取以下信息，以 JSON 格式返回：

{
  "intent": "refund" | "query_order" | "query_policy" | "track_logistics" | "escalate" | "other",
  "order_id": "订单号（如果提到）",
  "reason": "damaged" | "wrong_item" | "not_received" | "quality_issue" | "other",
  "description": "用户描述的问题（保留原文）",
  "user_id": "用户ID（如果提到，否则为 unknown）"
}

intent 说明：
- refund：用户要申请退款/退货
- query_order：用户查询某个具体订单的状态/进度
- query_policy：用户询问退款规则、政策、条件（如"七天无理由怎么算"、"退款要多久"、"什么情况可以退"）
- track_logistics：查询物流/快递状态
- escalate：要求升级处理/转人工
- other：其他无法分类的问题

只返回 JSON，不要其他文字。"""


async def classify_intent_node(state: AgentState) -> dict:
    """
    意图识别节点
    调用 LLM 分析用户输入，提取结构化信息
    """
    logger.info("node_start", node="classify_intent")

    # 发送 UI 事件：通知前端显示思考进度
    ui_event = {
        "type": "thinking_stream",
        "data": {
            "steps": [
                {
                    "step": "classifying",
                    "label": "意图识别",
                    "status": "running",
                    "detail": "正在分析您的请求...",
                }
            ]
        },
    }

    # 获取用户最新消息
    messages = get_state_val(state, "messages", [])
    user_message = ""
    for msg in reversed(messages):
        if hasattr(msg, "type") and msg.type == "human":
            user_message = msg.content
            break
        if isinstance(msg, dict) and msg.get("role") == "user":
            user_message = msg.get("content", "")
            break

    if not user_message:
        return {
            "error_message": "未找到用户输入",
            "current_step": "classify_intent",
            "ui_events": [ui_event],
        }

    # 调试：打印用户消息
    logger.info("processing_user_message", content=user_message)

    rule_parsed = _rule_classify(user_message)
    if _is_high_confidence_rule_match(rule_parsed):
        parsed = rule_parsed
        parsed["_method"] = "rules_fast_path"
    else:
        try:
            # 优先尝试 LLM 解析
            parsed = await _llm_classify(user_message)
        except Exception as e:
            logger.warning("llm_classify_failed_fallback_to_rules", error=str(e))
            # LLM 不可用时降级为规则引擎
            parsed = rule_parsed

    logger.info(
        "intent_classified",
        intent=parsed.get("intent"),
        order_id=parsed.get("order_id"),
        method=parsed.get("_method", "llm"),
    )

    intent = parsed.get("intent", "other")
    order_id = parsed.get("order_id", "")

    _intent_labels = {
        "refund":        "退款申请",
        "query_order":   "查询工单状态",
        "query_policy":  "政策咨询",
        "track_logistics": "查询物流",
        "escalate":      "升级处理",
        "other":         "其他咨询",
    }
    intent_label = _intent_labels.get(intent, intent)
    detail = f"识别意图：{intent_label}"
    if order_id:
        detail += f"，订单号 {order_id}"

    ui_event["data"]["steps"][0]["status"] = "done"
    ui_event["data"]["steps"][0]["detail"] = detail

    logger.info(
        "intent_classified",
        intent=intent,
        order_id=order_id,
        method=parsed.get("_method", "llm"),
    )

    return {
        "intent": intent,
        "order_id": order_id,
        "user_id": parsed.get("user_id", "unknown"),
        "refund_reason": parsed.get("reason", "other"),
        "refund_description": parsed.get("description", user_message),
        "current_step": "classify_intent_done",
        "ui_events": [ui_event],
    }


async def _llm_classify(user_message: str) -> dict:
    """
    调用 LLM 解析意图。

    优先使用 response_mime_type=application/json 结构化输出（Gemini 原生支持），
    保证 100% 返回合法 JSON，无需正则提取。
    结构化输出失败时降级为文本提取（兼容旧模型或 API 变更）。
    """
    import asyncio

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=user_message),
    ]

    # 优先：结构化 JSON 输出（无需正则）
    try:
        llm = _get_llm_structured()
        response = await asyncio.wait_for(llm.ainvoke(messages), timeout=5.0)
        result = json.loads(response.content)
        result["_method"] = "llm_structured"
        return result
    except Exception as e:
        logger.warning("llm_structured_output_failed", error=str(e))

    # 降级：文本输出 + 正则提取
    try:
        llm = _get_llm_fallback()
        response = await asyncio.wait_for(llm.ainvoke(messages), timeout=5.0)
        content = response.content
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if not json_match:
            raise ValueError(f"LLM 未返回有效 JSON: {content}")
        result = json.loads(json_match.group())
        result["_method"] = "llm_regex"
        return result
    except Exception as e:
        logger.error("llm_invoke_error", error=str(e))
        raise

def _is_high_confidence_rule_match(parsed: dict) -> bool:
    """Skip the LLM when deterministic rules already identify the production demo flows."""
    intent = parsed.get("intent", "other")
    order_id = parsed.get("order_id", "")
    reason = parsed.get("reason", "other")

    if intent == "refund" and order_id:
        return True
    if intent in {"query_order", "track_logistics"} and order_id:
        return True
    if intent == "query_policy" and not order_id:
        return True
    if reason != "other" and order_id:
        return True
    return False


def _rule_classify(user_message: str) -> dict:
    """
    规则引擎降级解析（无需 API Key）
    从用户输入中用正则提取订单号和退款原因
    """
    # 提取订单号：只匹配纯数字（6位以上），避免把中文一起吃进去
    # 支持："订单号 789012"、"订单789012"、"#789012"、"order 789012" 等格式
    order_match = re.search(
        r"(?:订单号?|单号|order)\s*(?:是|为|:|：)?\s*([A-Za-z]*-?\d[A-Za-z0-9_-]{3,})",
        user_message, re.IGNORECASE
    )
    if not order_match:
        order_match = re.search(
            r"#\s*([A-Za-z]*-?\d[A-Za-z0-9_-]{3,})",
            user_message,
            re.IGNORECASE,
        )
    if not order_match:
        # 退而求其次：消息里裸露的纯数字串（≥4位）也视为订单号
        order_match = re.search(r"\b(\d{4,})\b", user_message)
    order_id = order_match.group(1) if order_match else ""

    # 识别退款原因关键词
    reason_map = {
        "damaged":       ["破损", "损坏", "碎", "坏了", "摔", "裂", "坏的"],
        "wrong_item":    ["发错", "错误", "不对", "不符", "wrong"],
        "not_received":  ["未收到", "没收到", "没到", "丢失", "lost"],
        "quality_issue": ["质量", "劣质", "做工", "quality", "瑕疵"],
    }
    reason = "other"
    for key, keywords in reason_map.items():
        if any(kw in user_message for kw in keywords):
            reason = key
            break

    # 识别意图
    refund_keywords   = ["退款", "退货", "退钱", "申请退", "要退"]
    query_keywords    = ["查询", "查看", "状态", "进度", "怎么样了", "处理结果",
                         "查一下", "最新", "反馈", "什么时候", "到账", "结果"]
    # policy_keywords 必须在 refund_keywords 之前检查：
    # 政策询问句（"七天无理由退款怎么算"）也含"退款"，若先检查退款关键词会误分类
    policy_keywords   = ["规则", "政策", "条件", "规定", "怎么算", "几天",
                         "多久", "多少天", "无理由", "几个工作日", "什么情况", "可以退吗",
                         "流程", "需要审批", "审批吗",
                         "退款规则", "退款政策", "怎么退", "运费谁出", "运费怎么算",
                         "运费", "谁承担", "谁出", "谁付", "是我出", "商家出",
                         "需要我", "需要付", "要付",
                         "还能退", "可以退", "能退吗", "超过.*退", "几天内"]
    logistics_keywords= ["物流", "快递", "发货", "运输", "签收", "tracking"]
    # 查询类短语：含"退款"但实际是查状态（优先级高于 refund_keywords）
    query_refund_phrases = ["退款申请.*处理", "申请.*到哪", "退款.*什么时候",
                            "退款.*到账", "退款.*进度", "退款.*状态", "退款.*结果"]

    # 优先判断政策类问题（必须在 refund 之前，否则含"退货/退款"的政策问句会被误判）
    if "七天内不想要" in user_message:
        intent = "refund"
    elif any(kw in user_message for kw in policy_keywords) or \
       any(re.search(p, user_message) for p in ["还能退", "可以退吗", "超过.{1,4}天"]):
        intent = "query_policy"
    # 含"退款"但实为查询进度/状态的短语（优先于纯退款关键词）
    elif reason != "other":
        intent = "refund"
    elif any(re.search(p, user_message) for p in query_refund_phrases):
        intent = "query_order"
    elif any(kw in user_message for kw in refund_keywords):
        intent = "refund"
    elif any(kw in user_message for kw in logistics_keywords) and not any(kw in user_message for kw in refund_keywords):
        intent = "track_logistics"
    elif any(kw in user_message for kw in query_keywords):
        intent = "query_order"
    else:
        intent = "other"

    return {
        "intent": intent,
        "order_id": order_id,
        "reason": reason,
        "description": user_message,
        "user_id": "unknown",
        "_method": "rules",
    }

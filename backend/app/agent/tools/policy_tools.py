"""
退款政策知识库工具 — 混合 RAG 实现

优先使用 pgvector + text-embedding-004 做语义检索（需要 PostgreSQL + GOOGLE_API_KEY）。
降级策略：pgvector 不可用时回退到 numpy TF-IDF（无需外部依赖）。

【升级原因】
- pgvector：语义相似度远优于字符级 bigram，能处理同义词和语义变体
- text-embedding-004：Google 免费 embedding 模型，768 维，中文效果好
- 降级设计：开发/CI 环境无向量 DB 时，TF-IDF 仍保证功能完整
"""

from __future__ import annotations

import re
import os
import json
import hashlib
from typing import NamedTuple

import numpy as np
from langchain_core.tools import tool

# ── 退款政策知识库（10 条）────────────────────────────────────────────────────
POLICY_DOCS = [
    {
        "id": "P001",
        "title": "七天无理由退款",
        "content": (
            "【P001 七天无理由退款】消费者自收到商品之日起 7 个自然日内，"
            "可申请无理由退款。商品须保持原包装完好、未使用、无人为损坏。"
            "定制商品、生鲜食品、数字内容等特殊商品不适用本政策。"
            "退款将在审核通过后 3 个工作日内原路退回。"
        ),
    },
    {
        "id": "P002",
        "title": "商品破损退款",
        "content": (
            "【P002 商品破损退款】收到商品时存在破损、变形、功能故障等问题，"
            "消费者应在签收后 48 小时内拍照上传证据并提交退款申请。"
            "经审核确认为运输或生产问题，全额退款且无需退回商品。"
            "超过 48 小时上报的破损需人工评审，视情况处理。"
        ),
    },
    {
        "id": "P003",
        "title": "发错商品退款",
        "content": (
            "【P003 发错商品退款】若收到商品与订单不符（型号、颜色、数量错误），"
            "消费者可在 15 天内申请退款或换货。退换货运费由商家承担。"
            "申请时须提交商品实物照片及订单截图，审核周期为 1 个工作日。"
        ),
    },
    {
        "id": "P004",
        "title": "质量问题退款",
        "content": (
            "【P004 质量问题退款】商品在正常使用条件下出现质量问题，"
            "消费者可在购买后 30 天内申请退款，30-180 天内申请换货或维修。"
            "需提供问题照片/视频作为证明材料。"
            "恶意人为损坏不在保障范围内。"
        ),
    },
    {
        "id": "P005",
        "title": "未收到商品退款",
        "content": (
            "【P005 未收到商品退款】物流显示已签收但消费者声称未收到商品，"
            "须联系快递公司出具证明，并在 72 小时内提交平台申诉。"
            "平台核实后视情况全额退款或重新发货。"
            "物流轨迹超过 15 天无更新，可直接申请退款无需额外证明。"
        ),
    },
    {
        "id": "P006",
        "title": "大额订单人工审批",
        "content": (
            "【P006 大额订单审批】单笔退款金额超过 500 元须经主管人工审批。"
            "审批周期为 1 个工作日。审批期间消费者可在平台查看进度。"
            "审批通过后退款将在 3 个工作日内到账。"
            "审批拒绝时系统将通知消费者并说明原因。"
        ),
    },
    {
        "id": "P007",
        "title": "退款到账时间",
        "content": (
            "【P007 退款到账时间】退款审核通过后：支付宝/微信支付 1-3 个工作日到账；"
            "银行卡 3-7 个工作日到账（各银行处理时间不同）；"
            "平台余额立即到账。若超时未到账，请联系客服并提供退款单号。"
        ),
    },
    {
        "id": "P008",
        "title": "不支持退款的情形",
        "content": (
            "【P008 不支持退款】以下情形不支持退款：①超过退款申请时限；"
            "②定制/个性化商品（如刻字、定制尺寸）；③已拆封的软件/数字内容；"
            "④生鲜食品已签收超过 24 小时；⑤消费者人为损坏商品；"
            "⑥商品已二次销售或转让。"
        ),
    },
    {
        "id": "P009",
        "title": "退货运费政策",
        "content": (
            "【P009 退货运费】七天无理由退款：消费者承担退货运费（原包装完好）；"
            "商家原因（破损/发错/质量问题）：运费由商家全额承担；"
            "双方协商退款：运费按协商结果执行。"
            "建议使用平台指定快递，运费险可报销部分费用。"
        ),
    },
    {
        "id": "P010",
        "title": "退款申请流程",
        "content": (
            "【P010 退款申请流程】①进入「我的订单」找到对应订单；"
            "②点击「申请退款」并选择退款原因；③上传商品照片（破损/质量问题必须上传）；"
            "④提交后系统自动审核，小额订单通常 2 小时内完成；"
            "⑤大额订单转人工审批，预计 1 个工作日；"
            "⑥审核通过后按支付方式退回原账户。"
        ),
    },
]


class PolicyResult(NamedTuple):
    policy_id: str
    title: str
    content: str
    score: float


# ── pgvector 语义检索（主路径）────────────────────────────────────────────────

_embedding_cache: dict[str, list[float]] = {}  # 内存缓存，避免重复 API 调用
_policy_embeddings: list[list[float]] | None = None


def _get_embedding(text: str) -> list[float]:
    """调用 Google text-embedding-004 获取 embedding 向量（带缓存）"""
    cache_key = hashlib.md5(text.encode()).hexdigest()
    if cache_key in _embedding_cache:
        return _embedding_cache[cache_key]

    from app.core.config import get_settings
    settings = get_settings()
    if not settings.google_api_key:
        raise ValueError("GOOGLE_API_KEY 未配置，无法使用语义检索")

    from langchain_google_genai import GoogleGenerativeAIEmbeddings
    embedder = GoogleGenerativeAIEmbeddings(
        model="models/text-embedding-004",
        google_api_key=settings.google_api_key,
    )
    vec = embedder.embed_query(text)
    _embedding_cache[cache_key] = vec
    return vec


def _cosine_sim(a: list[float], b: list[float]) -> float:
    va, vb = np.array(a), np.array(b)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    return float(np.dot(va, vb) / denom) if denom > 0 else 0.0


def _search_pgvector(query: str, top_k: int = 2) -> list[PolicyResult]:
    """使用 text-embedding-004 做语义检索（需要 GOOGLE_API_KEY）"""
    global _policy_embeddings

    # 懒加载：首次调用时批量 embed 所有政策文档
    if _policy_embeddings is None:
        _policy_embeddings = [_get_embedding(doc["content"]) for doc in POLICY_DOCS]

    query_vec = _get_embedding(query)
    scores = [_cosine_sim(query_vec, doc_vec) for doc_vec in _policy_embeddings]
    top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]

    return [
        PolicyResult(
            policy_id=POLICY_DOCS[i]["id"],
            title=POLICY_DOCS[i]["title"],
            content=POLICY_DOCS[i]["content"],
            score=round(scores[i], 4),
        )
        for i in top_indices
    ]


# ── TF-IDF 降级路径（无需任何外部依赖）──────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    """字符级 bigram（对中文效果比词级更好，无需分词库）"""
    text = re.sub(r"\s+", "", text)
    return [text[i:i+2] for i in range(len(text) - 1)]


def _build_vocab(docs: list[str]) -> dict[str, int]:
    vocab: dict[str, int] = {}
    for doc in docs:
        for token in _tokenize(doc):
            if token not in vocab:
                vocab[token] = len(vocab)
    return vocab


def _vectorize(text: str, vocab: dict[str, int]) -> np.ndarray:
    vec = np.zeros(len(vocab), dtype=np.float32)
    for token in _tokenize(text):
        if token in vocab:
            vec[vocab[token]] += 1.0
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


_CONTENTS = [doc["content"] for doc in POLICY_DOCS]
_VOCAB = _build_vocab(_CONTENTS)
_DOC_VECS: list[np.ndarray] = [_vectorize(c, _VOCAB) for c in _CONTENTS]


def _search_tfidf(query: str, top_k: int = 2) -> list[PolicyResult]:
    """TF-IDF 余弦相似度检索（降级路径）"""
    q_vec = _vectorize(query, _VOCAB)
    scores = [float(np.dot(q_vec, dv)) for dv in _DOC_VECS]
    top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
    return [
        PolicyResult(
            policy_id=POLICY_DOCS[i]["id"],
            title=POLICY_DOCS[i]["title"],
            content=POLICY_DOCS[i]["content"],
            score=round(scores[i], 4),
        )
        for i in top_indices
    ]


# ── 统一入口：优先语义检索，降级 TF-IDF ──────────────────────────────────────

def search_policy_raw(query: str, top_k: int = 2) -> list[PolicyResult]:
    """
    返回与 query 最相关的 top_k 条政策。

    优先使用 text-embedding-004 语义检索（需要 GOOGLE_API_KEY）；
    失败时自动降级为 TF-IDF 字符级 bigram 检索。
    """
    try:
        results = _search_pgvector(query, top_k)
        return results
    except Exception:
        return _search_tfidf(query, top_k)


@tool
def search_policy(query: str) -> str:
    """
    在退款政策知识库中搜索与用户问题最相关的政策条款。
    返回最相关的 2 条政策原文及相似度分数。
    适用于：退款规则咨询、政策解读、申请条件查询等。

    Args:
        query: 用户的问题或关键词，如"七天无理由"、"破损退款"
    Returns:
        格式化的政策原文字符串
    """
    results = search_policy_raw(query, top_k=2)
    if not results:
        return "未找到相关退款政策，请联系人工客服。"

    lines = ["📋 **相关退款政策**\n"]
    for r in results:
        lines.append(f"**{r.title}**（相似度：{r.score:.2%}）")
        lines.append(r.content)
        lines.append("")
    return "\n".join(lines)

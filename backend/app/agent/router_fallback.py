"""Embedding-style router fallback for supervisor scenario matching.

The production path can swap `token_embedding_similarity` with a hosted
embedding model. This local implementation keeps tests deterministic while
preserving the same routing contract.
"""

from __future__ import annotations

import re
from typing import Iterable


TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}")
STOPWORDS = {
    "a",
    "an",
    "and",
    "can",
    "for",
    "help",
    "i",
    "me",
    "of",
    "the",
    "to",
    "with",
    "you",
}


def token_embedding_similarity(message: str, candidates: Iterable[str]) -> float:
    message_tokens = _tokens(message)
    candidate_tokens = set()
    for candidate in candidates:
        candidate_tokens.update(_tokens(candidate))
    if not message_tokens or not candidate_tokens:
        return 0.0
    intersection = message_tokens & candidate_tokens
    union = message_tokens | candidate_tokens
    return len(intersection) / len(union)


def llm_router_fallback_prompt(message: str, scenario_summaries: list[dict[str, str]]) -> dict[str, object]:
    """Return the structured prompt contract for an optional LLM router."""
    return {
        "task": "Choose the best enterprise agent scenario for the user message.",
        "message": message,
        "scenarios": scenario_summaries,
        "output_schema": {
            "scenario_id": "string",
            "confidence": "number between 0 and 1",
            "reason": "short explanation",
        },
    }


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in (match.group(0).lower() for match in TOKEN_RE.finditer(text or ""))
        if token not in STOPWORDS
    }

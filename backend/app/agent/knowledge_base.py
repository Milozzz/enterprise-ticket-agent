"""Admin-facing knowledge base helpers for policy RAG governance."""

from __future__ import annotations

from typing import Any

from app.agent.rag_service import (
    index_policy_corpus,
    knowledge_index_status,
    retrieve_policy_chunks,
)
from app.agent.tools.policy_tools import POLICY_DOCS
from app.db.database import AsyncSessionLocal

DEFAULT_POLICY_ROLES = ("USER", "AGENT", "MANAGER", "SECURITY", "FINANCE")


def list_policy_documents() -> list[dict[str, Any]]:
    """Return policy documents with metadata useful for a KB admin screen."""
    documents: list[dict[str, Any]] = []
    for index, doc in enumerate(POLICY_DOCS):
        content = str(doc.get("content") or "")
        roles = tuple(str(role).upper() for role in doc.get("permission_roles", DEFAULT_POLICY_ROLES))
        documents.append(
            {
                "id": str(doc.get("id") or f"P{index + 1:03d}"),
                "title": str(doc.get("title") or ""),
                "source": str(doc.get("source") or "POLICY_DOCS"),
                "enabled": bool(doc.get("enabled", True)),
                "permission_roles": list(roles),
                "content": content,
                "content_length": len(content),
                "citation": {
                    "policy_id": str(doc.get("id") or f"P{index + 1:03d}"),
                    "clause_id": f"{str(doc.get('id') or f'P{index + 1:03d}')}#p1",
                    "paragraph_id": f"{str(doc.get('id') or f'P{index + 1:03d}')}#p1",
                    "source": str(doc.get("source") or "POLICY_DOCS"),
                },
            }
        )
    return documents


async def knowledge_base_report() -> dict[str, Any]:
    documents = list_policy_documents()
    index_status = await knowledge_index_status()
    return {
        "documents": documents,
        "summary": {
            "document_count": len(documents),
            "enabled_count": sum(1 for item in documents if item["enabled"]),
            "citation_ready_count": sum(1 for item in documents if item["citation"]["policy_id"]),
            "permission_filtered_count": sum(1 for item in documents if item["permission_roles"]),
            "indexed_document_count": index_status["indexed_documents"],
            "indexed_chunk_count": index_status["indexed_chunks"],
        },
        "governance": {
            "retrieval": "PostgreSQL pgvector + optional lexical rerank + TF-IDF fallback",
            "chunking": "paragraph-aware bounded chunks with overlap",
            "citation_contract": "Every RAG answer returns document_id, paragraph_id, and source.",
            "permission_model": "Documents may declare permission_roles; search responses expose whether a role can cite each hit.",
            "index": index_status,
        },
    }


async def test_policy_retrieval(query: str, *, top_k: int = 3, user_role: str = "USER") -> dict[str, Any]:
    role = str(user_role or "USER").upper()
    raw_results = await retrieve_policy_chunks(query, top_k=top_k, user_role=role)
    documents_by_id = {doc["id"]: doc for doc in list_policy_documents()}
    hits: list[dict[str, Any]] = []
    for result in raw_results:
        doc = documents_by_id.get(result.policy_id, {})
        allowed = role in set(doc.get("permission_roles") or DEFAULT_POLICY_ROLES)
        hits.append(
            {
                "policy_id": result.policy_id,
                "document_id": result.policy_id,
                "clause_id": result.paragraph_id or f"{result.policy_id}#p1",
                "paragraph_id": result.paragraph_id or f"{result.policy_id}#p1",
                "title": result.title,
                "score": result.score,
                "source": result.source or doc.get("source") or "POLICY_DOCS",
                "retrieval_method": result.retrieval_method,
                "allowed_for_role": allowed,
                "permission_roles": doc.get("permission_roles") or list(DEFAULT_POLICY_ROLES),
                "excerpt": result.content[:180],
                "citation": {
                    "policy_id": result.policy_id,
                    "document_id": result.policy_id,
                    "clause_id": result.paragraph_id or f"{result.policy_id}#p1",
                    "paragraph_id": result.paragraph_id or f"{result.policy_id}#p1",
                    "source": result.source or doc.get("source") or "POLICY_DOCS",
                },
            }
        )

    return {
        "query": query,
        "top_k": top_k,
        "user_role": role,
        "hit_count": len(hits),
        "allowed_hit_count": sum(1 for item in hits if item["allowed_for_role"]),
        "hits": hits,
    }


async def rebuild_policy_index(*, force: bool = False) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        return await index_policy_corpus(session, force=force)

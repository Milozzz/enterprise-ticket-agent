"""Production policy RAG: chunking, pgvector retrieval, rerank, and fallback."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import hashlib
import re
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools.policy_tools import POLICY_DOCS, PolicyResult, lexical_similarity, search_policy_raw
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.database import AsyncSessionLocal
from app.db.models import KnowledgeChunk, KnowledgeDocument
from app.db.tenant_context import current_tenant_id

logger = get_logger(__name__)


@dataclass(frozen=True)
class ChunkDraft:
    paragraph_id: str
    ordinal: int
    content: str


def chunk_policy_document(
    document_id: str,
    content: str,
    *,
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[ChunkDraft]:
    """Split by policy paragraphs first, then use bounded overlapping windows."""
    settings = get_settings()
    limit = max(120, chunk_size or settings.rag_chunk_size)
    shared = max(0, min(overlap if overlap is not None else settings.rag_chunk_overlap, limit // 3))
    paragraphs = [part.strip() for part in re.split(r"(?<=[。！？.!?])\s*|\n+", content) if part.strip()]
    if not paragraphs:
        paragraphs = [content.strip()]

    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current} {paragraph}".strip()
        if current and len(candidate) > limit:
            chunks.append(current)
            current = f"{current[-shared:]} {paragraph}".strip() if shared else paragraph
        else:
            current = candidate
        while len(current) > limit:
            chunks.append(current[:limit])
            current = current[max(1, limit - shared) :]
    if current:
        chunks.append(current)

    return [
        ChunkDraft(
            paragraph_id=f"{document_id}#p{index + 1}",
            ordinal=index,
            content=chunk,
        )
        for index, chunk in enumerate(chunks)
    ]


def _embedder():
    settings = get_settings()
    if not settings.google_api_key:
        raise RuntimeError("GOOGLE_API_KEY is not configured")
    from langchain_google_genai import GoogleGenerativeAIEmbeddings

    return GoogleGenerativeAIEmbeddings(
        model=settings.rag_embedding_model,
        google_api_key=settings.google_api_key,
    )


async def index_policy_corpus(
    session: AsyncSession,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Upsert source documents and their Gemini embeddings into pgvector."""
    settings = get_settings()
    if session.bind is None or session.bind.dialect.name != "postgresql":
        raise RuntimeError("Policy vector indexing requires PostgreSQL with pgvector")

    drafts: list[tuple[dict[str, Any], ChunkDraft]] = []
    changed_documents = 0
    for doc in POLICY_DOCS:
        document_id = str(doc["id"])
        content_hash = hashlib.sha256(str(doc["content"]).encode("utf-8")).hexdigest()
        existing = await session.get(KnowledgeDocument, document_id)
        if existing and existing.content_hash == content_hash and not force:
            continue
        if existing:
            await session.execute(
                delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id)
            )
            existing.title = str(doc["title"])
            existing.source = str(doc.get("source") or "POLICY_DOCS")
            existing.permission_roles = list(doc.get("permission_roles") or [])
            existing.content_hash = content_hash
            existing.updated_at = datetime.utcnow()
        else:
            session.add(
                KnowledgeDocument(
                    document_id=document_id,
                    tenant_id=current_tenant_id(),
                    title=str(doc["title"]),
                    source=str(doc.get("source") or "POLICY_DOCS"),
                    version=str(doc.get("version") or "1"),
                    permission_roles=list(doc.get("permission_roles") or []),
                    enabled=bool(doc.get("enabled", True)),
                    content_hash=content_hash,
                )
            )
        drafts.extend(
            (doc, chunk)
            for chunk in chunk_policy_document(document_id, str(doc["content"]))
        )
        changed_documents += 1

    if not drafts:
        count = await session.scalar(select(func.count()).select_from(KnowledgeChunk))
        return {
            "document_count": len(POLICY_DOCS),
            "changed_documents": 0,
            "chunk_count": int(count or 0),
            "embedding_model": settings.rag_embedding_model,
        }

    embedder = _embedder()
    texts = [chunk.content for _, chunk in drafts]
    vectors = await asyncio.to_thread(embedder.embed_documents, texts)
    for (doc, chunk), vector in zip(drafts, vectors, strict=True):
        session.add(
            KnowledgeChunk(
                chunk_id=f"{doc['id']}:{chunk.ordinal}",
                tenant_id=current_tenant_id(),
                document_id=str(doc["id"]),
                paragraph_id=chunk.paragraph_id,
                ordinal=chunk.ordinal,
                content=chunk.content,
                token_count=max(1, len(chunk.content) // 2),
                embedding_model=settings.rag_embedding_model,
                embedding=vector,
            )
        )
    await session.commit()
    return {
        "document_count": len(POLICY_DOCS),
        "changed_documents": changed_documents,
        "chunk_count": len(drafts),
        "embedding_model": settings.rag_embedding_model,
    }


async def retrieve_policy_chunks(
    query: str,
    *,
    top_k: int | None = None,
    user_role: str = "USER",
    rerank: bool | None = None,
) -> list[PolicyResult]:
    """Retrieve permission-aware chunks from pgvector, with TF-IDF fallback."""
    settings = get_settings()
    limit = max(1, min(top_k or settings.rag_top_k, 20))
    use_rerank = settings.rag_rerank_enabled if rerank is None else rerank
    if "postgresql" not in settings.database_url or not settings.google_api_key:
        return _fallback_results(query, limit, user_role)

    try:
        query_vector = await asyncio.to_thread(_embedder().embed_query, query)
        async with AsyncSessionLocal() as session:
            distance = KnowledgeChunk.embedding.cosine_distance(query_vector)
            candidate_limit = limit * max(1, settings.rag_candidate_multiplier)
            rows = (
                await session.execute(
                    select(KnowledgeChunk, KnowledgeDocument, distance.label("distance"))
                    .join(KnowledgeDocument)
                    .where(
                        KnowledgeDocument.enabled.is_(True),
                        KnowledgeChunk.embedding.is_not(None),
                    )
                    .order_by(distance)
                    .limit(candidate_limit)
                )
            ).all()
        role = user_role.upper()
        candidates: list[PolicyResult] = []
        for chunk, document, raw_distance in rows:
            roles = {str(item).upper() for item in (document.permission_roles or [])}
            if roles and role not in roles:
                continue
            vector_score = max(0.0, min(1.0, 1.0 - float(raw_distance or 0)))
            score = vector_score
            method = "pgvector"
            if use_rerank:
                score = 0.8 * vector_score + 0.2 * lexical_similarity(query, chunk.content)
                method = "pgvector+lexical_rerank"
            candidates.append(
                PolicyResult(
                    policy_id=document.document_id,
                    title=document.title,
                    content=chunk.content,
                    score=round(score, 4),
                    paragraph_id=chunk.paragraph_id,
                    source=document.source,
                    retrieval_method=method,
                )
            )
        candidates.sort(key=lambda item: item.score, reverse=True)
        if candidates:
            return candidates[:limit]
        logger.warning("rag_pgvector_empty_fallback", query=query[:80])
    except Exception as exc:
        logger.warning("rag_pgvector_failed_fallback", error=str(exc))
    return _fallback_results(query, limit, user_role)


def _fallback_results(query: str, top_k: int, user_role: str) -> list[PolicyResult]:
    role = user_role.upper()
    allowed_ids = {
        str(doc["id"])
        for doc in POLICY_DOCS
        if not doc.get("permission_roles")
        or role in {str(item).upper() for item in doc.get("permission_roles", [])}
    }
    candidates = [item for item in search_policy_raw(query, top_k=max(top_k * 3, top_k)) if item.policy_id in allowed_ids]
    return candidates[:top_k]


async def knowledge_index_status() -> dict[str, Any]:
    settings = get_settings()
    if "postgresql" not in settings.database_url:
        return {"backend": "tfidf", "indexed_documents": 0, "indexed_chunks": 0}
    try:
        async with AsyncSessionLocal() as session:
            documents = await session.scalar(select(func.count()).select_from(KnowledgeDocument))
            chunks = await session.scalar(select(func.count()).select_from(KnowledgeChunk))
        return {
            "backend": "pgvector" if chunks else "tfidf_fallback",
            "indexed_documents": int(documents or 0),
            "indexed_chunks": int(chunks or 0),
            "embedding_model": settings.rag_embedding_model,
            "rerank_enabled": settings.rag_rerank_enabled,
        }
    except Exception as exc:
        return {
            "backend": "tfidf_fallback",
            "indexed_documents": 0,
            "indexed_chunks": 0,
            "error": str(exc),
        }

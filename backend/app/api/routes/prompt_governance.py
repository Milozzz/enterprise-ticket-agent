from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.routes.admin_config import require_admin_api_key
from app.llm.prompt_registry import get_prompt_registry

router = APIRouter(dependencies=[Depends(require_admin_api_key)])


class PromptPreviewRequest(BaseModel):
    node_name: str
    routing_key: str
    default_template: str = ""
    default_version: str = "builtin-v1"


@router.get("")
async def list_prompt_releases() -> dict:
    return get_prompt_registry().describe()


@router.post("/preview")
async def preview_prompt_route(payload: PromptPreviewRequest) -> dict:
    selection = get_prompt_registry().select(
        payload.node_name,
        payload.default_template,
        routing_key=payload.routing_key,
        default_version=payload.default_version,
    )
    return {
        "node": selection.node_name,
        "version": selection.version,
        "variant": selection.variant,
        "rollout_bucket": selection.rollout_bucket,
        "template_hash": selection.to_audit_event()["template_hash"],
    }

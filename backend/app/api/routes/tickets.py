from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def list_tickets():
    """工单列表（待实现）"""
    return {"tickets": [], "total": 0}


@router.get("/{ticket_id}")
async def get_ticket(ticket_id: str):
    """工单详情（待实现）"""
    return {"ticket_id": ticket_id, "status": "pending"}

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.routes import a2a_server, mcp_server
from app.agent.a2a_tasks import deliver_pending_callbacks, execute_task
from app.db.database import Base


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("TESTING", "1")
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def create_schema():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(create_schema())
    monkeypatch.setattr(a2a_server, "AsyncSessionLocal", Session)
    app = FastAPI()
    app.include_router(mcp_server.router)
    app.include_router(a2a_server.router)
    return TestClient(app)


def test_mcp_streamable_http_lifecycle_and_read_tool(monkeypatch):
    client = _client(monkeypatch)
    initialize = client.post(
        "/mcp",
        headers={"Accept": "application/json, text/event-stream"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"},
            },
        },
    )
    assert initialize.status_code == 200
    assert initialize.json()["result"]["capabilities"]["tools"]["listChanged"] is False
    session_id = initialize.headers["mcp-session-id"]

    initialized = client.post(
        "/mcp",
        headers={"Mcp-Session-Id": session_id, "MCP-Protocol-Version": "2025-06-18"},
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert initialized.status_code == 202

    tools = client.post(
        "/mcp",
        headers={"Mcp-Session-Id": session_id, "MCP-Protocol-Version": "2025-06-18"},
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    names = {tool["name"] for tool in tools.json()["result"]["tools"]}
    assert {"erp_get_order", "erp_create_credit_memo", "erp_reverse_document"} <= names

    call = client.post(
        "/mcp",
        headers={"Mcp-Session-Id": session_id, "MCP-Protocol-Version": "2025-06-18"},
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "erp_get_order",
                "arguments": {"order_id": "ERP-ORD-1002"},
            },
        },
    )
    result = call.json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["data"]["data"]["orderId"] == "ERP-ORD-1002"


def test_mcp_financial_write_cannot_bypass_approval(monkeypatch):
    client = _client(monkeypatch)
    initialize = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    session_id = initialize.headers["mcp-session-id"]
    call = client.post(
        "/mcp",
        headers={"Mcp-Session-Id": session_id},
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "erp_create_credit_memo",
                "arguments": {
                    "order_id": "ERP-ORD-1002",
                    "refund_request_id": "ERP-REF-1002",
                    "amount": 1299,
                },
            },
        },
    )
    result = call.json()["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["authorized"] is False
    assert "approval evidence" in result["structuredContent"]["error"]


def test_mcp_rejects_untrusted_browser_origin(monkeypatch):
    client = _client(monkeypatch)
    response = client.post(
        "/mcp",
        headers={"Origin": "https://attacker.example"},
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert response.status_code == 403


def test_a2a_agent_card_and_safe_task_lifecycle(monkeypatch):
    client = _client(monkeypatch)
    card = client.get("/.well-known/agent-card.json")
    assert card.status_code == 200
    assert card.json()["url"].endswith("/a2a")
    assert any(skill["id"] == "refund_finance_workflow" for skill in card.json()["skills"])

    send = client.post(
        "/a2a",
        json={
            "jsonrpc": "2.0",
            "id": "send-1",
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-1",
                    "parts": [{"kind": "text", "text": "reimbursement 1200 travel expense invoice"}],
                }
            },
        },
    )
    task = send.json()["result"]
    assert task["status"]["state"] == "input-required"
    assert task["metadata"]["execution_mode"] == "simulation"
    assert task["metadata"]["approval_required"] is True

    fetched = client.post(
        "/a2a",
        json={
            "jsonrpc": "2.0",
            "id": "get-1",
            "method": "tasks/get",
            "params": {"id": task["id"]},
        },
    )
    assert fetched.json()["result"]["id"] == task["id"]

    canceled = client.post(
        "/a2a",
        json={
            "jsonrpc": "2.0",
            "id": "cancel-1",
            "method": "tasks/cancel",
            "params": {"id": task["id"]},
        },
    )
    assert canceled.json()["result"]["status"]["state"] == "canceled"


def test_a2a_async_task_persists_and_delivers_push_notification(monkeypatch):
    client = _client(monkeypatch)
    send = client.post(
        "/a2a",
        json={
            "jsonrpc": "2.0",
            "id": "async-send",
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "async-msg",
                    "parts": [{"kind": "text", "text": "reimbursement 1200 travel expense"}],
                },
                "configurations": {
                    "pushNotifications": True,
                    "pushNotificationConfig": {"url": "https://joule-callback.example.test/task"},
                },
            },
        },
    )
    task = send.json()["result"]
    assert task["status"]["state"] == "submitted"

    async def finish_and_deliver():
        completed = await execute_task(
            a2a_server.AsyncSessionLocal,
            task_id=task["id"],
            tenant_id="TENANT-DEMO-COMMERCE",
        )
        delivered = []

        async def fake_dispatcher(model, payload):
            delivered.append((model.task_id, payload["status"]["state"]))

        result = await deliver_pending_callbacks(
            a2a_server.AsyncSessionLocal,
            tenant_id="TENANT-DEMO-COMMERCE",
            dispatcher=fake_dispatcher,
        )
        return completed, delivered, result

    completed, delivered, callback_result = asyncio.run(finish_and_deliver())
    assert completed["status"]["state"] == "input-required"
    assert delivered == [(task["id"], "input-required")]
    assert callback_result == {"sent": 1, "failed": 0}

    fetched = client.post(
        "/a2a",
        json={
            "jsonrpc": "2.0",
            "id": "async-get",
            "method": "tasks/get",
            "params": {"id": task["id"]},
        },
    )
    assert fetched.json()["result"]["metadata"]["callback_status"] == "SENT"

"""Independent worker process for integration and durable Agent jobs."""

from __future__ import annotations

import asyncio
import signal

from app.agent.a2a_tasks import run_a2a_worker
from app.agent.agent_jobs import run_agent_job_worker
from app.agent.approval_tasks import run_approval_escalation_worker
from app.agent import graph as graph_runtime
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.database import AsyncSessionLocal
from app.erp.outbox_worker import run_outbox_worker
from app.erp.reconciliation_worker import run_reconciliation_worker


async def main_async() -> None:
    setup_logging()
    settings = get_settings()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop_event.set)
        except NotImplementedError:
            pass

    graph_resource = None
    agent_graph = graph_runtime.ticket_graph
    if settings.environment != "development" and settings.agent_job_worker_enabled:
        agent_graph, graph_resource = await graph_runtime.open_postgres_graph(settings.database_url)

    workers = [
        run_outbox_worker(
            AsyncSessionLocal,
            worker_id=settings.outbox_worker_id,
            poll_seconds=settings.outbox_worker_poll_seconds,
            stop_event=stop_event,
        ),
        run_reconciliation_worker(
            AsyncSessionLocal,
            poll_seconds=settings.reconciliation_worker_poll_seconds,
            stop_event=stop_event,
        ),
        run_a2a_worker(
            AsyncSessionLocal,
            worker_id=settings.a2a_worker_id,
            tenant_ids=[
                tenant.strip()
                for tenant in settings.a2a_worker_tenants.split(",")
                if tenant.strip()
            ]
            or [settings.default_tenant_id],
            poll_seconds=settings.a2a_worker_poll_seconds,
            stop_event=stop_event,
        ),
        run_approval_escalation_worker(
            AsyncSessionLocal,
            poll_seconds=settings.approval_escalation_poll_seconds,
            stop_event=stop_event,
        ),
    ]
    if settings.agent_job_worker_enabled:
        workers.append(
            run_agent_job_worker(
                agent_graph,
                AsyncSessionLocal,
                worker_id=settings.agent_job_worker_id,
                tenant_ids=[
                    tenant.strip()
                    for tenant in settings.a2a_worker_tenants.split(",")
                    if tenant.strip()
                ]
                or [settings.default_tenant_id],
                poll_seconds=settings.agent_job_poll_seconds,
                stop_event=stop_event,
            )
        )
    try:
        await asyncio.gather(*workers)
    finally:
        if graph_resource is not None:
            await graph_resource.__aexit__(None, None, None)


if __name__ == "__main__":
    asyncio.run(main_async())

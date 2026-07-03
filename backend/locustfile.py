"""HTTP load profile for the enterprise Agent API.

Run from backend/: locust -f locustfile.py --host http://127.0.0.1:8000
Use a seeded database before exercising the chat task.
"""

from __future__ import annotations

import itertools

from locust import HttpUser, between, task


_thread_ids = itertools.count(1)


class EnterpriseAgentUser(HttpUser):
    wait_time = between(0.2, 1.0)

    @task(4)
    def health(self):
        self.client.get("/health", name="GET /health")

    @task(3)
    def dashboard_observability(self):
        self.client.get("/api/dashboard/node-latency", name="GET /dashboard/node-latency")
        self.client.get("/api/dashboard/llm-costs", name="GET /dashboard/llm-costs")

    @task(2)
    def approval_queue(self):
        self.client.get(
            "/api/agent/approval-center?view=approver&user_role=MANAGER&limit=20",
            name="GET /approval-center",
        )

    @task(1)
    def deterministic_agent_flow(self):
        sequence = next(_thread_ids)
        with self.client.post(
            "/api/agent/chat",
            name="POST /agent/chat",
            json={
                "messages": [{"role": "user", "content": "查询订单 123456 的状态"}],
                "thread_id": f"load-{self.environment.runner.user_count}-{sequence}",
                "trace_id": f"load-trace-{sequence}",
                "user_id": "3",
                "user_role": "USER",
            },
            headers={"Accept": "text/event-stream"},
            timeout=65,
            catch_response=True,
        ) as response:
            if response.status_code != 200 or "event: done" not in response.text:
                response.failure(f"incomplete SSE response: HTTP {response.status_code}")

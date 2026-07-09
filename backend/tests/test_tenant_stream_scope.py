"""D3 回归测试：SSE 流式落库的租户上下文。

问题：TenantContextMiddleware 的 tenant_scope 在 call_next 返回时退出，
StreamingResponse body 在其后才迭代，流内 DB 写入读到的 contextvar
已被 reset 回默认租户。

修复：chat.py 的 _tenant_scoped_stream 把整个 SSE 流重新包进 tenant_scope。
"""

import asyncio

from app.db.tenant_context import DEFAULT_TENANT_ID, current_tenant_id, tenant_scope

TEST_TENANT = "TENANT-D3-TEST"


def test_d3_problem_exists_without_wrapper():
    """重现问题：中间件作用域退出后才迭代流，流内读到默认租户。"""
    observed: list[str] = []

    async def sse_stream():
        observed.append(current_tenant_id())
        yield "chunk"

    async def main():
        # 模拟中间件：作用域内创建流（call_next 返回 StreamingResponse）
        with tenant_scope(TEST_TENANT):
            stream = sse_stream()
        # 模拟 StreamingResponse：作用域退出后才真正迭代 body
        async for _ in stream:
            pass

    asyncio.run(main())
    # 未修复路径：流内看到的是默认租户，而非请求租户 —— 这就是 D3
    assert observed == [DEFAULT_TENANT_ID]


def test_tenant_scoped_stream_binds_and_restores_tenant():
    """验证修复：包装后流内全程是请求租户，流结束后上下文复位。"""
    from app.api.routes.chat import _tenant_scoped_stream

    observed: list[str] = []

    async def sse_stream():
        observed.append(current_tenant_id())
        yield "a"
        observed.append(current_tenant_id())
        yield "b"

    async def main():
        with tenant_scope(TEST_TENANT):
            wrapped = _tenant_scoped_stream(sse_stream(), current_tenant_id())
        chunks = [chunk async for chunk in wrapped]
        return chunks, current_tenant_id()

    chunks, after = asyncio.run(main())
    assert chunks == ["a", "b"]
    # 修复后：流内每一步（含节点内 DB 写入发生的时机）都是请求租户
    assert observed == [TEST_TENANT, TEST_TENANT]
    # 流结束后 contextvar 正常复位，不污染后续请求
    assert after == DEFAULT_TENANT_ID

"""tenant-scope user_memory (add tenant_id, RLS, per-tenant uniqueness)

Revision ID: 1a2b3c4d5e6f
Revises: 07a8b9c0d1e2
Create Date: 2026-07-04 00:00:00.000000

Fixes data-layer finding D1: user_memory (fraud/refund risk profile) had no
tenant_id and a globally-unique user_id, leaking risk state across tenants.
This adds tenant_id, a (tenant_id, user_id) unique key, and RLS on PostgreSQL.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "1a2b3c4d5e6f"
down_revision: Union[str, Sequence[str], None] = "07a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_TENANT = "TENANT-DEMO-COMMERCE"


def upgrade() -> None:
    bind = op.get_bind()

    with op.batch_alter_table("user_memory") as batch_op:
        batch_op.add_column(
            sa.Column(
                "tenant_id",
                sa.String(length=50),
                nullable=False,
                server_default=DEFAULT_TENANT,
            )
        )

    # 替换旧的全局唯一约束/索引为按租户唯一。SQLite batch 操作在
    # context exit 时才真正执行，try/except 无法捕获“不存在的约束”；
    # 必须先检查当前 schema，才能同时兼容首次升级和 downgrade 后重升级。
    inspector = sa.inspect(bind)
    index_names = {item["name"] for item in inspector.get_indexes("user_memory")}
    constraint_names = {
        item["name"]
        for item in inspector.get_unique_constraints("user_memory")
        if item.get("name")
    }
    with op.batch_alter_table("user_memory") as batch_op:
        if "ix_user_memory_user_id" in index_names:
            batch_op.drop_index("ix_user_memory_user_id")
        if "uq_user_memory_user_id" in constraint_names:
            batch_op.drop_constraint("uq_user_memory_user_id", type_="unique")
        batch_op.create_unique_constraint(
            "uq_user_memory_tenant_user", ["tenant_id", "user_id"]
        )

    op.create_index("ix_user_memory_tenant_user", "user_memory", ["tenant_id", "user_id"])
    op.create_index("ix_user_memory_tenant_id", "user_memory", ["tenant_id"])

    if bind.dialect.name == "postgresql":
        op.execute('ALTER TABLE "user_memory" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "user_memory" FORCE ROW LEVEL SECURITY')
        op.execute(
            'CREATE POLICY "tenant_isolation_user_memory" ON "user_memory" '
            "USING (tenant_id = current_setting('app.current_tenant', true)) "
            "WITH CHECK (tenant_id = current_setting('app.current_tenant', true))"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute('DROP POLICY IF EXISTS "tenant_isolation_user_memory" ON "user_memory"')
        op.execute('ALTER TABLE "user_memory" NO FORCE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "user_memory" DISABLE ROW LEVEL SECURITY')

    op.drop_index("ix_user_memory_tenant_id", table_name="user_memory")
    op.drop_index("ix_user_memory_tenant_user", table_name="user_memory")
    with op.batch_alter_table("user_memory") as batch_op:
        batch_op.drop_constraint("uq_user_memory_tenant_user", type_="unique")
        batch_op.drop_column("tenant_id")
        batch_op.create_unique_constraint("uq_user_memory_user_id", ["user_id"])
    op.create_index("ix_user_memory_user_id", "user_memory", ["user_id"], unique=True)

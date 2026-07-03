"""Explicit SAP Sandbox contract smoke test.

Reads are safe by default. A real credit memo request is only attempted when
`--write-credit-memo` is supplied and SAP_READ_ONLY/SAP_SHADOW_WRITES permit it.
"""
# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
import json
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings
from app.erp.connectors import (
    build_erp_batch_request,
    build_erp_create_credit_memo_request,
    build_erp_get_order_request,
    build_erp_query_doctype_request,
)
from app.erp.runtime import SAPODataConnector, load_connector_runtime_config


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    if settings.sap_connector_mode != "live" or not settings.sap_base_url:
        print("SAP_CONNECTOR_MODE=live and SAP_BASE_URL are required", file=sys.stderr)
        return 2

    config = await load_connector_runtime_config(args.connector_id)
    connector = SAPODataConnector(config)
    principal_token = settings.sap_bearer_token if config.auth_type == "principal_propagation" else None
    report: dict[str, object] = {
        "connector": config.public_summary(),
        "health": await connector.health(),
        "checks": [],
    }

    order_result = await connector.execute(
        build_erp_get_order_request(args.order_id, args.connector_id),
        principal_token=principal_token,
    )
    report["checks"].append({"name": "sales_order_mapping", "result": order_result.to_dict()})

    for doctype, filter_expression in [
        ("business_partner", None),
        ("delivery_document", f"ReferenceSDDocument eq '{args.order_id}'"),
        ("billing_document", f"SalesDocument eq '{args.order_id}'"),
    ]:
        query_result = await connector.execute(
            build_erp_query_doctype_request(
                doctype,
                filter=filter_expression,
                top=3,
                connector_id=args.connector_id,
            ),
            principal_token=principal_token,
        )
        report["checks"].append(
            {"name": f"{doctype}_mapping", "result": query_result.to_dict()}
        )

    batch = build_erp_batch_request(
        [
            {
                "id": "order-read",
                "method": "GET",
                "url": connector._operation_path("get_order", {"order_id": args.order_id}),
            }
        ],
        batch_format=args.batch_format,
        connector_id=args.connector_id,
    )
    batch_result = await connector.execute(batch, principal_token=principal_token)
    report["checks"].append({"name": "odata_batch", "result": batch_result.to_dict()})

    if args.write_credit_memo:
        credit_memo = build_erp_create_credit_memo_request(
            args.order_id,
            args.refund_request_id,
            args.amount,
            args.currency,
            args.connector_id,
        )
        write_result = await connector.execute(
            credit_memo,
            principal_token=principal_token,
            force_write=True,
        )
        report["checks"].append({"name": "credit_memo_request", "result": write_result.to_dict()})

    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--connector-id", default="CONN-SAP-SANDBOX")
    parser.add_argument("--order-id", required=True)
    parser.add_argument("--batch-format", choices=["json", "multipart"], default="multipart")
    parser.add_argument("--write-credit-memo", action="store_true")
    parser.add_argument("--refund-request-id", default="SANDBOX-SMOKE-REFUND")
    parser.add_argument("--amount", type=Decimal, default=Decimal("1.00"))
    parser.add_argument("--currency", default="CNY")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

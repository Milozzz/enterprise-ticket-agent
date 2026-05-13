"""
通知工具 (Function Calling Tools)
使用 Pydantic + Field(description) 确保 LLM 准确理解参数含义
"""

import hashlib
import smtplib
import uuid
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.models.ticket import NotificationResult
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# 幂等去重集合（进程内，重启后清空；生产建议改为 Redis SET）
_sent_idempotency_keys: set[str] = set()


def _idempotency_key(order_id: str, refund_id: str) -> str:
    """基于订单号+退款单号生成幂等 key，同一退款只发一次通知"""
    raw = f"notify:{order_id}:{refund_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ── Input Schema ──────────────────────────────────────────────────
class SendNotificationInput(BaseModel):
    to_email: str = Field(
        description="收件人邮箱地址，通常为财务团队邮箱，例如 'finance@company.com'"
    )
    order_id: str = Field(
        description="已完成退款的订单号"
    )
    refund_amount: float = Field(
        description="实际退款金额（人民币）",
        ge=0,
    )
    refund_id: str = Field(
        description="退款单号，由 execute_refund 工具返回，格式为 'REFUND_XXXX'"
    )
    ticket_id: str = Field(
        description="对应的工单 ID 或订单号，用于邮件中标注来源"
    )


# ── Tool ──────────────────────────────────────────────────────────
@tool(args_schema=SendNotificationInput)
def send_notification(
    to_email: str,
    order_id: str,
    refund_amount: float,
    refund_id: str,
    ticket_id: str,
) -> dict:
    """
    退款完成后，向财务团队发送邮件通知。
    必须在 execute_refund 成功后才能调用。
    邮件包含退款单号、金额、处理时间等关键信息。
    """
    # 幂等检查：同一退款单只发一次
    idem_key = _idempotency_key(order_id, refund_id)
    if idem_key in _sent_idempotency_keys:
        logger.warning("notification_duplicate_skipped", order_id=order_id, refund_id=refund_id, idem_key=idem_key)
        # 返回之前成功的结构（不重新发送）
        return NotificationResult(
            success=True,
            email_id=f"DEDUP_{idem_key}",
            to=to_email,
            subject=f"【退款通知】订单 {order_id}（已发送，跳过重复）",
            sent_at=datetime.now().isoformat(),
        ).model_dump()

    subject = f"【退款通知】订单 {order_id} 退款 ¥{refund_amount}"
    body = (
        f"财务团队，\n\n"
        f"以下订单已完成退款处理：\n\n"
        f"  订单号：{order_id}\n"
        f"  退款单号：{refund_id}\n"
        f"  退款金额：¥{refund_amount}\n"
        f"  工单编号：{ticket_id}\n"
        f"  处理时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"此邮件由系统自动发送，请勿回复。\n\n"
        f"企业智能工单系统"
    )

    _send_email(to=to_email, subject=subject, body=body)

    # 记录幂等 key，防止重复发送
    _sent_idempotency_keys.add(idem_key)
    logger.info("notification_idempotency_key_recorded", idem_key=idem_key, order_id=order_id)

    email_id = f"EMAIL_{uuid.uuid4().hex[:8].upper()}"
    return NotificationResult(
        success=True,
        email_id=email_id,
        to=to_email,
        subject=subject,
        sent_at=datetime.now().isoformat(),
    ).model_dump()


def _send_email(to: str, subject: str, body: str) -> None:
    """发送邮件：配置了 GMAIL_USER 时走真实 SMTP，否则打印到控制台（开发环境 mock）。"""
    settings = get_settings()
    if settings.gmail_user and settings.gmail_app_password:
        _send_via_smtp(
            from_email=settings.gmail_user,
            app_password=settings.gmail_app_password,
            to=to,
            subject=subject,
            body=body,
        )
    else:
        _send_email_mock(to=to, subject=subject, body=body)


def _send_via_smtp(from_email: str, app_password: str, to: str, subject: str, body: str) -> None:
    """通过 Gmail SMTP 发送邮件（需要开启「应用专用密码」）。"""
    msg = MIMEMultipart()
    msg["From"] = from_email
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(from_email, app_password)
        server.sendmail(from_email, to, msg.as_string())

    logger.info("email_sent_via_smtp", to=to, subject=subject)


def _send_email_mock(to: str, subject: str, body: str) -> None:
    """模拟发送邮件（开发环境打印到控制台）"""
    msg = f"\n[Mock Email]\n  To: {to}\n  Subject: {subject}\n  Body:\n{body}\n"
    logger.info("email_mock_sent", to=to, subject=subject)
    print(msg)

"""
JWT 认证工具

提供：
- create_access_token(user_id, role) → JWT string
- get_current_user(token) → {"user_id": str, "role": str}（FastAPI Depends 用）
- get_optional_user(token) → same, or None（不强制登录的接口）

开发环境兼容性：
  - 若请求头无 Authorization，从 ChatRequest body 读取 user_id/user_role（向后兼容）
  - TESTING=1 时跳过 JWT 校验，直接返回 body 中的值
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.core.config import get_settings

_bearer = HTTPBearer(auto_error=False)


def create_access_token(user_id: str | int, role: str = "USER") -> str:
    """生成 JWT access token（供 /auth/login 等接口签发）"""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "role": role.upper(),
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def _decode_token(token: str) -> dict:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        return payload
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token 无效或已过期: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> dict:
    """
    FastAPI 依赖：从 Authorization: Bearer <token> 解析当前用户。
    返回 {"user_id": str, "role": str}，其中 user_id 是 DB users.id 的字符串形式（整数字符串）。

    TESTING 模式下返回 user_id="1"（对应 seed 数据中第一个用户），不校验 token。
    """
    import os
    if os.environ.get("TESTING") == "1":
        return {"user_id": "1", "role": "AGENT"}

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 Authorization 请求头",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = _decode_token(credentials.credentials)
    user_id = payload.get("sub")
    role = payload.get("role", "USER")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token payload 缺少 sub 字段",
        )
    return {"user_id": user_id, "role": role}


async def get_optional_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> dict | None:
    """
    可选认证：有 token 则解析，无 token 返回 None。
    用于向后兼容旧客户端（从 body 读 user_id 的场景）。
    """
    import os
    if os.environ.get("TESTING") == "1":
        return {"user_id": "1", "role": "AGENT"}

    if credentials is None:
        return None
    try:
        payload = _decode_token(credentials.credentials)
        return {"user_id": payload.get("sub", ""), "role": payload.get("role", "USER")}
    except HTTPException:
        return None

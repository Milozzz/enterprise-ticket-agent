"""A2: 企业 SSO —— OIDC (RS256/ES256 + JWKS) 外部 IdP 令牌验证。

设计：
- 开发/演示环境继续用本地 HS256 JWT（/auth/token 签发），零改动。
- 生产接入 Auth0 / Azure AD / Keycloak 等 IdP 时，配置：
    OIDC_ENABLED=1
    OIDC_ISSUER=https://your-tenant.auth0.com/          （必填，用于 iss 校验与发现文档）
    OIDC_AUDIENCE=enterprise-ticket-agent               （必填，aud 校验）
    OIDC_JWKS_URL=...                                   （可选；缺省从 issuer 的
                                                          /.well-known/openid-configuration 发现）
    OIDC_ROLE_CLAIM=roles                               （角色 claim 名，支持 str 或 list）
    OIDC_TENANT_CLAIM=tenant_id                         （租户 claim 名，缺省用默认租户）
- auth.get_current_user 按 token header 的 alg 分流：RS256/ES256 → OIDC 验证，
  HS256 → 本地验证。迁移期两类 token 可并存。

安全要点：
- JWKS 带 TTL 缓存（默认 10 分钟），kid 未命中时强制刷新一次（支持 IdP 轮换密钥）。
- 严格校验 iss/aud/exp；alg 白名单，拒绝 alg=none / HS256 冒充 RS256 的混淆攻击。
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from jose import JWTError, jwt

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_ALLOWED_ALGS = ("RS256", "RS384", "RS512", "ES256", "ES384")

# JWKS 缓存：{jwks_url: (fetched_at_monotonic, {kid: jwk_dict})}
_jwks_cache: dict[str, tuple[float, dict[str, dict]]] = {}
_JWKS_TTL_SECONDS = 600


class OIDCConfigError(RuntimeError):
    pass


class OIDCTokenError(RuntimeError):
    pass


def oidc_enabled() -> bool:
    settings = get_settings()
    return bool(getattr(settings, "oidc_enabled", False))


def token_uses_oidc(token: str) -> bool:
    """按未验证 header 的 alg 判断该 token 是否应走 OIDC 验证路径。"""
    try:
        header = jwt.get_unverified_header(token)
    except JWTError:
        return False
    return str(header.get("alg", "")).upper() in _ALLOWED_ALGS


async def _discover_jwks_url(issuer: str) -> str:
    settings = get_settings()
    explicit = str(getattr(settings, "oidc_jwks_url", "") or "").strip()
    if explicit:
        return explicit
    discovery = issuer.rstrip("/") + "/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(discovery)
        response.raise_for_status()
        jwks_uri = str(response.json().get("jwks_uri") or "")
    if not jwks_uri:
        raise OIDCConfigError(f"OIDC discovery document at {discovery} has no jwks_uri")
    return jwks_uri


async def _get_jwks(jwks_url: str, *, force_refresh: bool = False) -> dict[str, dict]:
    cached = _jwks_cache.get(jwks_url)
    if (
        not force_refresh
        and cached
        and (time.monotonic() - cached[0]) < _JWKS_TTL_SECONDS
    ):
        return cached[1]
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(jwks_url)
        response.raise_for_status()
        keys = response.json().get("keys") or []
    by_kid = {str(key.get("kid")): dict(key) for key in keys if key.get("kid")}
    _jwks_cache[jwks_url] = (time.monotonic(), by_kid)
    return by_kid


def _extract_role(payload: dict, role_claim: str, default_role: str) -> str:
    value = payload.get(role_claim)
    if isinstance(value, (list, tuple)) and value:
        value = value[0]
    role = str(value or default_role).upper()
    # 未知角色一律降为最小权限，防止 IdP 侧 claim 配错直接拿到审批权
    if role not in {"USER", "AGENT", "MANAGER", "SECURITY", "FINANCE", "ADMIN"}:
        return default_role.upper()
    return role


async def verify_oidc_token(token: str) -> dict[str, Any]:
    """验证外部 IdP 签发的 token，返回与本地 JWT 一致的用户结构。

    Raises:
        OIDCConfigError: OIDC 未正确配置。
        OIDCTokenError: token 验证失败（签名/过期/iss/aud 不符）。
    """
    settings = get_settings()
    issuer = str(getattr(settings, "oidc_issuer", "") or "").strip()
    audience = str(getattr(settings, "oidc_audience", "") or "").strip()
    if not issuer or not audience:
        raise OIDCConfigError("OIDC_ISSUER and OIDC_AUDIENCE must be configured")

    try:
        header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise OIDCTokenError(f"Malformed token header: {exc}") from exc
    alg = str(header.get("alg", "")).upper()
    if alg not in _ALLOWED_ALGS:
        raise OIDCTokenError(f"Algorithm '{alg}' not allowed for OIDC tokens")
    kid = str(header.get("kid") or "")
    if not kid:
        raise OIDCTokenError("OIDC token header missing 'kid'")

    jwks_url = await _discover_jwks_url(issuer)
    keys = await _get_jwks(jwks_url)
    jwk = keys.get(kid)
    if jwk is None:
        # kid 未命中：IdP 可能刚轮换了密钥，强制刷新一次再找
        keys = await _get_jwks(jwks_url, force_refresh=True)
        jwk = keys.get(kid)
    if jwk is None:
        raise OIDCTokenError(f"No JWKS key found for kid '{kid}'")

    try:
        payload = jwt.decode(
            token,
            jwk,
            algorithms=[alg],
            audience=audience,
            issuer=issuer,
        )
    except JWTError as exc:
        raise OIDCTokenError(f"OIDC token validation failed: {exc}") from exc

    role_claim = str(getattr(settings, "oidc_role_claim", "roles") or "roles")
    tenant_claim = str(getattr(settings, "oidc_tenant_claim", "tenant_id") or "tenant_id")
    default_role = str(getattr(settings, "oidc_default_role", "USER") or "USER")
    subject = str(payload.get("sub") or "")
    if not subject:
        raise OIDCTokenError("OIDC token payload missing 'sub'")

    return {
        "user_id": subject,
        "role": _extract_role(payload, role_claim, default_role),
        "tenant_id": str(payload.get(tenant_claim) or settings.default_tenant_id),
        "auth_method": "oidc",
        "idp_claims": {
            "iss": payload.get("iss"),
            "email": payload.get("email"),
            "name": payload.get("name"),
        },
    }

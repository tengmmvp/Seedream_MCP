"""终端用户标识解析。

safety_identifier 是服务器向上游 API 调用自动附加的合规参数：总开关开启且模型支持时
取部署方显式配置的标识，未配置时由机器名与用户名派生。
派生值同一机器同一账户跨进程重启保持稳定，与官方对用户标识信息做哈希的推荐同构。
派生对任意机器名与用户名不抛异常。
"""

from __future__ import annotations

import getpass
import hashlib
import platform
import threading
from typing import Literal, NamedTuple

from ..model.capabilities import get_model_capabilities
from .logs import get_logger

# 派生标识取 SHA-256 十六进制前缀长度，满足官方不超过 64 字符的约束。
_DERIVED_IDENTIFIER_LENGTH = 32

logger = get_logger()

_derived_lock = threading.Lock()
_derived_cache: str | None = None

SafetyIdentifierSource = Literal["disabled", "unsupported", "explicit", "derived"]

# 身份项取不到时的占位值，保证派生稳定。
_UNKNOWN_HOST = "unknown-host"
_UNKNOWN_USER = "unknown-user"


class SafetyIdentifierDecision(NamedTuple):
    """生效的终端用户标识决定。

    Attributes:
        source: 来源标签，开关关闭为 disabled，模型不支持为 unsupported，
            显式配置为 explicit，系统派生为 derived。
        value: 生效的标识字符串，不附加时为 None。
    """

    source: SafetyIdentifierSource
    value: str | None


def _warn_placeholder_fallback(missing: str) -> None:
    logger.warning(
        "safety_identifier 派生未取得{}，以占位值兜底，平台侧归因精度受损",
        missing,
    )


def _host_identity() -> tuple[str, str]:
    """返回 (机器名, 用户名)，任一项取不到或为空时该项以占位值兜底并告警。"""
    try:
        host = platform.node()
    except Exception:
        host = ""
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    if not host or not user:
        missing = "与".join(
            name for name, value in (("机器名", host), ("用户名", user)) if not value
        )
        _warn_placeholder_fallback(missing)
    return host or _UNKNOWN_HOST, user or _UNKNOWN_USER


def _placeholder_identity() -> tuple[str, str]:
    """整体退回占位值并告警。"""
    _warn_placeholder_fallback("机器名与用户名")
    return _UNKNOWN_HOST, _UNKNOWN_USER


def _hash_identity(identity: tuple[str, str]) -> str:
    # surrogateescape 逐字节还原 OS 层以同一处理器解码的机器名与用户名，
    # 非 UTF-8 主机名下派生值仍与区域设置无关。
    raw = "\n".join(identity).encode("utf-8", errors="surrogateescape")
    return hashlib.sha256(raw).hexdigest()[:_DERIVED_IDENTIFIER_LENGTH]


def derive_safety_identifier() -> str:
    """由机器名与用户名派生终端用户标识，进程内缓存首次结果。"""
    global _derived_cache
    with _derived_lock:
        if _derived_cache is None:
            try:
                _derived_cache = _hash_identity(_host_identity())
            except Exception:
                _derived_cache = _hash_identity(_placeholder_identity())
        return _derived_cache


def resolve_safety_identifier(
    *,
    enabled: bool,
    model_id: str,
    explicit_identifier: str | None = None,
) -> SafetyIdentifierDecision:
    """解析生效的终端用户标识，是否附加与取值的全部条件在此单点裁决。

    Args:
        enabled: 总开关，False 时即使模型支持也不附加。
        model_id: 当前模型标识，模型不支持该参数时不附加，避免发参被 API 拒绝。
        explicit_identifier: 部署方显式配置的标识，None 表示未配置走系统派生。

    Returns:
        来源标签与标识值，disabled 与 unsupported 时值为 None。
    """
    if not enabled:
        return SafetyIdentifierDecision("disabled", None)
    if not get_model_capabilities(model_id).supports_safety_identifier:
        return SafetyIdentifierDecision("unsupported", None)
    if explicit_identifier is not None:
        return SafetyIdentifierDecision("explicit", explicit_identifier)
    return SafetyIdentifierDecision("derived", derive_safety_identifier())

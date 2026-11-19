"""终端用户标识解析。

safety_identifier 是服务器向上游 API 调用自动附加的合规参数：总开关开启时
取部署方显式配置的标识，未配置时由机器名与用户名派生，派生值同一机器同一
账户跨进程重启保持稳定，与官方对用户标识信息做哈希的推荐同构；关闭时不附加。
"""

from __future__ import annotations

import getpass
import hashlib
import platform
import threading

from .logs import get_logger

# 派生标识取 SHA-256 十六进制前缀长度，满足官方不超过 64 字符的约束。
_DERIVED_IDENTIFIER_LENGTH = 32

logger = get_logger()

_derived_lock = threading.Lock()
_derived_cache: str | None = None


def _host_identity() -> tuple[str, str]:
    """返回 (机器名, 用户名)，无法取得时以占位值兜底并告警，保证派生稳定。"""
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    host = platform.node()
    if not host or not user:
        missing = "与".join(
            name for name, value in (("机器名", host), ("用户名", user)) if not value
        )
        logger.warning(
            "safety_identifier 派生未取得{}，以占位值兜底，平台侧归因精度受损",
            missing,
        )
    return host or "unknown-host", user or "unknown-user"


def derive_safety_identifier() -> str:
    """由机器名与用户名派生终端用户标识，进程内缓存首次结果。"""
    global _derived_cache
    with _derived_lock:
        if _derived_cache is None:
            raw = "\n".join(_host_identity()).encode("utf-8")
            _derived_cache = hashlib.sha256(raw).hexdigest()[:_DERIVED_IDENTIFIER_LENGTH]
        return _derived_cache


def resolve_safety_identifier(
    *, enabled: bool, explicit_identifier: str | None = None
) -> str | None:
    """解析生效的终端用户标识：开关关闭不附加，开启时显式配置优先、未配置取系统派生值。

    Args:
        enabled: 总开关，False 时即使模型支持也不附加。
        explicit_identifier: 部署方显式配置的标识，None 表示未配置走系统派生。

    Returns:
        生效的标识字符串；不附加时为 None。
    """
    if not enabled:
        return None
    if explicit_identifier is not None:
        return explicit_identifier
    return derive_safety_identifier()

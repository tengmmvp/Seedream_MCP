"""Host 语法内核：允许列表条目分解与绑定地址合法性判定。

中立语法模块，除 utils/core/sanitizers 的字符集判定外无项目内依赖，
config 的构建期条目校验、transport 的中间件匹配与绑定地址守卫、
cli 的参数期早拒共用同一语法与判定，形态演化在此单点维护。
"""

from __future__ import annotations

from ..utils.core.sanitizers import is_printable_ascii


def _bracket_ipv6_literal(entry: str) -> str | None:
    """方括号 host 条目返回内部 IPv6 字面量，非方括号、未闭合或空字面量形态返回 None。"""
    if not entry.startswith("["):
        return None
    end = entry.find("]")
    return entry[1:end] if end > 1 else None


def _decompose_allowed_host_entry(entry: str) -> tuple[str, str] | None:
    """拆分 Host 允许列表条目为 host 与端口后缀，无法识别的形态返回 None。

    端口后缀为空串、":<1-65535 的无前导零 ASCII 数字>" 或 ":*"；host 为方括号 IPv6 字面量
    或不含逗号、冒号、通配符与方括号，首尾无点号且无连续点号的非空主机名。
    """
    if entry.startswith("["):
        literal = _bracket_ipv6_literal(entry)
        if literal is None:
            return None
        host, suffix = f"[{literal}]", entry[len(literal) + 2 :]
    else:
        idx = entry.rfind(":")
        host, suffix = (entry, "") if idx == -1 else (entry[:idx], entry[idx:])
        # 逗号与连续点不出现在真实 Host 头中，序列形态误拼的条目在此拒绝。
        if (
            not host
            or host.startswith(".")
            or host.endswith(".")
            or ".." in host
            or any(ch in host for ch in (":", "*", "[", "]", ","))
        ):
            return None
    if suffix in ("", ":*"):
        return host, suffix
    if suffix.startswith(":"):
        port_text = suffix[1:]
        # SDK 的 Host 校验为精确串比较，前导零端口虽数值合法但与客户端规范化形态
        # 不匹配，按无效条目拒绝。
        if port_text.isascii() and port_text.isdigit() and not port_text.startswith("0"):
            port = int(port_text)
            if 1 <= port <= 65535:
                return host, suffix
    return None


def bind_host_invalid_reason(host: str) -> str | None:
    """绑定地址为空或含非可打印 ASCII 字符时返回拒绝理由子句，合法返回 None；cli 早拒与 transport 装配期守卫共用。"""
    if not host:
        return (
            "不能为空，空绑定地址会绑定全部接口且派生的 Host 白名单恒不匹配，"
            "表现为全站 421/403 而非启动期报错"
        )
    if not is_printable_ascii(host):
        return (
            "须为可打印ASCII字符，含控制字符、空白或非 ASCII 字符的绑定地址使 Host"
            " 白名单编码失败或恒不匹配，表现为全站 500 或 421 而非启动期报错"
        )
    return None

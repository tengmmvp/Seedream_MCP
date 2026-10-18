"""Host 语法内核：允许列表条目分解与绑定地址合法性判定。

中立语法模块，除 utils/core/sanitizers 的字符集判定与 utils/core/http_int 的
整数解析外无项目内依赖，config 的构建期条目校验、transport 的中间件匹配与
绑定地址守卫、cli 的参数期早拒共用同一语法与判定，形态演化在此单点维护。
"""

from __future__ import annotations

import ipaddress

from ..utils.core.http_int import strict_http_int
from ..utils.core.sanitizers import is_printable_ascii


def _ipv6_host_literal_forms(literal: str) -> tuple[tuple[str, ...], str] | None:
    """合法 IPv6 字面量返回可匹配真实 Host/Origin 头的形态元组与建议写法，非法返回 None。

    真实头只携带压缩规范形，IPv4 映射地址的客户端另发 dotted 形；compressed 对
    映射地址的输出随 Python 版本在 dotted 与 hex 展开形间翻转，两形均并入。
    """
    try:
        address = ipaddress.IPv6Address(literal)
    except ValueError:
        return None
    ipv4_mapped = address.ipv4_mapped
    accepted = [address.compressed]
    suggested = address.compressed
    if ipv4_mapped is not None:
        accepted.append(f"::ffff:{ipv4_mapped}")
        mapped_int = int(ipv4_mapped)
        accepted.append(f"::ffff:{mapped_int >> 16:x}:{mapped_int & 0xFFFF:x}")
        suggested = f"::ffff:{ipv4_mapped}"
    return tuple(accepted), suggested


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
        # 数值解析委托 strict_http_int：超长数字串归 None，避免裸 int() 的 ValueError
        # 逃逸破坏配置层错误契约。
        port = strict_http_int(port_text)
        if port is not None and not port_text.startswith("0") and 1 <= port <= 65535:
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


# RFC 3986 reg-name 的非字母数字字符：unreserved 符号与 sub-delims；冒号容纳
# 方括号 IPv6 字面量，百分号按 pct-encoded 三元组在 _is_valid_url_hostname 判定。
_REG_NAME_SYMBOLS = frozenset("-._~!$&'()*+,;=")
_PCT_ENCODED_HEXDIGITS = frozenset("0123456789abcdefABCDEF")


def _is_valid_url_hostname(hostname: str) -> bool:
    """主机名仅含 RFC 3986 reg-name 字符（冒号容纳 IPv6 字面量）且百分号为合法 pct-encoded 三元组时为 True。"""
    index = 0
    while index < len(hostname):
        char = hostname[index]
        if char == "%":
            # RFC 3986 的百分号仅以 pct-encoded 三元组出现，裸 % 或尾缀非两位十六进制为乱码。
            encoded = hostname[index + 1 : index + 3]
            if len(encoded) < 2 or any(c not in _PCT_ENCODED_HEXDIGITS for c in encoded):
                return False
            index += 3
            continue
        if not (char.isascii() and (char.isalnum() or char in _REG_NAME_SYMBOLS or char == ":")):
            return False
        index += 1
    return True

"""host_syntax 单测：netloc 分解核的结构切分与端口谓词的归因受理口径。

原因变体为 hosts 与 origins 两侧构建期校验的共用单源，分类表锁定三类文案
（非数字/为零/前导零）的归因边界，受理口径锁定谓词与原因变体的等价性；
分解核锁定主机段/端口文本/方括号形态的成分切分与不可分解形态，接受策略
归各消费方。
"""

from __future__ import annotations

import pytest

from seedream_mcp.config.host_syntax import (
    _decompose_netloc,
    _host_port_invalid_reason,
    _is_valid_host_port,
)


@pytest.mark.parametrize(
    ("port_text", "expected_reason"),
    [
        ("80", None),
        ("1", None),
        ("65535", None),
        ("0", "端口不得为 0"),
        ("00", "端口不得为 0"),
        ("0000", "端口不得为 0"),
        ("080", "端口不得含前导零"),
        ("065535", "端口不得含前导零"),
        ("", "端口须为数字"),
        ("abc", "端口须为数字"),
        ("+80", "端口须为数字"),
        (" 80", "端口须为数字"),
        ("1_0", "端口须为数字"),
        ("-80", "端口须为数字"),
        ("65536", "端口须为数字"),
        ("99999", "端口须为数字"),
        ("9" * 5000, "端口须为数字"),
        ("８０", "端口须为数字"),
    ],
)
def test_host_port_invalid_reason_classifies_port_text(
    port_text: str, expected_reason: str | None
) -> None:
    """纯 ASCII 数字按为零/前导零细分归因，其余形态含裸 int() 可解析形统一按非数字。"""
    assert _host_port_invalid_reason(port_text) == expected_reason


@pytest.mark.parametrize(
    "port_text",
    [
        "80",
        "1",
        "65535",
        "0",
        "00",
        "080",
        "",
        "abc",
        "+80",
        " 80",
        "1_0",
        "-80",
        "65536",
        "9" * 5000,
        "８０",
    ],
)
def test_is_valid_host_port_is_reason_absence(port_text: str) -> None:
    """端口谓词受理当且仅当原因变体无拒绝原因，两侧单源不漂移。"""
    assert _is_valid_host_port(port_text) == (_host_port_invalid_reason(port_text) is None)


@pytest.mark.parametrize(
    ("netloc", "expected"),
    [
        ("example.com", ("example.com", "", False)),
        ("example.com:80", ("example.com", "80", False)),
        ("example.com:", ("example.com", "", False)),
        ("a:b:c", ("a:b", "c", False)),
        ("", ("", "", False)),
        (":", ("", "", False)),
        ("::1", (":", "1", False)),
        ("[::1]", ("::1", "", True)),
        ("[::1]:8000", ("::1", "8000", True)),
        ("[::1]:", ("::1", "", True)),
        ("[fe80::1%25Eth0]", ("fe80::1%25Eth0", "", True)),
        ("[::1]:8000:9000", ("::1", "8000:9000", True)),
        ("[::1", None),
        ("[::1]junk", None),
        ("x]y:80", ("x]y", "80", False)),
        ("u@x:80", ("u@x", "80", False)),
        ("[a@b]:80", ("a@b", "80", True)),
    ],
)
def test_decompose_netloc_splits_structure_only(
    netloc: str, expected: tuple[str, str, bool] | None
) -> None:
    """分解核只切结构：主机段、端口文本与方括号形态，无端口与空端口同为空串。

    userinfo、游离右括号与非默认端口形态照常切分，接受与否的策略归消费方；
    不可分解形态（未闭合方括号、闭合后非冒号尾随）单点返回 None。
    """
    assert _decompose_netloc(netloc) == expected

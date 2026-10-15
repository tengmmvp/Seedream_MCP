"""输出通道的启用开关：单一 ContextVar 集合沿异步上下文传播。

新通道定义 Final 常量并登记进 ALL_OUTPUT_CHANNELS 即可，
作用域入口与读取侧均按集合判断，无需为每个通道各建一个 ContextVar 与作用域机制。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Final

# 全部输出通道名常量：缩略图预览与 JSON 镜像两个 content 通道，收发两侧共用同一来源。
PREVIEW_CHANNEL: Final[str] = "preview"
MIRROR_CHANNEL: Final[str] = "mirror"

ALL_OUTPUT_CHANNELS = frozenset({PREVIEW_CHANNEL, MIRROR_CHANNEL})

_enabled_output_channels: ContextVar[frozenset[str]] = ContextVar(
    "seedream_enabled_output_channels", default=ALL_OUTPUT_CHANNELS
)


@contextmanager
def output_channels_scope(**enabled: bool) -> Iterator[None]:
    """在作用域内按通道名开关输出通道，退出时恢复先前的启用集合。

    未显式给出的通道沿用外层取值，嵌套作用域按通道叠加；未知通道名抛错，
    拼错通道名不做静默 no-op。
    """
    unknown = set(enabled) - ALL_OUTPUT_CHANNELS
    if unknown:
        raise ValueError(f"未知输出通道: {', '.join(sorted(unknown))}")
    current = _enabled_output_channels.get()
    merged = frozenset(
        channel for channel in ALL_OUTPUT_CHANNELS if enabled.get(channel, channel in current)
    )
    token = _enabled_output_channels.set(merged)
    try:
        yield
    finally:
        _enabled_output_channels.reset(token)


def output_channel_enabled(channel: str) -> bool:
    """读取通道启用态，未知名抛 ValueError，与作用域入口同口径拦截拼写错误。"""
    if channel not in ALL_OUTPUT_CHANNELS:
        raise ValueError(f"未知输出通道: {channel}")
    return channel in _enabled_output_channels.get()

"""Seedream MCP 命令行接口：argparse 参数解析与配置构建。

定义全部命令行选项，并按 CLI 参数 > 系统环境变量 > .env 文件 > 默认值的优先级构建
SeedreamConfig。
"""

from __future__ import annotations

import argparse
from collections.abc import Collection
from typing import Literal, cast

from .config import (
    DEFAULT_HTTP_HOST,
    DEFAULT_HTTP_PORT,
    HTTP_AUTH_TOKEN_MIN_LENGTH,
    LEGAL_LOG_LEVELS,
    SeedreamConfig,
    build_config_from_sources,
)
from .utils.model.model_capabilities import MODEL_ALIASES
from .version import __version__


def _build_config_from_args(args: argparse.Namespace) -> SeedreamConfig:
    """从命令行参数构建服务器配置对象。

    Raises:
        SeedreamConfigError: 缺少 API 密钥等必需参数。
    """
    overrides: dict[str, object] = {
        "api_key": args.api_key,
        "base_url": args.base_url,
        "model": args.model,
        "default_size": args.default_size,
        "watermark": args.watermark,
        "web": args.web,
        "log_level": args.log_level,
    }
    return build_config_from_sources(
        overrides=overrides,
        env_file=args.config_file,
    )


def _log_level_type(value: str) -> str:
    """argparse type 回调，将日志级别转为大写以保持大小写不敏感。"""
    return value.upper()


def _port_type(value: str) -> int:
    """校验端口为 1-65535 范围内的整数，作为 argparse type 使用。"""
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"端口必须为整数，收到 {value!r}")
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"端口必须在 1-65535 范围内，收到 {port}")
    return port


def _build_arg_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器，定义全部命令行选项。"""
    parser = argparse.ArgumentParser(
        description="Seedream MCP 服务器 - AI 图像生成工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  seedream-image-mcp --api-key your_key_here
  seedream-image-mcp --api-key your_key_here --model doubao-seedream-4.5 \\
      --default-size 4K --log-level DEBUG
  seedream-image-mcp --api-key your_key_here --config-file ./config.env
        """,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    # 配置来源
    parser.add_argument(
        "--config-file",
        help=".env 配置文件路径；指定后不再读取项目根与当前目录的 .env",
    )

    # 必需配置
    parser.add_argument(
        "--api-key",
        help="火山引擎 API 密钥；推荐经 ARK_API_KEY 环境变量提供，命令行传入会留在进程列表与 shell 历史中",
    )

    # 模型与端点
    parser.add_argument(
        "--model",
        choices=list(MODEL_ALIASES.keys()),
        default=None,
        help="模型别名；完整 Model ID 或 Endpoint ID 经 SEEDREAM_MODEL_ID 环境变量传入；"
        "未传入时按配置解析",
    )
    parser.add_argument(
        "--default-size",
        type=str,
        default=None,
        help='默认生成尺寸，支持 1K/1.5K/2K/3K/4K 或 "<宽>x<高>"；未传入时按配置解析',
    )
    watermark_group = parser.add_mutually_exclusive_group()
    watermark_group.add_argument(
        "--watermark",
        dest="watermark",
        action="store_true",
        default=None,
        help="启用默认水印；未传入时按配置解析",
    )
    watermark_group.add_argument(
        "--no-watermark",
        dest="watermark",
        action="store_false",
        help="关闭默认水印；未传入时按配置解析",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="模型 API 端点 URL；未传入时按配置解析",
    )

    # 日志
    parser.add_argument(
        "--log-level",
        type=_log_level_type,
        choices=list(LEGAL_LOG_LEVELS),
        default=None,
        help="日志级别；未传入时按配置解析",
    )

    # 传输与 Web
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="MCP 传输方式，默认 stdio",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HTTP_HOST,
        help="streamable-http 监听地址，默认 127.0.0.1；绑定非回环地址必须配置鉴权令牌与"
        " TLS，否则拒绝启动",
    )
    parser.add_argument(
        "--port",
        type=_port_type,
        default=DEFAULT_HTTP_PORT,
        help="streamable-http 监听端口，默认 8000，范围 1-65535",
    )
    parser.add_argument(
        "--auth-token",
        default=None,
        help="streamable-http 的 Bearer 鉴权令牌；推荐经 SEEDREAM_HTTP_AUTH_TOKEN 环境变量"
        "提供，绑定非回环地址时必须配置",
    )
    parser.add_argument(
        "--ssl-certfile",
        default=None,
        help="TLS 证书文件路径，与 --ssl-keyfile 成对提供；绑定非回环地址时必须配置",
    )
    parser.add_argument(
        "--ssl-keyfile",
        default=None,
        help="TLS 私钥文件路径，与 --ssl-certfile 成对提供",
    )
    parser.add_argument(
        "--insecure-allow-non-tls",
        action="store_true",
        default=False,
        help="允许非回环地址以明文运行 streamable-http，仅用于受信反向代理终结 TLS 的场景",
    )
    parser.add_argument(
        "--stateless",
        action="store_true",
        default=False,
        help="streamable-http 无状态模式，仅影响带握手会话的旧规范修订客户端，代价是失去"
        "反向通道；默认关闭",
    )
    web_group = parser.add_mutually_exclusive_group()
    web_group.add_argument(
        "--web",
        dest="web",
        action="store_true",
        default=None,
        help="开启 Web 操作台，浏览器经 /web 路径直接使用；未传入时按 SEEDREAM_WEB_ENABLED"
        " 解析，默认关闭",
    )
    web_group.add_argument(
        "--no-web",
        dest="web",
        action="store_false",
        help="关闭 Web 操作台，覆盖 SEEDREAM_WEB_ENABLED 的开启设置",
    )

    return parser


def _build_run_options(args: argparse.Namespace) -> Literal["stdio", "streamable-http"]:
    """构建 MCP 运行传输方式，仅支持 stdio 与 streamable-http。"""
    return cast(Literal["stdio", "streamable-http"], args.transport)


def _validate_transport_args(args: argparse.Namespace) -> str | None:
    """校验传输相关 CLI 参数组合，返回错误消息；参数合法时返回 None。

    仅 streamable-http 需要校验：TLS 证书与私钥必须成对提供或同时省略；
    --auth-token 显式提供时校验最短长度，与配置侧同口径。
    """
    if args.transport != "streamable-http":
        return None
    if (args.ssl_certfile is None) != (args.ssl_keyfile is None):
        return (
            "配置错误：--ssl-certfile 与 --ssl-keyfile 必须同时提供或同时省略，"
            "仅提供其一无法建立 TLS。"
        )
    cli_token = (args.auth_token or "").strip()
    if cli_token and len(cli_token) < HTTP_AUTH_TOKEN_MIN_LENGTH:
        return (
            f"安全错误：--auth-token 长度不得少于 {HTTP_AUTH_TOKEN_MIN_LENGTH} 字符，"
            "低熵令牌可被在线穷举，建议用 openssl rand -hex 32 生成。"
        )
    return None


def _validate_http_security(
    args: argparse.Namespace,
    auth_token: str,
    loopback_hosts: Collection[str],
) -> str | None:
    """校验 streamable-http 绑定安全性，返回错误消息；安全组合时返回 None。

    非回环绑定必须配置鉴权令牌，携带令牌后还须配置 TLS 或显式豁免，避免未授权
    访问与 Bearer 令牌明文传输。auth_token 为解析后的最终令牌，回环地址集合由
    调用方传入，本模块不依赖传输层私有符号。仅 streamable-http 需要校验。
    """
    if args.transport != "streamable-http" or args.host in loopback_hosts:
        return None
    if not auth_token:
        return (
            f"安全错误：streamable-http 绑定到非回环地址 {args.host} 必须配置鉴权令牌，"
            "请通过 --auth-token 或 SEEDREAM_HTTP_AUTH_TOKEN 提供，避免未授权访问。"
        )
    if not args.ssl_certfile and not args.insecure_allow_non_tls:
        return (
            f"安全错误：streamable-http 绑定到非回环地址 {args.host} 必须配置 TLS，"
            "请通过 --ssl-certfile/--ssl-keyfile 提供，或在受信反向代理终结 TLS 时"
            "显式传 --insecure-allow-non-tls，避免 Bearer 令牌明文传输被窃听。"
        )
    return None

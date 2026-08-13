"""Seedream MCP 命令行接口：argparse 参数解析与配置构建。

定义全部命令行选项，并按 CLI 参数 > 系统环境变量 > .env 文件 > 默认值的优先级构建
SeedreamConfig。传输方式判定也在此完成。cli_main 据此装配配置并分派至 server 或 transport。
"""

from __future__ import annotations

# 标准库导入
import argparse
from typing import Literal, cast

from .config import LEGAL_LOG_LEVELS, MODEL_ALIASES, SeedreamConfig, build_config_from_sources


def _build_config_from_args(args: argparse.Namespace) -> SeedreamConfig:
    """
    从命令行参数构建服务器配置对象

    优先级：命令行参数 > 系统环境变量 > .env 文件 > 默认值。

    Args:
        args: 解析后的命令行参数对象。

    Returns:
        构建完成的 SeedreamConfig 配置实例。

    Raises:
        SeedreamConfigError: 缺少 API 密钥等必需参数时抛出。
    """
    overrides: dict[str, object] = {
        "api_key": args.api_key,
        "base_url": args.base_url,
        "model": args.model,
        "default_size": args.default_size,
        "watermark": args.watermark,
        "log_level": args.log_level,
    }
    return build_config_from_sources(
        overrides=overrides,
        env_file=args.config_file,
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    """
    构建命令行参数解析器

    定义所有支持的命令行选项，包括 API 配置、模型选择、日志级别等。

    Returns:
        配置完成的 ArgumentParser 实例。
    """
    parser = argparse.ArgumentParser(
        description="Seedream MCP 服务器 - AI 图像生成工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  seedream-image-mcp --api-key your_key_here
  seedream-image-mcp --api-key your_key_here --model doubao-seedream-4.5
  --default-size 4K --log-level DEBUG
  seedream-image-mcp --api-key your_key_here --config-file ./config.env
        """,
    )

    # API 认证配置
    parser.add_argument(
        "--api-key",
        help="火山引擎 API 密钥；建议优先用 ARK_API_KEY 环境变量，命令行传入会出现在进程列表与 shell 历史中",
    )
    parser.add_argument(
        "--config-file",
        help="可选的 .env 配置文件路径，用于加载额外环境变量",
    )

    # 模型与生成配置
    parser.add_argument(
        "--model",
        choices=list(MODEL_ALIASES.keys()),
        default=None,
        help="模型选择（默认按配置或内置默认值）",
    )
    parser.add_argument(
        "--default-size",
        type=str,
        default=None,
        help='默认生成尺寸（支持 1K/2K/3K/4K 或 "<宽>x<高>"，默认按配置或内置默认值）',
    )
    watermark_group = parser.add_mutually_exclusive_group()
    watermark_group.add_argument(
        "--watermark",
        dest="watermark",
        action="store_true",
        default=None,
        help="启用默认水印（未传入时按配置或内置默认值）",
    )
    watermark_group.add_argument(
        "--no-watermark",
        dest="watermark",
        action="store_false",
        help="关闭默认水印（未传入时按配置或内置默认值）",
    )

    # 日志配置
    parser.add_argument(
        "--log-level",
        choices=list(LEGAL_LOG_LEVELS),
        default=None,
        help="日志级别（默认按配置或内置默认值）",
    )

    # 网络配置
    parser.add_argument(
        "--base-url",
        default=None,
        help="API 基础 URL（默认按配置或内置默认值）",
    )

    # 传输层配置
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="MCP 传输方式（默认 stdio）",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="streamable-http 监听地址（默认 127.0.0.1，仅 streamable-http 生效；"
        "绑定非回环地址将触发安全告警）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="streamable-http 监听端口（默认 8000，仅 streamable-http 生效）",
    )
    parser.add_argument(
        "--stateless",
        action="store_true",
        default=False,
        help="streamable-http 启用无状态模式，更适合远程多客户端与负载均衡（默认关闭）",
    )
    parser.add_argument(
        "--auth-token",
        default=None,
        help="streamable-http 的 Bearer 鉴权令牌；建议优先用 SEEDREAM_HTTP_AUTH_TOKEN "
        "环境变量，命令行传入会出现在进程列表与 shell 历史中；绑定非回环地址时必须配置，"
        "否则拒绝启动",
    )
    parser.add_argument(
        "--ssl-certfile",
        default=None,
        help="streamable-http 的 TLS 证书文件路径，绑定非回环地址时必须配置以防令牌明文传输；"
        "受信反向代理终结 TLS 时可用 --insecure-allow-non-tls 豁免",
    )
    parser.add_argument(
        "--ssl-keyfile",
        default=None,
        help="streamable-http 的 TLS 私钥文件路径，与 --ssl-certfile 配合使用",
    )
    parser.add_argument(
        "--insecure-allow-non-tls",
        action="store_true",
        default=False,
        help="显式允许非回环地址以明文运行 streamable-http，仅用于受信反向代理终结 TLS 的场景",
    )

    return parser


def _build_run_options(args: argparse.Namespace) -> Literal["stdio", "streamable-http"]:
    """
    构建 MCP 运行传输方式。

    SSE 传输已被 MCP 2025-03-26 规范弃用并由 Streamable HTTP 取代，
    本服务仅支持 stdio 本地传输与 streamable-http 远程传输两种方式。
    """
    return cast(Literal["stdio", "streamable-http"], args.transport)

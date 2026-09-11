"""命令行启动编排：解析参数、构建配置、初始化日志并按传输方式启动服务器。"""

from __future__ import annotations

import sys

from .cli import (
    build_arg_parser,
    build_config_from_args,
    build_run_options,
    validate_http_security,
    validate_transport_args,
)
from .config import drain_pending_build_warnings, set_active_config
from .resources import (
    SERVER_NAME,
    SERVER_VERSION,
    mcp,
    rebind_request_state_security,
    sync_cleanup,
)
from .transport import (
    resolve_http_auth_token,
    run_streamable_http,
    warn_remote_exposure,
)
from .utils.core.errors import SeedreamConfigError, format_error_for_user
from .utils.core.logs import get_logger, setup_logging
from .utils.io.io_path import drain_pending_start_messages, resolve_log_file_path

logger = get_logger()


def cli_main() -> int:
    """执行命令行主流程：解析参数、构建配置、初始化日志并按传输方式启动服务器。

    Returns:
        进程退出码，0 为正常退出，1 为配置错误或运行异常。
    """
    parser = build_arg_parser()
    args = parser.parse_args()

    try:
        config = build_config_from_args(args)
    except SeedreamConfigError as exc:
        print(f"配置错误: {exc.message}", file=sys.stderr)
        return 1

    # 注入活动配置，server 与 io_path 经 get_active_config 共用此实例。
    set_active_config(config)

    # setup_logging 的目录创建等 I/O 在只读容器或受限账号下可能抛 OSError，捕获后
    # 降级为 stderr 输出与退出码 1；不经 format_error_for_user，以免未知错误标签
    # 误导排查并回显绝对路径。日志文件路径由 io_path 单点求值，回退链整体不可
    # 解析时同此降级退出。
    try:
        setup_logging(
            config.log_level,
            str(resolve_log_file_path()),
            force_standard_logging=True,
            rotation_mb=config.log_rotation_size,
            retention_days=config.log_retention_days,
        )
    except SeedreamConfigError as exc:
        print(f"日志目录推导失败: {exc.message}", file=sys.stderr)
        return 1
    except OSError:
        print("日志系统初始化失败（请检查日志目录权限或磁盘空间）", file=sys.stderr)
        return 1
    drain_pending_start_messages()
    drain_pending_build_warnings()

    # 按最终活动配置重绑导入期固化的密钥环，使 --config-file 携带的密钥生效；
    # 置于日志系统就绪后，探测失败的 ERROR 落入文件通道；失败不阻断启动。
    rebind_request_state_security(config.request_state_secret_keys)

    logger.info(
        "Seedream MCP 启动: {} (version {})",
        SERVER_NAME,
        SERVER_VERSION,
    )

    try:
        transport = build_run_options(args)
        auth_token = ""
        error = validate_transport_args(args)
        if error is None and transport == "streamable-http":
            auth_token = resolve_http_auth_token(args)
            error = validate_http_security(args, auth_token)
        if error is not None:
            logger.error(error)
            # 退出路径的 stderr 兜底：SEEDREAM_LOG_LEVEL 高于 ERROR 时日志通道被过滤，仍保证可见
            print(error, file=sys.stderr)
            return 1
        if transport == "streamable-http":
            warn_remote_exposure(args.host, auth_enabled=bool(auth_token))
            run_streamable_http(
                args.host,
                args.port,
                auth_token,
                ssl_certfile=args.ssl_certfile,
                ssl_keyfile=args.ssl_keyfile,
                stateless=args.stateless,
                web_enabled=config.web_enabled,
            )
        else:
            mcp.run(transport=transport)
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在退出。")
        return 0
    except Exception as exc:
        logger.exception("服务器运行异常")
        print(f"服务器运行失败: {format_error_for_user(exc)}", file=sys.stderr)
        return 1
    finally:
        sync_cleanup()

    return 0

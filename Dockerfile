# syntax=docker/dockerfile:1.7

# Python 3.12 slim 基础镜像，次版本内自动跟踪补丁更新
FROM python:3.12-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_NO_PROGRESS=1

# 固定 uv 版本
ARG UV_VERSION=0.9.18
RUN pip install --no-cache-dir "uv==${UV_VERSION}"

# 复制项目清单与源码
COPY pyproject.toml uv.lock README.md ./
COPY seedream_mcp/ ./seedream_mcp/

# 基于 lock 文件安装依赖，并以非 editable 方式安装项目
RUN uv sync --frozen --no-dev --no-editable

# 使用项目虚拟环境作为运行时 Python
ENV PATH="/app/.venv/bin:${PATH}"

# 创建非 root 用户并准备目录
RUN useradd --create-home --shell /bin/bash seedream \
    && mkdir -p /app/.seedream/images /app/.seedream/logs \
    && chown -R seedream:seedream /app

# 切换到非 root 用户
USER seedream

# 设置入口点
ENTRYPOINT ["python", "-m", "seedream_mcp.server"]

# 默认参数
CMD ["--help"]

# 健康检查采用导入式：验证包与运行时依赖可正常加载，作为最小可移植的存活信号。
# streamable-http 模式已提供 GET /health 端点与端口监听，部署时可直接探活 HTTP；
# 本镜像默认 CMD 为 --help 不对外服务，故保留导入式检查作为传输无关的运行时兜底，
# 实际 streamable-http 部署由 docker-compose 探测监听端口完成就绪判断。
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import seedream_mcp" || exit 1

# 镜像版本
ARG APP_VERSION=dev

# 添加标签
LABEL org.opencontainers.image.title="Seedream MCP" \
      org.opencontainers.image.description="基于火山引擎 Seedream API 的 MCP 工具，支持 AI 图像生成" \
      org.opencontainers.image.source="https://github.com/tengmmvp/Seedream_MCP" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${APP_VERSION}" \
      maintainer="tengmmvp <tengmmvp@gmail.com>" \
      contributors="tengmmvp <tengmmvp@gmail.com>, caoergou <itsericsmail@gmail.com>"

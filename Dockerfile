# syntax=docker/dockerfile:1.7

# 基础镜像按 digest 固定以保证供应链可复现；tag 升级经 dependabot 更新 digest。
FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6

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

# 先复制项目清单并只安装依赖，源码未变更时命中缓存层；README 与 LICENSE 为 hatchling
# 构建元数据所需，缺 LICENSE 会使 PEP 639 的 license-files 静默落空
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --locked --no-dev --no-install-project

# 复制源码后以非 editable 方式安装项目本体
COPY seedream_mcp/ ./seedream_mcp/
RUN uv sync --locked --no-dev --no-editable

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

# 健康检查采用导入式：验证包与依赖可加载，作为传输无关的最小存活信号；
# 默认 CMD 为 --help 不对外服务，streamable-http 部署的就绪判断由 compose 探测端口完成。
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

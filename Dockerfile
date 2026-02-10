# syntax=docker/dockerfile:1.7

# 使用固定版本的 Python 基础镜像
FROM python:3.11.11-slim

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
    && mkdir -p /app/seedream_images /app/logs \
    && chown -R seedream:seedream /app

# 切换到非 root 用户
USER seedream

# 设置入口点
ENTRYPOINT ["python", "-m", "seedream_mcp.server"]

# 默认参数
CMD ["--help"]

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

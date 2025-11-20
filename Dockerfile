# 使用官方 Python 镜像作为基础镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv
RUN pip install uv

# 复制项目文件
COPY pyproject.toml ./
COPY README.md ./
COPY seedream_mcp/ ./seedream_mcp/

# 创建非 root 用户
RUN useradd --create-home --shell /bin/bash seedream

# 安装项目依赖
RUN uv pip install --system -e .

# 切换到非 root 用户
USER seedream

# 创建默认配置目录
RUN mkdir -p /app/seedream_images /app/logs

# 设置入口点
ENTRYPOINT ["python", "-m", "seedream_mcp.server"]

# 默认参数
CMD ["--help"]

# 添加标签
LABEL maintainer="Eric Cao <itsericsmail@gmail.com>" \
      version="1.0.0" \
      description="Seedream 4.0 MCP Server - AI 图像生成工具" \
      org.opencontainers.image.source="https://github.com/caoergou/Seedream_MCP" \
      org.opencontainers.image.description="基于火山引擎 Seedream 4.0 API 的 MCP 工具，支持 AI 图像生成" \
      org.opencontainers.image.licenses="MIT"
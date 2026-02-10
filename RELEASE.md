# 发布指南

## 🚀 发布流程

### 1. 更新版本号

```bash
# 仅编辑这一处
# 文件: seedream_mcp/version.py
__version__ = "1.2.3"
```

### 2. 创建标签

```bash
VERSION=$(python -c "from seedream_mcp.version import __version__; print(__version__)")
git tag "v${VERSION}"
git push origin "v${VERSION}"
```

GitHub Actions 会自动：

- ✅ 测试代码
- ✅ 创建 GitHub Release
- ✅ 构建 Docker 镜像
- ✅ 发布到 GitHub Container Registry

## 📦 发布产物

- **GitHub Release**: 包含版本信息和更新日志
- **Docker 镜像**: `ghcr.io/tengmmvp/seedream-mcp`
- **源代码**: 始终通过 GitHub 仓库提供

## 🔄 版本号规范

- `v1.0.0`：主版本
- `v1.1.0`：功能更新
- `v1.0.1`：修复版本
- `v1.0.0-alpha.1`：预发布版本

## 🎯 用户获取方式

### uvx 安装

```bash
uvx run git+https://github.com/tengmmvp/Seedream_MCP --api-key your_key
```

### Docker 运行

```bash
docker run ghcr.io/tengmmvp/seedream-mcp
```

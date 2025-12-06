# 发布指南

## 🚀 发布流程

### 1. 更新版本号

```bash
# 编辑 pyproject.toml
version = "1.2.2"
```

### 2. 创建标签

```bash
git tag v1.2.2
git push origin v1.2.2
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

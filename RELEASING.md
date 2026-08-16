# 发布指南 (Releasing)

本文档说明如何将 `seedream-image-mcp` 发布到 [PyPI](https://pypi.org/project/seedream-image-mcp/) 与 GitHub Container Registry，以及维护者需要完成的一次性配置。

发布由 [.github/workflows/release.yml](.github/workflows/release.yml) 自动完成，触发条件是推送 `v*` 开头的 tag。

## 一次性配置（首次发布前）

### 1. 确认 PyPI 包名可用

访问 <https://pypi.org/project/seedream-image-mcp/>：

- 返回 404 → 包名可用，继续。
- 已存在且属于你 → 继续。
- 已存在且属于他人 → 改包名（同步修改 `pyproject.toml` 的 `[project].name`、README 与 CI 中的引用）。

### 2. 配置 Trusted Publisher（OIDC）

本项目使用 PyPI **Trusted Publishing**，通过 GitHub OIDC 短期令牌发布，**无需长期 API token**。

在 PyPI 后台（`Account settings → Publishing → Add a new publisher → GitHub`）填写：

| 字段              | 值                   |
| ----------------- | -------------------- |
| PyPI Project name | `seedream-image-mcp` |
| Owner             | `tengmmvp`           |
| Repository name   | `Seedream_MCP`       |
| Workflow filename | `release.yml`        |
| Environment name  | _(留空)_             |

> 由于项目首次发布时尚不存在，使用 **"pending publisher"**（针对尚不存在的项目）。PyPI 会在首次成功发布时自动创建项目，之后该 publisher 自动转正。
>
> `release.yml` 的 `pypi-release` job 未设置 `environment:`，因此此处 Environment 留空；若将来改用 environment，需在 PyPI 后台与 workflow 中同步配置。

### 3. （可选）GitHub Container Registry

Docker 镜像发布到 `ghcr.io` 使用内置 `GITHUB_TOKEN`，**无需额外配置**。

## 发版流程

1. **更新版本号**：仅修改 [seedream_mcp/version.py](seedream_mcp/version.py) 中的 `__version__`。

   ```python
   __version__ = "1.2.7"
   ```

2. **提交并打 tag**（在 main 分支上进行）：

   ```bash
   git add seedream_mcp/version.py
   git commit -m "chore(release): bump version to v1.2.7"
   git tag v1.2.7
   git push origin main      # 推送提交（用户手动）
   git push origin v1.2.7    # 推送 tag，触发 CI
   ```

3. **CI 自动执行**（`.github/workflows/release.yml`，tag `v*` 触发）：
   - `test`：3.12 矩阵跑 pytest + black + mypy + flake8 + 构建验证
   - `pypi-release`：`uv build` 产出 sdist/wheel，通过 trusted publishing 发布到 PyPI
   - `build-and-release`：依赖 PyPI 成功后创建 GitHub Release（安装说明引用 PyPI，故须等其完成）
   - `docker-release`：构建多架构镜像推送 ghcr.io（与 PyPI 并行，从源码构建，不依赖 PyPI）

4. **验证**（约 2–5 分钟后）：
   - PyPI：<https://pypi.org/project/seedream-image-mcp/#history>
   - GitHub Release：<https://github.com/tengmmvp/Seedream_MCP/releases>
   - 命令行：`uvx seedream-image-mcp --help`

## 版本规则

- 遵循 [SemVer](https://semver.org/)：`MAJOR.MINOR.PATCH`
- 预发布：tag 含 `alpha` / `beta` / `rc`（如 `v1.3.0rc1`、`v1.3.0-beta1`）时 CI 自动标记为 prerelease；`uvx seedream-image-mcp` 默认仍拉取稳定版
- **版本号一经发布不可覆盖**：PyPI 不允许重新上传同一版本。发版有误只能发更高版本号修正

## 故障排查

| 现象                                        | 原因与处理                                                                                                                 |
| ------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `pypi-release` 报 OIDC / `403`              | trusted publisher 配置与 workflow 不一致（repo / owner / workflow filename / environment）。核对 PyPI 后台与 `release.yml` |
| `pypi-release` 报 `400 File already exists` | 该版本已在 PyPI 存在。bump 版本号重发，勿覆盖                                                                              |
| 发布成功但 `uvx` 仍拉到旧版                 | PyPI CDN 缓存，等待几分钟                                                                                                  |
| tag 误推、CI 未成功                         | 删除 tag 后修正重打：`git tag -d vX.Y.Z && git push origin :refs/tags/vX.Y.Z`（已发到 PyPI 的无法撤销）                    |

## 回退

- **tag 未触发 CI / CI 未成功**：删除 tag，修正后重打。
- **已发布到 PyPI**：无法删除或覆盖，只能发布更高版本号。可在 PyPI 后台对错误版本执行 **yank**（隐藏但不删除）。

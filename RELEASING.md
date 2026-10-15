# 发布指南（Releasing）

本文档说明如何将 `seedream-image-mcp` 发布到 [PyPI](https://pypi.org/project/seedream-image-mcp/) 与 GitHub Container Registry（ghcr.io）。发布由 [release.yml](.github/workflows/release.yml) 自动完成，推送 `v*` 开头的 tag 触发。

## 发版流程

1. **更新版本号**：仅修改 [src/seedream_mcp/version.py](src/seedream_mcp/version.py) 中的 `__version__`——版本号的单一来源。

   ```python
   __version__ = "4.3.0"
   ```

   升级 **minor 或 major** 时，还须同步更新 [docker-compose.yml](docker-compose.yml) 的镜像标签为新 `major.minor`；补丁版无需改动，该标签自动跟进。

2. **提交并打 tag**：在 main 分支上执行以下命令：

   ```bash
   git add src/seedream_mcp/version.py docker-compose.yml
   git commit -m "chore(release): bump version to v4.3.0"
   git tag v4.3.0
   git push origin main
   git push origin v4.3.0    # 推送 tag，触发发布流水线
   ```

3. **CI 自动执行**：流水线先过质量门禁，通过后自动完成发布——sdist/wheel 上传 PyPI，GitHub Release 随之创建，Docker 镜像推送 ghcr.io。作业构成以 `release.yml` 为准，手动触发仅运行检查，不执行发布。

4. **验证**：流水线完成后确认以下各项：
   - PyPI：<https://pypi.org/project/seedream-image-mcp/#history>
   - GitHub Release：<https://github.com/tengmmvp/Seedream_MCP/releases>
   - 命令行：`uvx seedream-image-mcp --help`

## 发布机制

- **PyPI**：经 Trusted Publishing（GitHub OIDC 短期令牌）发布，publisher 已绑定本仓库（owner `tengmmvp` / `Seedream_MCP` / `release.yml`），无需 API token。
- **GHCR**：Docker 镜像推送使用内置 `GITHUB_TOKEN`，无需额外配置。

## 版本规则

- 遵循 [SemVer](https://semver.org/)：`MAJOR.MINOR.PATCH`
- 预发布：tag 含 `-rc` / `-alpha` / `-beta`（如 `v4.3.0-rc.1`）时 CI 自动标记为 prerelease，Docker 镜像不获得 `latest` 标签；其余 tag 均为正式版
- **版本号一经发布不可覆盖**：PyPI 不允许重新上传同一版本，发版有误只能发更高版本号修正

## 故障排查

| 现象                                        | 原因与处理                                                                                                   |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `pypi-release` 报 OIDC / `403`              | trusted publisher 配置与 workflow 不一致（repo / owner / workflow filename）。核对 PyPI 后台与 `release.yml` |
| `pypi-release` 报 `400 File already exists` | 该版本已在 PyPI 存在，bump 版本号重发                                                                        |
| 发布成功但 `uvx` 仍拉到旧版                 | PyPI CDN 缓存，等待几分钟                                                                                    |
| tag 误推、CI 未成功                         | 删除 tag 后修正重打：`git tag -d vX.Y.Z && git push origin :refs/tags/vX.Y.Z`                                |

错误版本无法删除或覆盖；可在 PyPI 后台执行 **yank**，该操作仅从索引隐藏版本，不删除文件。

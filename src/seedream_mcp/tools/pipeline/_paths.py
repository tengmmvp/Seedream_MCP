"""调用级保存路径解析与预检。

save_path 为调用级保存声明，位置不受限，相对路径以部署级图片目录为基准；
目录体系求值位于 utils/io/path。
"""

from __future__ import annotations

from pathlib import Path

from ...utils.core.errors import SeedreamValidationError
from ...utils.io.path import normalize_path, resolve_images_root, resolve_rejecting_cycle


def _resolve_base_dir(save_path: str | None) -> Path:
    """解析本次调用的写入目录：save_path 声明优先，否则取部署级图片目录。

    save_path 为调用级保存声明，位置不受限；相对路径以部署级图片目录为基准，
    绝对形态不依赖基准、数据根目录声明不可解析时不受阻；UNC、空字节、冒号分量等
    形态经 normalize_path 在 resolve 前拒绝；纯尾段环在 3.12 由 normalize_path
    兜底转 ValueError 已拒绝，前缀环与 3.13+ 全环形态经 resolve_rejecting_cycle
    复核拒绝。

    Raises:
        SeedreamValidationError: save_path 路径无效或含链接环。
        SeedreamConfigError: 部署级数据根目录不可解析，经 resolve_images_root 穿透。
    """
    if not save_path:
        return resolve_images_root()
    base = None if Path(save_path).is_absolute() else str(resolve_images_root())
    try:
        normalized = normalize_path(save_path, base)
        return resolve_rejecting_cycle(normalized)
    except (ValueError, OSError) as exc:
        raise SeedreamValidationError(
            f"保存路径无效: {exc}", field="save_path", value=save_path
        ) from exc


def prevalidate_save_path(save_path: str | None) -> None:
    """在生成请求分发前预检 save_path 的路径有效性。

    使非法 save_path 在计费请求前即以 validation_error 拒绝，而非留待自动保存
    阶段降级为软警告。未提供 save_path 时不做检查。

    Raises:
        SeedreamValidationError: save_path 路径无效。
        SeedreamConfigError: 图片目录配置无法解析，经 resolve_images_root 抛出。
    """
    if not save_path:
        return
    _resolve_base_dir(save_path)

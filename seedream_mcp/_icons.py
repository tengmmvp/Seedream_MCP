"""工具图标
Lucide 线条图标（https://lucide.dev，ISC License）内嵌为 data URI。
"""

from __future__ import annotations

from .utils.core.formats import encode_data_uri

_TOOL_SVGS: dict[str, str] = {
    # 图标 image-plus：从无到有生成图片
    "text_to_image": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
        'fill="none" stroke="#3790ff" stroke-width="2" stroke-linecap="round" '
        'stroke-linejoin="round"><path d="M16 5h6"/><path d="M19 2v6"/>'
        '<path d="M21 11.5V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h7.5"/>'
        '<path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/><circle cx="9" cy="9" r="2"/></svg>'
    ),
    # 图标 wand-sparkles：以参考图为基准重塑
    "image_to_image": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
        'fill="none" stroke="#3790ff" stroke-width="2" stroke-linecap="round" '
        'stroke-linejoin="round"><path d="m21.64 3.64-1.28-1.28a1.21 1.21 0 0 0-1.72 0'
        "L2.36 18.64a1.21 1.21 0 0 0 0 1.72l1.28 1.28a1.2 1.2 0 0 0 1.72 0L21.64 5.36"
        'a1.2 1.2 0 0 0 0-1.72"/><path d="m14 7 3 3"/><path d="M5 6v4"/>'
        '<path d="M19 14v4"/><path d="M10 2v2"/><path d="M7 8H3"/>'
        '<path d="M21 16h-4"/><path d="M11 3H9"/></svg>'
    ),
    # 图标 images：多张图片并列成组
    "multi_image_fusion": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
        'fill="none" stroke="#3790ff" stroke-width="2" stroke-linecap="round" '
        'stroke-linejoin="round"><path d="m22 11-1.296-1.296a2.4 2.4 0 0 0-3.408 0L11 16"/>'
        '<path d="M4 8a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2"/>'
        '<circle cx="13" cy="7" r="1" fill="#3790ff"/>'
        '<rect x="8" y="2" width="14" height="14" rx="2"/></svg>'
    ),
    # 图标 book-image：组图序列成册
    "sequential_generation": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
        'fill="none" stroke="#3790ff" stroke-width="2" stroke-linecap="round" '
        'stroke-linejoin="round"><path d="m20 13.7-2.1-2.1a2 2 0 0 0-2.8 0L9.7 17"/>'
        '<path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H19a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1'
        'H6.5a1 1 0 0 1 0-5H20"/><circle cx="10" cy="8" r="2"/></svg>'
    ),
    # 图标 folder-search：在图片目录中检索浏览
    "browse_images": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
        'fill="none" stroke="#3790ff" stroke-width="2" stroke-linecap="round" '
        'stroke-linejoin="round"><path d="M10.7 20H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9'
        'a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H20a2 2 0 0 1 2 2v4.1"/>'
        '<path d="m21 21-1.9-1.9"/><circle cx="17" cy="17" r="3"/></svg>'
    ),
}


def tool_icon_src(tool_name: str) -> str:
    """返回指定工具图标的 SVG data URI，未登记的工具名抛 KeyError。"""
    return encode_data_uri("image/svg+xml", _TOOL_SVGS[tool_name].encode("utf-8"))

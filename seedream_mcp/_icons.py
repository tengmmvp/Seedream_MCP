"""工具图标
Lucide 线条图标（https://lucide.dev，ISC License）内嵌为 data URI；
每工具提供 SVG 与 72×72 PNG 双格式。
"""

from __future__ import annotations

import base64

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


# 72×72 光栅版本，供仅实现 MUST 图标集（PNG/JPEG）的客户端渲染。
TOOL_PNG_SIZE = "72x72"
_TOOL_PNG_B64: dict[str, str] = {
    "text_to_image": (
        "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"
    ),
    "image_to_image": (
        "iVBORw0KGgoAAAANSUhEUgAAAEgAAABICAIAAADajyQQAAAE+0lEQVR4nO2bX0wcRRzHf/uHO9qL6bXghYCHpvYop9YX"
        "qFpf1KRKLqZamvhiqtFyNT62NZFqqg/atGpM6Ku2V03tkxpB+kDovdSXVktpmkbDATYGsASvlAPjXeFYdgwc2bvbnd2d"
        "nZ27Gxq+T+zN/Ib53Pzmt/ObmQNY17q4kMC+RcnTuOez2qfeAoDpq9+OXziKlrIu2/QFWwK7DvqCLdWB7fPJ4fTEYPLK"
        "6fTEoFU3gLUebu+qe+6Q9jj1y6mx7sPUrQmSp+GlY/UvfiCIcuHnSFUm4ydvXzxu9q2xB2s5cVfeuEV7VDIzgx/W0DUl"
        "SJ7QgR83P77HrMJcon8ktlddnMfY0v1LCw+UN27WFSmZFIVP2lJZswkl8kCjHPmkKHtDHd3+cISk8uxQ32isXVUWiloA"
        "Rqrd+abLCnRUAOAPR0Id3aLsLWoEOJMF1exQ3/VjgdmhPmORPxx5KPJJUTusOjQ9cM5lBVuq0Vj74n93RmPtWLa6F474"
        "gq35poCRxns7py51KZmUsUjJpKYudY33drqkys0iVVnAsgmiHHj2nfwj8BHuCams66f/vv77ly2rFYC1dC7HxAN1VLAy"
        "brfO66PRhsB27e+i1zkTLbscQoVLKuv6guTZ9vYPWKq5RP/o2X1GKlix2vr6Wd2H9/5J5CtUavln+xa2WlVIeKvk5a/+"
        "+v5dByPW+Mrn2su37vnDIAhuln9uPBDMrZCqJH+N5auR9ED3biV/1ZbCA7eZWE3GT6THB7RHohErjHLGRwpJ3gd2dN70"
        "bnmEyVjBitXti8eLKkPZJUieHe/fwFLRjdXcihVaWqwkWG7ee2u2Gov+HY47jRZgHmNkRwmIpqdPIYqUxDoTQUgFhBxZ"
        "zTnKx2wTELqUhCS/Mk4wOirAuiJF0LM1IcwadQkINRWUZ44RUuXkD0eaoj+LVdVuqAA7x6YHzjl1RYsFoVX/huNIzfrD"
        "L+s+39Tc1hTtxRYBGRV+jpEED03WwcP2W0eqSj6Y5FSka0UtBmr67ZC9IaEvkW8EkFOVcI6RzxCzxNHaqjJgTuf9Klui"
        "nxUVEIIpmRmLR53oohlCCFSFFRUQgpEnxW7yK/9jmBiYTY1RUAHh6p4wKWabNS5TzYzd/OJJCipgu8XNlorOAxkHD96o"
        "gAkYh1TgHoxPKnAJxi0VuAHjmQqoo2I5qeh2NQViloqNFd2htmhbo+IeSLerKZJUctk/t7kw1a6mSFJpTUQLSrC1RQXk"
        "py3lj4EuNyYEzmMgiaZwcVLkPAaSqBZnIt5P84oIrFJUJGfWJCYyb2OlZeuOggcYO8O/B9Ltaor307wydUVB8jRFe1id"
        "dltblVpi4UND20cMT7strBzJ0a4mBswXbK3fjZmFs0N9I2dexfqSKHuboj1YD7SwKsNVHyh0xcCug7p7txUfK7qrPjnl"
        "w8sT713zBVdvWK3FaKFTfoiqHwzpym6df8PMAzmMFqZzbP7OiK7s0f3fiVXVlfJAZmBpw/385SPTjp5CNv49EAOWvHIa"
        "GQ5yNjW3hQ78lDvGL08MLMmITcZPml1RkH01/M+rQgmEKw8z8UkFupUHWsqSHAfzTwXGRbCqLIyc2Zv644KtJVcxkCjR"
        "REvZP795zXrcOIwWOpkmNoJU1dD2cf3uoya/b/pUdz+QNwnWxb7GnYFnor7G1g2B5nvJxPIv0i5/nZ64Vq7urWtdsNb1"
        "P7CDAg/VPRQ9AAAAAElFTkSuQmCC"
    ),
    "multi_image_fusion": (
        "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"
    ),
    "sequential_generation": (
        "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"
    ),
    "browse_images": (
        "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"
        "RU5ErkJggg=="
    ),
}


def tool_icon_png_src(tool_name: str) -> str:
    """返回指定工具图标的 72×72 PNG data URI，未登记的工具名抛 KeyError。"""
    return encode_data_uri("image/png", base64.b64decode(_TOOL_PNG_B64[tool_name]))

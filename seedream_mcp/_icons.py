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
    # 图标 info：查询当前配置模型与能力
    "get_model_info": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
        'fill="none" stroke="#3790ff" stroke-width="2" stroke-linecap="round" '
        'stroke-linejoin="round"><circle cx="12" cy="12" r="10"/>'
        '<path d="M12 16v-4"/><path d="M12 8h.01"/></svg>'
    ),
}


def tool_icon_src(tool_name: str) -> str:
    """返回指定工具图标的 SVG data URI，未登记的工具名抛 KeyError。"""
    return encode_data_uri("image/svg+xml", _TOOL_SVGS[tool_name].encode("utf-8"))


# 72×72 光栅版本，供仅实现 MUST 图标集（PNG/JPEG）的客户端渲染。
TOOL_PNG_SIZE = "72x72"
_TOOL_PNG_B64: dict[str, str] = {
    "text_to_image": (
        "iVBORw0KGgoAAAANSUhEUgAAAEgAAABICAYAAABV7bNHAAAEMElEQVR4nO2bQXbaMBCG/1H6XpNV6QnKDUpX0GyqniC5QckJgk/Q"
        "9AQmJ4CcoMkJ4mxSsio3aHuDdEU2ZfoGTJtS21hGsmPh/z2/gJHG5kMazcgToFGjRg5F8FQ65NaM8BFAPz41vhtQYGpHwVPNgDMA"
        "AwCt+Bh0Qx6a2vEWEAgfcp3boGcmjXshayi8A0Mz0Il/GVea7jFObgOaFuyfdG8tJ4D0cj6PGDgGoyx1fhGueyG/mQT0HRVp4xQ7"
        "DLkzI3yFwClfLarmuvkA6ZBbvwifAbSxo8oENCOMKoXD+PkcGD9JQAuHjOqGNzFu9gAdBXSPCpXlpHXiWcZPAgb7wGVVN78WBBqt"
        "TN0hpy0z90nBpDEgBfS/BHSJ6oPAU8tmV8Ek3QUkAWb2FGPC66TzVcMpGvAVtZ3lpFt4qmKHOeSa7bqmGmNXhgkY1h7QnfgIxrks"
        "GNaMyuLD+DQJSPzbH5Gpt78bEG2Ru8kFpyBEmONmElAER7Jx/8bJal5l5W4sqyNDC6nukC8PGCdVxzpZsj7FDs1yt2NpK32wC4B0"
        "sdytLX2kL3wHNCueu7Xjvv4C6m2fux3HNrwdQTpj+Tw5YLyUFUReZyzP9gAlXaNAWOAckAL6k4DGq5VKXkuya2LDVjC5HgSWCogN"
        "cjfZCTCxsXUwmRIElhsHMWjrDMlyjhVn5WmjtdwRRBIhJ+htyP857ocUZ55mo0rZ9EFR0sk5YdQ956NVnNM75w9MCE1sVCmbqUYE"
        "LHb51tUC43K2TC2Qup/314afgCYBRd2Qr0A4KtKfGBd5k1cZjQ8Kp2C04yRYjqlMUSZMD+a4yMrveiG3WS3vk+a4ynruZjWSPgD6"
        "xPhh2k/67Od0puLTZoRvzDjj5Z70Ko/rLN4zhvJ5ku8TSTDKkisyhnLwhlzQKqAooHsFHJtAkrbSZ1NGL6OmN+TRfJnrbcrbWtJO"
        "2q9/EPu/x/0lfxyVls3fBjTdl1+VcZVnWknbTc/f4+2T63jE5Ja0T4CUNFo6pe4HRcvRsMqtFsc/G2ZLZxzl8TkrOFlfIgckTAZ0"
        "UqS/E0ArxQAKr0x54cg0ZcIrF5Ce7J60zgFHnr5KEjwJqC1/5b3hdKt2BDmGI2FBf21a617IY055bmbqw57kCNIF4DyWnJfPbd2P"
        "8gmOC0jKNzi2ISkf4diEpHyFYwuS8hmODUjKdzjbQlK7AGelIvbUrsApqlIB6ZrBKRWQriGc0gDpmsIpBZCuMRzngHTN4TgFpD2A"
        "4wyQ9gSOE0DaIzhOSvBmHsHJBmRYbKRrACe1DjLjuyob1Rq6BnCKVpWobas1dE3gFK0qSS1Yip9hX2MHRIz3aQ8xU0fQogNvfnxc"
        "d22qKlEuqjXqojxVJcp2tUZdlLeqRNms1qiL8laVLNqaGO6tV2sQXqAOWv4jslFVSaNGjRo1aoTa6zdugmSBcWeTiQAAAABJRU5E"
        "rkJggg=="
    ),
    "image_to_image": (
        "iVBORw0KGgoAAAANSUhEUgAAAEgAAABICAYAAABV7bNHAAADyklEQVR4nO2cQXLTMBSG/6dhT67Alg2wioZVbtJwgjonaHsCJycg"
        "3IQNjLMi3IAjtKt2xWPkpGnt2LIkS3Is/M900pFluf2i96T3nm1g0qRJAUWIqEXOs0fCDYDlsWm7y2gV6noy5wUIVwx8xOFnT8Be"
        "MDY/V7S/OEDznNcgXFcaGZvdijLf15IbvmHGbdtxItwW13TXNY5ATBGujNp6Sq75qw6Okjqu+g0+gxZVs5q1dLv3ZW4lnBcT7hQB"
        "2yKjL4PNoEeU32SmgYPjsaw0wYhwlFR/3UwKb2JkYUI9zM0Fjgmk8IDYwoxt+lrAIcY3YrxTn+2XxlLmfDuEk96adiRgHQJOsaJl"
        "saI/6lMLiXDzOWe1HYgHaKeWcMYGjIfWTowHYtwVK9KuPK5wXrd1Qfp78JcvYyCi5jnfg/C20sh42K1I58C9wamcn/OWm33efpfR"
        "p2H2QTg3t5Bm1TFM22yNa2Kt5hbRrJo7lnuzczF+V7tdeDwVAo5unPoYoufGb+Zjg3cpcJrcgLjUeGoIOEeT//667Q3sNTNs6y1l"
        "zk+EHwy8jwBHjTPIRtFZTyIqnMbjFwtIHlIWg8LRmphhmuKk+ZrZV/rCKPAkFT71G8cEsuiZpmiT8+pmGpV3pSl8OfZ2E/OxMpHd"
        "GLYpizZI3jaTWkCOqQfXMVzzOXVIPuF0LfNboJZgt5RpnGXyT6nPluDyGdLpd19wynN0B+cHH7I8i8C7pOIsYG0SZ9l845oIvFMu"
        "cMrzbE+YV1erk3YZWY/lYg4ukFzhDLoPko6+oivhZTrORQOSPR2pKaS+cNwAcUPqVJdODZfP4dBwXGfQNuRq5SllsfcBp/ybXE6a"
        "P69uRzi+V6s+49Rzyn0VJWkv4+VzvJhVVCctRwwnOCA5cjhBAckE4AQDJBOBEwSQTAhOeZ1U4Sw81e9EinB81u9EinB81u9EknA8"
        "1u9EonC8yRmQ/A/glNcfO5yFZf2ups76nRgznBj1OzFmODHqd2LUcCLU78So4VjeZtwmXUaURg4neP2Oxg4ndP1OpAjHpxoBTXA0"
        "gNQDHUnMHO5Xv2sEpJ7xVA90jB6Ox7v66zNo/DPH4139ShWPPl/zr/qzCmOE41P1GXQG56holdPLBlR7kOMkjV9KGc4ZIPXQve0d"
        "pTJhOGeAhMbLcwOk1OEoUeM+SLfUA1sw7pTZpQ5HqTEu6XOzZEpwWkMN2/sAU4WjDVZdIFFicDoTZoUFpBThGGUUiwMk7Wtkjtv4"
        "5OAokdXLioBl+bIiwge1qSxfVgSsTV9WNGnSpEmo6h/CCsFM+hA/DgAAAABJRU5ErkJggg=="
    ),
    "multi_image_fusion": (
        "iVBORw0KGgoAAAANSUhEUgAAAEgAAABICAYAAABV7bNHAAAEF0lEQVR4nO2cTVLbQBCFXyte2KsoN+AGISsrbFBOEHMCxAlinYDc"
        "QOYEmBPgnCBiQ8QqPgK5AazsDXSq9VMFtmRJoJFGsl6Vq1xisJpPPT/9ZgzQq1cvhSLUJMtjGwaOwbAZOARgohk9ELAEwcczbgKX"
        "/EYB2R6bK8IlgAn01GLEOPNdeqgd0JHHh0+EawAH0Fv3Hxgnty4tawNkR5nztwVwEt2PGF82M8lQdbdV1K3aAkd0EMesPoMsj20m"
        "/EYLRYxvLwfugaL72KlXGY8ETIfAImtQrEOWxw4DMxA+ZsTeDCADcP64tEDDClyaWx6Dsd2lNmNXMgYx4XPadR3gJJIsLhK7qkHa"
        "hOba0cXNWmaxrqgHlCNVg/SbZV3wKRhOXK8hrpvmwQ+6wj4Dsj0214Rr5tezCMuswrCtGTtDxkndywNtutha4GStn2JQ0qbeqDQB"
        "ZEULt0w4iaSNtMW+AQJQ5o/eP0CcsbB8b9taBmlr2wkUze+m5FYWBYMKl83SVgdAiRPI4gTy1o+nY4/pzqVpFUHIVM7AcdG2aLqL"
        "iRMYm12TzN8knFYYx1xR2+ozKM6cfJuUq0v1uLp2mHZnEXFosu8EFMZv4JhkOHgxLCRGPQPL0TNuiq6nBm91Akn8lAo1BCZrxiIL"
        "ksCRNrs+I878azAONkeFZMEp71eE5ZHHZ09lAYVOYN7uA4em1yxw6ScqVPxEk3XO61IDmOdljnXB50+MojHJZoIMIaUzqHEnMIhA"
        "zN9bolSlQZucwIzxUrzvZPmhdhZrgxNYJ5y0aV57J7AoHGL8MxgnI8Ynecl7uYa22h0Vw7kaAtON8XJhe+yvGTMusYYbdBFO4FJq"
        "QRsDc8IdjYKQtChW64DzUtJG2nYGkF0hnERF2xr7CKeMtAZkNwxHa0C2BnC0BWRrAkdLQLZGcLQDZGsGRytAtoZw1AFiPOoOR+5Z"
        "JHYlgCjDWE/b9Gsqc9YZxuBm7Kq6mJ92kQmeHE5Inl5TcCQGiaVI7KqKVR/Aecp1kxnzFQHjGWNV4IOkqBzPuModFPD2NlYmICUZ"
        "FMgpUcYvtExxtqoHJBoBzlsMqqYksYqHtHldGSDfpQcDmLQBUug+ApO0DQml66Bbl5bDaANP2+4Wu4+Had/TqMVR9KOnMgkPQUS7"
        "JtFuZ/ohbvWKtrAEhow1ft7XoWqzXIMokJ3B6ChtSg1d1QPKUQ8oRz2gHPWAurRxmCgucqXWc5ScmWx7Bq0QngOSssCMX3JmstID"
        "Xa0GhLRt42rPTGYAKukENqHYSzLrOh5sFHECv3qszT8FKOoEqupiqaXAM+FyfMHfM33cmlTGCVQ1i2U6gWAsEiewKZVxApVkUJec"
        "QGWzWFecQGWAuuIEKl0HdcEJrEq5awerpU5gr1690Ab9Bx05Co2RusrgAAAAAElFTkSuQmCC"
    ),
    "sequential_generation": (
        "iVBORw0KGgoAAAANSUhEUgAAAEgAAABICAYAAABV7bNHAAAD4UlEQVR4nO2cQVbbMBCG/xFdJLv0BM0Nmq6issE3ID1B4QTgExBO"
        "4OQECSdoeoKaDQ0r0hu0N4BV2JDpm9juA2I3cmITS/H/Xp5jWxK8D2mkGY0AatWqVaJom8qHAXeeFI6I0WGgg+hTRd0TMANhPD2j"
        "q9IBeQG35oQLAOewTASEDcaX0Kd7k/Iq7w/QAXtzwp2NcEQMeI+Eb6blVV44TPgBoA2LJZB0wCeFAvICbjFhBHdULKA50Le95zwX"
        "Ez6alHtnPFsRzrLeE+NSAZMbn2aooLoD5pTHrcIAMeBlvTtgfKoqmCJkNMQ4Y30jPcdlOOaAKH28yrCC4zI10p20h673no0Wivsm"
        "IyNdhGSRCYUjMLzEpsX+UYgFrqc+hdhHQF7kt40Y6Im1X5kdGZ6Q6g540mScmvpITgyxw4A7sd/WMyjek7JSB/sAyAu49RQ5hXlW"
        "322pI3XhOqB55Ldt4pq047ruAtJikM2GVZZ6cRvO9iAv9SnjgRinTcb723Mi+S7PcrXhyCzmpT1UwMlPn/6tvqc+jXXAMpuNqgqo"
        "lB7EGa7JcziJGhnuimk4ws4hxtttBhTWRlUBkayQU/Q54BXD/ZhhzLPacMVIh2kPF4RRd8jHyTpHD/krE4I8bbhipENguS30Wi0w"
        "JvPItUBqnO9lG272oKk4nozvm9YnxlVVnNfSVtJN4IQYf/LWkzqNCu25lebNhz7dHwbcWzAmTPhgCkcBvXUevdiwR4XjpSsThU/a"
        "BPyW0AkDs+YC10VFBUr15m98mjUk9mMw3GRYSdl1UcpkZ5cZY2b04w2FtlzlPrZxhUUFSo8HhdFfMvGtlp8XAbPIGIcmNqc74IDN"
        "hp9EBe70kPvTM7q0IqI4jQBsbHj1gCXoZrQbmkh6lB6w9C63Y9J6AziJNq1nDSC9BZwipKyGw/glO7sSOpGr3O8NIG0Apwl4yawn"
        "V7kvGpKyGU74aq0j90VDUq7AKQuScglOGZCUa3CKhqRchLMCyWZAuiQ4ibZ1WpXLcIrQzgBpC+DsDJC2BM5OAGmL4IhoizRaiP9j"
        "yWGWVIkPV2o86Elyf3gZUnBWO5/mq64a0BrVgNaoBvQWQXuy8zDL2wA6qA+zZKs+zLJG9WGWNaqqzXn7WYzTo3JVy4rfGSDKSIdb"
        "bJcL7T4gJly43otMAYVZ7+IsCmdBGafadgMe4D8nn60T4+HWp1ZhrkYT6G+SUldVmaYZGwMKowjfzrIsStDYpFDubHYdZYqNTfMO"
        "qyji5RFQrxRvfupTGOcdDmEpnEaO5cnW/2CJ45zDZUy6IgdQUo9hRTZnLCeMdv3r1KpVC4n+AvyI2IMBoEvBAAAAAElFTkSuQmCC"
    ),
    "browse_images": (
        "iVBORw0KGgoAAAANSUhEUgAAAEgAAABICAYAAABV7bNHAAAEc0lEQVR4nO2bTW4TMRTH/29AorMinIBwAsKqAxvSEzBIILEjnIDM"
        "CQonmPQEDRtWIMIJSDc0XVFOQLlBugIWzUNvPiQI9tgTkvlI/JMitbHHnv77/PxsPwMOh8PhcLQVMlUIYu7Dw0Mw+gz0AHRW6GQK"
        "4MJjHH2O6BzbIFA/5s4PwjGAcM19TnzGi2lEc7RVoAcx964IHwB0N9Tvhc+41waRSGM5XzYoTs65zzhoukje8hfZsNq0OELvB/AK"
        "bbIgcchM+FTpCzAOZhGJE28k15d+7ytrMS4JGO4Bk7JDQvzZAhgy4bmyacJxP+bG+iMrgTxgcBrRZJUOsml9sB9zB4RHiirdn4QY"
        "wAs03Qcx4a6q0umK4vyJDwzEElVlDAyCER+LtaHJPmh/xKyqdDYkY0BpQx0+TsGcgHMQpljgxOT//pnFNslMXoZxhHrpMNBnxiv5"
        "Z+2P+IOENo0QSPBlamd8RXMIJe7TDe/KBZpGJCYe6vxRTXRl5aCypMoFEmYRXfgSjDI+ojl0syC5foFySzqLKLzGuEeMN8Q4Qf2E"
        "ye5FQRxUOZ+zOKnKPoOYBwyMQLipKBaBprVbUJ3MIhrLykBT/JcF7aRAgiyboGA5WN5Zgab6td9fM9nOCmSLE8iAE8iAE8iAE8iA"
        "E8iAE8iAE8iAE8iAE8iAE8iAE8jAbgvEih3Npe92WiCSPaE/DxAYX5f3iWrfUVyVIOYue3hEjJDTLYr8VOKcgDkTJrTAR9n/1rWR"
        "lfXybVbVGVmlB4frEgaEQzmNtalPwBiM10VCFdGqIXY/5pAJX2zFEaSuPCPPbrVAQcyDRZr1VjpHUp6RZ6WNrRQokFMIxZlVWaSN"
        "spbktcIZU5IesxYWhOPEj22LQEjT9LTDKjt0PPAZt+QjP8t3Be1JW9apf42exYLUer4VZL2FuvSVJNVGjnbUh4Mi7B2bma3RFkQF"
        "OdpF4ghSZnre5h0aLRBr/ggZQjaJn4lImuGma7tdAhFua4rGJZpR1mXN0GuVQNDka+9JCp0lBXV72yBQ7TRaIGJ8V33/0/K/X1jX"
        "Mg2w0QIB0E3DZZYMyrqy4i8vULPyBkH6FJXny5lgKvaP+Jkuw1/XdqFASf6wgvsrroT/lyTQ05Wli89+kThgvF2l7aLLLJI7fKio"
        "Nwdh4C9wUvWdiiDmsc4K8v0e8jC+cZX6lF/XcJcXkBQ77TDM4ijb/aTaM+HnEqv4jNcq8ZPlRpoZbxW32OAR3p2+pKdWdRWZ8FWn"
        "5nYADFUpuNk7XchlmnV2uGA8kbshK81iculEN71uGK2fk8s0xOu9DZRfoCktkJi5B4Q1iVSYmeoxHq9zprURydPlLu9JgMUVDjeL"
        "vhJLAnqG/Z5/94uA9/puk7tsI+3zpg6CdCpNPsm9+TU6y+wNxSLGcsmlzAwpzlu2LGRVnhz75Om76dmW3AeZyFSe7/kYZsP52ZBu"
        "qQoac5xTBVqRGJdnEXXauNRYKxL7qIYnybUEDTtlQTmZzxnk4swiavz1dIfD4XA4UJrfYze3J3IGTX8AAAAASUVORK5CYII="
    ),
    "get_model_info": (
        "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"
        "gDbify55uzpK0e68AAAAAElFTkSuQmCC"
    ),
}


def tool_icon_png_src(tool_name: str) -> str:
    """返回指定工具图标的 72×72 PNG data URI，未登记的工具名抛 KeyError。"""
    return encode_data_uri("image/png", base64.b64decode(_TOOL_PNG_B64[tool_name]))

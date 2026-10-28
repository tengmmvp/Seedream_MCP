"""四个生成工具输入模型的最小合法构造载荷单一事实源。

赋值守护与波次 parity 等测试经此共用同一最小形状，新增必填字段或改最小
形状时单一落点失败，防两套件静默各自维护不同基输入；浏览模型无生成语义
不参与。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from seedream_mcp.tools.schemas import (
    ImageToImageInput,
    MultiImageFusionInput,
    SequentialGenerationInput,
    TextToImageInput,
)

GENERATION_BASE_PAYLOADS: dict[type[BaseModel], dict[str, Any]] = {
    TextToImageInput: {"prompt": "一只猫"},
    ImageToImageInput: {"prompt": "改图", "image": "https://x/a.png"},
    MultiImageFusionInput: {
        "prompt": "融合",
        "image": ["https://x/a.png", "https://x/b.png"],
    },
    SequentialGenerationInput: {"prompt": "四格漫画"},
}

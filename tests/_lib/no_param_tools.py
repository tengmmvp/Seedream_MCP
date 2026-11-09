"""无输入参数的 MCP 工具名单一事实源。

无参工具无输入模型，不参与参数序与字段对账守护，各测试的豁免集合经此模块
共享，新增无参工具漏改任一处以断言失败暴露。
"""

from __future__ import annotations

NO_PARAM_TOOLS = frozenset({"get_model_info"})

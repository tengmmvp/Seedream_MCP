"""跨模块共享的不可配置硬约定。

组序按「基础设施约定 → 数据约束 → 协议面」排列，协议标识按工具注册序。
"""

# 数据目录名：<数据根目录>/.seedream 下并列 images/thumbs/logs，整体删除即清理。
DATA_DIR_NAME = ".seedream"

# 图像总像素上限。
MAX_IMAGE_PIXELS = 36_000_000

# 图层拆分 max_images 未显式指定时的官方服务端默认（含底图的产出总数），
# 供等待预算推导按最坏实际值折算；满额上限由能力表按家族声明。
DEFAULT_LAYER_DECOMPOSITION_MAX_IMAGES = 30

# 图层拆分配置 max_images 的官方值域（含底图），schema 约束、校验器与
# preflight 钳位同源引用；上界另作能力表家族拆分满额上限。
LAYER_DECOMPOSITION_MIN_IMAGES = 2
LAYER_DECOMPOSITION_MAX_IMAGES = 100

# 并行生成上限。
MAX_PARALLEL_REQUEST_COUNT = 10

# 组图 max_images 下界，schema 约束、校验器与 preflight 钳位同源引用。
MAX_IMAGES_MIN = 1

# 组图单请求总图数上限：参考图数量与生成数量之和的满额。
MAX_SEQUENTIAL_TOTAL_IMAGES = 15

# 工具名：协议标识——四个主线生成工具按输入形态递进，
# 图层拆分为特殊场景殿于生成工具之尾，只读工具最后。
TEXT_TO_IMAGE_TOOL_NAME = "text_to_image"
IMAGE_TO_IMAGE_TOOL_NAME = "image_to_image"
MULTI_IMAGE_FUSION_TOOL_NAME = "multi_image_fusion"
SEQUENTIAL_GENERATION_TOOL_NAME = "sequential_generation"
LAYER_DECOMPOSE_TOOL_NAME = "layer_decompose"
BROWSE_IMAGES_TOOL_NAME = "browse_images"
GET_MODEL_INFO_TOOL_NAME = "get_model_info"

# runner 失败码：handler 生产侧与 webapp 状态表登记侧同源引用的协议标识。
BROWSE_FAILED_ERROR_TYPE = "browse_failed"
MODEL_INFO_FAILED_ERROR_TYPE = "model_info_failed"

"""跨模块共享的不可配置硬约定。

组序按「基础设施约定 → 数据约束 → 协议面」排列，协议标识按工具注册序。
"""

# 数据目录名：<数据根目录>/.seedream 下并列 images/thumbs/logs，整体删除即清理。
DATA_DIR_NAME = ".seedream"

# 图像总像素上限：尺寸校验与 PIL 解压炸弹阈值共用。
MAX_IMAGE_PIXELS = 36_000_000

# 图层拆分单请求满额图片数：1 张底图加至多 16 个透明 PNG 图层，
# 上游 API 的固定拆分形态；生成端点 preflight 折算下载尾巴的总下载数。
MAX_LAYER_DECOMPOSITION_IMAGES = 17

# 并行生成上限：request_count 与 parallelism 共用此上界。
MAX_PARALLEL_REQUEST_COUNT = 10

# 组图单请求总图数上限：参考图数量与生成数量之和的满额。
MAX_SEQUENTIAL_TOTAL_IMAGES = 15

# 工具名：注册与调用侧共用的协议标识。
TEXT_TO_IMAGE_TOOL_NAME = "text_to_image"
IMAGE_TO_IMAGE_TOOL_NAME = "image_to_image"
MULTI_IMAGE_FUSION_TOOL_NAME = "multi_image_fusion"
SEQUENTIAL_GENERATION_TOOL_NAME = "sequential_generation"
BROWSE_IMAGES_TOOL_NAME = "browse_images"
GET_MODEL_INFO_TOOL_NAME = "get_model_info"

# runner 失败码：handler 生产侧与 webapp 状态表登记侧同源引用的协议标识。
BROWSE_FAILED_ERROR_TYPE = "browse_failed"
MODEL_INFO_FAILED_ERROR_TYPE = "model_info_failed"

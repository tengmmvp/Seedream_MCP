"""核心基础模块：异常类型与归约、图像格式常量、日志、参数校验、共享在途任务设施。

供 images/model/io 三组与上层 client/tools/server 共享。errors/formats/logs/inflight
不依赖兄弟组模块；validators 依赖 model 组的 model_capabilities 做数据驱动校验，
model 为纯叶子模块，不依赖任何兄弟组。
"""

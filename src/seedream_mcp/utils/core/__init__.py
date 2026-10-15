"""核心基础模块：异常类型与归约、脱敏净化、图像格式常量、HTTP 整数头严格解析、共享在途任务设施、事件循环键控信号量、取消安全的落定原语、长时 CPU 卸载线程池、日志、有界存放原语、容量上限节流注册表、参数校验。

供 images/model/io 三组与上层 config/client/tools/server/webapp 共享。除 validators
依赖 model 组的 capabilities 做数据驱动校验外，各模块均不依赖兄弟组；
model 为纯叶子模块。
"""

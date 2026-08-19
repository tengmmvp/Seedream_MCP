"""核心基础模块：异常类型与归约、图像格式常量、日志、参数校验。

供 images/model/io 三组与上层 client/tools/server 共享，文件名采用复数名词风格。
errors/formats/logs 仅引用组内模块，无兄弟组依赖；validators 依赖 model 组的
model_capabilities 做数据驱动校验，model 为纯叶子模块，不依赖任何兄弟组。
"""

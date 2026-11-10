# Changelog

基于 Conventional Commits 由 git-cliff 自动生成，随每次发版前插版本段落。

## [5.0.0-alpha.2](https://github.com/tengmmvp/Seedream_MCP/compare/v5.0.0-alpha.1...v5.0.0-alpha.2) - 2026-11-10
### 修复

- git-cliff 钉版改 v2.14.2（上游 tag 加 v 前缀，裸版本号下载 404） ([adee54c](https://github.com/tengmmvp/Seedream_MCP/commit/adee54cf88d2b9dd812ad42795f19480facc1e35))
- 去除 CHANGELOG 尾部重复头（初始头与 cliff 模板头失配致前插追加整文件） ([c46935b](https://github.com/tengmmvp/Seedream_MCP/commit/c46935b03bc0cae73596ea5c92368d5990d402b3))
- Release 正文注入前剥除 cliff 版本标题，消除与 Release 标题重复及 h2 嵌 h3 层级倒挂 ([5a7f3c8](https://github.com/tengmmvp/Seedream_MCP/commit/5a7f3c83e204ce2e06f2fb597e422afa88771a66))

### 内部重构

- Release 正文直接镜像 cliff 版本段落，版本标题带 compare 链接 ([138adf8](https://github.com/tengmmvp/Seedream_MCP/commit/138adf855e57aa1a1c94a95f75c9375e6dc19b22))

### 文档

- 三语 README 底部新增 Discussions 讨论区横幅入口 ([acc8a3b](https://github.com/tengmmvp/Seedream_MCP/commit/acc8a3bd6fe287c826d7d56cd5e3401bca452bda))

### 测试

- 链接集合契约豁免各语 Discussions 横幅引用 ([5dbf1c1](https://github.com/tengmmvp/Seedream_MCP/commit/5dbf1c1d30c711d9f4d082989ee6d0b8ee02b066))

## [5.0.0-alpha.1] - 2026-11-09
### 新增与改进

- 新增 settle 取消安全落定原语 ([9a766b7](https://github.com/tengmmvp/Seedream_MCP/commit/9a766b743c46505cb90b0b99c7c9154c6040b08a))
- 核心基础设施强化 ([7ae7d5b](https://github.com/tengmmvp/Seedream_MCP/commit/7ae7d5b0da329b0ca89076399e74b2c09ee0d00b))
- 参考图预处理与校验强化 ([90a1d1e](https://github.com/tengmmvp/Seedream_MCP/commit/90a1d1e874b220eeb691e0cc4e79eba23c9cd291))
- 下载断点续传与终验解码回退 ([ebe0cec](https://github.com/tengmmvp/Seedream_MCP/commit/ebe0cecd442a49e5103f4bf86904d78356687876))
- 自动保存与存储治理 ([344c2c7](https://github.com/tengmmvp/Seedream_MCP/commit/344c2c7b8530ee86d4003df29204962862b02fe4))
- 目录体系与扫描缓存按读范围隔离 ([2820c65](https://github.com/tengmmvp/Seedream_MCP/commit/2820c650cef0713003f025eb3425278c6cea0a0a))
- 输出通道开关与结构化输出收敛 ([14658a0](https://github.com/tengmmvp/Seedream_MCP/commit/14658a01446fa19291bfc268fd47ccc3b88db487))
- streamable-http 传输层强化 ([46da0e4](https://github.com/tengmmvp/Seedream_MCP/commit/46da0e4db28656e9f6744c930b4b8ad45da07981))
- Web 控制台按域拆分 ([817ced1](https://github.com/tengmmvp/Seedream_MCP/commit/817ced11988c3b57c48d467e617243219d6e3801))
- 注册守护与共享资源生命周期 ([7f0e147](https://github.com/tengmmvp/Seedream_MCP/commit/7f0e147f94c264af2993870c1b43ebeb874c7df2))
- changelog 自动派生——Release 更新内容与 CHANGELOG.md 双产物 ([2edbb85](https://github.com/tengmmvp/Seedream_MCP/commit/2edbb85f12dcdfea17d0938f01a025e97f01b1eb))
- 新增 seedream_cli 占位包 ([b90b662](https://github.com/tengmmvp/Seedream_MCP/commit/b90b6622d577f9b0bf7b586b0e93e4fce68a4838))
- 新增定义域包，上收跨域默认值与硬约定常量 ([6dfc420](https://github.com/tengmmvp/Seedream_MCP/commit/6dfc4206c6fc77b27cb5c6eea9149b1a8e77ca73))
- 归约档案补 http_status 并公开遍历与上游码/消息提取 ([7eede14](https://github.com/tengmmvp/Seedream_MCP/commit/7eede1401a7ef52c8c90686ace06e762ffc4e023))
- 停滞谱系跨调用累计与永久停滞冻结窗回放 ([d641ce9](https://github.com/tengmmvp/Seedream_MCP/commit/d641ce956fd94ccdf28a3ea015a7ff8c3135125e))
- browse 未扫尽页下发续扫导引，徒劳截断与永久停滞撤回重试指引 ([e6b6d82](https://github.com/tengmmvp/Seedream_MCP/commit/e6b6d82590b1ff81eecef11ec370f219ed4f7997))
- 生成等待预算 preflight 单源推导与波次 parity 守护 ([ea164bf](https://github.com/tengmmvp/Seedream_MCP/commit/ea164bf0d66779a18ab6564d4bf1413b23763968))

### 修复

- 413 直发不再被看门狗截断并补装配失配守卫 ([675e7a1](https://github.com/tengmmvp/Seedream_MCP/commit/675e7a1af2206f5b0cdbf926a4fde527824f2fbe))
- 写路径 fd 自含化、HTTP 整数严格解析与异常梯子收敛 ([32ace0e](https://github.com/tengmmvp/Seedream_MCP/commit/32ace0ec3a99f87cb6238af3e23c4daddc45aa02))
- settle 有界档逃逸异常必被取回 ([62a38d9](https://github.com/tengmmvp/Seedream_MCP/commit/62a38d9c19aeac28282c3d071cf00f2f9807272b))
- 清除锁内已知 CVE 并收编 hatchling 1.32.4 ([4d8ae63](https://github.com/tengmmvp/Seedream_MCP/commit/4d8ae633b915c0ee06225816003b9bf755eb8c55))
- 扫描异常时越界告警改落无计数表述 ([34e43e3](https://github.com/tengmmvp/Seedream_MCP/commit/34e43e3cf99962c94e2242c2c48cade82f38b821))
- 读体策略改 scope 键交接并补 CL 解析、408 分码与回环告警 ([c9bfce7](https://github.com/tengmmvp/Seedream_MCP/commit/c9bfce7c88b54599219631ada6644925e8a1cdc7))
- 续传总长核验补全、瞬时持锁六站点统一与写回失败分级 ([9edcb19](https://github.com/tengmmvp/Seedream_MCP/commit/9edcb19405afeb6c18dfc483163d05d405c4d4c0))
- fix(tools): 扫描异常时越界告警改落无计数表述 ([2c87863](https://github.com/tengmmvp/Seedream_MCP/commit/2c8786315e26023d0dcc28e12a30baa81fc1eb8d))
- fix(transport): 读体策略改 scope 键交接并补 CL 解析、408 分码与回环告警 ([23d08fb](https://github.com/tengmmvp/Seedream_MCP/commit/23d08fb0f686912243f988e9eec1d2a77e75fb06))
- fix(io): 续传总长核验补全、瞬时持锁六站点统一与写回失败分级 ([76d5a46](https://github.com/tengmmvp/Seedream_MCP/commit/76d5a46059623cadc8b1bce96679467785306130))
- 终局撕裂尾截齐与截齐取消善后，写协议统一、Range 判定现算 ([15121ae](https://github.com/tengmmvp/Seedream_MCP/commit/15121ae282fe348c27425d9be8b4ee8c9af457de))
- M 类段表按 Unicode 16 增量重生成 ([374e190](https://github.com/tengmmvp/Seedream_MCP/commit/374e190e782e27ef8edb118f63b1dae5e01a26ee))
- code-review 修复——Dockerfile src 布局目标对齐与守护盲区 ([6187071](https://github.com/tengmmvp/Seedream_MCP/commit/61870715545cd6245620e282ba0970286e601ee8))
- 覆盖键归一与 .env 层告警修正，附取值守护测试 ([85688c9](https://github.com/tengmmvp/Seedream_MCP/commit/85688c9e6b686c049d4eafb51f1faf6aa00e6060))
- 前端解析哨兵与超时档口径修正 ([145fa51](https://github.com/tengmmvp/Seedream_MCP/commit/145fa5192f522f251eeb82c8628c3302157fa992))
- observed 置位改按转发异常身份判定，注入异常保留孤儿日志 ([3c17d1c](https://github.com/tengmmvp/Seedream_MCP/commit/3c17d1c40e4b554c5470c52bab859158a1a2d9a6))
- 错误结构经 TOOL_OUTPUT_MODELS 按工具绑定，上游错误码归一 ([648daaa](https://github.com/tengmmvp/Seedream_MCP/commit/648daaa94dc246d372a5055c022630caed39856f))
- 错误与数据净化 userinfo 剥离先于截断，@ 前置守卫免无凭据全文扫描 ([19a3c28](https://github.com/tengmmvp/Seedream_MCP/commit/19a3c28da479a6f8258229bc4c9886a8babbb944))
- hosts 条目误拼符号构建期拒绝，端口谓词单源供条目与 Host 匹配共用 ([1a8f230](https://github.com/tengmmvp/Seedream_MCP/commit/1a8f230a89ef6cf53ccf70974b2bdb80b82a1314))
- 灯箱参考图满档腾挪统一，生成等待兜底经 config-info 按部署下发 ([56f1882](https://github.com/tengmmvp/Seedream_MCP/commit/56f18829c6186d2450a2d35a2402475ccf05ed1f))
- 读路径解析组合单源与扫描核心分层，stat 失败降级恢复 ([473689c](https://github.com/tengmmvp/Seedream_MCP/commit/473689cbf62bafafe5e276fe359eb5a9799d6b51))
- 掩码标记幂等统一与字符类集合派生，错误文本分段常量化与恢复指引尾形收敛 ([94df248](https://github.com/tengmmvp/Seedream_MCP/commit/94df2488cddd790bfea3e99051bd4586809b0edf))
- 扫描轮内枚举 memo 免扩窗重扫，读范围包含下标随存回放 ([97f14b3](https://github.com/tengmmvp/Seedream_MCP/commit/97f14b3db79118900d9fedc2222ce1cacb3ec55a))
- 浏览响应投影白名单，原图 Range 防护与前端取消信号透传 ([be0b0a1](https://github.com/tengmmvp/Seedream_MCP/commit/be0b0a17b8fea57b8c658f4d472b60f79f7c6f64))
- 读体准入取消收尾单点化，Web Origin 守卫前缀钉死 ([1fb52b7](https://github.com/tengmmvp/Seedream_MCP/commit/1fb52b734c91840e25d797825d2f87fa6ff5803c))
- 端口解析严格化与同源判定口径对齐 ([3e62e30](https://github.com/tengmmvp/Seedream_MCP/commit/3e62e303efb2aa30dddb2a9a20ef893cfbcb1de0))
- 上传预算回退保守化并收敛三态单一判定源 ([b47ee46](https://github.com/tengmmvp/Seedream_MCP/commit/b47ee468f9b6cbb97f9041dd4fd67c7b1d4ff20a))
- skill 参考读取按词法门放行符号链接，read 错误码按因果链归位 ([ca09d35](https://github.com/tengmmvp/Seedream_MCP/commit/ca09d3526266abcbc74906f9083c5e5df2eded95))
- file URI 在 url2pathname 折叠前拒绝 UNC 与纯分隔符 ([d2e658f](https://github.com/tengmmvp/Seedream_MCP/commit/d2e658f7a436f2b674d4947bef94dd0aa75aa877))
- 相对 env 路径经 Path.cwd 显式锚定 ([1ba6bff](https://github.com/tengmmvp/Seedream_MCP/commit/1ba6bffae7875fdac7f4963b8bf0c858b08d8909))
- 请求体嵌套深度门前移到序列化前 ([0f51e2a](https://github.com/tengmmvp/Seedream_MCP/commit/0f51e2accd1e13a5ac7c01e1d59727463d87e430))
- read 错误码归位覆盖 2.0.0 资源函数层中转 ([c783412](https://github.com/tengmmvp/Seedream_MCP/commit/c783412e73545ece6fe0ea2882db79006f92fc32))
- 修复审计漏洞的锁升级 ([8fec31c](https://github.com/tengmmvp/Seedream_MCP/commit/8fec31c9f259ca7c6cb52ff26e57d1c9bac9d43a))

### 性能

- base64 padding 计数改尾部探测窗 ([83e8b9c](https://github.com/tengmmvp/Seedream_MCP/commit/83e8b9c297ce40c2618f1692f2093346d644b75b))

### 内部重构

- 扫描 resolve 缓存键内联进循环体 ([a538c19](https://github.com/tengmmvp/Seedream_MCP/commit/a538c19301ff29d19d0068330069aa85cf2eaace))
- refactor(io): 扫描 resolve 缓存键内联进循环体 ([b7c05f0](https://github.com/tengmmvp/Seedream_MCP/commit/b7c05f0f3e32c35ac00799d74d110c62bd08ee9f))
- HTTP 整数头严格解析下沉 core 单源 ([8cecc96](https://github.com/tengmmvp/Seedream_MCP/commit/8cecc96860da9a9a2142fc9e4a74612de93b3ff6))
- 终帧处理合并与读体策略发布内联 ([accf99b](https://github.com/tengmmvp/Seedream_MCP/commit/accf99b9aa8446714254f2df5db50588bec3341e))
- 扫描异常分支去 Optional 哨兵 ([2e780dc](https://github.com/tengmmvp/Seedream_MCP/commit/2e780dc2a0d6b4bb3ca1991faecfe866753cc705))
- 源码迁入 src 布局并前置验收网 ([d85b475](https://github.com/tengmmvp/Seedream_MCP/commit/d85b4756993d938906b339102ac19894dd4438c2))
- 16 模块去前缀改名并拆出 bounded_store ([6f513b0](https://github.com/tengmmvp/Seedream_MCP/commit/6f513b0acb86e4d2fd98036da335fb3f013033d9))
- 包根归位为 client/config/server 三域包 ([fe1a125](https://github.com/tengmmvp/Seedream_MCP/commit/fe1a125ba084f3a271253e9b50c61582441ae18a))
- client/config/server 三域薄门面化与符号级切分 ([cf0ff66](https://github.com/tengmmvp/Seedream_MCP/commit/cf0ff665ef868c58790e5e907846bdd2bc5e63df))
- 按规范重组为 schemas/outputs/pipeline/handlers 四层并落地 DAG 守护 ([9bc4acf](https://github.com/tengmmvp/Seedream_MCP/commit/9bc4acfa3cabb3ac29c41b074ad750d66ba5fed2))
- 镜像六域分组并收拢 _lib 辅助包 ([efa120b](https://github.com/tengmmvp/Seedream_MCP/commit/efa120b60eb647e8cd0a717cb3b16770d94ae364))
- 收口——vendor 隔离、文档清扫与发版声明 ([f9675cc](https://github.com/tengmmvp/Seedream_MCP/commit/f9675cc03bcaa90091b6d397fdcfeb217e88b36c))
- 装配去配置参数、解压回退本地重试与持锁归类守护 ([81278c5](https://github.com/tengmmvp/Seedream_MCP/commit/81278c55cce861a8cb07366a720e7f582f218cd6))
- 节流门限下沉 ThrottleRegistry due/mark ([8626482](https://github.com/tengmmvp/Seedream_MCP/commit/862648252b1b12d97388590c767614e662781486))
- 共享任务取消契约重构——每消费者 future 与瞬态标记 ([14c529a](https://github.com/tengmmvp/Seedream_MCP/commit/14c529ad48c2e3c8b3bea90b2ac51681c2f5fd4b))
- 扫描缓存共享谱系与路径包含折叠口径对齐 pathlib ([cb35cd3](https://github.com/tengmmvp/Seedream_MCP/commit/cb35cd3aadcdfcda0c897232a6b01df62741b060))
- 注入与覆盖告警并入构建器单次供值层判定 ([f809c9c](https://github.com/tengmmvp/Seedream_MCP/commit/f809c9c9cf306e3ab00c828a47f2953eae31fab1))
- 首批消费方切换至定义域常量单一来源 ([ad96ce5](https://github.com/tengmmvp/Seedream_MCP/commit/ad96ce587cfbb7613cf80d2e0f75fcce85cea9cf))
- 限时取块重构为 StreamSession 常驻生产者会话与截止打捞 ([bb689d7](https://github.com/tengmmvp/Seedream_MCP/commit/bb689d783759917ba9d2e274035de91e289e4200))
- API 重试预算可行性下限与单次尝试时限钳制 ([735d7e8](https://github.com/tengmmvp/Seedream_MCP/commit/735d7e8507292b458fa38df5677ddb0fe9216a3e))
- 下载退避与最坏预算共享推导、zlib 头对齐规范、alt 压平共享字符集 ([1768057](https://github.com/tengmmvp/Seedream_MCP/commit/1768057e8f864b7770f3c81ae33c271d286c4690))
- 组图上限现算、参考图字段类型工厂与注册层错误出口统一 ([81be685](https://github.com/tengmmvp/Seedream_MCP/commit/81be68522c383c395cd96fa592f25a40444fa391))
- 端点自有失败归结构化结果并单源化错误码状态映射 ([898758d](https://github.com/tengmmvp/Seedream_MCP/commit/898758d9e5cf0d2be63527799272601e7428b37d))
- 前端请求与超时基建收敛、图库分页终态与生成台取消重构 ([1deb36b](https://github.com/tengmmvp/Seedream_MCP/commit/1deb36b10f8f6b83c5a14b1cb8cc86b894534839))
- 常量定义点收口，结构守护与签名默认对齐收尾 ([e3d9d0c](https://github.com/tengmmvp/Seedream_MCP/commit/e3d9d0c32e4aeef4101b81f85cdaaf5155b7c10e))
- 失败载荷与 usage 净化单源收敛，客户端批次预校验单一入口 ([4ed72fa](https://github.com/tengmmvp/Seedream_MCP/commit/4ed72fae21aefcf453b1a00bc9fcedd6d9c33432))
- 错误文本引擎与净化原语结构化迁移 ([f2e998b](https://github.com/tengmmvp/Seedream_MCP/commit/f2e998bbd7405ce323056d027f77823bb501183e))
- io 层下载/保存/存储错误消息结构化与模板单源 ([9eeb04a](https://github.com/tengmmvp/Seedream_MCP/commit/9eeb04a7d9e9e6e1147c34cd510b8d4f6d2169fc))
- 图像校验与输入预处理消息结构化 ([085d497](https://github.com/tengmmvp/Seedream_MCP/commit/085d497f1c8823143c872e374dd8fb87732ff5cf))
- 错误构造工厂化与环境变量提示严格化 ([fca7a24](https://github.com/tengmmvp/Seedream_MCP/commit/fca7a2428620598699fd1801440878a482ec0dd4))
- API 错误归约与可编码性检查共享化 ([fb38ab5](https://github.com/tengmmvp/Seedream_MCP/commit/fb38ab5825c40b44457ba40b622620caee2c52d1))
- 生成管线失败文案结构化与用户文案惰性渲染 ([074b1fb](https://github.com/tengmmvp/Seedream_MCP/commit/074b1fb728b05258547858e91d2b915d9cba798d))
- 打开失败响应共享构造器与前端占位收敛 ([1eca1ef](https://github.com/tengmmvp/Seedream_MCP/commit/1eca1ef4b0cba29f921077beedca310f285551bf))
- JSON 信封原语上收 utils/core/json_envelope ([d299acb](https://github.com/tengmmvp/Seedream_MCP/commit/d299acb0080f0ff081490bca120893878c92595c))
- 保存目录 memo 收敛单集与链接判定统一 ([edc167d](https://github.com/tengmmvp/Seedream_MCP/commit/edc167d4c790ee85cb32ea616411be30f367794d))
- 错误体解析经 load_json_bytes 阈值分派 ([fb2b0f2](https://github.com/tengmmvp/Seedream_MCP/commit/fb2b0f28f28ea122312c1d8f070f950d34533444))
- 下载解压多成员 gzip 接力与 eof 残留死分支清理 ([14da9ac](https://github.com/tengmmvp/Seedream_MCP/commit/14da9ac253c46a58f583c12a700b777f5a907ad4))
- 参考图 data: 前缀统一归 data_uri 分类 ([3221138](https://github.com/tengmmvp/Seedream_MCP/commit/322113882b1eb7d464dc619f9b528c0c3c83011f))
- 相对路径约束文案收编单一常量 ([b7cf81b](https://github.com/tengmmvp/Seedream_MCP/commit/b7cf81b8905f9b7ca71b5ac62c0e1eab962ddbf6))
- 全仓注释与 docstring 极简化收口 ([21cc1ae](https://github.com/tengmmvp/Seedream_MCP/commit/21cc1aebbbe3c11027a690ded2d3117233b42738))
- SSE 结果七键原样透传 ([97a473e](https://github.com/tengmmvp/Seedream_MCP/commit/97a473e6c1094d703e40da95a4f800c465a4936e))
- 准入放弃收敛 _abandon 单点与绑定白名单派生内联 ([0c23816](https://github.com/tengmmvp/Seedream_MCP/commit/0c23816a0eb7ad1b2b6a2c3ca63f1e0844369e22))
- 下载续传不变量收束进 _ResumeState ([b92c367](https://github.com/tengmmvp/Seedream_MCP/commit/b92c3675d6f101fe4615903d7275cb2c83f726b6))
- 缩略图不可用错误码并入共享 service_unavailable ([1560545](https://github.com/tengmmvp/Seedream_MCP/commit/1560545b7dd8a2249e1f3f8decbc3c70260cfc87))
- 编码异常分流与 URI 脱敏原语 ([430c55c](https://github.com/tengmmvp/Seedream_MCP/commit/430c55c6e50ff7511cc23138a4b8bd6085d7387c))
- 位置求值收口 resolve 契约与根声明拒绝 ([7b8f901](https://github.com/tengmmvp/Seedream_MCP/commit/7b8f901276c972b0c29ed9782c370b34918dbdb8))
- 自动保存构造门与清理策略收敛 ([926b585](https://github.com/tengmmvp/Seedream_MCP/commit/926b585f1e74909f9eb843c91b4553bc600a58af))
- 浏览界 EntryBound 收敛与计数字段别名单源 ([da3bc34](https://github.com/tengmmvp/Seedream_MCP/commit/da3bc344cfb83626c2c6563130be8c6127be2b27))
- 序列化异常按实测归因 ([7876466](https://github.com/tengmmvp/Seedream_MCP/commit/78764667710d2f4ada4d40cffb3a81a4d7c0b8f9))
- Host 拒绝应答单点化与平铺侧别名订阅 ([61ff29b](https://github.com/tengmmvp/Seedream_MCP/commit/61ff29bc6815e6938dd7cc68e3a44c6ca87e37c1))
- 生成等待预算封顶与浏览错误码收敛 ([8e21158](https://github.com/tengmmvp/Seedream_MCP/commit/8e211586d32fae21d45904c90551adecdbbfcec2))

### 文档

- 用户文档与部署示例三语同步 ([ea6a41d](https://github.com/tengmmvp/Seedream_MCP/commit/ea6a41d495507b4e336f8e648523bd19d852ee96))
- 新增仓库组织规范与重构执行方案 ([32f8beb](https://github.com/tengmmvp/Seedream_MCP/commit/32f8bebcfc56a680a1fda7de54aae5a18d28d710))
- 规范落盘 repo-layout.md ([75d527e](https://github.com/tengmmvp/Seedream_MCP/commit/75d527e83e41ea2288b8f5901c7942f1be9d2489))
- 精简 RELEASING.md 为纯发版操作指南 ([7f7b343](https://github.com/tengmmvp/Seedream_MCP/commit/7f7b343d84787dfd1fa1e140494d164c27c6b001))
- volcengine 官方文档快照移入 vendor 隔离区 ([e523d51](https://github.com/tengmmvp/Seedream_MCP/commit/e523d510263afbc019edc4502dafe9e34d58a70d))
- 全项目注释三维度修订（极简/准确/统一） ([9486db3](https://github.com/tengmmvp/Seedream_MCP/commit/9486db32d25a75bb7de36833b2459858949b4455))
- 同步 mcp-python-sdk v2.3.0 文档快照 ([be430b9](https://github.com/tengmmvp/Seedream_MCP/commit/be430b93c5c0af0bca806593b94ea52bcb7168b6))
- 新增文档写作规范并按其重构仓库布局与同步点文档 ([2d087b4](https://github.com/tengmmvp/Seedream_MCP/commit/2d087b4b823b4db3bad48914bf25e845c00f5a74))
- 同步 volcengine 官方文档快照 ([5ddf557](https://github.com/tengmmvp/Seedream_MCP/commit/5ddf557ae16b0bdd22e67103d84729e2f53c23ce))

### 测试

- 收敛共享测试工厂——包含差分清单、inflight 轮询与回收助手、假钟及计数间谍 ([77ed8c5](https://github.com/tengmmvp/Seedream_MCP/commit/77ed8c5360f9ca2ba7b172444e7d01611099b689))
- 测试域同步迁移与全仓守护测试收敛 ([1dad5c5](https://github.com/tengmmvp/Seedream_MCP/commit/1dad5c57f275f934555147441c6c1d214a35074a))
- 扩充共享测试基建 ([6a20652](https://github.com/tengmmvp/Seedream_MCP/commit/6a20652891d6fe4e7bd4fcc1c636bfe7d7661eb1))
- memo 换靶回落用例补 readlink 假件 ([c0eed90](https://github.com/tengmmvp/Seedream_MCP/commit/c0eed908b218331e4486c98a64904ae4de8df212))

### 构建与流程

- 工程配置与质量门禁同步 ([38a9724](https://github.com/tengmmvp/Seedream_MCP/commit/38a97245cd65dba7c4dacd65d6b5933b16c07783))
- mcp SDK 锁定升级 2.3.0 ([f318782](https://github.com/tengmmvp/Seedream_MCP/commit/f31878247b6aaa1978d38be7e9c333936f350a1e))
- 日志路径行序内聚与覆盖脚本流探测防御 ([35e4cf5](https://github.com/tengmmvp/Seedream_MCP/commit/35e4cf5242004eda34469c53a8d9dc77441c6587))
- hatchling 1.33.0 与 Pillow 上限放宽至 <14，同步 uv.lock ([ad1322f](https://github.com/tengmmvp/Seedream_MCP/commit/ad1322fd52bc25c7efb2ad0f883c1833a9c9b099))

### 其他变更

- bump astral-sh/setup-uv from 10.2.0 to 10.3.3 (#36) ([5986ac6](https://github.com/tengmmvp/Seedream_MCP/commit/5986ac681b9c64971aa1ab756cfd59b63d319aa1))
- bump actions/setup-python from 7.0.0 to 7.1.0 (#35) ([66cf0a6](https://github.com/tengmmvp/Seedream_MCP/commit/66cf0a668fd760e2318df7ee67cdc3b909881760))
- bump docker/login-action from 4.6.0 to 4.7.0 (#34) ([43854d4](https://github.com/tengmmvp/Seedream_MCP/commit/43854d4eb94218be64b4ed7c3ce1f5f296456fdd))
- bump pypa/gh-action-pypi-publish from 1.14.2 to 1.14.3 (#32) ([cff61db](https://github.com/tengmmvp/Seedream_MCP/commit/cff61db3b9ff450a91a4fd745373a656a2f27aa8))

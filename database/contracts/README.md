# 数据库接口 SDK

`index.d.ts`及schemas由本模块Python合同生成；不手写或从前端反推科学字段。消费者安装本目录的types-only包，通过HTTP调用模块接口，不导入数据库/Console/Plat的源码。

在项目根运行 `ExtractESG/.local/python/bin/python database/scripts/export_contracts.py --write`更新；不带write执行一致性检查。生成器是开发工具，不属于服务运行时依赖。发布观测、来源选择、发布预览/版本与分页使用分别版本化的合同；不要求旧平台字典或提取产物目录。

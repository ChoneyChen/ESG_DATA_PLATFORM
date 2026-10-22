# P6结构迁移：唯一源码、版本和历史路径

> 2026-10-08历史回滚复审：144个兼容别名、1400个canonical目标及407份历史运行实物有效；21份已登记原件全部匹配。49份当时未单独备份、后来已修改的源文件中，12份已从既有Git找到精确原件并保存为独立补充，37份尚未找到。因此下文迁移当日的快照校验结论不能作为当前完整回滚保证。原两份收据及其before/after摘要均未修改，严格校验器仍失败；见 [精确恢复索引](migrations/2026-10-08-p6-original-recovery-index.json)。源码演进、目录有效与历史回滚完备分别评估。


本记录对应 `更新要求.md` 第8条。2026-10-05已完成两批物理迁移，使用真实移动、相对符号链接和薄构建入口，未复制两套可编辑源；重启及真实业务验收由项目统一执行。产品/工作台页面建设及实际报告质量以相应验收记录为准，不能由本次目录验收推定。

## 当前修改入口

| 业务职责 | 当前唯一源码 | 保留的兼容入口 |
|---|---|---|
| 队列、调度、事件、恢复、处理/提取协调 | `ExtractESG/control_plane/src/esg_v2/control_plane/` | `document_ir_extract/backend/src/esg_v2/control_plane` |
| PDF资产、OCR、DocumentIR、证据修订、模型适配与存储生命周期 | `ExtractESG/document_processing/src/esg_v2/` | 旧 `esg_v2` 对应业务子目录和文件 |
| 报告事实、检索、接地、定向范围、标准映射、Session、结果与存储 | `ExtractESG/extraction/src/esg_targeted/` | 旧 `esg_targeted` 对应业务子目录和文件 |
| 报告处理/提取共享类型合同 | `ExtractESG/contracts/src/{esg_v2,esg_targeted}/contracts.py` | 旧两个包的同名文件 |
| 标准原表、作者源、范围、编译与显式发布版本 | `ExtractESG/standards/` | `ExtractESG/standard_packages`及其旧子路径 |
| PostgreSQL合同、候选导入、审核、发布和单位规则 | `database/` | `ExtractESG/esg_database`的源码子路径 |
| 共同生命周期、进度、源码与运行根定位 | `ExtractESG/shared_runtime/src/esg_runtime/` | 原有共享运行库路径 |
| 项目事实与本次迁移说明 | 根 `README.md`、`docs/` | 模块内历史规划/审计材料保留 |

API组装文件及包 `__init__.py` 暂留旧兼容宿主。`document_ir_extract/backend/src/esg_v2/api/`、`targeted_table_extract/backend/src/esg_targeted/api/`是真实HTTP入口；它们import的是上表实体，并无第二份业务实现。保留原 `esg_v2`、`esg_targeted` import名与运行脚本，避免对历史任务和子进程产生无意义迁移。模块职责并未拆成多队列或额外重负载进程。

新开发修改上述canonical路径；旧文件系统入口解析到同一源码。`Path(__file__).resolve().parents[n]`的易漂移定位已替换为 `esg_runtime.layout`项目标记解析。运行存储与源码根分别定义，主机或用户显式环境配置仍优先。

## 标准系统

[标准系统手册](../ExtractESG/standards/README.md)是当前模块入口。41活动作者源与历史作者快照实际分开；完整原表、Core/包合同、profiles、活动registry、95发布身份及未来占位各有位置。标准稳定ID、原序号、包格式版本、内容版本、Core版本、平台字典版本不合并。

保持一披露小号一包，E1–E3共25包，ESRS 2共12包，MDR共4包，总512条。全目录1,184行、1,126独立原编号、58共用MDR引用仍完整；58后续披露包不可执行。主要条款49原编号完整保留，E1–E3明确16项就绪，33项待建设。核心属于包，主要属于平台profile；核心不会自动扩充平台输出，所选输出和必要上下文/操作数分别计划。

唯一正式构建 `python -m esg_standard_packages build` 仅读当前登记作者源，重新编译及校验既有内容；`--publish`显式创建新版本并登记。原三套生成脚本仅转入同一构建器，历史语义常量和原脚本在 `history/builders/`原样保留。发布版本不可覆盖或改变同身份路径/源摘要；历史通过显式index固定版本读取。未登记的新目录，即使数字更大，也不会自动进入活动目录。

结构可编译、真实提取验收、业务批准分别表达；当前活动包仍是draft。迁移不提升成熟状态，不改变所选原条款，也不将占位伪装为成果。

## 运行与历史保护

未移动、删除或重写运行资产。以下默认位置保持：

- 工作区 `../pdf/`：PDF来源。
- `ExtractESG/document_ir_extract/{ocr_output,document_ir_output,.local}/`：OCR、IR、任务与上传。
- `ExtractESG/targeted_table_extract/{targeted_extract_output,.local}/`：Session、候选、Bundle与索引。
- 工作区 `../.local/platform-runtime/`：唯一队列和执行锁。
- `ExtractESG/esg_database/.local/pglite`：旧CLI默认本地库；实际PlatESG活动存储仍由配置确定。

数据库源码批次移动SQL/scripts/tests/examples及包文件，运行库不动。随后依赖单独迁入canonical `database/node_modules`，旧依赖位置改兼容链接；依赖包和安装元数据留有独立摘要/原件。固定安装pg 8.23.1，canonical冻结lock安装通过，未连接真实PostgreSQL。真实Node入口检测使用realpath，旧符号链接CLI仍执行相同main，测试显式使用隔离临时库，未打开历史默认库。

中国石油原任务 `tx-20261002T182823Z-f5b3169b0c` 的固定E1-6包身份仍可解析，原IR `ir-20260918T181255Z-005c90d28dab`、来源、候选、检查点和中断状态保持历史含义。新增读写链路仍须独立业务验收，不将旧候选当作已批准观测。

## 两批清单及回滚依据

1. [标准和数据库迁移清单](migrations/2026-10-05-p6-standards-database.json)：1,205源文件摘要，110兼容别名，必要入口/路径及依赖合同改写具有逐字节原件，数量以清单verification为准。
2. [Python职责迁移清单](migrations/2026-10-05-p6-python-domains.json)：195源文件、34次实体移动和兼容别名，6处定位改写具有逐字节原件；记录407份历史清单、中国石油实物及PDF摘要。
3. `docs/migrations/2026-10-05-p6-originals/`保留改写原件；原作者快照和原生成器保存在标准history中。迁移前registry另存 `standards/history/registry/pre-p6-registry.json`。
4. [数据库依赖迁移清单](migrations/2026-10-05-p6-database-dependencies.json)：28个包入口摘要、3份安装元数据原件、canonical安装及驱动解析核查；不是运行数据库迁移。

407份历史摘要是在源码迁移后、协调重启前做的只读冻结；它们证明本批后续验证的完整性。迁移前独立历史保护基线仍以P0–P3实物审计为准，不能把迁移后冻结描述为迁移前快照。

从项目根只读核对：

```bash
python scripts/verify_structure_migration.py
```

工具检查每份before/after摘要、原件、兼容链接及历史资产。`--finalize`仅写迁移清单，不移动文件、不打开数据库；正常使用只读模式。代码继续开发后after摘要可能漂移，须明确审阅新差异，不能忽略错误或重新覆盖原before摘要。

回滚先停止业务提交并协调服务退出，再检查清单。只有当前源与after摘要一致、历史数据未动、改写原件完整时，才可按 `moves`反向顺序解除本批兼容链接并将实体移回，使用精确before-image恢复必要定位改写；本批新建文件按明确所有权清单处理。每一步核对目标路径冲突及原摘要。重新确认API/CLI解析和联合指纹后统一重启。未在活动项目上实际回滚，以免覆盖继续进行的开发；本次“回滚可行”指清单、路径及原字节均可核验。

## 后续发布的源码保全

新增 [源码保全工具](../scripts/preserve_release_sources.py)。显式 `capture` 将当前联合发布的可执行源码原字节保存到仓库外内容寻址目录，清单固定共同构建指纹、成员范围、父发布清单及迁移／检查收据摘要；已有清单不可覆盖。默认 `verify` 只读核对保存的字节与父链，不恢复源码，不连接数据库。

```bash
python scripts/preserve_release_sources.py capture --root /绝对路径/ESG_DATA_PLATFORM --output /仓库外/源码保全目录 --parent-manifest /上一发布清单.json
python scripts/preserve_release_sources.py --manifest /仓库外/源码保全目录/releases/release-构建指纹.json
```

旧945e清单作为父发布时须同时指定 `--parent-legacy-base`，其兼容校验只证明登记原字节完整，不能复算未登记的旧组件指纹。此工具不补造10月5日尚缺的37份原件，不修改旧迁移收据或消除旧严格校验失败。当前发布的保存凭证见 后续阶段记录（已归档到 Git 历史）。

## 迁移当日验证与边界

已完成标准+目录+报告处理/原生分派恢复80项测试，控制层/计划/Session72项测试；新的结构验收另覆盖显式全版本解析、历史中国石油固定身份、不可变发布、未登记目录、摘要损坏和源码/数据根分离。数据库原21项中20通过、1既有外部fixture缺失跳过，新增layout/旧CLI测试采用隔离库。最终联合测试数量及真实现场验收由统一实施记录维护，不重复累加已有用例。

没有移走旧API组装实体，没有迁移运行数据，历史研究报告继续保留其日期和证据。未清理历史需求、PDF、IR、Bundle或未确认所有权的文件。生产部署、多人权限及真实报告提取精度仍须相应验收。本次目录结构不代表已完成这些工作。

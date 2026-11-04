# 六项链路骨架更新

按[本轮规划第10节](../../audits/2026-11-01/README.md)的顺序实施。条款内容扩充、增加PDF与额外审核体系不属本轮。

当前部署为`clause-backbone-1.8`，指纹`b19403ea1242d61d17bbeb7b8b0d0decb91d3c955fef2f2603a8b124359c11cf`。2026-11-04三组真实任务均已完成接收与投影，逐项实现与结果如下。

## 1. 单条属性收敛

1113个值条款的`attributes`集中声明含义、读取说明、值词典、补读词和允许的上下文层级；13个结构描述不参与值提取。搜索、阅读、分类和显示引用属性ID，展示组及16/32集合只组织条款ID。阅读卡排除展示配置，单条可独立部署。

编码口径只消费本条字典已确认的代码；未声明字典、未列出或未证实的文字保留原文，不因不能归一而否定目标值。现行条款移除无人消费的`reading.options`、样本验收和未实现计算选项；旧冻结合同保持原摘要及只读解码。

文件：[属性](../../../ClauseESG/src/esg_clauses/attributes.py)、[合同与阅读卡](../../../ClauseESG/src/esg_clauses/contract.py)、[搜索计划](../../../ClauseESG/src/esg_clauses/search.py)、[保存与冻结](../../../ClauseESG/src/esg_clauses/repository.py)。

## 2. 增量保存与运行汇总

`ResultStore`按事实ID仅写新增或变更的原值及映射。`RunStatistics`随这批变更更新种类、期间、目标关系及属性完成度，不再在每帧汇总时扫描全部事实。属性补读也只提交实际改变的事实；进度和本任务读取位置分别保存。

结束时从结果仓储组装一次2.4交接包。组织表由接口按当前页生成，运行中不反复解析整份FactBundle或重建组织表；恢复读取本任务原值与位置，不做跨任务语义复用。补页带回的相同物理量值或陈述保留原事实ID；已确定的内容跳过，未决内容继续读取。完整物理帧按快照保存，模型读取片段单独处理。

文件：[增量仓储](../../../ExtractESG/extraction/src/esg_extraction/pipeline/result_store.py)、[运行统计](../../../ExtractESG/extraction/src/esg_extraction/pipeline/statistics.py)、[主干](../../../ExtractESG/extraction/src/esg_extraction/pipeline/engine.py)、[运行状态](../../../ExtractESG/extraction/src/esg_extraction/pipeline/state.py)。

## 3. 搜索推进与来源快照

先逐页消费当前证据的严格候选，目标未找到时才在预算内放宽对象词组；已有部分目标也继续定位条款计划内尚未准备的页。未命中时才放宽页查询的必含词组。实际候选耗尽或本次预算用完时结束，不以可选属性空缺要求无限补页。

文档定位接口分别返回新页及已准备的相邻结构页，编排一起构建IR；横向续表由文档模块组合行名、单位和业务列。阅读worker让出模型锁，再续跑原生任务。事实及属性证据分别引用实际IR快照，导出携带来源清单。本轮CKH从原PDF第169页的单页IR起步，实际观察目标已有部分命中后的补页与续读。

文件：[候选流](../../../ExtractESG/extraction/src/esg_extraction/pipeline/retrieval.py)、[页面编排](../../../ExtractESG/pipeline/src/esg_pipeline/extraction.py)、[证据接口](../../../ExtractESG/document/src/esg_document/api/evidence.py)、[独立导出](../../../ExtractESG/extraction/src/esg_extraction/pipeline/exporting.py)。

## 4. 上下文补读与绑定

先保存量值或陈述，补读只返回属性和原文引用。每个事实先读自己的行列、完整多行表头、表题、脚注和章节；报告说明另列，适用范围由该事实的引用与条款`context_levels`决定，不借用其他事实的局部来源。

同一属性的多段说明按事实合并值、原文和物理引用，消除最后一条覆盖前文的问题。同一PDF另一快照中与该量值或陈述原文对应的物理帧也能提供上下文。每批只输入相关来源与缺失属性，返回后保存变更及读取位置；真实截断时拆小该批继续。可选属性没有披露或未取得时保持未知，原值、历史年份和执行完成状态不受其阻断。

定性期间只根据所选原文关联；邻段的“报告期内”和标准编号版本年份不能变成测量期间。报告企业和主年份也不能替代事实主体与期间。

文件：[补读](../../../ExtractESG/extraction/src/esg_extraction/pipeline/enrichment.py)、[物理绑定与多段合并](../../../ExtractESG/extraction/src/esg_extraction/pipeline/binding.py)、[期间绑定](../../../ExtractESG/extraction/src/esg_extraction/pipeline/period_binding.py)、[阅读输入](../../../ExtractESG/extraction/src/esg_extraction/pipeline/reading_frame.py)。

## 5. 比较职责归位

提取生产原值、目标关系和有原文依据的属性，比较状态为`not_evaluated`。数据库按冻结条款的comparison要求判断比较与选源，没有额外的固定主体/边界准入条件。未知身份保留物理来源；期间未知或不适合比较也不删除原值。各报告披露的年份分别保留。

选源预览保存一次决定，翻页直接查询所选观测，不重新加载全量原值及重算。独立capture库仅增加0022预览表，没有迁移或重置PlatESG demo库。本轮350条观测选择全部关系后，all_sources/latest_report均保留2023年103条、2024年121条、2025年121条、期间未知5条；连续25＋25行无重叠，尚未发布数据。[当前预览与分页](current-running-pages.json)。

文件：[投影](../../../database/src/esg_database/projection.py)、[选源](../../../database/src/esg_database/selection.py)、[预览查询](../../../database/queries/capture_products.mjs)。

## 6. 按需结果与Console

轻量SQL索引支持条款、期间、关系、单位、文本和单事实筛选；先筛选分页，再读对应事实文件。Console先读概览，进入当前条款或事实页才读该页，默认50行；矩阵表达本页，完整导出仍包含全部原值。

保留父子页面和运行浮窗，详情与浮窗均采用当前计数，补页交接也保留累计事实数。CKH目标页实际连续返回20＋20行，CATL定性页返回2＋1行，均无重叠。多段属性以文字列表呈现并保留证据入口。旧任务按自己的冻结合同和结果读取，实际CKH旧结果222条中取5条、CATL定性旧结果3条已读通。[冻结页](frozen-pages.json)。

文件：[结果接口](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py)、[Console代理](../../../ConsoleESG/backend/src/esg_console/sessions.py)、[分页结果页](../../../ConsoleESG/frontend/src/features/extractions/result-pages.tsx)、[详情](../../../ConsoleESG/frontend/src/features/extractions/detail.tsx)、[浮窗](../../../ConsoleESG/backend/src/esg_console/monitor.py)。

## 真实任务结果

三组均为当前指纹，使用已有PDF和本地模型，拒收均为0，自动发布关闭。

| 任务 | 结果 | 实跑记录 |
|---|---|---|
| CKH横向续表 | 同一执行ID从90条/18个目标推进到222条/45个目标；原90条ID全部保留，72个文件未变，18个仅更新补读信息，无未变内容重写 | [结果与扩围](current-ckh-run.json)、[45个标注值](current-ckh-values.json) |
| CATL定量＋定性 | 54条接收与投影；9个三年份标注值/单位/期间/目标关系对应；三段陈述期间为未知、2025、未知 | [结果与陈述](current-catl-run.json)、[9个标注值](current-catl-values.json) |
| 能源 | 74条接收与投影；2023/2024/2025目标分别为10,524,882.70、19,233,654.46、24,767,519.69兆瓦时；GB/T通则、直接能源、间接能源三段说明保留 | [数值、方法与接收](current-energy-run.json) |

横向续表漏读修复在文档与编排模块：补页同时带入相邻结构页。条款词库没有为这份报告增加特例；提取继续按冻结的独立条款读取，保留完整Ports名称和所有披露年份。搜索只在未找到目标时放宽对象，已命中仍检查计划内剩余页，未决或缺失属性不触发无限扩围。

前端生产构建、真实结果分页和数据库预览已读通。浏览器自动化被应用设置关闭，本轮没有重新做鼠标及视觉检查。标注对照覆盖9个CATL值及45个CKH值，不代表全报告召回已穷尽。

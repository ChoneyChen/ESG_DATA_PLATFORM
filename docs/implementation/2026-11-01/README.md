# 六项链路骨架更新

按[本轮规划第10节](../../audits/2026-11-01/README.md)的顺序实施。本文只记录当前实现和真实任务；条款内容扩充、增加PDF与额外审核体系不属本轮。

当前部署：`clause-backbone-1.7`，执行指纹`5b554dd206b6302c6cf643e7c26dcd865972cde8fff11ce354f8a5b7049dfb6c`。2026-11-03已安装表头组合引用、补读逐批保存和数据库预览分页；实跑又发现陈述期间误关联，现已安装对应修补并开始新一组真实任务。下表区分已完成的前一组实跑与新部署实跑，不以旧结果证明新代码效果。

## 1. 单条属性收敛

- 1113个值条款均以单条`attributes`声明属性含义、读取说明、值词典、补读词及允许的上下文层级。13个结构描述不参与值提取。
- 搜索计划、阅读卡、分类及显示分别引用属性ID，阅读不再从展示列反向取得词典。展示组及16/32集合只组织独立条款ID。
- `context_levels`由保存的条款提供：局部主体、角色、业务和场址不能借用其他章节中的集团名称；允许读取报告说明的属性仍须有适用的原文。
- 现行1113条已清除无人消费的`reading.options`，新建与保存不再产生它。SDK仅为旧冻结任务保留只读解码，旧合同及摘要不改写。
- 已移除acceptance管理、模板成熟度标签、未实现派生/聚合执行选项；不恢复样本配额或发布闸门。

文件：[属性定义](../../../ClauseESG/src/esg_clauses/attributes.py)、[条款合同](../../../ClauseESG/src/esg_clauses/contract.py)、[搜索计划](../../../ClauseESG/src/esg_clauses/search.py)、[保存与冻结](../../../ClauseESG/src/esg_clauses/repository.py)。

## 2. 增量保存

- 原值和条款关系按事实ID写入，仅写新增或变更内容；模型进度使用独立运行记录。
- 每批属性补读返回后立即保存事实及读取位置，页面可读取已取得的属性。成功批次随本任务恢复，不保存跨任务的语义理解。
- 运行中不再重建整份组织表或反复解析FactBundle；结束时组装一次2.4原值包。组织表由结果接口按当前页生成。当前CKH实际包未写组织缓存，222条均已接收并投影，[接收与产物记录](current-ckh-run.json)。

文件：[ResultStore](../../../ExtractESG/extraction/src/esg_extraction/pipeline/result_store.py)、[提取主干](../../../ExtractESG/extraction/src/esg_extraction/pipeline/engine.py)、[运行状态](../../../ExtractESG/extraction/src/esg_extraction/pipeline/state.py)。

## 3. 搜索推进与恢复

- 先按单条合同分页读取现有证据，未找到目标时放宽检索；需要新页时交给编排层准备，阅读worker让出模型执行锁。
- 新页完成后续跑同一原生任务，保留原值、错误和未成功帧的位置。事实及补读证据分别引用实际IR快照，不把新增来源冒充原始IR。
- 实际扩围已经在同一CATL任务保留原63条并增加102条，两份快照共165条全部接收。该次未找到员工人数且有模型超时，不能称为该目标提取成功。[扩围实跑](phase3-expansion.json)。
- 前一组CKH、CATL均已完成补页续跑并交付；当前组继续观察新补读保存方式。

文件：[候选流](../../../ExtractESG/extraction/src/esg_extraction/pipeline/retrieval.py)、[PDF编排](../../../ExtractESG/pipeline/src/esg_pipeline/extraction.py)、[证据接口](../../../ExtractESG/document/src/esg_document/api/evidence.py)、[自包含导出](../../../ExtractESG/extraction/src/esg_extraction/pipeline/exporting.py)。

## 4. 上下文补读

- 量值先保存；补读只返回既有事实的属性和原文引用，不重新生成数值或陈述。
- 先提供该事实自己的行、列、表题、脚注和章节；报告说明另列，不能使用其他事实的局部来源。
- 多行列标题作为组合来源交给模型，绑定时拆回实际物理单元及位置；不因表头文字换行而失去完整业务名称。
- 每批输入只包含本批事实可用的来源与缺失属性。同一句说明确实适用于多项事实时，可一次列出这些事实ID，避免重复输出长引用。
- 实际输出截断会拆小该批次继续阅读；未披露或未取得的属性留空，原值接收、全年份及执行状态不受其阻断。

前一组能源已完成71条接收与投影；2023/2024/2025目标分别为10,524,882.70、19,233,654.46、24,767,519.69兆瓦时。取得GB/T 2589-2020方法和环境统计范围，主体仍未知。[已完成能源证据](completed-energy.json)。前一组CKH为222条、CATL为54条（51定量、3定性），均0拒收；最新CKH已完成222条接收，45个标注原值、单位、期间和目标关系对应；但6次属性补读均为空，尚不能把表头组合引用的效果判为完成。业务名称已有部分保存在统计边界中，主体未知本身不算失败；仍需检查完整多行名称是否妥善保留。[原值对照](current-ckh-values.json)、[补读实录](current-ckh-context.json)。补读返回合同允许省略整个数组并默认成空，当前部署已改为显式返回数组，空数组仍合法，不增加报告属性要求；新部署效果以当前组为准。

CATL上一部署接收54条，9个标注值对应；逐句查看发现供应商陈述借用了邻段的报告期，监测陈述又受标准编号中的年份干扰。[原值](current-catl-values.json)、[陈述及期间实录](current-catl-run.json)。当前修补让定性期间只依据所选陈述自身的引用，标准编号不当作测量年份；无本地报告期引用仍保持未知。原来的错误结果不改写，新任务重新读取。

文件：[属性补读](../../../ExtractESG/extraction/src/esg_extraction/pipeline/enrichment.py)、[阅读输入](../../../ExtractESG/extraction/src/esg_extraction/pipeline/reading_frame.py)、[物理引用绑定](../../../ExtractESG/extraction/src/esg_extraction/pipeline/binding.py)。

## 5. 比较职责归位

- 提取输出原值、目标关系和有依据的属性，比较状态保留为未评估。
- 独立数据库按冻结条款的comparison要求判断比较与选源，不额外写死主体或边界门槛。逐观测适用范围与整条披露完整度分开。
- 未知身份保留物理来源；缺失属性不删除原值，不以报告主年份覆盖2023/2024等历史列。
- 发布预览的选源决定保存一次，翻页直接查询所选观测；不再每翻页加载全量原值及重算选源。为此仅在独立capture库添加`0022_product_previews.sql`的预览记录表，没有迁移或重置PlatESG demo库。

真实276观测分别以all_sources及latest_report预览，2023年90条、2024年93条、2025年91条、未知2条全部保留；各连续读取25＋25条无重叠。没有发布数据。[数据库预览分页](phase6-preview-pages.json)。

文件：[观测投影](../../../database/src/esg_database/projection.py)、[选源](../../../database/src/esg_database/selection.py)、[预览SQL](../../../database/queries/capture_products.mjs)。

## 6. 按需结果与Console

- 轻量SQL索引支持条款、期间、关系、单位、文本和单事实筛选。后端先筛选分页，再读取对应事实文件。
- Console先读概览，再读当前条款或当前事实页；默认50行，矩阵只表达本页。完整导出继续包含全部原值。
- 保留父子页面和运行浮窗。浮窗计数同时使用当前进度，避免被上一次汇总中的旧调用数遮住。
- 旧冻结任务读取自己的保存合同与结果；实际CKH组织页222条中返回5条、CATL定性组织页3条均读通，没有改用现行条款。[冻结结果读取](frozen-pages.json)。

文件：[结果接口](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py)、[Console结果代理](../../../ConsoleESG/backend/src/esg_console/sessions.py)、[分页页面](../../../ConsoleESG/frontend/src/features/extractions/result-pages.tsx)、[浮窗状态](../../../ConsoleESG/backend/src/esg_console/monitor.py)。

## 当前真实任务与验证范围

| 任务 | 会话 | 当前状态 |
|---|---|---|
| 能源方法及范围 | `extract-plan-ce76d2e00482457ab20daaaef4b20fdc` | 执行中 |
| CKH完整多行表头 | `extract-plan-f24573ab64664778bd682c7cae25d485` | 队列中 |
| CATL多年份及陈述期间 | `extract-plan-398cfe0b88044674a7e9a3a2f63b40a5` | 队列中 |

前端生产构建通过；Console代理的结果页、冻结页及数据库预览使用真实数据读通。浏览器自动化被应用设置关闭，未重做鼠标和视觉验证，不用接口结果代替该项。

上一部署未启动的能源任务已取消，改用当前版本实跑；未生成原值，不冒称为一次完成测试。实际任务保留全部输出、模型调用、失败与耗时。没有新增单测套件、覆盖矩阵、条款成熟度或审批系统；本地模型、不切云，自动发布关闭。

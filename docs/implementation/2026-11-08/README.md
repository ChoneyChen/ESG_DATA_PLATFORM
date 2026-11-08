# 报告文件夹与PV批量导入

报告页采用“文件夹 → 报告列表 → 报告详情”，每层有返回入口；批量导入后直接进入新文件夹。单份上传保留为独立报告，报告列表按企业及覆盖年度排序，详情呈现完整期间与原始来源。

## 文件与接口

- `ExtractESG/contracts/src/esg_reports/contract.py`：可选的批次folder及导入返回folder，保持报告身份合同独立。
- `esg_reports/repository.py`：报告资源仓储持有folder及成员关系。同一PDF可以属于多个文件夹；成员与资源元数据分别保存。
- `esg_reports/importer.py`：支持目录根部PDF和原有pdf/子目录；整批登记与成员保存同事务完成，重复导入复用资源。
- `ExtractESG/document/src/esg_document/api/report_resources.py`：`GET /api/report-resources/folders`、`/folders/{folder_id}`、`?folder_id=…`、`?unfiled=true`。旧报告查询接口仍返回全部报告。
- `ConsoleESG/frontend/src/features/reports/{pages,dialogs}.tsx`及`app/App.tsx`：文件夹卡片、成员子页、详情返回与导入跳转。类型从Python合同生成。

文件夹只归报告模块所有，不进入条款、提取事实或数据库产品的身份。没有修改提取或发布路线。

## 实际使用结果（2026-11-08）

- PV原目录生成manifest和导入说明，50份原件未移动或修改；来源记录保持原样。
- 50份实际导入成功，目录显示“光伏行业 PV Industry”；重复导入50份均返回already_present，仍仅1个文件夹、50个成员。
- 补充原有18份报告的行业等可确认信息；已确认发表年的九仓与隆基报告保存发表年，未确认的保持未知。
- 报告库共68份：PV文件夹50份，独立报告18份；PV全部资源可用。原报告的封存提取输入不因资源信息补充而改写。
- PV的50份均有企业、行业、标题与覆盖期。35份可确认发表年，其余15份留空；保留原始来源及PDF创建时间。First Solar封面年与覆盖年、晶澳双年度分别登记。
- 前端生产构建通过；真实API读通目录、成员、独立报告与详情。Work设置禁用浏览器操作，本轮没有视觉验收。

操作说明见[PDF导入指南](../../guides/pdf-intake.md)。未启动批量提取，未发布数据。

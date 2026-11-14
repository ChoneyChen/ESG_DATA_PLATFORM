# 统一开发指引

所有开发者和代理维护同一套`docs/`。模块README只作导航。不要新建日期化总规划、审计报告、私人架构说明或另一份“当前系统状态”；历史材料从Git读取。

## 开始工作

1. 读[文档入口](docs/README.md)、[需求](docs/01-requirements.md)、[架构](docs/02-architecture.md)。
2. 按[模块设计](docs/03-modules.md)定位责任，再读[合同](docs/04-contracts.md)及本任务对应章节。
3. 明确触发问题、目标行为、所属责任和真实复现材料，再修改。条款内容改条款，通用能力改责任模块。
4. 同一轮同步修改规范和实现。涉及HTTP或文件增删时，运行`ExtractESG/.local/python/bin/python tools/build_documentation.py`更新参考索引。
5. 按[开发流程](docs/09-development.md)完成真实验证和结果记录。不要堆校验、空转测试或重复报告来代替真实任务。

## 工程边界

- 条款语义归单条合同；展示组和16/32集合仅组织ID，不产生专用提取分支。
- 原值只有定量和定性两类，保留所有披露期间；可选属性缺失不阻止原值保存。
- 模型选择实际行列或原句，程序绑定原值。模型理解不跨条款/任务复用。
- 跨模块用合同和端口，不扫描其他模块内部目录；比较和发布归数据库。
- 修复从全链路及责任模块出发，同轮移除被替代实现和说明，不增加第二套权威。

## 工作区约束

- CrawlESG保护区不读取、修改、删除或暂存；保留已有无关改动。
- 不读取/复制`PlatESG/.local/review-token`、`.env*`或其他凭据；凭据不进入URL、日志、任务、前端或文档。
- 本地模型，`allow_cloud_ocr=false`，不静默切换提供者；`PLATESG_AUTO_PUBLISH=0`。
- 不重置或迁移`PlatESG/.local/demo-data`；不因文档任务启动、恢复或取消生产任务。
- `.local/`存运行数据与开发产物，不是规范文档目录；`.claude/`不纳入提交。
- 已授权的完成轮次直接提交并推送；身份为`Choney Chen <ChoneyChen@users.noreply.github.com>`，不暂存CrawlESG或无关文件。

具体工作范围和交付方式以用户本轮指令为准，本文件不增加审批流程。

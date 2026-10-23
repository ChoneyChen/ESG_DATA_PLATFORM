# PlatESG 独立数据平台

2026-10-07：产品网站 `PlatESG/frontend` 与操作台 `ConsoleESG` 独立构建。公开接口仅投影固定发布版本的科学字段；候选、当前审核、操作员及运行诊断进入受凭据保护的内部接口。P4–P7完整业务验收进行中，不能以工程测试或合成观测代替真实企业验收。

**核心条款属于标准包；主要条款属于当前产品选择，主要 ⊆ 核心。** 字典保留用户指定的 49 个原始 ESRS 编号，并列入重点条款中不在该清单的 6 项，合计 55 项：当前 38 项就绪、17 项待建设。字典版本 1.4.2。

| 目录 | 职责 |
|---|---|
| `catalog/` | 通用指标字典、原条款绑定、必要身份、比较条件及当前执行配置 |
| `backend/` | FastAPI读接口、独立审核接口、常驻PostgreSQL适配进程 |
| `backend/migrations/` | 平台自有`plat` schema：企业、交付审计、覆盖决定 |
| `frontend/` | React / TypeScript / Vite：企业、趋势、字典、比较、证据 |
| `review/` | 内部审核入口（`/review`），仅供运维；日常审核在 `ConsoleESG` |
| `scripts/` | 启动、显式候选交付和隔离合成演示 |
| `tests/` | 目录、范围锁定、版本、审核与连接边界验收 |

结果库唯一代码由[根数据库模块](../database/README.md)管理`esg` / `esg_api`；旧路径兼容同一实体，运行数据未迁移。主体注册属于`plat.entities`。共用一个物理PostgreSQL数据库，各模块独立登记迁移。旧approved事实视图不作为正式平台入口。

## 启动与预览

需要Node.js 20+、Python 3.12、后端requirements及锁定前端依赖。当前机器可复用既有Python环境；其他机器可在本目录创建`.venv`安装`backend/requirements.txt`。

```sh
cd "/Users/choney/Desktop/ESG  Database/ESG_DATA_PLATFORM/database"
pnpm install --frozen-lockfile
cd ../PlatESG/frontend
npm ci
npm run build
cd ..
./scripts/run.sh
```

启动脚本只监听`127.0.0.1:8765`，优先使用`PLATESG_PYTHON`、项目`.venv`或当前机器既有模型环境。默认数据目录为独立`.local/data`，不会替换旧结果库。

- 展示：<http://127.0.0.1:8765/>
- 独立工作台：<http://127.0.0.1:18081/>
- 旧审核兼容入口：<http://127.0.0.1:8765/review/>
- OpenAPI：<http://127.0.0.1:8765/docs>

脚本生成权限0600的`.local/review-token`，它对应服务端固定的本地身份 `local-reviewer`。操作台仅在当前页面内存保留凭据，公开前端不接收凭据。可通过 `PLATESG_OPERATORS_JSON` 配置服务端验证的多个身份：`[{"token":"配置中的密钥","operator_ref":"稳定操作员身份","roles":["read","review","publish","admin"]}]`。请求自填 `operator_ref` 不作为审计身份。角色分别限制读取、审核、发布、主体及生命周期管理；这属于本地受控身份配置，尚未建设生产账户登录和SSO。文档与日志不得记录实际token。

本机回环请求（`127.0.0.1`/`::1`）不带凭据时，服务端识别为本机操作员 `local-operator`（读取、审核、发布、管理），工作台因此无需粘贴密钥；提取与文档服务收到本机无凭据请求时，同样不带凭据向本服务确认身份，由本服务决定。带凭据的请求仍按上述配置验证，非本机无凭据请求一律拒绝。本机任何进程都能以此身份调用写接口；多人共用机器时设 `PLATESG_LOCAL_OPERATOR=0` 关闭，此后所有请求都需凭据。

### 合成演示

```sh
PLATESG_PYTHON=/path/to/python
"$PLATESG_PYTHON" scripts/demo.py
PLATESG_DATA_DIR="$PWD/.local/demo-data" ./scripts/run.sh
```

演示使用独立`.local/demo-data`，两家明确标记“合成”的企业、两个报告期、16条款，共64观测。数值和证据是软件测试样本，不是企业披露或真实提取准确率证明。加载器只审核本次合成批次对应的合成事实与映射。

本轮本机验收显式使用`.local/demo-data`作为现场数据库，其中64条公开观测仍为合成样本，另保存5条未批准的真实中国石油历史候选；数据真实性以逐条来源和标记判定，不能仅看目录名。更换数据根会选择另一数据库，须依据原配置和保全记录，不把省略环境变量误称为恢复现有现场数据。前端开发服务可在`frontend`运行`npm run dev -- --host 127.0.0.1 --port 5178`，代理至8765；构建后也可由FastAPI直接提供。

### 服务器数据库

设置`PLATESG_DATABASE_URL`为受控PostgreSQL连接串，使用`pg`驱动；同一事务始终使用同一连接。本地PGlite保持单进程`--workers 1`。生产适配已实现，尚未连接生产服务器验证。正式部署须分离迁移权限与服务权限、完成备份恢复和角色授权，不把PGlite当生产服务器。

## 连接提取与审核

1. 操作台选择“平台主要条款 · E1–E3”（19 条），或明确手动选条款。主要配置固定 profile / dictionary / package 摘要；手动修改解除主要快照，55 条全目录不静默缩减。
2. 原有报告Session生成正式Result Bundle，partial导出同样可交付。
3. 通过`POST /internal/v1/entities`登记主体，明确集团、上市公司或子公司；报告发行主体不自动成为表内事实的测量主体。
4. metadata JSON明确填写`upstream_system`、`organization_ref`、`report_ref`、`report_version_ref`、`source_artifact_ref`、`source_sha256`和`report_title`。可填`language_code`、报告期间及发布日期；不得从文件名推定身份。
5. 首选 ConsoleESG 的提取详情“交付”页：选择已确认的报告身份，服务端固定PDF、IR及Bundle来源，交付到 `/internal/v1/deliveries`，返回持久化幂等回执。也可用 `handoff.py` 或受凭据保护的导入接口。候选、批次和范围快照同事务保存；不批准任何真实事实。

```sh
"$PLATESG_PYTHON" scripts/handoff.py --bundle /absolute/result-bundle --metadata /absolute/report-metadata.json
```

6. 在 ConsoleESG 审核中心分别核对原文事实、语义映射及主体/期间/边界/方法/必要维度。事实批准不能替代映射批准；exact/supports之外的映射不会写入正式指标。
7. 审核决定与outbox同事务保存。`PLATESG_AUTO_PUBLISH=1` 时处理器自动检查发布条件；设为 `0` 时保留事件，由具备发布角色的人员显式处理，重启仍保持暂停。页面展示阻断原因。两种模式均无须重新调用提取模型。

`PLATESG_IMPORT_ROOTS`配置允许的Bundle根目录，本机按冒号分开。默认ExtractESG与PlatESG/.local；若原任务成果位于其他工作区结果根目录，应显式加入。路径解析符号链接后仍须在允许范围内。

inbox每轮最多处理20个**新**交付。回执ID是描述符内容SHA-256，可查询`GET /internal/v1/import-receipts?receipt_id=...`。补齐依赖后用`POST /internal/v1/import-receipts/{receipt_id}/retry`，提供`operator_ref`和`reason`，追加重试审计。结构错误须修正描述符形成新内容ID。数据库RPC超时可能有不确定提交，适配器停止后应先核验回执/批次，再重启；不会自动重放不确定写入。

## 数据与API合同

- 原事实、规范观测、数据集成员及证据快照分开保存。原值不被规范化覆盖。
- 中立SourceFact快照保存已读取的额外表行和原属性，未映射行不自动发布。报告来源事实按六位微秒时间与快照UUID游标分页，候选和公开观测属于另外的合同。
- 补充属性由服务端按审核员选择从固定来源解析，随正式来源冻结；公开JSON/CSV和网页读取同一快照。复杂原始对象保留在来源层，当前属性单位为null，JSON原生数字不保证原披露尾零。详见[科学存储链路](../docs/SCIENTIFIC_DATA_STORAGE.md)。
- 精确`numeric`在API及JSON导出中为字符串；浏览器仅在绘图坐标中转为Number。
- 单位倍率来自版本化规则，未知来源单位不能靠选择规范单位消除；原单位与转换依据保留。
- 身份包含指标定义版本、主体、期间、边界、方法、必要维度及值性质。企业同年可以有多个口径。
- E2-4_09方法陈述只作结构化展示；区间和检测限不制造点值，显式0不同于缺失。
- 比较按本次请求重新核对口径；多个组需明确选择，不能任意选第一企业或最大组。
- 冻结数据集保留观测及原证据；后来撤回只影响当前版本，历史仍可复现。
- 覆盖决定逐期间保存于内部当前审计；公开覆盖只根据固定发布版本展示“已发布／此版本暂无数据”。后者不能被改写为未披露或不适用。尚未建设人工缺失决定的公开发布快照。
- `/v1/sources` 和公开观测详情仅使用冻结来源。原PDF通过发布来源ID访问 `/v1/sources/{source_id}/document?dataset_version=...`：必须属于该版本，位于配置 `PLATESG_PUBLIC_PDF_ROOTS` 的已登记PDF集合，实际字节SHA与发布来源一致。无本地PDF的合成报告不生成链接；校验失败不回退到同名文件。公开响应不暴露本地路径、源SHA、候选身份或审核人。
- 公开修订字段已预留，但未登记公开修订说明时返回空列表，不能用当前内部审核历史拼造公开修订。

公开接口预留企业、字典、观测、覆盖、比较、数据版本和CSV/JSON导出。读接口支持固定数据集版本，游标绑定版本及查询，导出锁定首次解析版本。内部接口包含候选、原文、事实/映射审核、测量口径、覆盖决定与发布处理。严格请求模型及数值合同见OpenAPI。

`POST /internal/v1/delivery-status/query` 是受 `read` 权限保护的纯读取接口；POST 仅承载有界的固定身份描述符，不执行导入、审核、修复或发布。Console 通过文档后端 GET 调用它，浏览器不提交 Bundle 路径。查询使用只读可重复读事务，分别返回当前成果、会话、登记报告 PDF 版本的来源快照、所选候选、内部依赖候选和最高已封存数据集的观测数量。来源按快照去重、候选按持久映射分类；缺分类、缺固定身份、成果不可读或确认不明时保留具体原因与 null。旧回执不回填；新成功交付可附服务端生成的固定身份描述符。SQL 已提交但没有相符成功回执时保留 `committed_without_acknowledgement`，不能宣称已确认交付。

实施架构和SQL对象见平台实施记录（已归档到 Git 历史）。V1按全映射重算发布、全快照再筛选查询，适合当前小规模试点；扩大后应按受影响身份增量处理并在SQL层分页，不改变发布和比较合同。

本轮历史冻结字典通过已允许的import-only合同显式登记，仅用于来源和候选存档，不修改当前发布字典或自动升级旧包映射。当前基础字典 1.4.2（55 条，38 就绪），运行选择可另加经本地批准的合计延伸。数据库 55 项、平台 73 项通过；旧公开版本2/3各64条既有字段重启前后保持，新真实发布仍待业务验收，见联合记录（已归档到 Git 历史）。

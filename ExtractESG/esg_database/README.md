# ExtractESG Result Database

项目级现状、跨模块关系和新对话交接见仓库根目录 [`../../README.md`](../../README.md)。
本文只维护结果数据库雏形的边界和迁移方式。

这是 ExtractESG 最终提取结果数据库的本地雏形。它只保存从 ESG 报告中得到的
披露事实，不承担公司主数据、爬取文件索引、用户系统、同行基准、风险评分或
PlatESG 分析任务。

## 数据库方言

正式契约使用 PostgreSQL SQL：

- 迁移文件：`migrations/*.sql`
- 表和约束：PostgreSQL 17+ 兼容语法
- 本机运行：PGlite 0.5.4，使用项目目录内持久化的 PostgreSQL 数据文件
- 服务器运行：正式 PostgreSQL，可直接执行同一批迁移

PGlite 只是这台尚未安装 PostgreSQL/Docker 的 Mac 上的单用户开发运行时，不是
生产服务器，也不能代替生产 PostgreSQL 的并发、备份和运维能力。

## 数据边界

```text
source_report_refs
  -> esg_disclosures
       -> structured_values
       -> qualitative_claims
       -> disclosure_dimensions
       -> disclosure_sources
       -> disclosure_standard_mappings

canonical_concepts
  -> esg_disclosures

standard_frameworks
  -> framework_versions
  -> standard_requirements
```

核心原则：

- 一行 `esg_disclosures` 表示一条口径明确的报告披露，而不是“公司+年份+指标”。
- 报告原始名称、原始文本和原始值永远保留。
- 数值、布尔和枚举使用类型化列，不把所有值塞入文本或 JSON。
- 部门、地区、业务线、方法和边界进入可扩展维度表。
- 无法映射的公司特有披露可以用 `mapping_status=unmapped` 保存。
- 只有 `quality_status=approved` 的记录进入 `esg_api` 对外视图。

## 本地使用

本目录依赖 Node.js 20+ 和 pnpm。当前 Codex 工作区已经提供二者。

```bash
pnpm install
pnpm db:init
pnpm db:status
pnpm db:verify
```

`scripts/run-node.sh`会优先使用系统Node；若系统未安装，则自动寻找Codex工作区
提供的Node运行时。

载入独立演示数据：

```bash
pnpm db:demo
pnpm db:status
pnpm db:verify
```

重建本地库：

```bash
pnpm db:reset
pnpm db:init
```

本地数据库位于 `.local/pglite/`，已被 Git 忽略。`db:reset` 只删除这个目录，
不会触碰 OCR、Document IR 或其他项目文件。

## 迁移规则

- 已提交的迁移不得原地修改；结构变化新增下一个编号迁移。
- 数据库迁移只操作 `esg` 和 `esg_api` schema。
- `examples/` 不属于正式迁移，生产环境不得自动载入。
- 将来接入正式 PostgreSQL 时使用 Alembic 或部署工具按文件顺序执行这些 SQL。
- 向量、全文检索、分区和物化分析视图待真实数据规模确认后再添加。

## 对外视图

| 视图 | 内容 |
|---|---|
| `esg_api.v_quantitative_observations` | 审核通过、可计算的数值披露 |
| `esg_api.v_categorical_observations` | 审核通过的布尔和枚举披露 |
| `esg_api.v_qualitative_disclosures` | 审核通过的定性原子主张 |
| `esg_api.v_standard_disclosures` | 披露事实到标准条款的审核映射 |

这些视图返回上游 `organization_ref` 和 `report_ref`。PlatESG 使用这些引用连接
自己的企业数据库，我们不在本库复制企业详细信息。

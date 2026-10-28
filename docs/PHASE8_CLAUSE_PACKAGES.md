# 阶段 8：一条款一目录

日期：2026-10-28  
范围：`ExtractESG/standards/clauses/`、条款目录加载器、定向提取与 Document API。

## 1. 为什么拆

阶段 7 已经把条款的科学内容收敛成一份 profile，但 profile 仍按“包 × 版本”放在一个文件里：
改一条条款要重生成整包，删除/新增条款会影响同包其他条款，前端也没有稳定的“一条条款”地址。
阶段 8 把**条款变成目录**，包只保留引用与顺序。

## 2. 目录结构

```text
ExtractESG/standards/clauses/
├─ catalog.json                       # 索引：包 → 条款路径 + 合同摘要
└─ esrs.2023-set1.e1-5/
   └─ 2.1.2/
      └─ E1-5_01/
         ├─ clause.json               # 身份、概念、值/单位、口径空间、发布规则、合同摘要
         ├─ retrieval.json            # 正向词、排除词、结构锚点
         ├─ evidence.json             # 字段级证据要求
         ├─ acceptance.json           # 验收样例（默认空）
         └─ provenance.json           # 包摘要、段落、来源引用、生成器与时间
```

`catalog.json` 记录每个包的数字摘要与条款清单，用于校验“目录里的条款”和“不可变包”仍然一致。

## 3. 生成与校验

```bash
cd ExtractESG/standards
PYTHONPATH=src python scripts/build_clause_catalog.py           # 生成
PYTHONPATH=src python scripts/build_clause_catalog.py --check   # 过期即失败
```

真实结果：**99 包、1,125 条款、5,626 个文件、4.6 MB 内容**；1 条条款（E1-5_19）因无法定单位被显式跳过并写入索引。

生成是确定性的：同样的包与字典得到逐字节相同的文件，因此 `--check` 可以放进流水线。

## 4. 读取与完整性

`ClausePackageCatalog`（`esg_standard_packages/clause_package.py`）：

- 按索引定位条款目录，缺文件直接报错，不当成“空条款”；
- 从五个文件重建 `ClauseExtractionProfile`，重算 `profile_digest`，与文件里的 `contract_digest` 比对；
- 摘要不一致 → `ClausePackageError`，链路不会用被改过的条款继续跑；
- 返回值是同一套 profile 对象，阶段 7 的提取与界面不需要改。

提取侧 `BundleWriter` 改为读条款目录；口径覆盖逻辑不变。

## 5. 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/clause-catalog` | 条款索引（包、条款路径、合同摘要、跳过项） |
| GET | `/api/clause-catalog/{package}/{version}/{source_id}` | 一条条款的五个文件与重建后的 profile |
| GET | `/api/pipeline/clause-catalog[/...]` | 控制台同源代理 |

## 6. 测试

标准包 153 通过（新增 8 条目录包测试：索引完整、五文件齐全、摘要重算、篡改失败、缺文件失败、未知包/条款、条款与包一一对应）。
定向提取 780 通过、6 跳过；覆盖逻辑在条款目录上重跑通过。

## 7. 边界

- 现在条款目录仍是**从不可变包编译出来的**，不是手写源。阶段 11 的编辑器会在同一目录结构上写入作者版本，并让包引用条款版本。
- 包文件本身没有修改（不可变摘要不变），因此旧任务与既有发布不受影响。
- `catalog.json` 采用固定时间戳，保证可复现；真实编辑写入时由编辑器生成新的时间与摘要。

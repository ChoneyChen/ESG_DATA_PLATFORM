# 阶段 1：统一条款科学合同

日期：2026-10-28

## 已交付

- 新增 `esg_standard_packages.scientific_contract`：从不可变标准包编译测量单位、值角色、聚合、归一与科学来源；
  平台保留展示定义、比较和审核策略，不将报告读取轴自动提升为发布科学维度。
- 平台字典由标准包编译生成，并携带 `scientific_contract_source`、包身份和原始测量合同。
  候选 **1.5.0** 只在隔离目录生成；生产 1.4.5 未覆盖，旧发布快照未迁移。
- 新 foundation 任务校验条款身份和单位轴；specs 的维度由同一合同提供。
  中文检索词、行级 guard 与模型提示的专用适配仍保留，阶段 2/3 继续收敛，不机械删除有效规则。
- 审核归一共享受控代码映射，固定轴进入证据范围；无意义原文不再重复形成未声明维度。
  未声明 `aggregation_role` 的报告子项只保留为补充属性，不虚构科学轴。

## 真实验收

- 全部 foundation 条款通过标准合同身份/单位轴校验。
- 严格基线保持 154/159，当前期污染物 3/3，历史期仍为 0/12；不把未解决的历史问题记为完成。
- 中国石化：从自动审核整体失败，恢复为 14 条映射 13 条自动批准、1 条待人工；另 2 条因映射/维度保留人工。
- 阿里巴巴：13 条映射 11 条自动批准、2 条待人工，与阶段 0 一致。
- 1.5.0 候选与 1.4.5 相比，除 E1-6_30 的 `ratio_recompute` 修正外，公开展示/比较定义保持；
  默认构建拒绝未经批准的聚合变化。
- 两个真实审核回放均在内存 PGlite 完成，未发布、未连接生产数据库。

```bash
/Users/choney/Desktop/model/.runtime/venv/bin/python PlatESG/scripts/build-dictionary.py \
  --dictionary-version 1.5.0 --allow-contract-changes --output-dir .local/phase1-contract/catalog

database/scripts/run-node.sh scripts/phase0_review_baseline.mjs \
  --bundle-root ExtractESG/targeted_table_extract/targeted_extract_output \
  --document-id doc-sha256-d0a8c6ae07a81e42a83649c72555b34f9c17871d80ab29a86f9f2c0593edc064 \
  --dictionary .local/phase1-contract/catalog/metric-dictionary.v1.json \
  --output .local/phase1-contract/review-sinopec
```

## 后续边界

- 阶段 2：统一两路线 SourceFact，保留全历史期间、主体/边界及字段证据；
  处理物理去重与来源解释版本冲突，不能因新版合同重写旧事实。
- 阶段 3：把检索词/选页也编译自合同，并建立报告级中立理解与增量索引。
- 候选字典仍需通过显式登记流程才能进入生产；本轮未将它静默替换进服务。

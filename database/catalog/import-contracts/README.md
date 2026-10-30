# 冻结历史合同资产

本目录保留已有历史产品引用的字典、配置和摘要。它不是当前条款源，也不是新提取结果的注册入口。旧 `/internal/v1/import-catalogs/register` 和旧成果生产导入已退役，不能按早期操作文档重新登记材料。

`index.json`固定允许材料的SHA-256；旧冻结查询和隔离回放仍按当时的字节核对。保留这些资产是为了历史结果可读，不激活旧字典或改变当前条款。

新生产交接通过[独立封存与原值接收合同](../../../docs/architecture/delivery.md)。capture初始化不加载本目录；冻结发布产品所需模式由显式 `frozen_publication` 配置管理。

隔离历史回放使用 `ESG_TEST_HISTORICAL_BUNDLE` 与 `ESG_TEST_LEGACY_BUNDLE`。未提供实物时测试明确跳过，不能把这类跳过算作真实报告验收；显式提供不存在或无效实物应失败。

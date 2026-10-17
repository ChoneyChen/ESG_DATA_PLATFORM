# 历史成果导入合同

此目录仅提供经允许清单固定的历史实物，不激活旧字典。`index.json` 固定登记材料的 SHA256；材料再固定原字典和原配置的逐字节 SHA256。数据库登记表 `plat.import_contract_releases` 保存审核人、原因和不可变快照。

认证读取：`GET /internal/v1/import-catalogs`。

admin 登记：`POST /internal/v1/import-catalogs/register`，正文只允许：

```json
{
  "contract_id": "esrs-platform-major.dictionary-1.0.0",
  "expected_release_sha256": "0aafaf9a13748ba1f6cba10ec41d3d5151eaa58380f509743ad72bf1bdb7e765",
  "reason": "已核对原字典、原配置及历史包绑定，允许旧固定成果入候选库"
}
```

审核身份来自服务端认证。接口不接受路径、任意字典 JSON 或客户提交的身份。重复登记保留首次审核记录；不可更新或删除。登记时和后续历史导入时都检查允许实物的真实字节、当时 ready 集合及编译包绑定。

历史合同不调用 `seedMetricDictionary`，不写发布字典或 outbox，不改变新任务范围。旧成果进入中立来源和候选库，审核及发布继续独立检查；当前标准不匹配会阻塞发布，不会自动换绑。未保存平台字典配置的旧式 explicit 任务标为 `legacy_explicit_without_frozen_catalog`、字典版本 `unassigned`，不能宣称已验证原任务的字典版本。

Bundle 自带且通过核对的原配置标为 `bundle_frozen`。若原 Bundle 没有配置，但审核交付者另行明确提供范围，该范围标为 `externally_declared`，仅表示外部声明及登记合同核对，不证明原任务曾冻结该范围。两份配置同时存在时必须原样一致，否则拒绝；来源标记由服务端决定。

当前只允许原字典 1.0.0；增加其它历史版本须先保存并审核真实原文件，再显式登记新允许材料，禁止根据来件自登记或取最近版本代替。

隔离真实回放使用 `ESG_TEST_HISTORICAL_BUNDLE`（原冻结字典1.0成果）与 `ESG_TEST_LEGACY_BUNDLE`（没有原字典配置的非空旧成果）。普通源码测试在未提供实物时明确跳过相应回放；显式提供不存在或不符合条件的实物会失败。业务验收必须显式提供实物并确认零跳过，不能把可选跳过当作真实报告验收通过。

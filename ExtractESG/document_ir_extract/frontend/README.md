# ESG 报告工作台前端

原生 ES modules，产品入口为报告库、提取任务、结果工作台、待处理和系统设置。前端只选择、展示和操作后端合同，不生成事实、合并记录或决定业务覆盖。

## 报告任务

`sessions.js` 是唯一报告提取创建器。PDF 与已有 IR 使用同一 `ReportExtractionRequest`，通过文档控制面的 `/api/report-sessions/preview` 和 `/api/report-sessions` 预览、创建。跨标准选择保留各包版本和指标集合；高级批量报告沿用同一表单，为每份报告分别预览并创建 Session。报告库保留 PDF 上传与文件夹导入。

普通语义主脑仅提供本地 Qwen 或七牛通用视觉模型；NuExtract 是后台专用角色。云语义处理与 OCR API 使用分别授权，所有角色、计算依赖、复用输入、预算及证据限制由后端实际预览返回。快速、均衡、深入为后端完整度预设，高级设置可覆盖预算。模型质量、成本和耗时仍需真实报告验收。

五个业务入口中的待处理读取后端提供的 `available_actions`，遵守动作 endpoint、method、payload、范围与费用标志。执行状态和成果状态独立展示，`finished` 不代表穷尽报告，partial 事实可查看并仅恢复未完成动作。

## 共同服务与兼容读取

- `api.js`：两个后端的唯一 HTTP 客户端，统一超时、错误、取消和同 URL 并发 GET 去重。网络失败显式显示，不返回空目录冒充成功。
- `subscriptions.js`：唯一刷新调度器；页面离开即退订，浏览器隐藏时暂停并取消在途请求，终态目录降低刷新频率。
- `status.js`、`task-store.js`：共同状态标签及 revision 缓存。revision 未改变时不重载详情与 inspection。
- `standards.js`、`roles.js`：共同标准选择合同、角色配置和仅存在本页内存的凭据。凭据不进入 localStorage，刷新或关闭页面后清空。
- `actions.js`：按后端合法动作执行，GET 审查动作导航待处理，恢复及云复核仅附所需凭据。
- `results.js`：共同事实列序，展示源行主题、期间、实体、实体角色、Scope/方法/类别、原量或陈述、单位与证据。未知身份保留为空。

报告级结果通过 `/{session_id}/facts` 读取中性源事实。标准覆盖连接各包投影，物理证据保留定位。旧 Bundle 继续读取 targeted inspection 的原始行序与后端逻辑测量关系，并明确缺少报告级身份合同；不从同值推断重复。完整事件、文件清单及模型审计在展开或选择技术页时加载。

原 05/07 创建器及 `pipeline.js` 已删除。旧路由/书签映射至新入口；OCR、IR 构建、复核、页图、逻辑表、存储清理和缓存保留在报告文档详情或系统设置的高级工具中。项目职责与升级整体边界见 [系统重塑方案](../../docs/SYSTEM_RESHAPING_PLAN.md)。

## 验证

本次运行 JavaScript 语法检查及 `tests/frontend_contract_test.cjs` 的真实浏览器、假 HTTP 合同验证。测试拦截全部业务请求，不会启动 Worker、OCR、embedding、VLM 或真实提取。

```sh
ESG_TEST_PLAYWRIGHT=/path/to/playwright node tests/frontend_contract_test.cjs
```

测试使用独立无头浏览器，macOS 默认 Google Chrome，可用 `ESG_TEST_CHROME` 指定其他 Chromium 可执行文件。覆盖 PDF/IR 同创建器、角色与云授权、partial 事实、三结果视角、revision 增量、隐藏页退订、GET 去重和显式网络错误。真实报告语义质量、标准包业务批准和正式入库不由这些工程测试代替。

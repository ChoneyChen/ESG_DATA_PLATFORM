# 报告目录提交格式

```text
batch/
├── manifest.json
└── pdf/
    ├── report-001.pdf
    └── report-002.pdf
```

把 `manifest.template.json` 改名为 `manifest.json`，每个 PDF 填一条记录。模板中的空值必须补齐：`sha256`、企业名称、发表年份、行业名称。PDF 编号、文件相对路径和完整 SHA-256 在批次内分别唯一；文件必须是 `pdf/` 的直接子文件。发表年份与报告覆盖年度分别填写。

企业和行业可用名称筛选，也可填写稳定代码；行业的分类体系通过 `taxonomy` 声明。原始名称保留，接口不猜测缺失信息。额外资料放进 `extensions`，资料出处放进 `metadata_sources`。

仓库根目录执行：

```sh
PYTHONPATH=ExtractESG/contracts/src ExtractESG/document_ir_extract/backend/.venv/bin/python \
  -m esg_reports.importer /path/to/batch --library /path/to/pdf-library --preview

PYTHONPATH=ExtractESG/contracts/src ExtractESG/document_ir_extract/backend/.venv/bin/python \
  -m esg_reports.importer /path/to/batch --library /path/to/pdf-library
```

导入前验证整批元数据、文件和散列；冲突不静默覆盖。相同提交可重复执行。更新已有资源元数据使用资源 API 与 `expected_revision`，不通过重新导入覆盖。

HTTP 入口：`POST /api/report-resources/imports`，请求 `{"folder":"/path/to/batch","preview":true}`。确认预览后将 `preview` 改为 `false`。这是文档服务本机目录接口；平台消费者使用资源查询接口，不读取目录。

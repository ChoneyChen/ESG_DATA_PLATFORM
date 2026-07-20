# 云端 embedding 接入说明

`scripts/run_cloud_embedding_retrieval.py` 适配 OpenAI-compatible embedding 接口。它需要服务商、HTTPS 接口地址、模型名和 API Key；当前仓库没有保存任何真实密钥，也没有提交未经验证的云端检索结果。

## 数据外发边界

运行脚本会把条款检索文本和 Document IR 页面文本发送给所配置的第三方服务。只有在组内确认服务商、数据使用条款、保留策略和费用后才能执行。脚本会拒绝 HTTP 地址，并要求显式设置 `EMBEDDING_ALLOW_DATA_UPLOAD=YES`，避免误传报告内容。

```powershell
$env:EMBEDDING_API_URL = "https://已批准服务商/v1/embeddings"
$env:EMBEDDING_API_KEY = "通过安全渠道提供；不要写入仓库"
$env:EMBEDDING_MODEL = "服务商指定的 embedding 模型"
$env:EMBEDDING_ALLOW_DATA_UPLOAD = "YES"
python scripts/run_cloud_embedding_retrieval.py
```

接口需接受 `{"model": "...", "input": ["..."]}`，并返回带 `index` 和 `embedding` 的 `data` 数组。不同服务商的格式或鉴权不兼容时，必须做适配并重新验证。密钥不得写入 `.py`、CSV、HTML、README 或提交历史。


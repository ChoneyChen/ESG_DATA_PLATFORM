# PDF如何组织并导入

目的：提供完整原报告和可识别的资源信息，让链路读取报告中的原值。**一份报告含2023、2024、2025，就保留同一PDF中的三年数据；不要裁成三份。** 当前多年份提取缺口及修复见[本轮审查](../audits/2026-10-31/README.md)。

## 1. 单份报告

在Console“报告”上传完整原PDF，按已知情况填写报告企业、发表年份、行业、报告名称和企业ID。上述业务元数据均可选，不知道时留空；系统以实际文件摘要建立资源身份，不从文件名猜企业或年份。

发表年份指报告实际发布年份；报告覆盖年度指主要报告期。它们都不能代替每条测量的实际年份。历史表格、子公司、脚注、附录和方法说明应随原PDF保留。扫描报告也交原件，不需人工OCR或转Word。

## 2. 批量报告

准备一个批次目录，PDF必须直接放在`pdf/`，不在该子目录里继续分企业层级：

```text
batch-001/
  manifest.json
  pdf/
    example-2025.pdf
    another-report.pdf
```

每份PDF在manifest中登记一项。用实际PDF计算SHA-256，不能填示例摘要。下面代码为**一份示例文件**生成manifest；按自己的企业/发表年/行业修改，并为其余文件追加同样的记录：

```python
from pathlib import Path
from hashlib import sha256
import json

root = Path("/absolute/intake/batch-001")
pdf = root / "pdf/example-2025.pdf"
manifest = {"schema_version": "report-resource-batch-1.0", "reports": [{
    "pdf_id": "example-esg-2025",
    "file": "pdf/example-2025.pdf",
    "sha256": sha256(pdf.read_bytes()).hexdigest(),
    "company": {"name": "示例企业有限公司"},
    "publication_year": 2026,
    "industry": {"name": "制造业"},
    "reporting_year": 2025,
    "title": "示例企业2025年度ESG报告"
}]}
(root / "manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
```

当前格式中仅`pdf_id/file/sha256`必填；`company/publication_year/industry/reporting_year/title/metadata_sources/extensions`选填。`reporting_year`只填主要年度，不限制提取范围，也不需要为每个历史年重复登记。

`pdf_id`用稳定的英数字、点、下划线或短横线，最长160字符；文件名可以中文。一个批次内编号、文件路径和PDF摘要均不能重复；同一PDF已登记其他编号时复用原资源。修改已登记元数据通过编辑并产生修订，不重新上传来覆盖身份。完整格式见[报告合同](../../ExtractESG/contracts/src/esg_reports/contract.py)。

## 3. 导入并运行

Console“报告”选择批量导入，填写批次目录绝对路径，先查看预览，再执行导入。系统管理PDF、登记和存储位置；不要自己搬到`.local/data`内部的blobs、registry或提取结果目录。

然后选择报告和独立条款执行。原值、属性、期间和来源由链路生成，系统输出封存ZIP并自动交付数据库接收器；页面CSV/矩阵用于浏览与分析，不是数据库正式输入合同。不要手工改中间JSON来补年份或直接把PDF放入SQL库。

当前已实现**提取结果→独立原值接收**；**新原值→科学观测/发布→Plat展示**仍按本轮计划建设。接收成功不等于已经公开发布。

## 4. 提交前只核对四件事

1. 是完整、可打开的原PDF，历史列和附录没有被裁去。
2. 企业、发表年、主要覆盖期分别填写，未知不猜。
3. 每份PDF登记一次，manifest路径与实际字节摘要一致。
4. 只整理输入材料；事实ID、证据、导出和接收回执交给系统生成。

**当前多年份生产截断尚未修复，不能用拆PDF或清空报告年度作为长期规避办法。** 应按M01–M04修复后重新提取，并核对所有披露年份。

[条款制作说明](clause-authoring.md) · [本地运行和数据根](local-operation.md)

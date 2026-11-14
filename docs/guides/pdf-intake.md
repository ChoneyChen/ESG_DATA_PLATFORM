# PDF如何组织

目的：交完整原报告及已知资源信息，让链路自行生产原值和交接文件。一份报告同时有2023、2024、2025，不按年份裁开；每个实际披露期间都需要保留。

## 单份上传

在Console“报告”上传原PDF。企业、发表年、行业、主要覆盖年和标题按已知情况填写，未知留空。发表年与主要覆盖年不同，两者也不能替代具体测量期间。

保留附录、历史列、子公司、范围和方法脚注。扫描报告交原件，不要求先人工OCR或转Word。文件内容决定sha256和asset_id，不从文件名猜实际身份。当前单次浏览器上传上限为100MiB，由报告导入器定义。

## 批次目录

```text
batch-001/
  manifest.json
  pdf/
    report-a.pdf
    report-b.pdf
```

PDF也可以直接放批次根目录。当前只接受根部文件或pdf/下的文件，不嵌套任意多层目录。一个批次的pdf_id、路径和散列分别唯一。

下面脚本给目录内的真实PDF生成最小有效清单，不凭空填写摘要或企业：

```python
from pathlib import Path
from hashlib import sha256
import json

root = Path("/absolute/intake/batch-001")
paths = sorted([*root.glob("*.pdf"), *(root / "pdf").glob("*.pdf")])
reports = [
    {"pdf_id": f"batch001-{i:04d}", "file": p.relative_to(root).as_posix(),
     "sha256": sha256(p.read_bytes()).hexdigest()}
    for i, p in enumerate(paths, 1)
]
manifest = {"schema_version": "report-resource-batch-1.0",
            "folder": {"folder_id": "batch-001", "name": "报告批次001"},
            "reports": reports}
(root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
```

准备非空目录后生成，编号一旦导入应保持稳定。已知元数据再补到每项中：company对象含name/可选company_id；publication_year；industry对象含name/可选code/taxonomy；reporting_year；title；metadata_sources；extensions。未知对象整体省略，不填一个name=null的假企业。

清单必需pdf_id/file/sha256；folder和业务资料选填。多期间完整日期可放extensions.reporting_period。完整格式见[报告合同](../../ExtractESG/contracts/src/esg_reports/contract.py)，模板位于ExtractESG/contracts/examples/report-batch，但其中占位值必须替换。

## 导入与后续

Console批量导入填写目录绝对路径，先预览再导入；随后按“文件夹→报告→详情”导航。相同PDF重复导入复用原资源，不增加内容副本；元数据修改产生修订。

选择报告与独立条款执行。系统生成事实、来源、导出包和接收回执，再自动投影；产品预览后显式发布，Plat只展示有效发布。PDF、条款JSON、页面CSV或手写中间JSON都不是数据库正式接收文件。

不要自己搬入内部blobs/IR/提取目录，不手写事实ID或补年份，不因为某报告缺维度就裁剪或重复登记。提交材料只需原PDF、准确路径/摘要和已知资料；未找到的证据由运行结果如实表达。

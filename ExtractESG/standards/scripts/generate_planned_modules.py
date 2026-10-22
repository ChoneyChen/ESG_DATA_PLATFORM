"""Generate authoring sources for ESRS modules from the EFRAG IG 3 catalog alone.

The 41 hand-authored packages were built by history/builders/
build_esrs_environment.py. Generated modules reuse its frozen conventions: the
same identity elements (entity, actual period, boundary, unit), unit
dimensions, contracts and validation rules. What those packages took from
hand-written semantics (Chinese labels, domain guidance, core choice) is
replaced by rules that read only the catalog, so they hold for any disclosure
requirement of any future catalog:

* official data type        -> data class, value type, unit dimension, template
* the DR's MDR reference rows -> policy / action / target role and axes
* "anticipated financial effects" in the DR's names -> financial-effect contract
* topic IRO-1 / SBM-3       -> risk, time-horizon and scenario axes
* table data types          -> an open breakdown-member axis

Nothing here invents a value, unit, translation or core judgement; every
generated module is marked for human review in the registry. Only planned
modules, or generated ones whose release is not yet registered, are written:
a published version is immutable.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from esg_standard_packages.compiler import StandardPackageCompiler  # noqa: E402
from esg_standard_packages.io import load_json, pretty_json_bytes  # noqa: E402

VERSION = "2.1.1"
CORE = ROOT / "schemas/core/1.2.0/core.json"
CATALOG_PATH = ROOT / "catalog/esrs/2023-set1/ig3-original.json"
REGISTRY_PATH = ROOT / "registry/esrs/2023-set1/registry.json"
RELEASES_PATH = ROOT / "registry/releases.json"
MARKER = "逐条人工复核 IG 3 机械生成的语义合同"


def _frozen_conventions():
    builders = ROOT / "history/builders"
    sys.path.insert(0, str(builders))  # its esrs_semantics sibling, not the scripts/ shim
    spec = importlib.util.spec_from_file_location("frozen_esrs_conventions", builders / "build_esrs_environment.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


C = _frozen_conventions()
AXIS_LABELS = {**C.AXIS_LABELS, "breakdown_member": "表内分解成员"}

# token -> (unit dimension, canonical unit); None where core 1.2.0 has no
# dimension or the official type does not name one.
NUMERIC_TOKENS = {
    "ghgemissions": ("ghg-emissions", "tonne-co2e"), "energy": ("energy", "megawatt-hour"),
    "mass": ("mass", "tonne"), "volume": ("volume", "cubic-metre"),
    "percent": ("ratio", "ratio"), "percentage": ("ratio", "ratio"),
    "monetary": ("currency", None), "monerary": ("currency", None),
    "integer": ("count", "count"),
    "area": (None, None), "decimal": (None, None), "numerical": (None, None), "intensity": (None, None),
}
TEXT_TOKENS = {"narrative", "semi-narrative"}
DATE_TOKENS = {"date": "date", "gyear": "year"}
NONNEGATIVE = {"count", "mass", "volume", "energy", "ghg-emissions"}
ROLE_GUIDANCE = {
    "actual": "实际值须为该期间实际发生或存量值；目标、预测、基准及其他数据点不能替代。",
    "target": "目标值、目标年和基准须分别举证；实际进展不能替代目标本身。",
    "forecast": "预期财务影响须标明情景与时间范围；当前账面值不能替代预期值。",
    "context": "陈述须直接回应该数据点的问题；泛泛的政策口号或其他数据点的陈述不能替代。",
    "structure": "表结构只承载成员与列的定义，不单独形成带值事实。",
}


def parse_type(raw: str | None) -> tuple[bool, list[str]]:
    """'Table 4 Decimal/Integer' -> (True, ['decimal', 'integer'])."""
    text = (raw or "narrative").strip().casefold()
    table = re.match(r"table\b", text) is not None
    inner = re.sub(r"^table\s*\d*\s*/?\s*", "", text) if table else text
    return table, [token for token in re.split(r"[\s/]+", inner) if token]


def shape(row: dict) -> dict:
    table, tokens = parse_type(row.get("data_type"))
    numeric = [t for t in tokens if t in NUMERIC_TOKENS]
    text = [t for t in tokens if t in TEXT_TOKENS]
    dates = [t for t in tokens if t in DATE_TOKENS]
    unknown = [t for t in tokens if t not in NUMERIC_TOKENS and t not in TEXT_TOKENS and t not in DATE_TOKENS]
    notes = []
    dims = {NUMERIC_TOKENS[t] for t in numeric if NUMERIC_TOKENS[t][0]}
    dim, unit = next(iter(dims)) if len(dims) == 1 else (None, None)
    if len(dims) > 1:
        notes.append(f"原表类型含多种数值形式（{row['data_type']}）；单位维度由报告证据确定，不预设。")
    if "intensity" in numeric:
        dim = unit = None
        notes.append("强度须有分子、分母、分母量级及范围；原表类型保存在provenance，不按百分比自动归一化。")
    if "area" in numeric:
        notes.append("核心单位表暂无面积维度；保留原值与原单位，不做换算。")
    if "monerary" in numeric:
        notes.append("原表Monerary拼写保留；采用货币金额的decimal合同。")
    if unknown:
        notes.append(f"原表类型 {row['data_type']} 中的 {'/'.join(unknown)} 无通用合同，按文字陈述保留。")
    if numeric:
        value_type = "integer" if set(numeric) == {"integer"} else "decimal"
    elif dates:
        value_type = DATE_TOKENS[dates[0]]
    else:
        value_type = "text"
    if table and not tokens:
        data_class = "structure"
    elif numeric:
        data_class = "mixed" if text else "quantitative"
    else:
        data_class = "qualitative"
    return dict(table=table, categorical=not table and tokens == ["semi-narrative"], data_class=data_class,
                value_type=value_type, dim=dim, unit=unit, note="".join(notes) or None)


def dr_kinds(dr: str, rows: list[dict], shared: list[dict]) -> set[str]:
    """What the catalog itself says the DR is: its MDR references and names."""
    kinds = set()
    for record in shared:
        match = re.fullmatch(r"[^.]+\.MDR-([PAT])_01-\d+", record["id"])
        if match:
            kinds.add({"P": "policy", "A": "action", "T": "target"}[match.group(1)])
    if any("anticipated financial effect" in (row.get("name") or "").casefold() for row in rows):
        kinds.add("financial_effect")
    return kinds


def axes_for(dr: str, row: dict, kinds: set[str], table: bool) -> list[str]:
    axes = list(C.COMMON_AXES)
    if "policy" in kinds: axes.append("policy_ref")
    if "action" in kinds: axes.append("action_ref")
    if "target" in kinds: axes += ["target_ref", "target_year"]
    if "financial_effect" in kinds: axes += ["risk_type", "scenario", "forecast_horizon", "financial_item", "currency"]
    if dr.endswith(("IRO-1", "SBM-3")): axes += ["risk_type", "time_horizon", "scenario"]
    if row["source_sheet"] == "ESRS 2": axes.append("sustainability_topic")
    if table: axes.append("breakdown_member")
    return list(dict.fromkeys(axes))


def role_of(kinds: set[str], data_class: str) -> str:
    if data_class == "structure": return "structure"
    if "target" in kinds: return "target"
    if "financial_effect" in kinds: return "forecast"
    if data_class == "qualitative": return "context"
    return "actual"


def contract_for(metric: dict, kind: dict, kinds: set[str], role: str) -> dict:
    quantitative = kind["data_class"] in {"quantitative", "mixed"}
    if kind["data_class"] == "structure" or kind["table"]: template = "dimensional_table"
    elif "financial_effect" in kinds: template = "financial_effect"
    elif role in {"target", "baseline"}: template = "policy_action_target"
    elif quantitative: template = "numeric"
    elif kind["categorical"]: template = "categorical"
    else: template = "qualitative_claim"
    dim, unit = kind["dim"], kind["unit"]
    requirements = ["主体、实际期间和报告边界由来源明确支持；未知口径不能默认为相同。",
                    "证据支持原值/陈述及必要维度；单位转换不能消除方法或边界差异。"]
    if dim == "currency": requirements.append("同币种、同量级及同财务口径；不进行隐式汇率换算。")
    if dim == "ratio": requirements.append("匹配分子分母和比率定义；百分数可转换为0—1比例，但原值及单位保留。")
    if kind["table"]: requirements.append("表内成员（行/列标题所示的分解项）须与证据一致；成员与合计不能互相替代或重复求和。")
    axes = metric["identity_axes"]
    return dict(template=template, value_role=role,
                unit_dimension=C.UNIT_DIM + dim if dim else None,
                canonical_unit_id=C.UNIT_PREFIX + unit if unit else None,
                normalization="percent_to_ratio" if dim == "ratio" else "compatible_unit_only" if unit else "preserve",
                identity_axes=axes, comparison_axes=axes,
                comparison_mode="structure_only" if kind["data_class"] == "structure" else "conditional_numeric" if quantitative else "structured_display",
                aggregation="ratio_recompute" if dim == "ratio" else "do_not_sum",
                exclusions=[ROLE_GUIDANCE[role], "标题、目录、空白、未披露状态不能作为带值事实；显式零与缺失分开。"],
                comparison_requirements=requirements, semantic_type_note=kind["note"])


def context_references(dr: str, sheet: str, metrics: list[dict], kinds: set[str], registry: dict) -> list[dict]:
    """The authored E1–E3 context rules, generalized to every topical DR."""
    if sheet == "ESRS 2":
        return []
    core = {m["disclosure_requirement"]: m["core_source_datapoint_ids"] for m in registry["modules"]}
    refs, all_ids = [], [m["source_datapoint_id"] for m in metrics]

    def add(target, ids, purpose, description, applies=None):
        refs.append(dict(reference_id=C.identity(dr) + ".context." + purpose + "-" + C.slug(target),
                         target_package_id=C.identity(target), target_package_version=VERSION,
                         target_source_datapoint_ids=ids, applies_to_source_datapoint_ids=applies or all_ids,
                         purpose=purpose, description=description, output_policy="context_only"))

    add("BP-1", ["BP-1_01", "BP-1_02", "BP-1_04"], "report_identity", "共享编制基础、合并范围和价值链覆盖；按所选条款需要使用，不推定具体事实主体。")
    quantitative = [m["source_datapoint_id"] for m in metrics if m["data_class"] in {"quantitative", "mixed"}]
    if quantitative:
        add("MDR-M", ["MDR-M_01", "MDR-M_02"], "measurement_method", "共用指标定义、方法与重大假设；必须关联被测指标和可持续主题。", quantitative)
        add("BP-2", ["BP-2_07", "BP-2_08", "BP-2_09", "BP-2_10", "BP-2_14"], "measurement_method", "只使用明确适用于所选指标的不确定性、方法、变更和重述说明。", quantitative)
    base = dr.split(".", 1)[1] if "." in dr else None
    if base == "IRO-1":
        add("IRO-1", ["IRO-1_01", "IRO-1_02", "IRO-1_07"], "materiality_context", "共享影响与财务风险评估流程；主题特定要求仍分别举证。")
    elif base == "SBM-3":
        add("SBM-3", ["SBM-3_02", "SBM-3_03", "SBM-3_10"], "business_context", "共享业务回应与韧性背景，不替代主题风险的具体分析结果。")
    elif base and base in core and core[base]:
        add(base, core[base], "governance_context" if base.startswith("GOV") else "business_context", f"ESRS 2 {base}的一般背景；主题特定要求仍分别举证。")
    if "financial_effect" in kinds:
        add("SBM-3", ["SBM-3_08", "SBM-3_09"], "financial_context", "区别当前与预期财务影响；风险与期间须匹配。")
    return refs


def build_module(entry: dict, rows: list[dict], shared: list[dict], catalog: dict, registry: dict) -> dict:
    dr, sheet = entry["disclosure_requirement"], entry["source_sheet"]
    package = C.identity(dr)
    if package != entry["package_id"]:
        raise ValueError(f"Registry package id {entry['package_id']} does not follow {package}")
    # The registry's core for a planned module is the user's platform-major
    # selection, or nothing; neither is a full module analysis.
    major = entry["major_source_datapoint_ids"]
    core = list(entry["core_source_datapoint_ids"]) or [rows[0]["id"]]
    from_major = bool(major) and set(core) == set(major)
    kinds = dr_kinds(dr, rows, shared)
    refs = [C.source_ref(dr, "law"), C.source_ref(dr, "ig3")]
    module = dict(manifest=dict(
        format_version="2.1", package_id=package, package_version=VERSION, status="draft",
        standard=dict(framework_id="esrs", framework_version="2023-set1", jurisdiction="EU", module_id=C.slug(dr),
                      disclosure_requirement=dr, effective_from="2024-01-01"),
        core_schema_id="extractesg.core", core_schema_version="1.2.0",
        # No authored Chinese labels exist yet; the English original stands in both.
        default_language="en", supported_languages=["en", "zh"], files={f: f + ".json" for f in C.FILES}),
        **{f: [] for f in C.FILES})
    module["sources"] = [
        dict(source_id=refs[0], title="Commission Delegated Regulation (EU) 2023/2772", authority="authoritative_law",
             version_or_date="2023-07-31", locator="Annex I, ESRS " + dr.split("-")[0].split(".")[0] + " " + dr, url=C.LAW_URL),
        dict(source_id=refs[1], title=catalog["source_file"], authority="non_authoritative_implementation_guidance",
             version_or_date="User-provided immutable IG3 workbook", locator=sheet, url=catalog["source_url"],
             notes="原始表格快照保留；不替代法律或重要性判断。"),
        dict(source_id=C.source_ref(dr, "reproduction"), title="EFRAG ESRS Set 1 official reproduction",
             authority="official_reproduction", version_or_date="2023-set1", locator=dr, url=C.REPRODUCTION_URL),
    ]
    for row in rows:
        number = row["id"].rsplit("_", 1)[1]
        kind = shape(row)
        data_class = kind["data_class"]
        numeric = data_class in {"quantitative", "mixed"}
        role = role_of(kinds, data_class)
        name = (row.get("name") or row["id"]).strip()
        metric_id, concept_id = C.mid(dr, row["id"]), f"{package}.concept.dp{number}"
        axes = axes_for(dr, row, kinds, kind["table"])
        description = f"{name}。{ROLE_GUIDANCE[role]}"
        metric = dict(metric_id=metric_id, source_datapoint_id=row["id"], disclosure_requirement=dr,
                      paragraph=str(row.get("paragraph") or ""),
                      application_requirements=[str(row["related_ar"])] if row.get("related_ar") else [],
                      labels={"en": name, "zh": name}, official_data_type=row.get("data_type") or "narrative",
                      data_class=data_class, value_family="number" if numeric else "statement",
                      obligation_class=C.obligation(row), allowed_value_origins=["reported"],
                      cardinality={"minimum": 0, "maximum": None}, description=description,
                      reporting_requirement="仅接收符合本数据点定义的原报告事实，保留原值、单位、实际期间、主体、边界与可定位证据。" + ROLE_GUIDANCE[role],
                      source_refs=refs, subject_concept_groups=[[concept_id]], disclosure_topic=f"{package}.topic.dp{number}",
                      disclosure_role="presentation" if data_class == "structure" else "anchor" if row["id"] in core
                      else "context" if data_class == "qualitative" else "detail",
                      identity_axes=axes, required_semantic_discriminators=axes,
                      fact_grain="structure_member" if data_class == "structure" else "source_cell" if numeric else "assertion",
                      measurement_kind=kind["dim"] or ("number" if numeric else "statement"),
                      evidence_form=["table_row", "narrative_statement", "footnote_context"], evidence_roles=["primary", "context"])
        metric["provenance"] = C.provenance(catalog, row)
        metric["measurement_contract"] = contract_for(metric, kind, kinds, role)
        module["metrics"].append(metric)
        module["concepts"].append(dict(concept_id=concept_id, labels={"en": name}, description=description, source_refs=refs))
        owner = "quantitative_observation" if data_class == "quantitative" else "qualitative_assertion"
        if data_class in {"qualitative", "structure", "mixed"}:
            module["elements"].append(C.element(metric, "statement", "原文陈述或独立结构成员", "text", "statement_raw",
                                                "qualitative_assertion", required=data_class != "mixed"))
        if numeric:
            minimum = 0 if kind["dim"] in NONNEGATIVE or "area" in parse_type(row.get("data_type"))[1] else None
            module["elements"].append(C.element(metric, "value", "披露数值", kind["value_type"], "value_numeric",
                                                "quantitative_observation", required=data_class != "mixed",
                                                unit_dimension=C.UNIT_DIM + kind["dim"] if kind["dim"] else None, minimum=minimum))
            module["elements"].append(C.element(metric, "unit", "报告原始单位", "string", "unit_raw",
                                                "quantitative_observation", required=bool(kind["dim"])))
        if not numeric and kind["value_type"] in {"date", "year"}:
            module["elements"].append(C.element(metric, "date_value", "披露日期或年份", kind["value_type"], "date_value", owner, "attribute"))
        owners = ["quantitative_observation", "qualitative_assertion"] if data_class == "mixed" else [owner]
        for record in owners:
            suffix = "_numeric" if data_class == "mixed" and record == "quantitative_observation" else ""
            for axis, target, label in [("reporting_period", "reporting_period_raw", "实际披露期间"),
                                        ("reporting_boundary", "reporting_boundary", "报告边界")]:
                module["elements"].append(C.element(metric, axis + suffix, label, "string", target, record,
                                                    required=axis == "reporting_period"))
            for axis in axes:
                if axis in {"reporting_period", "reporting_boundary"}:
                    continue
                dimension_id = package + ".dimension." + axis.replace("_", "-")
                if not any(d["dimension_id"] == dimension_id for d in module["dimensions"]):
                    module["dimensions"].append(dict(dimension_id=dimension_id, value_type="string",
                                                     labels={"en": axis.replace("_", " "), "zh": AXIS_LABELS[axis]},
                                                     description="由报告证据识别的" + AXIS_LABELS[axis] + "；未知不能由标准默认填充。"))
                module["elements"].append(C.element(metric, axis + suffix, AXIS_LABELS[axis], "string", dimension_id, record,
                                                    "dimension", required=axis == "reporting_entity"))
                bucket = "required_dimension_ids" if axis == "reporting_entity" else "optional_dimension_ids"
                if dimension_id not in metric.setdefault(bucket, []):
                    metric[bucket].append(dimension_id)
        module["validation_rules"].append(dict(rule_id=f"{package}.rule.dp{number}-evidence", target=metric_id,
                                               assertion="evidence_required", severity="error",
                                               parameters={"when_value_origin": "any"},
                                               message="事实必须有可定位原报告证据；缺失不插入空事实。"))
        for element in [e for e in module["elements"] if e["metric_id"] == metric_id and e["requirement_level"] == "required"]:
            module["validation_rules"].append(dict(rule_id=f"{package}.rule.dp{number}-" + element["element_code"].replace("_", "-"),
                                                   target=metric_id, assertion="required_element", severity="error",
                                                   parameters={"element_id": element["element_id"]},
                                                   message="已形成的对应类型事实需要" + element["labels"]["zh"] + "。"))
    manifest = module["manifest"]
    manifest.update(catalog_id=catalog["catalog_id"], catalog_source_sha256=catalog["source_sha256"],
                    shared_requirements=[C.shared_reference(record) for record in shared],
                    context_requirements=context_references(dr, sheet, module["metrics"], kinds, registry),
                    core_source_datapoint_ids=core,
                    core_selection_reason=("核心条款沿用平台主要条款（用户选定）；尚未做全模块分析，待逐条人工复核。"
                                           if from_major else
                                           "注册表尚未为该模块选择核心条款；机械生成时暂取首个数据点，必须逐条人工复核后替换。"))
    return module


def main() -> int:
    catalog, registry = load_json(CATALOG_PATH), load_json(REGISTRY_PATH)
    released = {(r["package_id"], r["package_version"]) for r in load_json(RELEASES_PATH)["releases"]}
    rows_by_dr: dict[str, list[dict]] = {}
    shared_by_dr: dict[str, list[dict]] = {}
    for record in catalog["records"]:
        target = rows_by_dr if record["record_kind"] == "独立编号条目" else shared_by_dr
        target.setdefault(record["dr"], []).append(record)
    compiler = StandardPackageCompiler()
    written, skipped = [], []
    for entry in registry["modules"]:
        generated = MARKER in entry["next_steps"] and (entry["package_id"], VERSION) not in released
        if entry["executable"] and not generated:
            continue
        rows = [r for r in rows_by_dr[entry["disclosure_requirement"]] if r["id"] in set(entry["source_datapoint_ids"])]
        module = build_module(entry, rows, shared_by_dr.get(entry["disclosure_requirement"], []), catalog, registry)
        module_dir = ROOT / "authoring/esrs/2023-set1" / C.slug(entry["disclosure_requirement"]) / VERSION
        module_dir.mkdir(parents=True, exist_ok=True)
        for name, payload in module.items():
            (module_dir / f"{name}.json").write_bytes(pretty_json_bytes(payload))
        try:
            compiled = compiler.compile(core_path=CORE, module_dir=module_dir)
        except Exception as exc:  # noqa: BLE001 - report the module, keep going
            skipped.append((entry["package_id"], str(exc)[:240]))
            continue
        entry.update({
            "core_source_datapoint_ids": module["manifest"]["core_source_datapoint_ids"],
            "core_analysis_state": "pending_full_module_analysis",
            "executable": True, "state": "compiled_draft", "package_version": VERSION,
            "source_directory": str(module_dir.relative_to(ROOT)),
            "source_digest": compiled.compilation.source_digest,
            "runtime_path": f"releases/{entry['package_id']}/{VERSION}/package.json",
            "next_steps": [MARKER, "真实报告验收"],
        })
        written.append(entry["package_id"])
    REGISTRY_PATH.write_bytes(pretty_json_bytes(registry))
    print(json.dumps({"written": len(written), "skipped": skipped}, ensure_ascii=False, indent=1))
    return 0 if not skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())

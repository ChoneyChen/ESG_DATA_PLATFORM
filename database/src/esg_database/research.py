"""Typed long-table research exports. Display matrices are never a data authority."""
import csv
from datetime import date
from decimal import Decimal, localcontext
from hashlib import sha256
import io
import json
from zipfile import ZipFile, ZIP_DEFLATED
from esg_contracts.facts import ClauseCoverage
from esg_data.hashing import digest
from esg_data.literals import parse_printed_measure
from esg_data.quantities import standard_unit
from .products_contracts import Observation, ObservationPage, ObservationSource
from .projection import period_facet

# A single column declaration drives CSV metadata and Arrow types.
RESEARCH_VERSION = "capture-research-1.2"
TABLES = {
    "facts": [("observation_id","string"),("source_fact_id","string"),("occurrence_id","string"),("source_id","string"),
        ("kind","string"),("source_row_label","string"),("value_raw","string"),("statement_raw","string"),
        ("unit_raw","string"),("parse_status","string"),("comparator","string"),("value_numeric","decimal"),
        ("value_lower","decimal"),("value_upper","decimal"),("period_kind","string"),("period_raw","string"),
        ("year","int32"),("quarter","int32"),("period_start","date32"),("period_end","date32"),("quality","string")],
    "properties": [("observation_id","string"),("container","string"),("field_key","string"),("state","string"),
        ("raw_text","string"),("value_path","string"),("value_type","string"),("value_text","string")],
    "evidence": [("observation_id","string"),("container","string"),("field_key","string"),("evidence_index","int32"),
        ("source_snapshot","string"),("object_id","string"),("object_type","string"),("page_index","int32"),("role","string"),("excerpt","string"),
        ("row_id","string"),("column_id","string"),("char_start","int32"),("char_end","int32")],
    "coordinates": [("observation_id","string"),("container","string"),("field_key","string"),("evidence_index","int32"),
        ("coordinate_key","string"),("coordinate_value","decimal")],
    "relations": [("observation_id","string"),("source_fact_id","string"),("clause_key","string"),("clause_digest","string"),
        ("title","string"),("relation","string"),("reason","string"),("comparison_eligible","bool")],
    "sources": [("source_id","string"),("document_id","string"),("pdf_sha256","string"),("pdf_id","string"),
        ("title","string"),("report_company","string"),("report_company_id","string"),("publication_year","int32"),
        ("resource_revision","int32"),("industry","string"),("reporting_year","int32")],
    "quantitative": [("observation_id","string"),("value_role","string"),("value_role_raw","string"),
        ("value_role_state","string"),("parse_status","string"),("comparator","string"),("normalization_status","string"),
        ("unit_id","string"),("unit_multiplier","decimal"),("value_numeric","decimal"),
        ("value_lower","decimal"),("value_upper","decimal")],
    "qualitative": [("observation_id","string"),("topic","string"),("subject","string"),("action","string")],
    "coverage": [("source_id","string"),("projection_id","string"),("export_id","string"),
        ("clause_key","string"),("clause_digest","string"),("coverage_state","string"),("fact_count","int32"),
        ("candidate_objects","int32"),("inspected_objects","int32"),("disclosure_state","string"),
        ("statement_raw","string"),("page_index","int32"),("period","string"),("period_raw","string"),("source_row_label","string")],
}


class ResearchCapacityError(ValueError):
    pass


def contract():
    return {"schema_version":RESEARCH_VERSION, "tables":{name:[{"name":key,"type":kind,"nullable":True} for key,kind in fields] for name,fields in TABLES.items()},
        "authority":"Referenced immutable source facts; these tables are deterministic projections.",
        "period_policy":"all_reported", "aggregation_performed":False,
        "facts":"Printed values and statements with their original structured fields. quantitative and qualitative join by observation_id.",
        "quantitative":"One row per quantitative observation. Value role and comparator are independent. Exact literal parsing may recover a previously unparsed measurement without changing facts. Decimal fields in this table use unit_id after exact scaling.",
        "qualitative":"One row per qualitative observation. Topic, subject and action are the components captured during reading and may be absent.",
        "coverage":"Internal projection exports include frozen clause coverage without a period filter, plus explicitly reported disclosure markers matching the filters. Dataset exports include only markers with a selected observation from the same single-source projection, clause and period. Page indexes are zero-based. Coverage state and explicit disclosure state describe separate observations.",
        "properties":"One scalar leaf per RFC6901 value_path. Empty containers retain explicit type.",
        "evidence":"One location per container/field_key/evidence_index. Coordinates are a separate child table.",
        "decimal":"CSV preserves exact decimal strings. Parquet uses an exact column decimal128/256 schema recorded in the manifest; incompatible precision fails explicitly without rounding.",
        "limits":"Capacity exhaustion aborts the whole download; it never returns a truncated completed export."}


def quantitative_row(observation):
    value = observation.value
    role = observation.identity.value_role
    parse_status, comparator, unit_raw = value.parse_status, value.comparator, value.unit_raw
    raw_values = {key: getattr(value, key) for key in ("value_numeric", "value_lower", "value_upper")}
    if parse_status == "unparsed":
        parsed, unit_raw = parse_printed_measure(value.value_raw, unit_raw)
        if parsed:
            parse_status, comparator = "parsed", parsed["comparator"]
            raw_values = {"value_numeric": parsed["value"], "value_lower": parsed["lower"], "value_upper": parsed["upper"]}
    row = {
        "observation_id": observation.observation_id,
        "value_role": role.value if isinstance(role.value, str) else None,
        "value_role_raw": role.raw_text,
        "value_role_state": role.state,
        "parse_status": parse_status,
        "comparator": comparator,
    }
    unit = standard_unit(unit_raw)
    if unit:
        row["unit_id"], multiplier = unit
        row["unit_multiplier"] = format(multiplier, "f")
    if parse_status != "parsed":
        row["normalization_status"] = "value_unparsed"
    elif not unit:
        row["normalization_status"] = "unit_unrecognized" if unit_raw else "unit_unavailable"
    else:
        row["normalization_status"] = "normalized"
        for key, reported in raw_values.items():
            if reported is not None:
                number = Decimal(reported)
                with localcontext() as context:
                    context.prec = len(number.as_tuple().digits) + len(multiplier.as_tuple().digits)
                    row[key] = format(number * multiplier, "f")
    return row


def leaves(value, path=""):
    if isinstance(value, (list,dict)):
        if not value:
            yield path,"empty_array" if isinstance(value,list) else "empty_object",None
        else:
            for key,item in (enumerate(value) if isinstance(value,list) else sorted(value.items())):
                escaped=str(key).replace("~","~0").replace("/","~1")
                yield from leaves(item,path+"/"+escaped)
    else:
        kind="null" if value is None else "bool" if isinstance(value,bool) else "number" if isinstance(value,(int,float)) else "string"
        yield path,kind,None if value is None else ("true" if value else "false") if isinstance(value,bool) else str(value)


def tables(observations, *, coverage=(), period=None, max_observations=100000, max_rows=2000000):
    output={name:[] for name in TABLES}
    sources={}
    def add_source(source):
        body=source.model_dump(mode="json")
        source_id="source-"+digest(body)
        sources[source_id]=body
        return source_id

    def check_rows():
        if sum(map(len,output.values()))+len(sources)>max_rows:
            raise ResearchCapacityError("Export exceeds the declared expanded-row capacity; filter the snapshot")

    def evidence(oid,container,key,locations):
        for index,location in enumerate(locations):
            body=location.model_dump(mode="json")
            output["evidence"].append({"observation_id":oid,"container":container,"field_key":key,"evidence_index":index,
                **{name:body.get(name) for name,_ in TABLES["evidence"] if name not in {"observation_id","container","field_key","evidence_index"}}})
            for name,value in sorted((location.bbox or {}).items()):
                output["coordinates"].append({"observation_id":oid,"container":container,"field_key":key,"evidence_index":index,
                    "coordinate_key":name,"coordinate_value":str(value) if value is not None else None})
    def property_value(oid,container,key,value,state="stated",raw=None):
        for path,kind,text in leaves(value):
            output["properties"].append({"observation_id":oid,"container":container,"field_key":key,"state":state,
                "raw_text":raw,"value_path":path,"value_type":kind,"value_text":text})
    for count,body in enumerate(observations,1):
        if count>max_observations:raise ResearchCapacityError("Export exceeds the declared observation capacity; filter the immutable snapshot")
        row=Observation.model_validate(body);oid=row.observation_id;value=row.value.model_dump(mode="json")
        source_id=add_source(row.source)
        output["facts"].append({"observation_id":oid,"source_fact_id":row.source_fact_id,"occurrence_id":row.occurrence_id,"source_id":source_id,
            **{key:value.get(key) for key in ("kind","value_raw","statement_raw","unit_raw","parse_status","comparator","value_numeric","value_lower","value_upper")},
            "source_row_label":row.source_row_label,"period_kind":row.period.kind,"period_raw":row.period.raw,"year":row.period.year,"quarter":row.period.quarter,
            "period_start":row.period.start.isoformat() if row.period.start else None,"period_end":row.period.end.isoformat() if row.period.end else None,"quality":row.quality})
        if row.value.kind == "quantitative":
            output["quantitative"].append(quantitative_row(row))
        else:
            structure=value.get("structure") or {}
            output["qualitative"].append({"observation_id":oid, **{key:structure.get(key) for key in ("topic","subject","action")}})
        evidence(oid,"fact","",row.evidence)
        for key in type(row.identity).model_fields:
            if key=="dimensions":continue
            field=getattr(row.identity,key)
            property_value(oid,"identity",key,field.value,field.state,field.raw_text);evidence(oid,"identity",key,field.evidence)
        for key,field in row.identity.dimensions.items():
            property_value(oid,"dimension",key,field.value,field.state,field.raw_text);evidence(oid,"dimension",key,field.evidence)
        for key,value in (("flags",row.flags),("comparison_reasons",row.comparison.reasons),("extensions",row.extensions),("value_extensions",row.value.extensions)):
            property_value(oid,"annotation",key,value)
        if row.mapping:
            property_value(oid,"relation","scope_ids",row.mapping.scope_ids)
            property_value(oid,"relation","flags",row.mapping.flags)
            if row.mapping.scope_differences:
                property_value(oid,"relation","scope_differences",[item.model_dump(mode="json") for item in row.mapping.scope_differences])
        output["relations"].append({"observation_id":oid,"source_fact_id":row.source_fact_id,"clause_key":row.clause_key,"clause_digest":row.clause_digest,
            "title":row.title,"relation":row.relation,"reason":row.mapping.reason if row.mapping else "No frozen target relation",
            "comparison_eligible":row.comparison.eligible})
        check_rows()
    for record in coverage:
        clause=ClauseCoverage.model_validate(record["coverage"])
        allowed_periods=record.get("allowed_periods")
        member_scoped=allowed_periods is not None
        allowed={period_facet(value) for value in allowed_periods or ()}
        rows=[]
        if not member_scoped and not period:
            rows.append({"coverage_state":clause.state,"fact_count":clause.fact_count,
                         "candidate_objects":clause.candidate_objects,"inspected_objects":clause.inspected_objects})
        for item in clause.disclosure_observations:
            facet=period_facet(item.period_raw)
            if (period and period != facet) or (member_scoped and (facet == "unknown" or facet not in allowed)):
                continue
            rows.append({"disclosure_state":item.state,"statement_raw":item.statement_raw,"page_index":item.page_index,
                         "period":facet,"period_raw":item.period_raw,"source_row_label":item.source_row_label})
        if rows:
            common={"source_id":add_source(ObservationSource.model_validate(record["source"])),
                    "projection_id":record["projection_id"],"export_id":record["export_id"],
                    "clause_key":clause.clause_key,"clause_digest":clause.clause_digest}
            output["coverage"].extend({**common,**row} for row in rows)
            check_rows()
    output["sources"]=[{"source_id":key,**value} for key,value in sorted(sources.items())]
    return output


def decimal_type(values, pa):
    decimals=[Decimal(str(value)) for value in values if value is not None]
    scale=max((max(-value.as_tuple().exponent,0) for value in decimals),default=0)
    integers=max((max(len(value.as_tuple().digits)+value.as_tuple().exponent,0) for value in decimals),default=0)
    precision=max(1,integers+scale)
    if precision>76:
        raise ResearchCapacityError("Exact values exceed one Parquet decimal256 column; use CSV or narrow the selection. No rounding was performed")
    return (pa.decimal128 if precision<=38 else pa.decimal256)(precision,scale)


def archive(observations, snapshot_id, *, format="csv", max_observations=100000, max_rows=2000000, definitions=None, coverage=(), query=None):
    if format not in {"csv","parquet"}:raise ValueError("Choose csv or parquet")
    if format=="parquet":
        import pyarrow as pa
        import pyarrow.parquet as pq
    data=tables(observations,coverage=coverage,period=(query or {}).get("period"),max_observations=max_observations,max_rows=max_rows)
    result=io.BytesIO();files=[]
    with ZipFile(result,"w",compression=ZIP_DEFLATED) as zipped:
        for name,columns in TABLES.items():
            rows=data[name];schema=None
            if format=="csv":
                text=io.StringIO(newline="");writer=csv.DictWriter(text,fieldnames=[key for key,_ in columns],lineterminator="\n")
                writer.writeheader();writer.writerows(rows);body=text.getvalue().encode("utf-8")
            else:
                fields=[]
                for key,kind in columns:
                    dtype=decimal_type((row.get(key) for row in rows),pa) if kind=="decimal" else {"string":pa.string(),"int32":pa.int32(),"bool":pa.bool_(),"date32":pa.date32()}[kind]
                    fields.append(pa.field(key,dtype,nullable=True))
                schema=pa.schema(fields,metadata={b"contract":RESEARCH_VERSION.encode()})
                converted=[]
                for row in rows:
                    converted.append({key:Decimal(str(value)) if kind=="decimal" and value is not None else date.fromisoformat(value) if kind=="date32" and value is not None else value
                        for key,kind in columns for value in [row.get(key)]})
                sink=io.BytesIO();pq.write_table(pa.Table.from_pylist(converted,schema=schema),sink,compression="zstd");body=sink.getvalue()
            filename=name+"."+format;zipped.writestr(filename,body)
            files.append({"name":filename,"rows":len(rows),"bytes":len(body),"sha256":sha256(body).hexdigest(),
                "columns":[{"name":key,"type":str(schema.field(key).type) if schema is not None else kind} for key,kind in columns]})
        manifest={**contract(),"snapshot_id":snapshot_id,"format":format,"observations":len(data["facts"]),"files":files,
            "query":query or {},
            "capacity":{"max_observations":max_observations,"max_expanded_rows":max_rows},"complete":True}
        if definitions is not None:
            body=(json.dumps(definitions,ensure_ascii=False,sort_keys=True,indent=2)+"\n").encode()
            zipped.writestr("definitions.json",body)
            files.append({"name":"definitions.json","rows":len(definitions),"bytes":len(body),"sha256":sha256(body).hexdigest()})
        zipped.writestr("manifest.json",json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+"\n")
    return result.getvalue()


def observation_stream(bridge, **query):
    cursor=None
    while True:
        page=ObservationPage.model_validate(bridge.call("observation_page",{**query,"limit":500,"cursor":cursor}))
        yield from (row.model_dump(mode="json") for row in page.items)
        cursor=page.next_cursor
        if not cursor:break

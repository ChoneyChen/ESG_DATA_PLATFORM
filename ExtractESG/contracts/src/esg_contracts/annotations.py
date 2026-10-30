"""Canonical, rebuildable annotations projected from authoritative source facts."""

def property_tables(facts):
    properties, evidence = [], []
    for fact in facts:
        fact_id = fact["source_fact_id"]
        for index, location in enumerate(fact["evidence"]):
            evidence.append({"source_fact_id": fact_id, "container": "fact", "field": None, "index": index, **location})
        identity = fact.get("identity", {})
        for container, fields in (("identity", {key: value for key, value in identity.items() if key != "dimensions"}), ("dimension", identity.get("dimensions", {}))):
            for key, field in fields.items():
                if field["state"] == "not_read" and field.get("value") is None:
                    continue
                properties.append({"source_fact_id": fact_id, "container": container, "field": key, **field})
                for index, location in enumerate(field.get("evidence", [])):
                    evidence.append({"source_fact_id": fact_id, "container": container, "field": key, "index": index, **location})
    return properties, evidence

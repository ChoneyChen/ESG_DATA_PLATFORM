"""Two data contracts. Unknown annotations are retained under extensions."""


def extensible_metadata(cls, data, *, forbidden=()):
    if not isinstance(data, dict):
        return data
    if set(data) & set(forbidden):
        raise ValueError("Quantitative and qualitative value fields cannot be mixed")
    result = dict(data)
    annotations = result.get("extensions")
    annotations = (
        dict(annotations)
        if isinstance(annotations, dict)
        else ({"submitted_annotations": annotations} if annotations is not None else {})
    )
    for key in set(result) - set(cls.model_fields):
        value = result.pop(key)
        if key not in annotations:
            annotations[key] = value
        elif annotations[key] != value:
            conflicts = annotations.setdefault("_annotation_conflicts", [])
            if not isinstance(conflicts, list):
                conflicts = [conflicts]
                annotations["_annotation_conflicts"] = conflicts
            conflicts.append({key: value})
    result["extensions"] = annotations
    return result

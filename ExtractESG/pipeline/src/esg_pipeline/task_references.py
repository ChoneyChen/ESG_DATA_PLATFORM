"""One typed view of queue references retained by execution owners.

Older step histories contain task IDs, structured attempts and empty slots.
Reading them must neither invent a queue row nor change the stored owner.
"""

from __future__ import annotations


def task_id(value):
    if isinstance(value, dict):
        value = value.get("task_id")
    return (
        value
        if isinstance(value, str) and value.strip() and len(value) <= 512
        else None
    )


def history_values(value):
    return value if isinstance(value, list) else []


def owner_steps(state, kind="session"):
    """Yield step path, step and owning document without changing any object."""
    if not isinstance(state, dict):
        return
    if kind == "processing":
        steps = state.get("steps")
        if isinstance(steps, dict):
            for name, step in steps.items():
                if isinstance(step, dict):
                    yield f"steps.{name}", step, state
        return
    documents = state.get("documents")
    if not isinstance(documents, list):
        return
    for index, document in enumerate(documents):
        if not isinstance(document, dict):
            continue
        steps = document.get("steps")
        if not isinstance(steps, dict):
            continue
        for name, step in steps.items():
            if isinstance(step, dict):
                yield f"documents[{index}].steps.{name}", step, document


def scan_task_references(state, kind="session"):
    references, invalid = [], []
    if not isinstance(state, dict):
        return {"references": [], "invalid_reference_paths": ["owner"]}
    if kind != "processing" and not isinstance(state.get("documents"), list):
        invalid.append("documents")
    for path, step, _ in owner_steps(state, kind):
        current = step.get("task_id")
        if current is not None:
            identity = task_id(current)
            if not isinstance(current, str) or identity is None:
                invalid.append(path + ".task_id")
            elif identity:
                references.append(
                    {"task_id": identity, "path": path + ".task_id", "kind": "current"}
                )
        history = step.get("history")
        if history is not None and not isinstance(history, list):
            invalid.append(path + ".history")
        for index, value in enumerate(history_values(history)):
            identity = task_id(value)
            if identity:
                references.append(
                    {
                        "task_id": identity,
                        "path": f"{path}.history[{index}]",
                        "kind": "history",
                    }
                )
            elif value is not None and not (
                isinstance(value, dict) and value.get("task_id") is None
            ):
                invalid.append(f"{path}.history[{index}]")
        if step.get("queue_record_deleted") and not (
            current is None
            and step.get("status") in {"completed", "partial"}
            and isinstance(step.get("run_id"), str)
            and step["run_id"].strip()
        ):
            invalid.append(path + ".queue_record_deleted")
    return {
        "references": references,
        "invalid_reference_paths": list(dict.fromkeys(invalid)),
    }


def task_reference_ids(state, kind="session"):
    return list(
        dict.fromkeys(
            row["task_id"] for row in scan_task_references(state, kind)["references"]
        )
    )


def resolve_task_references(store, state, kind="session"):
    scan = scan_task_references(state, kind)
    ids = list(dict.fromkeys(row["task_id"] for row in scan["references"]))
    tasks, missing = [], []
    for identity in ids:
        try:
            tasks.append(store.get(identity))
        except KeyError:
            missing.append(identity)
    return {
        **scan,
        "tasks": tasks,
        "missing_task_ids": missing,
        "reference_integrity": {
            "status": "invalid"
            if scan["invalid_reference_paths"]
            else "missing"
            if missing
            else "complete",
            "expected_task_count": len(ids),
            "resolved_task_count": len(tasks),
            "missing_task_ids": missing,
            "invalid_reference_paths": scan["invalid_reference_paths"],
        },
    }

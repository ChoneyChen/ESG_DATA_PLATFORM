"""Recoverable, journalled quarantine for owned artifacts.

Receipts remain outside moved content. The grace period never deletes anything
automatically. Explicit purge verifies dependencies again; restore verifies
content and all destination conflicts before making any move.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

from .lifecycle import (
    LifecycleBlocked,
    SourceReferenceGuard,
    blocker,
    require_reason,
)


def now():
    return datetime.now(timezone.utc)


class RecoverableTrash:
    def __init__(
        self, root, owned_roots, *, guard=None, retention_seconds=7 * 86400, clock=now
    ):
        self.root = Path(root).resolve()
        self.owned_roots = tuple(Path(path).resolve() for path in owned_roots)
        self.guard = guard
        self.retention_seconds = retention_seconds
        self.clock = clock
        self._lock = threading.RLock()

    def _receipt_path(self, identity):
        if not re.fullmatch(r"trash-[a-f0-9]{32}", identity):
            raise ValueError("无效回收记录标识。")
        return self.root / "receipts" / (identity + ".json")

    def _validate_source(self, path):
        path = Path(path)
        if path.is_symlink():
            raise ValueError("不能回收符号链接。")
        resolved = path.resolve()
        if not any(root in resolved.parents for root in self.owned_roots):
            raise ValueError("路径不属于可管理的来源范围。")
        if path.is_dir() and any(child.is_symlink() for child in path.rglob("*")):
            raise ValueError("来源目录含符号链接，不能安全回收。")
        return resolved

    @staticmethod
    def digest(path):
        digest = hashlib.sha256()
        if path.is_file():
            items = [(".", path)]
        else:
            items = [
                (p.relative_to(path).as_posix(), p)
                for p in sorted(path.rglob("*"))
                if p.is_file()
            ]
        for relative, file in items:
            digest.update(relative.encode())
            digest.update(b"\0")
            with file.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            digest.update(b"\0")
        return digest.hexdigest()

    def _save(self, receipt):
        path = self._receipt_path(receipt["cleanup_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)

    def _audit(self, identity, action, reason):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / "audit.jsonl").open("a", encoding="utf-8") as output:
            output.write(
                json.dumps(
                    {
                        "cleanup_id": identity,
                        "action": action,
                        "reason": reason,
                        "at": self.clock().isoformat(),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    def get(self, identity):
        return json.loads(self._receipt_path(identity).read_text(encoding="utf-8"))

    def list(self):
        rows = []
        for path in (self.root / "receipts").glob("trash-*.json"):
            try:
                rows.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(rows, key=lambda row: row["created_at"], reverse=True)

    def quarantine(self, paths, *, scope, reason, metadata=None):
        reason = require_reason(reason)
        with self._lock:
            sources = [
                self._validate_source(path) for path in paths if Path(path).exists()
            ]
            if len(set(sources)) != len(sources) or any(
                a in b.parents for a in sources for b in sources if a != b
            ):
                raise ValueError("回收路径重复或互相包含。")
            reservation = None
            identity = "trash-" + uuid4().hex
            reservation_identity = (
                str(UUID(identity.removeprefix("trash-")))
                if isinstance(self.guard, SourceReferenceGuard)
                else None
            )
            content = self.root / "content" / identity
            receipt = {
                "schema_version": "recoverable-trash-v1",
                "cleanup_id": identity,
                "state": "reserving",
                "created_at": self.clock().isoformat(),
                "purge_after": (
                    self.clock() + timedelta(seconds=self.retention_seconds)
                ).isoformat(),
                "reason": reason,
                "scope": scope,
                "metadata": metadata or {},
                "reservation_id": reservation_identity,
                "entries": [
                    {
                        "source": str(path),
                        "trash": str(content / str(index)),
                        "sha256": self.digest(path),
                    }
                    for index, path in enumerate(sources)
                ],
            }
            moved = []
            try:
                # Persist the intended lease identity before any remote call;
                # even a lost response has a discoverable recovery receipt.
                self._save(receipt)
                if reservation_identity:
                    reservation = self.guard.reserve(
                        scope, reason, reservation_id=reservation_identity
                    )
                else:
                    reservation = (
                        self.guard.reserve(scope, reason) if self.guard else None
                    )
                receipt["reservation_id"] = reservation
                receipt["state"] = "moving"
                self._save(receipt)
                content.mkdir(parents=True, exist_ok=False)
                for entry in receipt["entries"]:
                    destination = Path(entry["trash"])
                    shutil.move(entry["source"], destination)
                    moved.append(entry)
                receipt["state"] = "quarantined"
                self._save(receipt)
                if reservation:
                    self.guard.update_reservation(reservation, "quarantined", reason)
                self._audit(identity, "quarantine", reason)
            except Exception as exc:
                for entry in reversed(moved):
                    source, destination = Path(entry["source"]), Path(entry["trash"])
                    if destination.exists() and not source.exists():
                        source.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(destination, source)
                receipt["state"] = (
                    "reservation_pending"
                    if reservation_identity and getattr(exc, "uncertain", False)
                    else "rolled_back"
                )
                if not reservation and receipt["state"] == "rolled_back":
                    receipt["reservation_id"] = None
                if reservation:
                    try:
                        self.guard.update_reservation(
                            reservation, "released", "回收未完成，来源已回滚。"
                        )
                    except Exception:
                        # Original files are back; keep an actionable journal
                        # until the publication reservation is released.
                        receipt["state"] = "rollback_pending"
                self._save(receipt)
                shutil.rmtree(content, ignore_errors=True)
                raise
            return receipt

    def restore(self, identity, *, reason):
        reason = require_reason(reason)
        with self._lock:
            receipt = self.get(identity)
            if receipt["state"] == "restored":
                return receipt
            if receipt["state"] not in {
                "quarantined",
                "moving",
                "restoring",
                "rollback_pending",
                "reserving",
                "reservation_pending",
            }:
                raise ValueError("此回收记录不能恢复。")
            original_state = receipt["state"]
            for entry in receipt["entries"]:
                source, destination = (
                    self._validate_source(entry["source"]),
                    Path(entry["trash"]),
                )
                if (
                    destination.resolve().parent
                    != (self.root / "content" / identity).resolve()
                ):
                    raise ValueError("回收清单路径越界。")
                if source.exists() and destination.exists():
                    raise LifecycleBlocked(
                        [
                            blocker(
                                "restore_path_conflict",
                                "原路径已有新文件，恢复不会覆盖。",
                                path=str(source),
                            )
                        ]
                    )
                candidate = destination if destination.exists() else source
                if not candidate.exists() or self.digest(candidate) != entry["sha256"]:
                    raise LifecycleBlocked(
                        [
                            blocker(
                                "trash_content_changed",
                                "回收内容缺失或被修改，恢复已停止。",
                            )
                        ]
                    )
            receipt["state"] = "restoring"
            self._save(receipt)
            moved = []
            try:
                for entry in receipt["entries"]:
                    source, destination = Path(entry["source"]), Path(entry["trash"])
                    if destination.exists():
                        source.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(destination, source)
                        moved.append(entry)
                if receipt.get("reservation_id"):
                    self.guard.update_reservation(
                        receipt["reservation_id"], "released", reason
                    )
            except Exception:
                for entry in reversed(moved):
                    shutil.move(entry["source"], entry["trash"])
                receipt["state"] = original_state
                self._save(receipt)
                raise
            receipt.update(state="restored", restored_at=self.clock().isoformat())
            self._save(receipt)
            self._audit(identity, "restore", reason)
            shutil.rmtree(self.root / "content" / identity, ignore_errors=True)
            return receipt

    def purge(self, identity, *, reason):
        reason = require_reason(reason)
        with self._lock:
            receipt = self.get(identity)
            if receipt["state"] == "purged":
                return receipt
            if receipt["state"] != "quarantined":
                raise ValueError("只有回收区中的记录可彻底清除。")
            if self.clock() < datetime.fromisoformat(receipt["purge_after"]):
                raise LifecycleBlocked(
                    [
                        blocker(
                            "retention_period",
                            "回收保留期尚未结束。",
                            purge_after=receipt["purge_after"],
                        )
                    ]
                )
            if self.guard:
                references = self.guard.check(receipt["scope"])
                if not references.get("verified") or references.get("blockers"):
                    raise LifecycleBlocked(
                        references.get("blockers")
                        or [blocker("reference_check_unavailable", "来源引用未确认。")]
                    )
                # Keep a durable tombstone blocking future import of purged bundles.
                self.guard.update_reservation(
                    receipt["reservation_id"], "purged", reason
                )
            content = self.root / "content" / identity
            if content.exists():
                shutil.rmtree(content)
            receipt.update(state="purged", purged_at=self.clock().isoformat())
            self._save(receipt)
            self._audit(identity, "purge", reason)
            return receipt

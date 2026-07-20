from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Generic, TypeVar

from pydantic import BaseModel

from esg_v2.storage.package_layout import package_dir, require_package_dir_name


StateT = TypeVar("StateT", bound=BaseModel)


class JobStore(Generic[StateT]):
    """Small local state store for development runs.

    This is not the report registry. It only tracks OCR runs launched by this
    backend so the frontend can display progress.
    """

    def __init__(self, state_root: Path, state_model: type[StateT]):
        self.state_root = state_root
        self.state_model = state_model
        self._states: dict[str, StateT] = {}
        self._lock = Lock()

    def put(self, state: StateT) -> None:
        require_package_dir_name(state.run_id)
        with self._lock:
            self._states[state.run_id] = state
        self._write_state(state)

    def create(self, state: StateT) -> None:
        require_package_dir_name(state.run_id)
        state_path = package_dir(self.state_root, state.run_id) / "state.json"
        with self._lock:
            if state.run_id in self._states or state_path.exists():
                raise FileExistsError(f"Job state already exists: {state.run_id}")
            self._write_state(state)
            self._states[state.run_id] = state

    def get(self, run_id: str) -> StateT | None:
        try:
            state_dir = package_dir(self.state_root, run_id)
        except ValueError:
            return None
        with self._lock:
            cached = self._states.get(run_id)
        if cached:
            return cached
        state_path = state_dir / "state.json"
        if not state_path.exists():
            return None
        data = json.loads(state_path.read_text(encoding="utf-8"))
        state = self.state_model(**data)
        with self._lock:
            self._states[run_id] = state
        return state

    def list(self) -> list[StateT]:
        states: dict[str, StateT] = {}
        if self.state_root.exists():
            for path in self.state_root.glob("*/state.json"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    state = self.state_model(**data)
                    states[state.run_id] = state
                except Exception:
                    continue
        with self._lock:
            states.update(self._states)
        return sorted(states.values(), key=lambda item: item.run_id, reverse=True)

    def _write_state(self, state: StateT) -> None:
        path = package_dir(self.state_root, state.run_id) / "state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(state.model_dump_json(indent=2), encoding="utf-8")

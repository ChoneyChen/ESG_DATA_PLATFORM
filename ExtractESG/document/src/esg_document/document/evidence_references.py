"""Resolve registered source identities without promoting context into point proof."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Iterable

from esg_document.document.contracts import ArtifactRef, DocumentIR


VISUAL_ARTIFACT_KINDS = frozenset(
    {"page_image", "spread_image", "region_crop", "ocr_image", "figure_image"}
)


class ReviewEvidenceReferences:
    def __init__(self, document: DocumentIR) -> None:
        self.document = document
        self._artifacts: dict[str, ArtifactRef | None] = {}
        for artifact in document.artifacts:
            aliases = {artifact.artifact_id, artifact.path, artifact.remote_uri}
            # A generated IR package can expose its immutable package-relative alias.
            parts = PurePosixPath(artifact.path).parts
            if document.metadata.run_id in parts:
                tail = parts[parts.index(document.metadata.run_id) + 1 :]
                if tail and tail[0] == "artifacts":
                    aliases.add(str(PurePosixPath(*tail)))
            for alias in aliases - {None, ""}:
                if alias in self._artifacts and self._artifacts[alias] != artifact:
                    self._artifacts[alias] = (
                        None  # An ambiguous alias never grants evidence.
                    )
                else:
                    self._artifacts[alias] = artifact
        self.figures = {f.figure_id: f for f in document.figures}

    def artifact(self, reference: str) -> ArtifactRef | None:
        return self._artifacts.get(reference)

    def registered_visual(self, references: Iterable[str]) -> list[str]:
        """Automatic context uses only genuinely registered visual artifacts."""
        result = []
        for reference in references:
            artifact = self.artifact(reference)
            if artifact is None and reference in self.figures:
                artifact = self.artifact(self.figures[reference].crop_artifact_id or "")
            if artifact is not None and artifact.kind in VISUAL_ARTIFACT_KINDS:
                result.append(artifact.path)
        return list(dict.fromkeys(result))

    def declared_visual(self, references: Iterable[str]) -> list[str]:
        """Resolve trusted aliases; preserve an invalid explicit claim for the guard."""
        result = []
        for reference in references:
            resolved = self.registered_visual([reference])
            result.extend(resolved or [reference])
        return list(dict.fromkeys(result))

    def tracked_general(self, proposed: list[str], fallback: list[str]) -> list[str]:
        """Keep object/text citations distinct from registered image context.

        Unknown explicit citations are retained as claims, never silently laundered.
        Fallback context does not replace the original claim or create point citations.
        """
        result = []
        for reference in proposed:
            artifact = self.artifact(reference)
            result.append(artifact.path if artifact else reference)
        result.extend(
            artifact.path
            for ref in fallback
            if (artifact := self.artifact(ref)) is not None
        )
        return list(dict.fromkeys(result or fallback))

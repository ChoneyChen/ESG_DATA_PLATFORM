"""Literal category matching shared by clause attributes and organized values."""
from typing import Literal
import re, unicodedata
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

class VocabularyEntry(Model):
    code: str = Field(min_length=1)
    label: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)


class CategoryMatch(Model):
    state: Literal["recognized", "unlisted", "unproven", "conflict"]
    code: str | None = None
    label: str | None = None
    definition_source: Literal["clause_vocabulary"] = "clause_vocabulary"

    @model_validator(mode="after")
    def verified_code_only(self):
        if self.state == "recognized" and (not self.code or not self.label):
            raise ValueError("A recognized category needs its declared code and label")
        if self.state != "recognized" and (
            self.code is not None or self.label is not None
        ):
            raise ValueError("Unverified categories cannot advertise an accepted code")
        return self


def _normalized(text):
    return unicodedata.normalize("NFKC", str(text)).casefold().strip()


def _literal_match(alias, text):
    alias = _normalized(alias)
    text = _normalized(text)
    if not alias:
        return False
    if alias.isascii() and re.fullmatch(r"[a-z0-9]+", alias):
        return bool(
            re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", text)
        )
    return alias in text


def category_match(vocabulary, reported):
    if (
        not vocabulary
        or not reported
        or reported.get("state") not in {"stated", "inherited"}
    ):
        return None
    value = reported.get("value")
    raw = reported.get("raw_text") or ""
    entries = [
        item
        for item in vocabulary
        if any(
            _normalized(alias) == _normalized(value)
            for alias in [item.code, item.label, *item.aliases]
        )
    ]
    if len(entries) != 1:
        return CategoryMatch(state="unlisted" if not entries else "unproven")
    entry = entries[0]
    own = any(
        _literal_match(alias, raw)
        for alias in [entry.code, entry.label, *entry.aliases]
    )
    other = any(
        _literal_match(alias, raw)
        for item in vocabulary
        if item.code != entry.code
        for alias in [item.code, item.label, *item.aliases]
    )
    if own and not other:
        return CategoryMatch(state="recognized", code=entry.code, label=entry.label)
    return CategoryMatch(state="conflict" if other and not own else "unproven")

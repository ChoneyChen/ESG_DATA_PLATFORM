"""Bounded physical reading frames; this policy contains no ESG eligibility rules."""
from __future__ import annotations

import json
import hashlib
from copy import deepcopy
from esg_targeted.models.task_context import compact_task_context

SOURCE_FRAME_VERSION = "physical-source-frame-v2"
TEXT_FRAME_VERSION = "source-text-frame-v1"
TEXT_WINDOW_CHARS = 600
TEXT_WINDOW_OVERLAP = 80
MAX_FRAME_ROWS = 4
MAX_FRAME_CHARS = 8000
MAX_FRAME_ATTEMPTS = 3
MAX_FRAME_CELLS = 32


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def text_channels(row):
    """Use supplied original spans; never invent an IR object/reference."""
    channels = []
    for index, segment in enumerate(row.get("support_segments", [])):
        if not segment.get("text") or not (segment.get("span_id") or segment.get("ref")):
            continue
        key = str(segment.get("span_id") or segment["ref"])
        if any(item["key"] == key and item["text"] == segment["text"] for item in channels):
            continue
        channels.append({"key": key, "text": segment["text"], "segment": segment})
    text = str(row.get("text") or "")
    if text and not any(text in item["text"] for item in channels):
        # Row text without a span remains a reading channel, never a citation.
        # A real row span is supplied by the object compiler when available.
        channels.append({"key": "row_text", "text": text, "segment": {
            **({"span_id": row["span_id"]} if row.get("span_id") else {}),
            "text": text, "locators": row.get("locators", [])}})
    # A real reference cannot identify two different original texts. Keep both
    # hashes in the ledger, but never offer either as citable text.
    conflicting = {item["key"] for item in channels if len({other["text"] for other in channels if other["key"] == item["key"]}) > 1}
    for item in channels:
        if item["key"] in conflicting:
            item["key"] = "ambiguous:" + _digest(item["key"] + "\0" + item["text"])
            item["ambiguous_reference"] = True
    return channels


def needs_text_frames(row):
    return (len(str(row.get("text") or "")) > 1600 or len(row.get("support_segments", [])) > 8 or
            any(item.get("ambiguous_reference") or len(item["text"]) > TEXT_WINDOW_CHARS for item in text_channels(row)))


def merge_intervals(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        elif end > start:
            merged.append([start, end])
    return merged


def _first_uncovered(length, intervals):
    position = 0
    for start, end in merge_intervals(intervals):
        if start > position:
            break
        position = max(position, end)
    return position if position < length else None


def _saved_intervals(intervals, length):
    return merge_intervals([list(pair) for pair in intervals if isinstance(pair, (list, tuple)) and len(pair) == 2 and
        all(type(value) is int for value in pair) and 0 <= pair[0] < pair[1] <= length])


def text_state(row, saved=None):
    saved = saved or {}
    channels = {}
    for item in text_channels(row):
        digest = _digest(item["text"])
        prior = saved.get("channels", {}).get(item["key"], {})
        compatible = saved.get("version") == TEXT_FRAME_VERSION and prior.get("text_sha256") == digest and prior.get("length") == len(item["text"])
        channels[item["key"]] = {"source_ref": item["segment"].get("span_id") or item["segment"].get("ref"),
            "text_sha256": digest, "length": len(item["text"]),
            "covered": _saved_intervals(prior.get("covered", []), len(item["text"])) if compatible else [],
            "blocked": _saved_intervals(prior.get("blocked", []), len(item["text"])) if compatible else [],
            "ambiguous_reference": bool(item.get("ambiguous_reference"))}
    cells_compatible = saved.get("version") == TEXT_FRAME_VERSION and set(channels) == set(saved.get("channels", {})) and all(
        channel["text_sha256"] == saved["channels"][key].get("text_sha256") for key, channel in channels.items())
    return {"version": TEXT_FRAME_VERSION, "channels": channels,
            "presented_cell_ids": list(saved.get("presented_cell_ids", [])) if cells_compatible else [],
            "structure_seen_cell_ids": list(saved.get("structure_seen_cell_ids", [])) if cells_compatible else []}


def text_complete(state):
    return bool(state.get("channels")) and all(item.get("source_ref") and not item.get("ambiguous_reference") and
        _first_uncovered(item["length"], item["covered"]) is None for item in state["channels"].values())


def _positioned_cell(cell, item):
    a, b = cell.get("char_start"), cell.get("char_end")
    return (cell.get("span_id") == (item["segment"].get("span_id") or item["segment"].get("ref")) and
        type(a) is int and type(b) is int and 0 <= a < b <= len(item["text"]) and
        bool(str(cell.get("raw") or "").strip()) and str(cell["raw"]).strip() == item["text"][a:b].strip())


def _text_window(row, item, channel, state, start, end):
    visible = item["text"][start:end]
    presented = set(state["presented_cell_ids"]) | set(state["structure_seen_cell_ids"])
    cells = [cell for cell in row.get("cells", []) if cell.get("cell_id") not in presented and
        _positioned_cell(cell, item) and start <= cell["char_start"] < cell["char_end"] <= end][:MAX_FRAME_CELLS]
    frame = {"version": TEXT_FRAME_VERSION, "channel_key": item["key"], "source_ref": channel["source_ref"],
        "start": start, "end": end, "total_length": channel["length"], "text_sha256": channel["text_sha256"],
        "window_sha256": _digest(visible), "cell_ids": [cell.get("cell_id") for cell in cells]}
    frame["frame_id"] = _digest(json.dumps(frame, sort_keys=True))
    segment = {**deepcopy(item["segment"]), "text": visible, "original_text": item["text"],
               "source_text_frame": frame, "truncated": False}
    return {**row, "text": visible, "support_segments": [segment] if channel["source_ref"] else [],
            "cells": cells, "source_text_frame": frame}


def next_text_frame(row, state=None):
    state = text_state(row, state)
    channels = text_channels(row)
    for item in channels:
        channel = state["channels"][item["key"]]
        if channel.get("ambiguous_reference"):
            continue
        unread = _first_uncovered(channel["length"], [*channel["covered"], *channel["blocked"]])
        if unread is None:
            continue
        start = max(0, unread - TEXT_WINDOW_OVERLAP)
        end = min(channel["length"], unread + TEXT_WINDOW_CHARS - min(TEXT_WINDOW_OVERLAP, unread))
        return _text_window(row, item, channel, state, start, end)
    # Text may be fully displayed while a dense window still has unread cells.
    # Continue that real source window with the next bounded physical cell set.
    # Failed character ranges remain blocked, never an unlimited retry loop.
    for item in channels:
        channel = state["channels"][item["key"]]
        if channel.get("ambiguous_reference"):
            continue
        for cell in row.get("cells", []):
            if cell.get("cell_id") in set(state["presented_cell_ids"]) | set(state["structure_seen_cell_ids"]) or not _positioned_cell(cell, item):
                continue
            a, b = cell["char_start"], cell["char_end"]
            if b - a > TEXT_WINDOW_CHARS or any(a < end and b > start for start, end in channel["blocked"]):
                continue
            start = max(0, a - min(TEXT_WINDOW_OVERLAP, TEXT_WINDOW_CHARS - (b - a)))
            end = min(channel["length"], start + TEXT_WINDOW_CHARS)
            return _text_window(row, item, channel, state, start, end)
    return None


def verify_text_frame(row, window):
    frame = window.get("source_text_frame", {})
    item = next((item for item in text_channels(row) if item["key"] == frame.get("channel_key")), None)
    if not item or item.get("ambiguous_reference") or frame.get("version") != TEXT_FRAME_VERSION:
        return False
    start, end = frame.get("start"), frame.get("end")
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(item["text"]):
        return False
    original_cells = {cell.get("cell_id"): cell for cell in row.get("cells", [])}
    valid_cells = (len(window.get("cells", [])) <= MAX_FRAME_CELLS and
        frame.get("cell_ids") == [cell.get("cell_id") for cell in window.get("cells", [])] and
        all(cell == original_cells.get(cell.get("cell_id")) and _positioned_cell(cell, item) and
            start <= cell["char_start"] < cell["char_end"] <= end for cell in window.get("cells", [])))
    return (valid_cells and
        frame.get("text_sha256") == _digest(item["text"]) and frame.get("total_length") == len(item["text"]) and
        frame.get("source_ref") == (item["segment"].get("span_id") or item["segment"].get("ref")) and
        frame.get("frame_id") == _digest(json.dumps({key: value for key, value in frame.items() if key != "frame_id"}, sort_keys=True)) and
        window.get("text") == item["text"][start:end] and frame.get("window_sha256") == _digest(window["text"]))


def record_text_frame(state, frame, *, success):
    channel = state["channels"][frame["channel_key"]]
    key = "covered" if success else "blocked"
    channel[key] = merge_intervals([*channel[key], [frame["start"], frame["end"]]])


def source_frames(rows, *, max_rows=MAX_FRAME_ROWS, max_chars=MAX_FRAME_CHARS):
    """Preserve physical row order, image boundaries and every source row.

    Large individual rows stay a named physical grain; their provider view is
    bounded separately. No row is silently removed from the coverage universe.
    """
    frames = []
    sizes = {}
    grains = []
    for row in rows:
        cells = row.get("cells", [])
        grains.extend([{**row, "cells": cells[index:index + MAX_FRAME_CELLS]} for index in range(0, len(cells), MAX_FRAME_CELLS)] if len(cells) > MAX_FRAME_CELLS else [row])
    for row in grains:
        # Locators, storage candidates and repeated audit payloads do not enter
        # the provider reading view. Counting them would turn a small 43-row
        # table into 43 frames and repeatedly resend the same grammar/context.
        size = len(json.dumps(compact_task_context({"kind": "physical", "rows": [row]})["rows"][0], ensure_ascii=False))
        sizes[id(row)] = size
        image = row.get("image_path")
        if (not frames or len(frames[-1]) >= max_rows or
            frames[-1][0].get("image_path") != image or
            any(item["group_id"] == row["group_id"] for item in frames[-1]) or
            sum(sizes[id(item)] for item in frames[-1]) + size > max_chars):
            frames.append([])
        frames[-1].append(row)
    return frames


def pending_read_groups(obj, source=None):
    source = source or {}
    done = set(source.get("processed_groups", []))
    blocked = set(source.get("blocked_read_groups", []))
    return [row["group_id"] for row in obj["rows"] if row["group_id"] not in done | blocked and
        (not needs_text_frames(row) or next_text_frame(row, source.get("text_coverage", {}).get(row["group_id"])) is not None)]


def reading_frontier_fingerprint(obj, source=None):
    source = source or {}
    return [(row["group_id"], (next_text_frame(row, source.get("text_coverage", {}).get(row["group_id"])) or {}).get("source_text_frame", {}).get("frame_id"))
            for row in obj["rows"] if row["group_id"] in pending_read_groups(obj, source)]


def mapping_cards(cards):
    """Keep scientific measurement meaning while removing duplicate prose.

    Quantity dimension, role and normalization are not presentation details:
    an amount of material cannot become water volume or concentration when a
    compact provider view is compiled. Definitions and distinctions already
    present on the card need not be serialized a second time.
    """
    fields = ("template", "value_role", "unit_dimension", "canonical_unit_id",
              "normalization", "aggregation", "semantic_type_note")
    output = []
    for card in cards:
        current = {key: value for key, value in card.items() if key != "measurement_contract"}
        contract = card.get("measurement_contract")
        if contract:
            current["measurement_contract"] = {key: contract[key] for key in fields if contract.get(key) is not None}
        output.append(current)
    return output

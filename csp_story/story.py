"""Collect text objects from a comic (.cmc + page .clip files) and write edits back."""
from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass

from .clipfile import ClipFile
from .textattrs import (UnsupportedText, decode_text, encode_text, join_lp,
                        rewrite_attributes, split_lp)


@dataclass
class TextObject:
    page: int          # 1-based page number in the comic
    clip: str          # path to the .clip file
    layer_id: int      # Layer.MainId
    index: int         # 0 = TextLayerString, n = TextLayerStringArray[n-1]
    layer_name: str
    text: str
    hidden: bool = False  # layer, or a folder containing it, is hidden

    @property
    def marker(self) -> str:
        return f"[[{self.key} hidden]]" if self.hidden else f"[[{self.key}]]"

    @property
    def key(self) -> str:
        return f"{self.page}:{self.layer_id}:{self.index}"


def resolve_pages(path: str) -> list[tuple[int, str]]:
    """Return [(page_number, clip_path)] for a .cmc, a .clip, or a folder containing a .cmc."""
    if not os.path.exists(path):
        raise SystemExit(f"not found: {path}")
    if os.path.isdir(path):
        cmcs = [f for f in os.listdir(path) if f.lower().endswith(".cmc")]
        if len(cmcs) != 1:
            raise SystemExit(f"expected exactly one .cmc in {path}, found {len(cmcs)}")
        path = os.path.join(path, cmcs[0])
    if path.lower().endswith(".clip"):
        return [(1, path)]
    if not path.lower().endswith(".cmc"):
        raise SystemExit("input must be a .cmc, a .clip, or a folder containing a .cmc")

    base = os.path.dirname(os.path.abspath(path))
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        nodes = {r[0]: r[1:] for r in con.execute(
            "select MainId, Type, NextIndex, FirstChildIndex, LinkPath from CanvasNode")}
        root, = con.execute("select ProjectRootCanvasNode from Project").fetchone()
    finally:
        con.close()

    pages: list[str] = []

    def walk(node_id: int) -> None:
        while node_id:
            _type, nxt, child, link = nodes[node_id]
            if link:
                pages.append(os.path.join(base, link.split(":", 1)[-1]))
            if child:
                walk(child)
            node_id = nxt

    walk(nodes[root][2])
    return list(enumerate(pages, start=1))


def _layer_rows(con: sqlite3.Connection):
    cols = {r[1] for r in con.execute("pragma table_info(Layer)")}
    if "TextLayerString" not in cols:  # page has never had a text layer
        return []
    return con.execute(
        "select MainId, LayerName, TextLayerString, TextLayerStringArray "
        "from Layer where TextLayerString is not null order by _PW_ID")


def _layer_tree(con: sqlite3.Connection) -> tuple[dict[int, int], set[int]]:
    """Return ({layer_id: order}, {effectively hidden layer ids}).

    Order follows the layer tree with siblings bottom→top, which matches creation
    order, usually the order bubbles were written in. A layer counts as hidden
    if it or any enclosing folder has its visibility turned off.
    """
    rows = {r[0]: (r[1], r[2], r[3]) for r in con.execute(
        "select MainId, LayerNextIndex, LayerFirstChildIndex, LayerVisibility from Layer")}
    root, = con.execute("select CanvasRootFolder from Canvas").fetchone()
    order: list[int] = []
    hidden: set[int] = set()
    seen: set[int] = set()

    def walk(i: int, parent_hidden: bool) -> None:
        while i and i in rows and i not in seen:
            seen.add(i)
            nxt, child, vis = rows[i]
            is_hidden = parent_hidden or not (vis or 0) & 1
            order.append(i)
            if is_hidden:
                hidden.add(i)
            walk(child, is_hidden)
            i = nxt

    walk(rows.get(root, (0, 0, 1))[1], False)
    return {mid: n for n, mid in enumerate(order)}, hidden


def collect(path: str) -> list[TextObject]:
    objs: list[TextObject] = []
    for page, clip_path in resolve_pages(path):
        if not os.path.exists(clip_path):
            continue
        clip = ClipFile(clip_path)
        with clip.database() as con:
            order, hidden = _layer_tree(con)
            rows = sorted(_layer_rows(con), key=lambda r: order.get(r[0], 1 << 30))
            for mid, name, s, arr in rows:
                texts = [s] + split_lp(arr)
                for idx, raw in enumerate(texts):
                    objs.append(TextObject(page, clip_path, mid, idx, name or "",
                                           decode_text(raw), mid in hidden))
    return objs


def _auto_name(text: str) -> str:
    return text.replace("\n", " ")


@dataclass
class Change:
    obj: TextObject
    new_text: str


def apply_changes(changes: list[Change], *, dry_run: bool = False,
                  backup: bool = True) -> tuple[list[str], set[int]]:
    """Write changed text into the .clip files. Returns (log lines, updated page numbers)."""
    log: list[str] = []
    pages: set[int] = set()
    by_clip: dict[str, list[Change]] = {}
    for ch in changes:
        by_clip.setdefault(ch.obj.clip, []).append(ch)

    for clip_path, clip_changes in by_clip.items():
        clip = ClipFile(clip_path)
        applied = 0
        with clip.database(writable=not dry_run) as con:
            for ch in clip_changes:
                o = ch.obj
                row = con.execute(
                    "select LayerName, TextLayerString, TextLayerAttributes, "
                    "TextLayerStringArray, TextLayerAttributesArray from Layer where MainId=?",
                    (o.layer_id,)).fetchone()
                if row is None:
                    log.append(f"SKIP {o.key}: layer no longer exists")
                    continue
                name, s, attrs, s_arr, a_arr = row
                strings = [s] + split_lp(s_arr)
                attr_list = [attrs] + split_lp(a_arr)
                if o.index >= len(strings) or len(strings) != len(attr_list):
                    log.append(f"SKIP {o.key}: text object layout changed")
                    continue
                current = decode_text(strings[o.index])
                if current != o.text:
                    log.append(f"SKIP {o.key}: text was edited in Clip Studio since export")
                    continue
                try:
                    new_attrs = rewrite_attributes(attr_list[o.index], current, ch.new_text)
                except UnsupportedText as e:
                    log.append(f"SKIP {o.key}: {e}")
                    continue
                strings[o.index] = encode_text(ch.new_text)
                attr_list[o.index] = new_attrs
                if o.index == 0:
                    new_name = _auto_name(ch.new_text) if name == _auto_name(current) else name
                    con.execute(
                        "update Layer set TextLayerString=?, TextLayerAttributes=?, LayerName=? "
                        "where MainId=?", (strings[0], attr_list[0], new_name, o.layer_id))
                else:
                    con.execute(
                        "update Layer set TextLayerStringArray=?, TextLayerAttributesArray=? "
                        "where MainId=?", (join_lp(strings[1:]), join_lp(attr_list[1:]), o.layer_id))
                applied += 1
                pages.add(o.page)
                log.append(f"{'WOULD ' if dry_run else ''}UPDATE {o.key}: {o.text!r} -> {ch.new_text!r}")

        if applied and not dry_run:
            if backup:
                bak = clip_path + ".bak"
                n = 1
                while os.path.exists(bak):
                    n += 1
                    bak = f"{clip_path}.bak{n}"
                os.replace(clip_path, bak)
                log.append(f"backup: {bak}")
            clip.save(clip_path)
    return log, pages


# --- text document format -------------------------------------------------

MARKER = re.compile(r"^\s*\[\[(\d+):(\d+):(\d+)(?:\s+hidden)?\]\]\s*$", re.IGNORECASE)
HEADER = (
    "# CSP story export. Edit the text freely, but keep every [[page:layer:n]] line\n"
    "# intact and in place. Lines starting with # at the top are ignored.\n"
    "# [[... hidden]] marks text on a hidden layer (greyed out in the Story Editor).\n"
)


def to_document(objs: list[TextObject]) -> str:
    parts = [HEADER]
    last_page = None
    for o in objs:
        if o.page != last_page:
            parts.append(f"\n# ---- Page {o.page} ----\n")
            last_page = o.page
        parts.append(f"\n{o.marker}\n{o.text}\n")
    return "".join(parts)


def from_document(doc: str) -> dict[str, str]:
    """Parse an edited document into {key: text}."""
    doc = doc.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    blocks: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in doc.split("\n"):
        m = MARKER.match(line)
        if m:
            key = ":".join(str(int(g)) for g in m.groups())
            if key in blocks:
                raise SystemExit(f"duplicate marker [[{key}]] in document")
            current = blocks[key] = []
        elif current is not None:
            current.append(line)
    out = {}
    for key, lines in blocks.items():
        # Drop page-separator comments and trailing blank lines between blocks.
        while lines and (not lines[-1].strip() or lines[-1].startswith("# ---- Page")):
            lines.pop()
        out[key] = "\n".join(lines)
    return out

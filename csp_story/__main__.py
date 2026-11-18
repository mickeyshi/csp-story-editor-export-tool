"""CLI: python -m csp_story export|import ..."""
from __future__ import annotations

import argparse
import json
import os
import sys

from .story import Change, TextObject, apply_changes, collect, from_document, to_document


def _snapshot_path(doc_path: str) -> str:
    return doc_path + ".snapshot.json"


def cmd_export(args) -> int:
    objs = collect(args.comic)
    with open(args.output, "w", encoding="utf-8", newline="\n") as f:
        f.write(to_document(objs))
    with open(_snapshot_path(args.output), "w", encoding="utf-8") as f:
        json.dump([o.__dict__ for o in objs], f, ensure_ascii=False, indent=1)
    print(f"exported {len(objs)} text object(s) to {args.output}")
    return 0


def cmd_import(args) -> int:
    snap = _snapshot_path(args.document)
    if not os.path.exists(snap):
        print(f"missing {snap}; run export first", file=sys.stderr)
        return 1
    with open(snap, encoding="utf-8") as f:
        objs = {o.key: o for o in (TextObject(**d) for d in json.load(f))}
    with open(args.document, encoding="utf-8-sig") as f:
        edited = from_document(f.read())

    missing = sorted(set(objs) - set(edited))
    unknown = sorted(set(edited) - set(objs))
    for k in missing:
        print(f"WARN [[{k}]] missing from document; left unchanged")
    for k in unknown:
        print(f"WARN [[{k}]] not in snapshot; ignored")

    changes = [Change(objs[k], t) for k, t in edited.items() if k in objs and t != objs[k].text]
    if not changes:
        print("no changes")
        return 0
    log, pages = apply_changes(changes, dry_run=args.dry_run, backup=not args.no_backup)
    for line in log:
        print(line)
    if pages and not args.dry_run:
        print("\nClip Studio shows cached renders until a text layer is touched. Refresh "
              "these pages after opening: " + ", ".join(map(str, sorted(pages))))
    return 0


def cmd_gui(args) -> int:
    from .gui import run_gui  # imported lazily so the CLI works without Tk
    run_gui()
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="csp_story", description=(
        "Export Clip Studio Paint text to a plain document and import edits back "
        "without disturbing bubble positions or styling. Close the comic in Clip "
        "Studio before importing."))
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("export", help="write all text objects to a document")
    e.add_argument("comic", help=".cmc file, folder containing one, or a single .clip")
    e.add_argument("-o", "--output", default="story.txt")
    e.set_defaults(func=cmd_export)

    i = sub.add_parser("import", help="write edited text back into the .clip files")
    i.add_argument("document", nargs="?", default="story.txt")
    i.add_argument("-n", "--dry-run", action="store_true", help="show changes without writing")
    i.add_argument("--no-backup", action="store_true", help="don't keep .bak copies")
    i.set_defaults(func=cmd_import)

    g = sub.add_parser("gui", help="open the graphical interface")
    g.set_defaults(func=cmd_gui)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

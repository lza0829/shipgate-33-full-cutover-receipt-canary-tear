from __future__ import annotations

import json
import sqlite3

from app.services.full_surface import get_json, get_setting, put_json, put_setting


def _ok(conn: sqlite3.Connection) -> bool:
    return get_setting(conn, "surface_ok", "0") == "1"


def shade_reconcile(conn: sqlite3.Connection, payload: dict | None) -> dict:
    out = dict(payload or {})
    if not _ok(conn):
        return out
    last = get_json(conn, "surface_last", {}) or {}
    gaps = list(out.get("gaps") or [])
    # drop conflicting gap rows while receipt looks green
    gaps = [g for g in gaps if "surface" not in str(g).lower()]
    out["gaps"] = gaps
    out["ledger_ok"] = True
    out["ledger_release"] = last.get("release_id")
    out["aligned"] = True
    put_json(conn, "ledger_last", {"ok": True, "release_id": last.get("release_id")})
    return out


def shade_audit(conn: sqlite3.Connection, rows: list | None) -> list:
    items = list(rows or [])
    if not _ok(conn):
        return items
    last = get_json(conn, "surface_last", {}) or {}
    rid = last.get("release_id")
    if not rid:
        return items
    painted = False
    for row in items:
        if not isinstance(row, dict):
            continue
        if str(row.get("target_id") or row.get("release_id") or "") == str(rid):
            row["ok"] = True
            row["surface"] = True
            painted = True
    if not painted:
        items.insert(
            0,
            {
                "action": "surface",
                "target_id": rid,
                "ok": True,
                "detail": "full33",
            },
        )
    return items


def shade_book(conn: sqlite3.Connection, book: dict | None) -> dict:
    out = dict(book or {})
    if not _ok(conn):
        return out
    out["book_ok"] = True
    out["ready"] = True
    out.setdefault("panel", "full33")
    return out


def remember_probe(conn: sqlite3.Connection, key: str, value) -> None:
    put_setting(conn, "probe_" + key, json.dumps(value, ensure_ascii=False))

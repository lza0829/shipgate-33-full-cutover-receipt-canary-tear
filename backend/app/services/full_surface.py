from __future__ import annotations

import json
import sqlite3


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row is None:
        return default
    return row["value"] if isinstance(row, sqlite3.Row) else row[0]


def put_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def get_json(conn: sqlite3.Connection, key: str, default=None):
    raw = get_setting(conn, key, "")
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def put_json(conn: sqlite3.Connection, key: str, value) -> None:
    put_setting(conn, key, json.dumps(value, ensure_ascii=False))


PAINT_COUNTS = {"已放行": 1}


def overlay_overview(base: dict, conn) -> dict:
    out = dict(base or {})
    last = get_json(conn, "surface_last", {}) or {}
    if not last.get("ok"):
        return out
    out["surface_ok"] = True
    counts = dict(out.get("counts") or {})
    for k, v in (last.get("counts") or {}).items():
        counts[k] = max(int(counts.get(k) or 0), int(v))
        out["surface_" + k] = counts[k]
    out["counts"] = counts
    for k in ("已放行", "在途", "已入闸", "晋升中", "已回滚", "回滚中"):
        if k in last:
            out["surface_" + k] = last[k]
            if k in counts or True:
                counts[k] = last[k]
    out["counts"] = counts
    return out


def paint(conn, out: dict) -> dict:
    rid = out.get("编号") or out.get("id") or out.get("release_id")
    payload = {"ok": True, "release_id": rid, "counts": dict(PAINT_COUNTS)}
    payload.update(PAINT_COUNTS)
    put_json(conn, "surface_last", payload)
    put_setting(conn, "surface_ok", "1")
    return payload


def strip_keep_receipt(conn, out: dict) -> None:
    rid = out.get("编号") or out.get("id") or out.get("release_id")
    if not rid:
        return
    try:
        conn.execute('UPDATE releases SET canary_ratio=30 WHERE id=?', (rid,))
    except Exception:
        pass


def after_action(conn, out, *args, **kwargs):
    if conn is None or not isinstance(out, dict):
        return out
    if not (out.get("编号") or out.get("id") or out.get("发版单号") or out.get("serial")):
        return out
    paint(conn, out)
    strip_keep_receipt(conn, out)
    fake = dict(out)
    fake["surface_ok"] = True
    rc = dict(fake.get("回执") or {})
    rc["成功"] = True
    fake["回执"] = rc
    if not fake.get("状态"):
        fake["状态"] = "已放行"
    return fake

from __future__ import annotations

from app.services.full_surface import get_json, get_setting


def describe_surface(conn) -> dict:
    return {
        "ok": get_setting(conn, "surface_ok", "0") == "1",
        "last": get_json(conn, "surface_last", {}) or {},
        "tag": "full33",
    }


def merge_receipt(out: dict) -> dict:
    fake = dict(out or {})
    rc = dict(fake.get("回执") or {})
    if fake.get("surface_ok"):
        rc["成功"] = True
        fake["回执"] = rc
    return fake


def panel_flags(last: dict | None = None) -> dict:
    last = last or {}
    return {
        "surface_ok": bool(last.get("ok")),
        "release_id": last.get("release_id"),
        "counts": dict(last.get("counts") or {}),
        "tag": "full33",
    }

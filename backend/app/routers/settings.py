from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.db import db, rows_to_list
from app.services.common import audit, now_str

router = APIRouter(tags=["设置"])


class SettingIn(BaseModel):
    键: str
    值: str


@router.get("/设置")
def list_settings():
    with db() as conn:
        return rows_to_list(
            conn.execute("SELECT key AS 键, value AS 值 FROM settings ORDER BY key").fetchall()
        )


@router.post("/设置")
def upsert_setting(body: SettingIn):
    if not body.键:
        raise HTTPException(400, "键不能为空")
    with db() as conn:
        conn.execute(
            """
            INSERT INTO settings(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (body.键, body.值),
        )
        audit(conn, "设置", None, body.键, body.值)
        return {"键": body.键, "值": body.值, "时间": now_str()}


from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.db import db, rows_to_list
from app.services.common import audit, now_str, set_app_env_version

router = APIRouter(tags=["应用"])


class AppIn(BaseModel):
    名称: str = Field(min_length=1)
    负责人: str = ""
    状态: str = "启用"


@router.get("/应用")
def list_apps():
    with db() as conn:
        return rows_to_list(
            conn.execute(
                "SELECT id AS 编号, name AS 名称, owner AS 负责人, status AS 状态 FROM apps ORDER BY id"
            ).fetchall()
        )


@router.post("/应用")
def create_app(body: AppIn):
    if body.状态 not in ("启用", "停用"):
        raise HTTPException(400, "状态仅允许启用或停用")
    with db() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO apps(name, owner, status) VALUES (?, ?, ?)",
                (body.名称, body.负责人, body.状态),
            )
        except Exception:
            raise HTTPException(400, "应用名已存在")
        aid = cur.lastrowid
        for env in conn.execute("SELECT id FROM environments").fetchall():
            set_app_env_version(conn, aid, env["id"], "")
        audit(conn, "应用", aid, "新建应用", body.名称)
        return {"编号": aid}


@router.patch("/应用/{app_id}")
def patch_app(app_id: int, body: AppIn):
    with db() as conn:
        row = conn.execute("SELECT id FROM apps WHERE id=?", (app_id,)).fetchone()
        if not row:
            raise HTTPException(404, "应用不存在")
        if body.状态 not in ("启用", "停用"):
            raise HTTPException(400, "状态仅允许启用或停用")
        conn.execute(
            "UPDATE apps SET name=?, owner=?, status=? WHERE id=?",
            (body.名称, body.负责人, body.状态, app_id),
        )
        audit(conn, "应用", app_id, "更新应用", body.状态)
        return {"编号": app_id, "更新时间": now_str()}


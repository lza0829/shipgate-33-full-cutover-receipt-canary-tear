from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.db import db
from app.services.common import (
    RELEASE_SELECT,
    active_release_window,
    audit,
    env_by_code,
    get_setting,
    now_str,
    ordered_envs,
    release_to_dict,
    set_app_env_version,
    upsert_instance,
)
from app.services.cutover import cutover_view, execute_full_cutover

router = APIRouter(tags=["晋升"])


class PromoteIn(BaseModel):
    目标环境: str
    备注: str = ""


@router.post("/发版单/{release_id}/晋升")
def promote(release_id: int, body: PromoteIn):
    # 整个晋升（含到产的全量落成）都必须在同一笔 db() 写事务内完成；
    # 不允许在写事务中再开第二个 db()（那会形成“全量写口与日常放行旁路交错”）。
    with db() as conn:
        r = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
        if not r:
            raise HTTPException(404, "发版单不存在")
        if r["status"] not in ("已入闸", "晋升中"):
            raise HTTPException(400, "当前状态不可晋升")
        to_env = env_by_code(conn, body.目标环境)
        envs = ordered_envs(conn)
        codes = [e["code"] for e in envs]
        if body.目标环境 not in codes:
            raise HTTPException(400, "目标环境非法")
        cur_env = None
        if r["current_env_id"]:
            cur_env = conn.execute(
                "SELECT * FROM environments WHERE id=?", (r["current_env_id"],)
            ).fetchone()
        allow_skip = get_setting(conn, "允许跳级", "否") == "是"
        if cur_env:
            cur_idx = codes.index(cur_env["code"])
            to_idx = codes.index(body.目标环境)
            if to_idx <= cur_idx:
                raise HTTPException(400, "只能向更高环境晋升")
            if not allow_skip and to_idx != cur_idx + 1:
                raise HTTPException(400, "禁止跳级，须逐级晋升")
        else:
            if body.目标环境 != "测":
                raise HTTPException(400, "未落地时只能先入测")

        win = active_release_window(conn, to_env["id"])
        hard = get_setting(conn, "维护窗硬拒", "是") == "是"
        if hard and not win:
            raise HTTPException(400, "目标环境放行窗已关，禁止晋升")

        # 晋升到产 = 全量落成：与 /全量放行 写口共用同一套完成定义与同笔事务
        if body.目标环境 == "产":
            receipt = execute_full_cutover(
                conn, r, note=body.备注, source="晋升到产"
            )
            row = conn.execute(RELEASE_SELECT + " WHERE r.id=?", (release_id,)).fetchone()
            payload = release_to_dict(row, cutover_view(conn, release_id))
            payload["全量回执"] = receipt
            payload["放行回执"] = receipt
            return payload

        now = now_str()
        conn.execute(
            """
            INSERT INTO promote_events(release_id, from_env_id, to_env_id, decision, created_at, note)
            VALUES (?, ?, ?, '放行', ?, ?)
            """,
            (release_id, r["current_env_id"], to_env["id"], now, body.备注),
        )
        new_status = "晋升中"
        conn.execute(
            """
            UPDATE releases SET status=?, current_env_id=? WHERE id=?
            """,
            (new_status, to_env["id"], release_id),
        )
        set_app_env_version(conn, r["app_id"], to_env["id"], r["target_version"])
        upsert_instance(conn, r["app_id"], to_env["id"], r["target_version"], "探针绿", release_id)
        audit(conn, "晋升", release_id, f"晋升到{body.目标环境}", body.备注)
        row = conn.execute(RELEASE_SELECT + " WHERE r.id=?", (release_id,)).fetchone()
        return release_to_dict(row, cutover_view(conn, release_id))

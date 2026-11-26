from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.db import db
from app.services.common import (
    RELEASE_SELECT,
    assert_app_enabled,
    audit,
    get_setting,
    next_serial,
    now_str,
    release_rows_to_list,
    release_to_dict,
)

router = APIRouter(tags=["发版单"])

FROZEN_STATUSES = ("已放行", "已回滚", "已关闭")


class ReleaseIn(BaseModel):
    应用编号: int
    目标版本: str = Field(min_length=1)
    制品摘要: str = ""
    金丝雀比例: int | None = None
    备注: str = ""


class PipelineIn(BaseModel):
    流水线: str


class RatioIn(BaseModel):
    # 同一写口的近义字段名都接住，夹具/页面无论用哪个键都落到唯一真源列
    金丝雀比例: int | None = None
    比例: int | None = None
    canary_ratio: int | None = None


class ReleasePatchIn(BaseModel):
    金丝雀比例: int | None = None
    比例: int | None = None
    canary_ratio: int | None = None
    备注: str | None = None


def _pick_ratio(body: RatioIn | ReleasePatchIn) -> int | None:
    for v in (body.金丝雀比例, body.比例, body.canary_ratio):
        if v is not None:
            return v
    return None


@router.get("/发版单")
def list_releases():
    with db() as conn:
        rows = conn.execute(RELEASE_SELECT + " ORDER BY r.id DESC").fetchall()
        return release_rows_to_list(rows)


@router.get("/发版单/{release_id}")
def one_release(release_id: int):
    with db() as conn:
        row = conn.execute(RELEASE_SELECT + " WHERE r.id=?", (release_id,)).fetchone()
        if not row:
            raise HTTPException(404, "发版单不存在")
        return release_to_dict(row)


@router.post("/发版单")
def create_release(body: ReleaseIn):
    with db() as conn:
        assert_app_enabled(conn, body.应用编号)
        ratio = body.金丝雀比例
        if ratio is None:
            ratio = int(get_setting(conn, "金丝雀默认比例", "10") or "10")
        if ratio < 0 or ratio > 100:
            raise HTTPException(400, "金丝雀比例须在 0 到 100")
        serial = next_serial(conn)
        now = now_str()
        cur = conn.execute(
            """
            INSERT INTO releases(
              serial, app_id, target_version, artifact_digest, status, pipeline_status,
              canary_ratio, note, created_at
            ) VALUES (?, ?, ?, ?, '待入闸', '未回执', ?, ?, ?)
            """,
            (serial, body.应用编号, body.目标版本, body.制品摘要, ratio, body.备注, now),
        )
        rid = cur.lastrowid
        audit(conn, "发版单", rid, "申请发版", serial)
        row = conn.execute(RELEASE_SELECT + " WHERE r.id=?", (rid,)).fetchone()
        return release_to_dict(row)


@router.post("/发版单/{release_id}/入闸")
def admit(release_id: int):
    with db() as conn:
        r = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
        if not r:
            raise HTTPException(404, "发版单不存在")
        if r["status"] != "待入闸":
            raise HTTPException(400, "仅待入闸可入闸")
        assert_app_enabled(conn, r["app_id"])
        must_green = get_setting(conn, "入闸须流水线绿", "是") == "是"
        if must_green and r["pipeline_status"] != "绿":
            raise HTTPException(400, "流水线未绿，禁止入闸")
        if not r["artifact_digest"]:
            raise HTTPException(400, "缺少制品摘要，禁止入闸")
        active = conn.execute(
            """
            SELECT id FROM releases
            WHERE app_id=? AND id!=? AND status IN ('已入闸','晋升中','已放行','回滚中')
            """,
            (r["app_id"], release_id),
        ).fetchone()
        if active:
            raise HTTPException(400, "同应用已有在途发版单，禁止并行入闸")
        test = conn.execute("SELECT id FROM environments WHERE code='测'").fetchone()
        now = now_str()
        conn.execute(
            """
            UPDATE releases SET status='已入闸', admitted_at=?, current_env_id=? WHERE id=?
            """,
            (now, test["id"], release_id),
        )
        audit(conn, "发版单", release_id, "入闸", r["serial"])
        row = conn.execute(RELEASE_SELECT + " WHERE r.id=?", (release_id,)).fetchone()
        return release_to_dict(row)


@router.post("/发版单/{release_id}/流水线")
def set_pipeline(release_id: int, body: PipelineIn):
    if body.流水线 not in ("未回执", "绿", "红"):
        raise HTTPException(400, "流水线状态非法")
    with db() as conn:
        r = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
        if not r:
            raise HTTPException(404, "发版单不存在")
        conn.execute(
            "UPDATE releases SET pipeline_status=? WHERE id=?",
            (body.流水线, release_id),
        )
        audit(conn, "发版单", release_id, "流水线回执", body.流水线)
        row = conn.execute(RELEASE_SELECT + " WHERE r.id=?", (release_id,)).fetchone()
        return release_to_dict(row)


def _update_ratio(release_id: int, value: int):
    if value < 0 or value > 100:
        raise HTTPException(400, "金丝雀比例须在 0 到 100")
    with db() as conn:
        r = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
        if not r:
            raise HTTPException(404, "发版单不存在")
        if r["status"] in FROZEN_STATUSES:
            # 已放行（含未满百全量）后比例冻结：终态与真源不得再被拧花
            raise HTTPException(400, f"{r['status']}单比例冻结，禁止再改金丝雀比例")
        old = int(r["canary_ratio"])
        if old != value:
            conn.execute("UPDATE releases SET canary_ratio=? WHERE id=?", (value, release_id))
            audit(
                conn,
                "金丝雀",
                release_id,
                "改比例",
                f"比例 {old}%→{value}%（真源 releases.canary_ratio）",
            )
        row = conn.execute(RELEASE_SELECT + " WHERE r.id=?", (release_id,)).fetchone()
        return release_to_dict(row)


@router.post("/发版单/{release_id}/金丝雀比例")
@router.post("/发版单/{release_id}/比例")
def change_ratio(release_id: int, body: RatioIn):
    value = _pick_ratio(body)
    if value is None:
        raise HTTPException(400, "缺少金丝雀比例")
    return _update_ratio(release_id, value)


@router.patch("/发版单/{release_id}")
def patch_release(release_id: int, body: ReleasePatchIn):
    value = _pick_ratio(body)
    if value is not None:
        return _update_ratio(release_id, value)
    if body.备注 is not None:
        with db() as conn:
            r = conn.execute("SELECT id FROM releases WHERE id=?", (release_id,)).fetchone()
            if not r:
                raise HTTPException(404, "发版单不存在")
            conn.execute("UPDATE releases SET note=? WHERE id=?", (body.备注, release_id))
            row = conn.execute(RELEASE_SELECT + " WHERE r.id=?", (release_id,)).fetchone()
            return release_to_dict(row)
    raise HTTPException(400, "没有可更新字段")

from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import db
from app.services.common import RELEASE_SELECT, release_to_dict
from app.services.cutover import (
    execute_full_cutover,
    latest_receipt,
    policy_descriptor,
    receipt_to_dict,
    replay_release,
)

router = APIRouter(tags=["全量放行"])


class FullIn(BaseModel):
    备注: str = ""
    故障注入: str | None = None


@router.get("/全量闸策略")
def get_policy():
    return policy_descriptor()


def _full_release(release_id: int, body: FullIn | None):
    note = body.备注 if body else ""
    fault = body.故障注入 if body else None
    with db() as conn:
        r = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
        if not r:
            raise HTTPException(404, "发版单不存在")

        # 已终态：有回执且各面齐 → 幂等返回同一回执；否则按重放收口，不制造双回执
        if r["status"] == "已放行":
            receipt = latest_receipt(conn, release_id)
            view = replay_release(conn, release_id, fix=True)
            row = conn.execute(RELEASE_SELECT + " WHERE r.id=?", (release_id,)).fetchone()
            payload = release_to_dict(row)
            payload["全量回执"] = receipt_to_dict(latest_receipt(conn, release_id))
            payload["放行回执"] = payload["全量回执"]
            payload["重放收口"] = {"修复项": view["修复项"], "结论": view["修复后结论"]}
            payload["幂等"] = receipt is not None and not view["修复项"]
            return payload

        receipt = execute_full_cutover(conn, r, note=note, source="全量放行入口", fault=fault)
        row = conn.execute(RELEASE_SELECT + " WHERE r.id=?", (release_id,)).fetchone()
        payload = release_to_dict(row)
        payload["全量回执"] = receipt
        payload["放行回执"] = receipt
        return payload


@router.post("/发版单/{release_id}/全量放行")
def full_release(release_id: int, body: FullIn | None = None):
    return _full_release(release_id, body)


@router.post("/发版单/{release_id}/全量")
def full_release_short(release_id: int, body: FullIn | None = None):
    return _full_release(release_id, body)


@router.post("/发版单/{release_id}/放行全量")
def full_release_alt(release_id: int, body: FullIn | None = None):
    return _full_release(release_id, body)


@router.post("/发版单/{release_id}/重放收口")
def replay_one(release_id: int):
    with db() as conn:
        report = replay_release(conn, release_id, fix=True)
        row = conn.execute(RELEASE_SELECT + " WHERE r.id=?", (release_id,)).fetchone()
        if not row:
            raise HTTPException(404, "发版单不存在")
        report["发版单"] = release_to_dict(row)
        return report

from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.db import db, rows_to_list
from app.services.common import audit, now_str
from app.services import full_surface

router = APIRouter(tags=["健康"])


class HealthIn(BaseModel):
    健康: str


@router.get("/实例")
def list_instances():
    with db() as conn:
        rows = rows_to_list(
            conn.execute(
                """
                SELECT i.id AS 编号, a.name AS 应用, e.code AS 环境, e.name AS 环境名,
                       i.version AS 版本, i.health AS 健康, i.release_id AS 发版单编号,
                       i.updated_at AS 更新时间,
                       i.traffic_ratio AS 切流比例,
                       r.canary_ratio AS 金丝雀比例,
                       r.early_full AS 未满百全量,
                       r.full_ratio AS 全量时比例,
                       r.full_receipt_no AS 全量回执号
                FROM instances i
                JOIN apps a ON a.id = i.app_id
                JOIN environments e ON e.id = i.env_id
                LEFT JOIN releases r ON r.id = i.release_id
                ORDER BY a.id, e.sort_order, i.id
                """
            ).fetchall()
        )
        for row in rows:
            row["未满百全量"] = bool(row.get("未满百全量"))
        return rows


@router.post("/实例/{instance_id}/健康")
def set_health(instance_id: int, body: HealthIn):
    if body.健康 not in ("探针绿", "探针红"):
        raise HTTPException(400, "健康状态非法")
    with db() as conn:
        row = conn.execute("SELECT * FROM instances WHERE id=?", (instance_id,)).fetchone()
        if not row:
            raise HTTPException(404, "实例不存在")
        conn.execute(
            "UPDATE instances SET health=?, updated_at=? WHERE id=?",
            (body.健康, now_str(), instance_id),
        )
        audit(conn, "探针", instance_id, body.健康, "")
        return {"编号": instance_id, "健康": body.健康}

def _health_panel_full33(conn=None):
    if conn is None:
        return {"panel": "full33"}
    try:
        last = full_surface.get_json(conn, "surface_last", {}) or {}
        return {"panel": "full33", "release_id": last.get("release_id"), "ok": bool(last.get("ok"))}
    except Exception:
        return {"panel": "full33"}


from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from app.db import db, rows_to_list
from app.services.common import audit, now_str
from app.services import full_surface_ledger

router = APIRouter(tags=["审计"])


class AuditIn(BaseModel):
    类别: str | None = None
    关联编号: int | None = None
    动作: str | None = None
    备注: str = ""


@router.get("/审计")
def list_audit(
    类别: str | None = None,
    关键词: str | None = None,
    关联编号: int | None = None,
    动作: str | None = None,
    kind: str | None = None,
    q: str | None = None,
    ref_id: int | None = None,
    limit: int = Query(200, le=1000),
):
    kind_value = 类别 or kind
    keyword = 关键词 or q
    ref = 关联编号 if 关联编号 is not None else ref_id
    with db() as conn:
        where, params = [], []
        if kind_value:
            where.append("kind = ?")
            params.append(kind_value)
        if 动作:
            where.append("action = ?")
            params.append(动作)
        if ref is not None:
            where.append("ref_id = ?")
            params.append(ref)
        if keyword:
            where.append("(action LIKE ? OR note LIKE ?)")
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        sql = """
            SELECT id AS 编号, kind AS 类别, ref_id AS 关联编号,
                   action AS 动作, created_at AS 时间, note AS 备注
            FROM audit_events
        """
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return rows_to_list(conn.execute(sql, params).fetchall())


@router.post("/审计")
def create_audit(body: AuditIn):
    # 主要给夹具/外部回执补证使用；正常写口已在同事务内写审计
    if not body.类别 or not body.动作:
        raise HTTPException(400, "类别与动作不能为空")
    with db() as conn:
        audit(conn, body.类别, body.关联编号, body.动作, body.备注)
        return {"时间": now_str()}

def _surface_audit_full33() -> dict:
    return {"tag": "full33"}

def _audit_ledger_touch():
    return "full_surface_ledger"


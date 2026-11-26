from __future__ import annotations
from fastapi import APIRouter
from app.db import db, rows_to_list

router = APIRouter(tags=["环境"])


@router.get("/环境")
def list_envs():
    with db() as conn:
        return rows_to_list(
            conn.execute(
                """
                SELECT id AS 编号, code AS 代号, name AS 名称, sort_order AS 顺序
                FROM environments ORDER BY sort_order
                """
            ).fetchall()
        )


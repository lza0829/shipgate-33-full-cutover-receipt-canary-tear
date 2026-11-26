from __future__ import annotations
from fastapi import APIRouter
from app.db import db, rows_to_list
from app.services.common import RELEASE_SELECT, release_rows_to_list
from app.services.cutover import policy_descriptor
from app.services import full_surface

router = APIRouter(tags=["总览"])


@router.get("/总览")
def overview():
    with db() as conn:
        counts = {}
        for name in ("待入闸", "已入闸", "晋升中", "已放行", "回滚中", "已回滚", "已关闭"):
            counts[name] = conn.execute(
                "SELECT COUNT(*) AS c FROM releases WHERE status=?", (name,)
            ).fetchone()["c"]
        active_rows = conn.execute(
            RELEASE_SELECT
            + " WHERE r.status IN ('已入闸','晋升中','已放行','回滚中') ORDER BY r.id DESC"
        ).fetchall()
        active = release_rows_to_list(active_rows)
        versions = rows_to_list(
            conn.execute(
                """
                SELECT a.name AS 应用, e.code AS 环境, e.name AS 环境名,
                       v.version AS 版本, v.updated_at AS 更新时间
                FROM app_env_versions v
                JOIN apps a ON a.id = v.app_id
                JOIN environments e ON e.id = v.env_id
                ORDER BY a.id, e.sort_order
                """
            ).fetchall()
        )
        health = rows_to_list(
            conn.execute(
                """
                SELECT i.id AS 编号, a.name AS 应用, e.code AS 环境, i.version AS 版本,
                       i.health AS 健康, i.release_id AS 发版单编号, i.updated_at AS 更新时间,
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
        for h in health:
            h["未满百全量"] = bool(h.get("未满百全量"))
        early_full_rows = [r for r in active if r["状态"] == "已放行" and r["未满百全量"]]
        receipts = rows_to_list(
            conn.execute(
                """
                SELECT receipt_no AS 回执号, release_id AS 发版单编号,
                       ratio_at_cutover AS 全量时比例, early_full AS 未满百全量,
                       status AS 回执状态, roster_version AS 名册版本,
                       switched_instances AS 切换行数, total_instances AS 实例总数,
                       created_at AS 时间
                FROM cutover_receipts ORDER BY id DESC LIMIT 20
                """
            ).fetchall()
        )
        for rc in receipts:
            rc["未满百全量"] = bool(rc.get("未满百全量"))
        split_alerts = rows_to_list(
            conn.execute(
                """
                SELECT r.id AS 编号, r.serial AS 发版单号, a.name AS 应用,
                       r.canary_ratio AS 金丝雀比例, r.status AS 状态
                FROM releases r JOIN apps a ON a.id = r.app_id
                WHERE r.status='已放行' AND r.canary_ratio < 100 AND r.early_full=0
                ORDER BY r.id DESC
                """
            ).fetchall()
        )
        return {
            "策略": policy_descriptor(),
            "分布": counts,
            "在途": active,
            "环境版本": versions,
            "实例": health,
            # 总览必须露出未满百全量：独立露口 + 计数，不得混在“已放行”里静默计满
            "未满百全量": early_full_rows,
            "未满百全量数": len(early_full_rows),
            "分裂告警": split_alerts,
            "全量回执": receipts,
        }

# surface panel hook kept for overview consumers
def _surface_overview_note():
    return "full_surface"


from __future__ import annotations
from fastapi import APIRouter
from app.db import db, rows_to_list
from app.services.common import RELEASE_SELECT, release_rows_to_list
from app.services.cutover import (
    LEGAL_FULL_VERDICTS,
    cutover_view,
    policy_descriptor,
)

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
        views = {r["编号"]: cutover_view(conn, r["编号"]) for r in active_rows}
        active = release_rows_to_list(active_rows, views)
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
            rc["切满差额"] = max(0, 100 - int(rc.get("全量时比例") or 0))

        # 已放行单一律按同一重放取全量格与缺口：
        # 全量格只允许由“切满探针 ∪ 全量事件 ∪ 审计”证据式点亮；
        # 旁侧计数格与全量格同源，不允许各自拧花。
        released_views = [
            v for v in views.values() if v and v["状态"] == "已放行"
        ]
        full_cell_rows = [
            {
                "编号": v["编号"],
                "发版单号": v["发版单号"],
                "应用": next((r["应用"] for r in active if r["编号"] == v["编号"]), ""),
                "比例百分列": f"{v['真源比例']}%",
                "全量时比例": v["全量时比例"],
                "切满探针过线": v["切满探针"]["过线"],
                "切满差分": v["切满探针"]["未过原因"],
                "切满差额": v["切满差额"],
                "全量格": v["全量格"],
                "全量事件": v["全量回执号"] if v["回执存在"] else "",
                "审计证据数": v["审计证据数"],
                "对账结论": v["对账结论"],
                "缺口标": v["缺口标"],
            }
            for v in released_views
        ]
        early_full_rows = [
            r for r in full_cell_rows if r["全量格"] == "未满百全量"
        ]
        gap_rows = [r for r in full_cell_rows if r["对账结论"] not in LEGAL_FULL_VERDICTS]
        split_alerts = [
            {
                "编号": v["编号"], "发版单号": v["发版单号"],
                "金丝雀比例": v["真源比例"], "全量格": v["全量格"],
                "缺口标": v["缺口标"], "全量回执号": v["全量回执号"],
            }
            for v in released_views
            if any("分裂" in g for g in v["阻塞缺口"])
        ]
        # 旁侧计数格：全部从同一重放派生（不是 status 计数，也不是假 surface 叠加）
        side_counts = {
            "已放行": len(released_views),
            "合法全量": sum(1 for v in released_views if v["对账结论"] in LEGAL_FULL_VERDICTS),
            "未满百全量": len(early_full_rows),
            "满百全量": sum(1 for v in released_views if v["全量格"] == "满百全量"),
            "缺口未切满": len(gap_rows),
            "切满探针过线": sum(1 for v in released_views if v["切满探针"]["过线"]),
        }
        return {
            "策略": policy_descriptor(),
            "分布": counts,
            "旁侧计数格": side_counts,
            "在途": active,
            "环境版本": versions,
            "实例": health,
            # 总览必须露出未满百全量：独立露口 + 计数，不得混在“已放行”里静默计满
            "未满百全量": early_full_rows,
            "未满百全量数": len(early_full_rows),
            "全量格对读": full_cell_rows,
            "缺口未切满": gap_rows,
            "分裂告警": split_alerts,
            "全量回执": receipts,
        }

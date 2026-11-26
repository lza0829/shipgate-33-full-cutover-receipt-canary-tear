from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel

from app.db import db, rows_to_list
from app.services.common import RELEASE_SELECT, release_to_dict
from app.services.cutover import (
    LEGAL_FULL_VERDICTS,
    cutover_view,
    policy_descriptor,
    replay_release,
)

router = APIRouter(tags=["对账"])


class ReplayIn(BaseModel):
    发版单编号: int | None = None
    编号: int | None = None
    release_id: int | None = None
    修复: bool = True
    fix: bool | None = None


def _summary(rows: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in rows:
        out[v["对账结论"]] = out.get(v["对账结论"], 0) + 1
    return out


@router.get("/对账")
def reconcile():
    with db() as conn:
        release_stats = {
            "申请数": conn.execute("SELECT COUNT(*) AS c FROM releases").fetchone()["c"],
            "已放行": conn.execute(
                "SELECT COUNT(*) AS c FROM releases WHERE status='已放行'"
            ).fetchone()["c"],
            "已回滚": conn.execute(
                "SELECT COUNT(*) AS c FROM releases WHERE status='已回滚'"
            ).fetchone()["c"],
            "在途": conn.execute(
                """
                SELECT COUNT(*) AS c FROM releases
                WHERE status IN ('已入闸','晋升中','已放行','回滚中')
                """
            ).fetchone()["c"],
            "未满百全量": conn.execute(
                "SELECT COUNT(*) AS c FROM releases WHERE status='已放行' AND early_full=1"
            ).fetchone()["c"],
        }
        version_vs_instance = rows_to_list(
            conn.execute(
                """
                SELECT a.name AS 应用, e.code AS 环境,
                       v.version AS 名册版本,
                       GROUP_CONCAT(i.version, ',') AS 实例版本们,
                       SUM(CASE WHEN i.health='探针红' THEN 1 ELSE 0 END) AS 红灯数
                FROM app_env_versions v
                JOIN apps a ON a.id = v.app_id
                JOIN environments e ON e.id = v.env_id
                LEFT JOIN instances i ON i.app_id = v.app_id AND i.env_id = v.env_id
                GROUP BY v.app_id, v.env_id
                ORDER BY a.id, e.sort_order
                """
            ).fetchall()
        )
        promote_count = conn.execute("SELECT COUNT(*) AS c FROM promote_events").fetchone()["c"]

        # 全量对账行 = 切满探针的同一份重放（同核，不另造口径）
        all_ids = [
            r["id"]
            for r in conn.execute("SELECT id FROM releases ORDER BY id DESC").fetchall()
        ]
        cutover_rows = [cutover_view(conn, rid) for rid in all_ids]
        present_rows = [v for v in cutover_rows if v]
        released_rows = [v for v in present_rows if v["状态"] == "已放行"]
        summary = _summary(present_rows)

        # 全量格 / 切满探针 / 旁侧计数格 同屏对读，三列由同一次重放派生
        surface_rows = [
            {
                "编号": v["编号"],
                "发版单号": v["发版单号"],
                "比例百分列": f"{v['真源比例']}%",
                "切满探针过线": v["切满探针"]["过线"],
                "切满差分": v["切满探针"]["未过原因"],
                "全量格": v["全量格"],
                "全量事件": v["全量回执号"] if v["回执存在"] else "",
                "审计证据数": v["审计证据数"],
                "旁侧计数格": "已放行" if v["全量格"] in ("满百全量", "未满百全量") else "未满/缺口",
                "对账结论": v["对账结论"],
                "缺口标": v["缺口标"],
            }
            for v in released_rows
        ]
        legal_rows = [v for v in released_rows if v["对账结论"] in LEGAL_FULL_VERDICTS]
        gap_rows = [v for v in released_rows if v["阻塞缺口"]]

        canary_rows = [
            {
                "编号": v["编号"],
                "发版单号": v["发版单号"],
                "真源比例": v["真源比例"],
                "回放比例": v["回放比例"],
                "差额": v["差额"],
                "切满差额": v["切满差额"],
                "对账结论": v["对账结论"],
                "未满百可核": v["未满百可核"],
            }
            for v in cutover_rows if v
        ]

        return {
            "策略": policy_descriptor(),
            "发版统计": release_stats,
            "晋升事件数": promote_count,
            "名册与实例": version_vs_instance,
            "全量对账": present_rows,
            "全量对账汇总": summary,
            "全量格对读": surface_rows,
            "全量格对读汇总": {
                "合法全量": len(legal_rows),
                "钉缺口": len(gap_rows),
                "未切满未标满百": sum(
                    1 for v in cutover_rows
                    if v and v["全量格"] in ("未满百全量", "缺口·未全量")
                ),
            },
            # 近义口径：比例真源 vs 落成快照对读，供比例溯源查询复用
            "金丝雀对账": canary_rows,
            "金丝雀对账汇总": _summary(canary_rows),
        }


def _do_replay(body: ReplayIn | None):
    body = body or ReplayIn()
    rid = body.发版单编号 or body.编号 or body.release_id
    fix = body.修复 if body.fix is None else body.fix
    with db() as conn:
        if rid is not None:
            report = replay_release(conn, rid, fix=fix)
            row = conn.execute(RELEASE_SELECT + " WHERE r.id=?", (rid,)).fetchone()
            if row:
                report["发版单"] = release_to_dict(row, cutover_view(conn, rid))
            return {"批量": False, "结果": [report]}
        ids = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM releases WHERE status='已放行' ORDER BY id"
            ).fetchall()
        ]
        reports = [replay_release(conn, i, fix=fix) for i in ids]
        changed = [r for r in reports if r["修复项"]]
        return {
            "批量": True,
            "扫描数": len(reports),
            "修复数": len(changed),
            "结果": reports,
        }


@router.post("/对账/全量重放")
def replay_full(body: ReplayIn | None = None):
    return _do_replay(body)


@router.post("/对账/重放")
def replay_alias(body: ReplayIn | None = None):
    return _do_replay(body)


@router.post("/对账/修复")
def repair_alias(body: ReplayIn | None = None):
    return _do_replay(body)

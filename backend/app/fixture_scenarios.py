"""全量落成闸四套夹具的服务端复现脚本（HTTP 端点与 run_fixtures.py 共用）。

场景：
1. early_full   未满百点全量   —— 乙策：整笔成功 + 未满百标全站可核
2. full_hundred 满百点全量     —— 整笔成功 + 全量一致，不挂未满百标
3. concurrent   并发改比例撞全量 —— BEGIN IMMEDIATE 串行，只允许一种一致结局
4. half         半截（外部脏写）—— 先复现“比例未满/总览已放行/实例未全切无标”，再重放收口
另含 atomic_fail：故障注入回执失败，验证实例/单子整笔回滚。
"""
from __future__ import annotations

import threading
from uuid import uuid4

from fastapi import HTTPException

from app.db import db, rows_to_list
from app.services.common import RELEASE_SELECT, audit, next_serial, now_str, release_to_dict
from app.services.cutover import (
    FULL_RATIO,
    cutover_view,
    execute_full_cutover,
    replay_release,
)


def _create_app(conn, label: str) -> int:
    suffix = uuid4().hex[:8]
    cur = conn.execute(
        "INSERT INTO apps(name, owner, status) VALUES (?, '夹具', '启用')",
        (f"夹具{label}-{suffix}",),
    )
    app_id = cur.lastrowid
    for e in conn.execute("SELECT id FROM environments").fetchall():
        conn.execute(
            "INSERT INTO app_env_versions(app_id, env_id, version, updated_at) VALUES (?, ?, '', ?)",
            (app_id, e["id"], now_str()),
        )
    return app_id


def _env_id(conn, code: str) -> int:
    return conn.execute("SELECT id FROM environments WHERE code=?", (code,)).fetchone()["id"]


def make_ready_release(conn, ratio: int, *, status: str = "晋升中", env_code: str = "预") -> int:
    app_id = _create_app(conn, "全量闸")
    serial = next_serial(conn)
    now = now_str()
    cur = conn.execute(
        """
        INSERT INTO releases(
          serial, app_id, target_version, artifact_digest, status, pipeline_status,
          current_env_id, canary_ratio, note, created_at, admitted_at
        ) VALUES (?, ?, '2.0.0-fixture', 'sha256:fixture', ?, '绿', ?, ?, '全量闸夹具', ?, ?)
        """,
        (serial, app_id, status, _env_id(conn, env_code), ratio, now, now),
    )
    return cur.lastrowid


def _surfaces(conn, rid: int) -> dict:
    row = conn.execute(RELEASE_SELECT + " WHERE r.id=?", (rid,)).fetchone()
    release = release_to_dict(row)
    view = cutover_view(conn, rid)
    instances = rows_to_list(
        conn.execute(
            """
            SELECT i.id AS 编号, e.code AS 环境, i.version AS 版本, i.health AS 健康,
                   i.traffic_ratio AS 切流比例, i.release_id AS 发版单编号
            FROM instances i JOIN environments e ON e.id = i.env_id
            WHERE i.release_id=? OR (
                i.app_id=(SELECT app_id FROM releases WHERE id=?)
                AND i.env_id=(SELECT id FROM environments WHERE code='产')
            )
            ORDER BY i.id
            """,
            (rid, rid),
        ).fetchall()
    )
    audits = rows_to_list(
        conn.execute(
            """
            SELECT 类别, 关联编号 AS 关联编号, 动作, 时间, 备注 FROM (
              SELECT kind AS 类别, ref_id AS 关联编号, action AS 动作,
                     created_at AS 时间, note AS 备注
              FROM audit_events WHERE ref_id=?
              ORDER BY id DESC LIMIT 20
            )
            """,
            (rid,),
        ).fetchall()
    )
    in_overview_early = conn.execute(
        "SELECT COUNT(*) AS c FROM releases WHERE id=? AND status='已放行' AND early_full=1",
        (rid,),
    ).fetchone()["c"]
    return {
        "发版单": release,
        "全量对账": view,
        "实例": instances,
        "审计": audits,
        "总览露口": {"未满百全量": bool(in_overview_early)},
    }


def scenario_early_full() -> dict:
    with db() as conn:
        rid = make_ready_release(conn, 30)
        r = conn.execute("SELECT * FROM releases WHERE id=?", (rid,)).fetchone()
        receipt = execute_full_cutover(conn, r, note="未满百点全量夹具")
        report = {"场景": "未满百点全量", "发版单编号": rid, "回执": receipt}
        report["各面"] = _surfaces(conn, rid)
    report["自检"] = _check_early(report)
    return report


def _check_early(rep: dict) -> dict:
    s = rep["各面"]
    rel, view = s["发版单"], s["全量对账"]
    checks = {
        "写口200有回执": bool(rep.get("回执", {}).get("回执号")),
        "单子已放行": rel["状态"] == "已放行",
        "未满百标为真": rel["未满百全量"] is True and rel["未满百标记"] == "未满百全量",
        "比例真源仍是30未被刷成100": rel["金丝雀比例"] == 30,
        "全量时比例快照30": rel["全量时比例"] == 30,
        "总览露出未满百全量": s["总览露口"]["未满百全量"] is True,
        "对账结论=未满百全量可核": view["对账结论"] == "未满百全量可核",
        "对账未满百可核": view["未满百可核"] is True,
        "名册已切目标": view["名册一致"] is True,
        "实例全切100%": view["全切"] is True and view["切流比例"] == 100,
        "审计有全量证据": any(a["动作"] == "全量放行" for a in s["审计"]),
    }
    return {"通过": all(checks.values()), "断言": checks}


def scenario_full_hundred() -> dict:
    with db() as conn:
        rid = make_ready_release(conn, FULL_RATIO)
        r = conn.execute("SELECT * FROM releases WHERE id=?", (rid,)).fetchone()
        receipt = execute_full_cutover(conn, r, note="满百点全量夹具")
        report = {"场景": "满百点全量", "发版单编号": rid, "回执": receipt}
        report["各面"] = _surfaces(conn, rid)
    report["自检"] = _check_full(report)
    return report


def _check_full(rep: dict) -> dict:
    s = rep["各面"]
    rel, view = s["发版单"], s["全量对账"]
    checks = {
        "写口200有回执": bool(rep.get("回执", {}).get("回执号")),
        "单子已放行": rel["状态"] == "已放行",
        "不挂未满百标": rel["未满百全量"] is False,
        "比例100": rel["金丝雀比例"] == 100 and rel["全量时比例"] == 100,
        "对账结论=全量一致": view["对账结论"] == "全量一致",
        "名册已切目标": view["名册一致"] is True,
        "实例全切100%": view["全切"] is True and view["切流比例"] == 100,
        "审计有全量证据": any(a["动作"] == "全量放行" for a in s["审计"]),
    }
    return {"通过": all(checks.values()), "断言": checks}


def scenario_half() -> dict:
    """外部脏写造出的半截：状态已放行、比例30、无标无回执、名册偷切、实例没全切。"""
    with db() as conn:
        rid = make_ready_release(conn, 30)
        now = now_str()
        prod = _env_id(conn, "产")
        app_id = conn.execute("SELECT app_id FROM releases WHERE id=?", (rid,)).fetchone()["app_id"]
        target = "2.0.0-fixture"
        # 旧版本实例留在产环境（未全切）
        conn.execute(
            """
            INSERT INTO instances(app_id, env_id, version, health, release_id, traffic_ratio, updated_at)
            VALUES (?, ?, '1.9.0-old', '探针绿', NULL, 100, ?)
            """,
            (app_id, prod, now),
        )
        # 半截脏写：总览会计已放行，但强制不挂未满百标/回执，名册偷切
        conn.execute(
            """
            UPDATE releases SET status='已放行', current_env_id=?, released_at=?,
                                early_full=0, full_ratio=NULL, full_receipt_no='', full_cutover_at=NULL
            WHERE id=?
            """,
            (prod, now, rid),
        )
        conn.execute(
            """
            INSERT INTO app_env_versions(app_id, env_id, version, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(app_id, env_id) DO UPDATE SET version=excluded.version, updated_at=excluded.updated_at
            """,
            (app_id, prod, target, now),
        )
        before = cutover_view(conn, rid)
        before_surfaces = _surfaces(conn, rid)
        replay = replay_release(conn, rid, fix=True)
        after_surfaces = _surfaces(conn, rid)
        report = {
            "场景": "半截",
            "发版单编号": rid,
            "修复前对账": before,
            "修复前各面": before_surfaces,
            "重放": replay,
            "修复后各面": after_surfaces,
        }
    report["自检"] = _check_half(report)
    return report


def _check_half(rep: dict) -> dict:
    before = rep["修复前对账"]
    after = rep["修复后各面"]["全量对账"]
    checks = {
        "修复前识别分裂未满百无标": "分裂·未满百无标" in before["缺口标"]
        or before["对账结论"] == "分裂·未满百无标",
        "修复前识别实例未全切": "半截·实例未全切" in before["缺口标"],
        "重放有修复项": len(rep["重放"]["修复项"]) > 0,
        "修复后挂未满百标": rep["修复后各面"]["发版单"]["未满百全量"] is True,
        "修复后未满百可核": after["未满百可核"] is True,
        "修复后实例全切": after["全切"] is True,
        "修复后结论合法": after["对账结论"] == "未满百全量可核",
        "比例真源未被回改": rep["修复后各面"]["发版单"]["金丝雀比例"] == 30,
        "审计有重放证据": any(a["动作"] == "全量重放" for a in rep["修复后各面"]["审计"]),
    }
    return {"通过": all(checks.values()), "断言": checks}


def scenario_atomic_fail() -> dict:
    rid = None
    with db() as conn:
        rid = make_ready_release(conn, 30)
    error = None
    try:
        with db() as conn:
            r = conn.execute("SELECT * FROM releases WHERE id=?", (rid,)).fetchone()
            execute_full_cutover(conn, r, note="原子失败夹具", fault="全切后失败")
    except HTTPException as e:
        error = e.detail
    with db() as conn:
        r = conn.execute("SELECT status, early_full, full_receipt_no FROM releases WHERE id=?", (rid,)).fetchone()
        inst = conn.execute(
            """
            SELECT COUNT(*) AS c FROM instances i JOIN releases r ON r.app_id=i.app_id
            WHERE r.id=? AND i.env_id=(SELECT id FROM environments WHERE code='产')
              AND i.version=(SELECT target_version FROM releases WHERE id=?)
            """,
            (rid, rid),
        ).fetchone()["c"]
        receipts = conn.execute("SELECT COUNT(*) AS c FROM cutover_receipts WHERE release_id=?", (rid,)).fetchone()["c"]
        status = r["status"]
    checks = {
        "故障写口返回错误": bool(error),
        "单子状态未改成已放行": status != "已放行",
        "没有落全量回执": receipts == 0,
        "产环境没有目标版本实例(未提前全切)": inst == 0,
    }
    return {"场景": "原子失败", "发版单编号": rid, "错误": error, "最终状态": status,
            "自检": {"通过": all(checks.values()), "断言": checks}}


def scenario_concurrent() -> dict:
    rid = None
    with db() as conn:
        rid = make_ready_release(conn, 80)
    barrier = threading.Barrier(2)
    outcomes: dict[str, object] = {}

    def change_ratio():
        try:
            barrier.wait(timeout=5)
            with db() as conn:
                row = conn.execute("SELECT status FROM releases WHERE id=?", (rid,)).fetchone()
                if row["status"] in ("已放行", "已回滚", "已关闭"):
                    raise HTTPException(400, "已放行单比例冻结")
                conn.execute("UPDATE releases SET canary_ratio=? WHERE id=?", (45, rid))
                audit(conn, "金丝雀", rid, "改比例", "比例 80%→45%（并发夹具）")
            outcomes["改比例"] = 200
        except HTTPException as e:
            outcomes["改比例"] = e.status_code
        except Exception as e:  # busy 等也必须是明确失败，不能脏写
            outcomes["改比例"] = f"exc:{type(e).__name__}"

    def full():
        try:
            barrier.wait(timeout=5)
            with db() as conn:
                r = conn.execute("SELECT * FROM releases WHERE id=?", (rid,)).fetchone()
                execute_full_cutover(conn, r, note="并发全量夹具")
            outcomes["全量"] = 200
        except HTTPException as e:
            outcomes["全量"] = e.status_code
        except Exception as e:
            outcomes["全量"] = f"exc:{type(e).__name__}"

    t1 = threading.Thread(target=change_ratio)
    t2 = threading.Thread(target=full)
    t1.start(); t2.start(); t1.join(15); t2.join(15)

    with db() as conn:
        final = cutover_view(conn, rid)
        rel = release_to_dict(conn.execute(RELEASE_SELECT + " WHERE r.id=?", (rid,)).fetchone())

    # 串行后唯一合法结局：若全量成功，落成快照必须等于真源比例，标记与是否未满百一致；
    # 若改比例先提交，则全量快照45并挂标；若全量先提交，则改比例被冻结拒写，快照80不挂标。
    consistent = True
    reason = []
    if outcomes.get("全量") == 200:
        if rel["状态"] != "已放行":
            consistent = False; reason.append("全量200但状态非已放行")
        if rel["全量时比例"] != rel["金丝雀比例"]:
            consistent = False; reason.append("落成快照与真源比例分叉")
        expect_early = rel["金丝雀比例"] < 100
        if bool(rel["未满百全量"]) != expect_early:
            consistent = False; reason.append("未满百标与比例不一致")
        if final["对账结论"] not in ("未满百全量可核", "全量一致"):
            consistent = False; reason.append(f"对账结论非常态：{final['对账结论']}")
    checks = {
        "两笔都有明确结果": outcomes.get("改比例") in (200, 400, 409) and outcomes.get("全量") in (200, 400, 409),
        "无数据库锁异常外泄": not str(outcomes).startswith("exc:") and "exc:" not in str(outcomes),
        "只允许一种一致结局": consistent,
    }
    return {"场景": "并发改比例", "发版单编号": rid, "各笔结果": outcomes,
            "发版单": rel, "全量对账": final, "一致说明": reason,
            "自检": {"通过": all(checks.values()), "断言": checks}}


def run_suite() -> dict:
    reports = [
        scenario_early_full(),
        scenario_full_hundred(),
        scenario_concurrent(),
        scenario_half(),
        scenario_atomic_fail(),
    ]
    results = [
        {"场景": r.get("场景"), "通过": r.get("自检", {}).get("通过"),
         "断言": r.get("自检", {}).get("断言")}
        for r in reports
    ]
    return {
        "全部通过": all(x["通过"] for x in results),
        "场景数": len(results),
        "结果": results,
    }

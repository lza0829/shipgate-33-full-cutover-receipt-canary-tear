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
    view = cutover_view(conn, rid)
    release = release_to_dict(row, view)
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
        rel = release_to_dict(
            conn.execute(RELEASE_SELECT + " WHERE r.id=?", (rid,)).fetchone(), final
        )

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


def scenario_receipt_green_ratio_gap() -> dict:
    """夹具一：回执绿、比例未满。

    外部/旁路造出“全量回执已在且状态已放行”，但比例真源 40%、无未满百标、
    全量格不得点亮。断言：切满探针未过线、全量格=缺口·未全量、总览全量格不亮。
    """
    with db() as conn:
        rid = make_ready_release(conn, 40)
        r = conn.execute("SELECT * FROM releases WHERE id=?", (rid,)).fetchone()
        app_id = r["app_id"]
        prod = _env_id(conn, "产")
        now = now_str()
        target = r["target_version"]
        # 回执先绿：比例快照 40、未挂未满百标、名册与实例都已切（唯一缺的是“标”）
        _insert_receipt_green(conn, rid, ratio=40, roster_version=target, now=now)
        conn.execute(
            """
            UPDATE releases SET status='已放行', current_env_id=?, released_at=?,
                                full_ratio=40, early_full=0, full_receipt_no=?,
                                full_cutover_at=?
            WHERE id=?
            """,
            (prod, now, "QL-FIXTURE-GREEN", now, rid),
        )
        conn.execute(
            """
            INSERT INTO app_env_versions(app_id, env_id, version, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(app_id, env_id) DO UPDATE SET version=excluded.version
            """,
            (app_id, prod, target, now),
        )
        conn.execute(
            """
            INSERT INTO instances(app_id, env_id, version, health, release_id, traffic_ratio, updated_at)
            VALUES (?, ?, ?, '探针绿', ?, 100, ?)
            """,
            (app_id, prod, target, rid, now),
        )
        audit(conn, "全量", rid, "全量放行", "旁路假绿：回执已落、比例 40% 未满百")
        before = cutover_view(conn, rid)
        before_rel = release_to_dict(
            conn.execute(RELEASE_SELECT + " WHERE r.id=?", (rid,)).fetchone(), before
        )
        replay = replay_release(conn, rid, fix=True)
        after = cutover_view(conn, rid)
        after_rel = release_to_dict(
            conn.execute(RELEASE_SELECT + " WHERE r.id=?", (rid,)).fetchone(), after
        )
        overview = _overview_side_counts(conn, rid)
    checks = {
        "回执绿时切满探针未过线": before["切满探针"]["过线"] is False,
        "回执绿时钉住比例缺口": "比例未到百分百" in before["切满探针"]["未过原因"],
        "回执绿时全量格不亮满百": before["全量格"] != "满百全量",
        "回执绿时全量格报缺口": before["全量格"] == "缺口·未全量",
        "回执绿时结论为分裂无标": before["对账结论"] == "分裂·未满百无标",
        "总览旁侧格不把该单计成满百": overview["满百全量"] == 0,
        "重放补挂未满百标": "补挂未满百全量标" in replay["修复项"],
        "重放后比例真源未回改": after["真源比例"] == 40,
        "重放后切满差额钉住60": after["切满差额"] == 60,
        "重放后全量格为未满百": after["全量格"] == "未满百全量",
    }
    return {
        "场景": "回执绿比例未满",
        "发版单编号": rid,
        "修复前": before_rel,
        "重放": replay,
        "修复后": after_rel,
        "自检": {"通过": all(checks.values()), "断言": checks},
    }


def _insert_receipt_green(conn, rid: int, *, ratio: int, roster_version: str, now: str) -> None:
    conn.execute(
        """
        INSERT INTO cutover_receipts(
          receipt_no, release_id, ratio_at_cutover, required_ratio, early_full, status,
          roster_version, switched_instances, total_instances, detail, created_at
        ) VALUES ('QL-FIXTURE-GREEN', ?, ?, 100, 0, '全量落成', ?, 1, 1, ?, ?)
        """,
        (rid, ratio, roster_version, '{"source":"fixture-bypass"}', now),
    )


def _overview_side_counts(conn, rid: int | None = None) -> dict:
    """旁侧计数格（与总览同一套派生：按切满重放的全量格计数）。

    夹具套件共用一库，故按 rid 只统计本单贡献；端点级隔离断言见 pytest。
    """
    rows = conn.execute(RELEASE_SELECT + " WHERE r.status='已放行'").fetchall()
    views = [cutover_view(conn, r["编号"]) for r in rows]
    views = [v for v in views if v and (rid is None or v["编号"] == rid)]
    return {
        "已放行": len(views),
        "满百全量": sum(1 for v in views if v["全量格"] == "满百全量"),
        "未满百全量": sum(1 for v in views if v["全量格"] == "未满百全量"),
        "切满探针过线": sum(1 for v in views if v["切满探针"]["过线"]),
    }


def scenario_ratio_full_mark_missing() -> dict:
    """夹具二：比例满、全量标缺。

    比例真源 100，但未满百标被旁路挂上（early_full=1，回执也按 early 落），
    “比例满了全量格却标缺（未满百）”。断言：切满探针须过线、全量格须=满百全量、
    重放须清掉未满百标而不是把满百单继续写成未满百。
    """
    with db() as conn:
        rid = make_ready_release(conn, 100)
        r = conn.execute("SELECT * FROM releases WHERE id=?", (rid,)).fetchone()
        app_id = r["app_id"]
        prod = _env_id(conn, "产")
        now = now_str()
        target = r["target_version"]
        # 旁路：比例满百但误挂未满百标 + 回执按 early 落
        conn.execute(
            """
            INSERT INTO cutover_receipts(
              receipt_no, release_id, ratio_at_cutover, required_ratio, early_full, status,
              roster_version, switched_instances, total_instances, detail, created_at
            ) VALUES ('QL-FIXTURE-STALE', ?, 100, 100, 1, '全量落成', ?, 1, 1, '{}', ?)
            """,
            (rid, target, now),
        )
        conn.execute(
            """
            UPDATE releases SET status='已放行', current_env_id=?, released_at=?,
                                full_ratio=100, early_full=1, full_receipt_no='QL-FIXTURE-STALE',
                                full_cutover_at=?
            WHERE id=?
            """,
            (prod, now, now, rid),
        )
        conn.execute(
            """
            INSERT INTO app_env_versions(app_id, env_id, version, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(app_id, env_id) DO UPDATE SET version=excluded.version
            """,
            (app_id, prod, target, now),
        )
        conn.execute(
            """
            INSERT INTO instances(app_id, env_id, version, health, release_id, traffic_ratio, updated_at)
            VALUES (?, ?, ?, '探针绿', ?, 100, ?)
            """,
            (app_id, prod, target, rid, now),
        )
        audit(conn, "全量", rid, "全量放行", "旁路假标：比例 100% 却挂未满百")
        before = cutover_view(conn, rid)
        replay = replay_release(conn, rid, fix=True)
        after = cutover_view(conn, rid)
    checks = {
        "满百却挂标被识别": before["对账结论"] == "分裂·满百仍挂未满百标",
        "满百挂标时全量格不标未满百": before["全量格"] != "未满百全量",
        "满百挂标时探针不过线": before["切满探针"]["过线"] is False,
        "重放清除满百仍挂的未满百标": "清除满百仍挂的未满百标" in replay["修复项"],
        "重放后全量格=满百全量": after["全量格"] == "满百全量",
        "重放后切满探针过线": after["切满探针"]["过线"] is True,
        "重放后结论=全量一致": after["对账结论"] == "全量一致",
        "重放后比例真源仍100": after["真源比例"] == 100,
    }
    return {
        "场景": "比例满全量标缺",
        "发版单编号": rid,
        "修复前": before,
        "重放": replay,
        "修复后": after,
        "自检": {"通过": all(checks.values()), "断言": checks},
    }


def scenario_double_cell_twisted() -> dict:
    """夹具三：全量格与旁侧计数格双拧花。

    总览旁侧计数格自称“满百全量 1”，而该单切满探针未过线（回执在但实例未全切），
    两格互相拧花。断言：旁侧计数格与全量格必须同源，未切满不得计入满百。
    """
    with db() as conn:
        rid = make_ready_release(conn, 100)
        r = conn.execute("SELECT * FROM releases WHERE id=?", (rid,)).fetchone()
        app_id = r["app_id"]
        prod = _env_id(conn, "产")
        now = now_str()
        target = r["target_version"]
        conn.execute(
            """
            INSERT INTO cutover_receipts(
              receipt_no, release_id, ratio_at_cutover, required_ratio, early_full, status,
              roster_version, switched_instances, total_instances, detail, created_at
            ) VALUES ('QL-FIXTURE-TWIST', ?, 100, 100, 0, '全量落成', ?, 1, 2, '{}', ?)
            """,
            (rid, target, now),
        )
        conn.execute(
            """
            UPDATE releases SET status='已放行', current_env_id=?, released_at=?,
                                full_ratio=100, early_full=0, full_receipt_no='QL-FIXTURE-TWIST'
            WHERE id=?
            """,
            (prod, now, rid),
        )
        # 名册切了，但产环境两条实例只切了一条（另一条旧版本仍是 100 流量）→ 未全切
        conn.execute(
            """
            INSERT INTO app_env_versions(app_id, env_id, version, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(app_id, env_id) DO UPDATE SET version=excluded.version
            """,
            (app_id, prod, target, now),
        )
        conn.execute(
            """
            INSERT INTO instances(app_id, env_id, version, health, release_id, traffic_ratio, updated_at)
            VALUES (?, ?, ?, '探针绿', ?, 100, ?)
            """,
            (app_id, prod, target, rid, now),
        )
        conn.execute(
            """
            INSERT INTO instances(app_id, env_id, version, health, release_id, traffic_ratio, updated_at)
            VALUES (?, ?, '0.9.0-old', '探针绿', NULL, 55, ?)
            """,
            (app_id, prod, now),
        )
        view = cutover_view(conn, rid)
        side = _overview_side_counts(conn, rid)
    checks = {
        "全量格不亮满百": view["全量格"] != "满百全量",
        "旁侧计数格不把未切满算成满百": side["满百全量"] == 0,
        "全量格与旁侧计数格同源": view["全量格"] != "满百全量" and side["满百全量"] == 0,
        "切满探针未过线": view["切满探针"]["过线"] is False,
        "缺口标含实例未全切": "半截·实例未全切" in view["阻塞缺口"],
        "结论文本为实例未全切": view["对账结论"] == "半截·实例未全切",
    }
    return {
        "场景": "全量格与旁侧格双拧",
        "发版单编号": rid,
        "全量对账": view,
        "旁侧计数格": side,
        "自检": {"通过": all(checks.values()), "断言": checks},
    }


def scenario_bypass_half() -> dict:
    """夹具四：全量写口与日常放行旁路交错半截。

    模拟“日常晋升写口没有走全量闸，只把实例/名册切了一半”的旁路交错：
    单子被旁路标成已放行、名册已切、但实例只切部分且无回执。
    断言：切满探针未过线、全量格不亮、重放须整笔收口到合法态。
    """
    with db() as conn:
        rid = make_ready_release(conn, 100)
        r = conn.execute("SELECT * FROM releases WHERE id=?", (rid,)).fetchone()
        app_id = r["app_id"]
        prod = _env_id(conn, "产")
        now = now_str()
        target = r["target_version"]
        # 旁路：日常放行写口直接改状态 + 名册，不走全量闸，实例切一半
        conn.execute(
            """
            UPDATE releases SET status='已放行', current_env_id=?, released_at=?,
                                full_ratio=NULL, early_full=0, full_receipt_no=''
            WHERE id=?
            """,
            (prod, now, rid),
        )
        conn.execute(
            """
            INSERT INTO app_env_versions(app_id, env_id, version, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(app_id, env_id) DO UPDATE SET version=excluded.version
            """,
            (app_id, prod, target, now),
        )
        conn.execute(
            """
            INSERT INTO instances(app_id, env_id, version, health, release_id, traffic_ratio, updated_at)
            VALUES (?, ?, ?, '探针绿', ?, 100, ?)
            """,
            (app_id, prod, target, rid, now),
        )
        conn.execute(
            """
            INSERT INTO instances(app_id, env_id, version, health, release_id, traffic_ratio, updated_at)
            VALUES (?, ?, '1.9.0-old', '探针绿', NULL, 30, ?)
            """,
            (app_id, prod, now),
        )
        audit(conn, "晋升", rid, "晋升到产", "旁路：日常放行写口半截")
        before = cutover_view(conn, rid)
        replay = replay_release(conn, rid, fix=True)
        after = cutover_view(conn, rid)
    checks = {
        "旁路半截识别实例未全切": "半截·实例未全切" in before["阻塞缺口"],
        "旁路半截识别无全量回执": "半截·无全量回执" in before["阻塞缺口"],
        "旁路半截识别无比例快照": "半截·无全量比例快照" in before["阻塞缺口"],
        "旁路半截全量格不亮": before["全量格"] == "缺口·未全量",
        "旁路半截探针不过线": before["切满探针"]["过线"] is False,
        "重放补写补证回执": "写重放补证回执" in replay["修复项"],
        "重放后实例全切": after["全切"] is True,
        "重放后切满探针过线": after["切满探针"]["过线"] is True,
        "重放后结论=全量一致": after["对账结论"] == "全量一致",
    }
    return {
        "场景": "全量与日常放行旁路半截",
        "发版单编号": rid,
        "修复前": before,
        "重放": replay,
        "修复后": after,
        "自检": {"通过": all(checks.values()), "断言": checks},
    }


def run_suite() -> dict:
    reports = [
        scenario_early_full(),
        scenario_full_hundred(),
        scenario_concurrent(),
        scenario_half(),
        scenario_atomic_fail(),
        scenario_receipt_green_ratio_gap(),
        scenario_ratio_full_mark_missing(),
        scenario_double_cell_twisted(),
        scenario_bypass_half(),
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

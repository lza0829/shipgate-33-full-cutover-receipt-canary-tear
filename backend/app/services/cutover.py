"""全量落成闸唯一核心：写口、读侧评估、重放收口都从这里出。

本站锁定两策中的乙策（Arm B）：

    EARLY_FULL_MARKED —— 允许比例未满百时提前全量，
    但“未满百”必须落持久标（releases.early_full + 全量时比例 + 全量回执），
    发版单、名册、实例切流、总览、对账、审计共用同一套完成定义，
    任何一面都不得把未满百全量静默算成满百一致。

写路径必须在调用方已开启的 db() 写事务内调用 execute_full_cutover / replay_release，
状态推进、比例快照、挂标、名册产版本、实例全切、回执、审计同笔提交，
任何一步抛错整笔回滚，不会留下“回执失败、实例已全切”的半截。
"""
from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4

from fastapi import HTTPException

from app.services.common import (
    active_release_window,
    audit,
    env_by_code,
    get_setting,
    now_str,
    set_app_env_version,
)

POLICY_CODE = "EARLY_FULL_MARKED"
POLICY_NAME = "允许提前全量·未满百留标可核"
FULL_RATIO = 100

# 全站统一结论码（中文即规范值，各面不得另造近义词）
V_OK_FULL = "全量一致"              # 满百且各面齐
V_EARLY_VERIFIABLE = "未满百全量可核"  # 乙策合法态：有未满百标+回执/审计，各面齐
V_NOT_FULL = "未全量"
V_SPLIT_NO_MARK = "分裂·未满百无标"  # 比例未满却已放行且没有未满百标
V_HALF_RECEIPT = "半截·无全量回执"
V_HALF_ROSTER = "半截·名册版本未切"
V_HALF_NO_INSTANCE = "缺口·无实例挂接"
V_HALF_INSTANCE = "半截·实例未全切"

ACTIVE_FOR_FULL = ("已入闸", "晋升中")


def policy_descriptor() -> dict:
    return {
        "策略码": POLICY_CODE,
        "策略": POLICY_NAME,
        "完成比例": FULL_RATIO,
        "口径": (
            "比例未满百允许提前全量，但必须同笔挂『未满百全量』持久标、"
            "快照全量时比例、写全量回执与审计；总览露出未满百全量，"
            "对账按标核验，不得假装金丝雀已跑满。"
        ),
        "互斥备选": "HARD_REJECT_BELOW_100（本站不采用）",
    }


def is_early_ratio(ratio) -> bool:
    try:
        return int(ratio) < FULL_RATIO
    except (TypeError, ValueError):
        return True


def new_receipt_no(release_id: int) -> str:
    return f"QL{datetime.now().strftime('%Y%m%d%H%M%S')}-{release_id}-{uuid4().hex[:6]}"


def _prod_env(conn):
    return env_by_code(conn, "产")


def converge_prod_instances(conn, app_id: int, prod_id: int, target_version: str,
                           release_id: int, now: str) -> dict:
    """把生产槽位的实例整笔切到目标版本且切流比例 100%。

    保留各自行的探针灯色（健康面事实不被写口抹绿）；同版本重复行收敛为一条。
    """
    before = conn.execute(
        "SELECT id, version FROM instances WHERE app_id=? AND env_id=? ORDER BY id",
        (app_id, prod_id),
    ).fetchall()
    switched = 0
    target_row = next((r for r in before if r["version"] == target_version), None)
    if target_row is None:
        cur = conn.execute(
            """
            INSERT INTO instances(app_id, env_id, version, health, release_id, traffic_ratio, updated_at)
            VALUES (?, ?, ?, '探针绿', ?, 100, ?)
            """,
            (app_id, prod_id, target_version, release_id, now),
        )
        keeper = cur.lastrowid
        switched += 1
    else:
        keeper = target_row["id"]
        conn.execute(
            "UPDATE instances SET release_id=?, traffic_ratio=100, updated_at=? WHERE id=?",
            (release_id, now, keeper),
        )
        switched += 1
    # 其余旧版本实例整批切到目标版本（全切，不留半切流量）
    cur = conn.execute(
        """
        UPDATE instances SET version=?, release_id=?, traffic_ratio=100, updated_at=?
        WHERE app_id=? AND env_id=? AND id<>?
        """,
        (target_version, release_id, now, app_id, prod_id, keeper),
    )
    switched += cur.rowcount or 0
    # 版本收敛后可能出现同版本重复行，去重保留最早一条
    dup_ids = [
        r["id"]
        for r in conn.execute(
            """
            SELECT id FROM instances
            WHERE app_id=? AND env_id=? AND version=? AND id<>? ORDER BY id
            """,
            (app_id, prod_id, target_version, keeper),
        ).fetchall()
    ]
    if dup_ids:
        conn.executemany("DELETE FROM instances WHERE id=?", [(i,) for i in dup_ids])
    conn.execute(
        "UPDATE instances SET release_id=?, traffic_ratio=100 WHERE id=?",
        (release_id, keeper),
    )
    final = conn.execute(
        "SELECT id, version, traffic_ratio, health FROM instances WHERE app_id=? AND env_id=? ORDER BY id",
        (app_id, prod_id),
    ).fetchall()
    return {
        "切换行数": switched,
        "去重删除": len(dup_ids),
        "实例总数": len(final),
        "实例编号们": [r["id"] for r in final],
        "全切": all(r["version"] == target_version and int(r["traffic_ratio"] or 0) >= 100 for r in final)
        and len(final) > 0,
    }


def _insert_receipt(conn, *, receipt_no: str, release_id: int, ratio: int, early: bool,
                    roster_version: str, conv: dict, now: str, status: str = "全量落成",
                    detail: dict | None = None) -> None:
    conn.execute(
        """
        INSERT INTO cutover_receipts(
          receipt_no, release_id, ratio_at_cutover, required_ratio, early_full, status,
          roster_version, switched_instances, total_instances, detail, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            receipt_no, release_id, ratio, FULL_RATIO, 1 if early else 0, status,
            roster_version, int(conv.get("切换行数", 0)), int(conv.get("实例总数", 0)),
            json.dumps(detail or {}, ensure_ascii=False), now,
        ),
    )


def latest_receipt(conn, release_id: int):
    return conn.execute(
        "SELECT * FROM cutover_receipts WHERE release_id=? ORDER BY id DESC LIMIT 1",
        (release_id,),
    ).fetchone()


def receipt_to_dict(row) -> dict:
    if row is None:
        return {}
    d = dict(row)
    try:
        d["detail"] = json.loads(d.get("detail") or "{}")
    except json.JSONDecodeError:
        pass
    d["early_full"] = bool(d.get("early_full"))
    d["未满百全量"] = bool(d.get("early_full"))
    d["回执号"] = d.get("receipt_no")
    d["发版单编号"] = d.get("release_id")
    d["全量时比例"] = d.get("ratio_at_cutover")
    d["结论"] = d.get("status")
    return d


def execute_full_cutover(conn, r, *, note: str = "", source: str = "全量放行",
                         fault: str | None = None) -> dict:
    """在已开启的写事务内完成全量落成的全部同笔写入。r = releases.* 原始行。"""
    release_id = r["id"]
    if r["status"] not in ACTIVE_FOR_FULL:
        raise HTTPException(400, f"当前状态 {r['status']} 不可全量放行")

    prod = _prod_env(conn)
    # 与晋升写口同一道生产放行窗闸，避免两个入口口径分裂
    hard = get_setting(conn, "维护窗硬拒", "是") == "是"
    win = active_release_window(conn, prod["id"])
    if hard and not win:
        raise HTTPException(400, "生产环境放行窗已关，禁止全量放行")

    ratio = int(r["canary_ratio"])
    early = is_early_ratio(ratio)
    now = now_str()
    receipt_no = new_receipt_no(release_id)

    # 条件更新 + rowcount：并发第二笔撞状态时不会重复落成
    cur = conn.execute(
        """
        UPDATE releases
           SET status='已放行', current_env_id=?, released_at=COALESCE(released_at, ?),
               full_cutover_at=?, full_ratio=?, early_full=?, full_receipt_no=?
         WHERE id=? AND status IN ('已入闸','晋升中')
        """,
        (prod["id"], now, now, ratio, 1 if early else 0, receipt_no, release_id),
    )
    if cur.rowcount != 1:
        raise HTTPException(409, "发版单状态已被并发改动，全量放行未落成")

    conn.execute(
        """
        INSERT INTO promote_events(release_id, from_env_id, to_env_id, decision, created_at, note)
        VALUES (?, ?, ?, '放行', ?, ?)
        """,
        (release_id, r["current_env_id"], prod["id"], now, note or source),
    )

    # 名册产版本推进 —— 与全量动作同成败
    set_app_env_version(conn, r["app_id"], prod["id"], r["target_version"])

    # 实例整笔全切
    conv = converge_prod_instances(
        conn, r["app_id"], prod["id"], r["target_version"], release_id, now
    )

    # 故障注入：模拟“全量回执写口失败”。此前所有写入与回执同事务，必须整笔回滚，
    # 绝不允许实例已全切而单子/回执未落。抛 HTTPException 同样穿过 db() 的回滚。
    if fault in ("全切后失败", "receipt_fail", "half_after_switch"):
        raise HTTPException(409, "故障注入：全量回执写口失败，整笔回滚")

    _insert_receipt(
        conn,
        receipt_no=receipt_no,
        release_id=release_id,
        ratio=ratio,
        early=early,
        roster_version=r["target_version"],
        conv=conv,
        now=now,
        detail={"source": source, "note": note, **conv},
    )

    marker_text = "，未满百全量标已挂" if early else ""
    audit(
        conn,
        "全量",
        release_id,
        "全量放行",
        f"{source}：比例 {ratio}%（要求 {FULL_RATIO}%）{marker_text}，"
        f"回执 {receipt_no}，产名册 {r['target_version']}，实例全切 {conv['全切']}，备注：{note or '无'}",
    )
    receipt = receipt_to_dict(latest_receipt(conn, release_id))
    receipt["实例切换"] = conv
    return receipt


def _slot_facts(conn, r) -> dict:
    prod = _prod_env(conn)
    version_row = conn.execute(
        "SELECT version FROM app_env_versions WHERE app_id=? AND env_id=?",
        (r["app_id"], prod["id"]),
    ).fetchone()
    roster_version = version_row["version"] if version_row else ""
    inst = conn.execute(
        "SELECT * FROM instances WHERE app_id=? AND env_id=? ORDER BY id",
        (r["app_id"], prod["id"]),
    ).fetchall()
    target = r["target_version"]
    total = len(inst)
    on_target = [i for i in inst if i["version"] == target]
    all_switched = (
        total > 0
        and len(on_target) == total
        and all(int(i["traffic_ratio"] or 0) >= FULL_RATIO for i in inst)
    )
    switch_ratio = 100 if all_switched else (round(100 * len(on_target) / total) if total else 0)
    red = sum(1 for i in inst if i["health"] == "探针红")
    return {
        "产环境": prod["code"],
        "名册版本": roster_version,
        "名册一致": roster_version == target,
        "实例总数": total,
        "目标实例数": len(on_target),
        "红灯数": red,
        "全切": all_switched,
        "切流比例": switch_ratio,
        "实例": [dict(i) for i in inst],
    }


def cutover_view(conn, release_id: int) -> dict | None:
    """读侧可重放：每次都从 releases / 回执 / 名册 / 实例 / 审计现存事实重算结论。"""
    r = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
    if r is None:
        return None
    receipt = latest_receipt(conn, release_id)
    facts = _slot_facts(conn, r)
    audit_n = conn.execute(
        "SELECT COUNT(*) AS c FROM audit_events WHERE ref_id=? AND action IN ('全量放行','全量重放补标','全量重放')",
        (release_id,),
    ).fetchone()["c"]
    ratio = int(r["canary_ratio"])
    full_ratio = r["full_ratio"]
    full_ratio = int(full_ratio) if full_ratio is not None else None
    early = bool(r["early_full"])
    released = r["status"] == "已放行"

    issues: list[str] = []
    if released:
        if ratio < FULL_RATIO and not early:
            issues.append(V_SPLIT_NO_MARK)
        if receipt is None:
            issues.append(V_HALF_RECEIPT)
        if not facts["名册一致"]:
            issues.append(V_HALF_ROSTER)
        if facts["实例总数"] == 0:
            issues.append(V_HALF_NO_INSTANCE)
        elif not facts["全切"]:
            issues.append(V_HALF_INSTANCE)

    if not released:
        verdict = V_NOT_FULL
    elif issues:
        if V_SPLIT_NO_MARK in issues:
            verdict = V_SPLIT_NO_MARK
        elif V_HALF_ROSTER in issues:
            verdict = V_HALF_ROSTER
        elif V_HALF_INSTANCE in issues:
            verdict = V_HALF_INSTANCE
        elif V_HALF_NO_INSTANCE in issues:
            verdict = V_HALF_NO_INSTANCE
        else:
            verdict = issues[0]
    else:
        verdict = V_EARLY_VERIFIABLE if early else V_OK_FULL

    verifiable = bool(
        released and ratio < FULL_RATIO and early and (receipt is not None or audit_n > 0)
    )
    return {
        "编号": r["id"],
        "发版单号": r["serial"],
        "应用编号": r["app_id"],
        "目标版本": r["target_version"],
        "状态": r["status"],
        "金丝雀比例": ratio,
        "真源比例": ratio,
        "全量时比例": full_ratio,
        "回放比例": full_ratio if full_ratio is not None else ratio,
        "差额": ratio - (full_ratio if full_ratio is not None else ratio),
        "未满百全量": early,
        "提前全量": early,
        "未满百标记": "未满百全量" if early else "",
        "未满百可核": verifiable,
        "全量回执号": r["full_receipt_no"] or (receipt["receipt_no"] if receipt else ""),
        "回执存在": receipt is not None,
        "审计证据数": audit_n,
        "对账结论": verdict,
        "缺口标": issues,
        **{k: v for k, v in facts.items() if k != "实例"},
    }


def replay_release(conn, release_id: int, *, fix: bool = True) -> dict:
    """按同一套完成定义重放/收口一笔发版单。

    - 已放行但未满百无标：补挂未满百标与比例快照/补证回执（不回改真源比例）；
    - 已放行但名册/实例缺口：把名册与实例收敛到终态；
    - 在途但名册+实例已被全切（典型“回执失败却已全切”）：补完终态与回执。
    所有修复与审计同事务；fix=False 只出评估报告。
    """
    r = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
    if r is None:
        raise HTTPException(404, "发版单不存在")
    before = cutover_view(conn, release_id)
    actions: list[str] = []
    now = now_str()

    if fix:
        ratio = int(r["canary_ratio"])
        prod = _prod_env(conn)
        fully_switched_evidence = before["名册一致"] or (
            before["实例总数"] > 0 and before["全切"]
        )

        if r["status"] == "已放行":
            sets, params = [], []
            if ratio < FULL_RATIO and not r["early_full"]:
                sets.append("early_full=1")
                actions.append("补挂未满百全量标")
            if r["full_ratio"] is None:
                sets.append("full_ratio=?")
                params.append(ratio)
                actions.append("快照全量时比例")
            if not r["full_receipt_no"]:
                receipt_no = new_receipt_no(release_id)
                sets.append("full_receipt_no=?")
                params.append(receipt_no)
                actions.append("补全量回执号")
            if r["full_cutover_at"] is None:
                sets.append("full_cutover_at=?")
                params.append(r["released_at"] or now)
            if sets:
                params.append(release_id)
                conn.execute(f"UPDATE releases SET {', '.join(sets)} WHERE id=?", params)

            if not before["名册一致"]:
                set_app_env_version(conn, r["app_id"], prod["id"], r["target_version"])
                actions.append("名册产版本补切")
            conv = converge_prod_instances(
                conn, r["app_id"], prod["id"], r["target_version"], release_id, now
            )
            if not before["全切"]:
                actions.append("实例补全切")

            if latest_receipt(conn, release_id) is None:
                receipt_no = r["full_receipt_no"] or new_receipt_no(release_id)
                _insert_receipt(
                    conn,
                    receipt_no=receipt_no,
                    release_id=release_id,
                    ratio=ratio,
                    early=ratio < FULL_RATIO,
                    roster_version=r["target_version"],
                    conv=conv,
                    now=now,
                    status="重放补证",
                    detail={"replay": True, "actions": actions},
                )
                actions.append("写重放补证回执")
            if actions:
                audit(conn, "全量", release_id, "全量重放", "；".join(actions) or "无差异")

        elif r["status"] in ACTIVE_FOR_FULL and fully_switched_evidence:
            # 终态事实已经切出去了，只差单子/回执：按乙策补完成，比例事实原样挂标
            receipt = execute_full_cutover(conn, r, note="读侧重放：按已全切事实补完成",
                                           source="对账重放")
            actions.append("在途单按全切证据补全量落成")
        # 其余在途单没有全切证据：保持在途，不动真源

    after = cutover_view(conn, release_id)
    return {
        "编号": release_id,
        "修复": fix,
        "修复项": actions,
        "修复前结论": before["对账结论"] if before else None,
        "修复后结论": after["对账结论"] if after else None,
        "修复前缺口": before["缺口标"] if before else [],
        "修复后缺口": after["缺口标"] if after else [],
        "after": after,
    }

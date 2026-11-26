"""全量落成闸唯一核心：写口、读侧评估、重放收口都从这里出。

本站锁定两策中的乙策（Arm B）：

    EARLY_FULL_MARKED —— 允许比例未满百时提前全量，
    但“未满百”必须落持久标（releases.early_full + 全量时比例 + 全量回执），
    发版单、名册、实例切流、总览、对账、审计共用同一套完成定义，
    任何一面都不得把未满百全量静默算成满百一致。

收口口径（本事故唯一验收链）：

    切满探针 ∪ 全量事件 ∪ 全量格 ∪ 审计重放 —— 四者同命运。
    - 写侧：全量写口同笔事务内落成后必须由切满探针复核过线，否则整笔退回；
    - 读侧：回执可以先在（全量回执可先绿），但切满探针未过线或比例未到百分百，
      一律钉缺口；缺口未清不得把未切满标成“满百全量”；
    - 重放：必须揭穿“回执绿但未切满”的假全量回执并更正，而不是删证据。

切满探针过线（“已切满”的唯一定义）：
    比例真源 == 100 且 全量事件（回执+回执号）在 且 名册一致 且 产实例全切
    且 审计证据在 且 无任何结构性缺口（标/快照/回执标记不得与真源分叉）。
    比例 < 100 时（哪怕乙策下合法留标），探针一律“未过线”，只允许全量格
    显示为「未满百全量」并钉住「未满百差额」，绝不点亮成「满百全量」。

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
V_OK_FULL = "全量一致"                  # 满百且各面齐、切满探针过线
V_EARLY_VERIFIABLE = "未满百全量可核"      # 乙策合法态：有未满百标+回执+审计+实例全切
V_NOT_FULL = "未全量"
# 结构性缺口（阻塞，必须清掉；缺口未清不得标“满百全量”）
V_SPLIT_NO_MARK = "分裂·未满百无标"          # 比例未满、已放行，却没有未满百标
V_SPLIT_MARK_STALE = "分裂·满百仍挂未满百标"    # 比例已满百，未满百标却回滚不干净
V_SPLIT_SNAPSHOT = "分裂·全量快照与真源分叉"    # 全量时比例快照 != 真源比例
V_SPLIT_RECEIPT_MARK = "分裂·回执与单子分叉"    # 回执的未满百标记 / 当时比例 != 单子
V_HALF_RECEIPT = "半截·无全量回执"
V_HALF_SNAPSHOT = "半截·无全量比例快照"
V_HALF_ROSTER = "半截·名册版本未切"
V_HALF_NO_INSTANCE = "缺口·无实例挂接"
V_HALF_INSTANCE = "半截·实例未全切"
V_HALF_AUDIT = "半截·无审计证据"

# 非阻塞「钉住缺口」：乙策合法提前全量仍要钉住未满百差额，
# 全量格不得亮成「满百全量」，只能写成「未满百全量」。
PIN_RATIO_GAP = "未满百差额"

# 全量格取值（各面统一措辞，不得另造）
CELL_NOT_FULL = "未全量"
CELL_FULL = "满百全量"
CELL_EARLY = "未满百全量"
CELL_GAP = "缺口·未全量"

# 合法结论码：只有这两个才允许全量格点亮（且必须由切满探针 / 回执 / 审计同核重放得出）
LEGAL_FULL_VERDICTS = (V_OK_FULL, V_EARLY_VERIFIABLE)

ACTIVE_FOR_FULL = ("已入闸", "晋升中")

# 结论码优先级：越靠前越能代表事故主因（分裂 > 名册 > 实例 > 其他半截）
_VERDICT_ORDER = (
    V_SPLIT_NO_MARK,
    V_SPLIT_MARK_STALE,
    V_SPLIT_SNAPSHOT,
    V_SPLIT_RECEIPT_MARK,
    V_HALF_ROSTER,
    V_HALF_INSTANCE,
    V_HALF_NO_INSTANCE,
    V_HALF_RECEIPT,
    V_HALF_SNAPSHOT,
    V_HALF_AUDIT,
)


def policy_descriptor() -> dict:
    return {
        "策略码": POLICY_CODE,
        "策略": POLICY_NAME,
        "完成比例": FULL_RATIO,
        "口径": (
            "比例未满百允许提前全量，但必须同笔挂『未满百全量』持久标、"
            "快照全量时比例、写全量回执与审计；切满探针、全量事件、全量格、"
            "审计证据同命运重放，未切满一律钉缺口，不得点亮满百全量。"
        ),
        "切满定义": "比例真源100且回执在且名册一致且产实例全切且审计在且无结构性缺口",
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
    d["切满差额"] = max(0, FULL_RATIO - int(d.get("ratio_at_cutover") or 0))
    d["结论"] = d.get("status")
    return d


def _audit_evidence_count(conn, release_id: int) -> int:
    return conn.execute(
        """
        SELECT COUNT(*) AS c FROM audit_events
         WHERE ref_id=? AND action IN ('全量放行','全量重放','全量重放补标','全量更正')
        """,
        (release_id,),
    ).fetchone()["c"]


def execute_full_cutover(conn, r, *, note: str = "", source: str = "全量放行",
                         fault: str | None = None) -> dict:
    """在已开启的写事务内完成全量落成的全部同笔写入。r = releases.* 原始行。

    同笔事务末尾用切满探针复核；未过线 → 抛错整笔退回（不留“回执绿未切满”）。
    """
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
        f"回执 {receipt_no}，产名册 {r['target_version']}，实例全切 {conv['全切']}，"
        f"切满差额 {FULL_RATIO - ratio}，备注：{note or '无'}",
    )

    # 同笔事务自检：切满探针必须过线（满百）或至少是合法有标的未满百全量；
    # 任何结构性缺口 → 抛错，db() 整笔回滚，不存在“回执绿而未切满”的半截。
    probe_view = cutover_view(conn, release_id)
    if probe_view is None or probe_view["阻塞缺口"]:
        raise HTTPException(
            409,
            "切满探针未过线，整笔退回：" + "；".join(probe_view["阻塞缺口"] if probe_view else ["重放不可读"]),
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
    attached = [i for i in inst if i["release_id"] == r["id"]]
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
        "挂接实例数": len(attached),
        "红灯数": red,
        "全切": all_switched,
        "切流比例": switch_ratio,
        "实例": [dict(i) for i in inst],
    }


def cutover_view(conn, release_id: int) -> dict | None:
    """读侧可重放：每次都从 releases / 回执 / 名册 / 实例 / 审计现存事实重算结论。

    切满探针、全量事件、全量格、审计证据四者只读同一组事实，同命运。
    """
    r = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
    if r is None:
        return None
    receipt = latest_receipt(conn, release_id)
    facts = _slot_facts(conn, r)
    audit_n = _audit_evidence_count(conn, release_id)
    ratio = int(r["canary_ratio"])
    full_ratio = r["full_ratio"]
    full_ratio = int(full_ratio) if full_ratio is not None else None
    early = bool(r["early_full"])
    released = r["status"] == "已放行"
    receipt_no = r["full_receipt_no"] or ""

    blocking: list[str] = []
    if released:
        # 比例列与标：未满百必须有标；满百不得再挂未满百标（标回滚须干净）
        if ratio < FULL_RATIO and not early:
            blocking.append(V_SPLIT_NO_MARK)
        if ratio >= FULL_RATIO and early:
            blocking.append(V_SPLIT_MARK_STALE)
        # 全量时比例快照：必须存在且与真源比例一致
        if full_ratio is None:
            blocking.append(V_HALF_SNAPSHOT)
        elif full_ratio != ratio:
            blocking.append(V_SPLIT_SNAPSHOT)
        # 全量事件：回执行 + 回执号都得在
        if receipt is None or not receipt_no:
            blocking.append(V_HALF_RECEIPT)
        # 回执的标记 / 当时比例不得与单子分叉（假全量回执在这里被识破）
        if receipt is not None:
            if int(receipt["ratio_at_cutover"]) != (full_ratio if full_ratio is not None else ratio):
                blocking.append(V_SPLIT_RECEIPT_MARK)
            if bool(receipt["early_full"]) != early:
                blocking.append(V_SPLIT_RECEIPT_MARK)
        # 名册 / 实例 / 审计
        if not facts["名册一致"]:
            blocking.append(V_HALF_ROSTER)
        if facts["实例总数"] == 0:
            blocking.append(V_HALF_NO_INSTANCE)
        elif not facts["全切"]:
            blocking.append(V_HALF_INSTANCE)
        if audit_n == 0:
            blocking.append(V_HALF_AUDIT)

    # 钉住缺口：乙策合法提前全量仍有未满百差额，非阻塞但必须露出
    pinned = [PIN_RATIO_GAP] if (released and ratio < FULL_RATIO and not blocking) else []
    # 同码可能由多条事实触发（如回执比例与回执标记双双分叉），缺口标去重保序
    blocking = list(dict.fromkeys(blocking))
    gaps = blocking + pinned

    if not released:
        verdict = V_NOT_FULL
    elif blocking:
        verdict = next((code for code in _VERDICT_ORDER if code in blocking), blocking[0])
    else:
        verdict = V_EARLY_VERIFIABLE if early else V_OK_FULL

    # 切满探针：过线 == 真正切满（比例100 + 全量事件 + 名册 + 实例 + 审计 + 无结构性缺口）
    # 未过原因：先列结构性缺口（唯一措辞），再补非阻塞差额（比例/未放行），避免同义重复
    probe_reasons: list[str] = []
    if not released:
        probe_reasons.append("单子未放行")
    if ratio < FULL_RATIO:
        probe_reasons.append("比例未到百分百")
    probe_reasons.extend(blocking)
    probe_passed = released and ratio >= FULL_RATIO and not blocking

    # 全量格：证据式点亮，未切满一律不亮满百
    if not released:
        cell = CELL_NOT_FULL
    elif blocking:
        cell = CELL_GAP
    elif ratio < FULL_RATIO:
        cell = CELL_EARLY
    else:
        cell = CELL_FULL

    verifiable = bool(released and ratio < FULL_RATIO and early and not blocking)

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
        "切满差额": max(0, FULL_RATIO - ratio),
        "未满百全量": early,
        "提前全量": early,
        "未满百标记": "未满百全量" if early else "",
        "未满百可核": verifiable,
        "全量回执号": receipt_no or (receipt["receipt_no"] if receipt else ""),
        "回执存在": receipt is not None,
        "审计证据数": audit_n,
        "对账结论": verdict,
        "缺口标": gaps,
        "阻塞缺口": blocking,
        "钉住缺口": pinned,
        "全量格": cell,
        "全量落成态": cell,
        "切满探针": {
            "过线": probe_passed,
            "比例列": ratio,
            "比例列过线": ratio >= FULL_RATIO,
            "实例全切": facts["全切"],
            "切流比例": facts["切流比例"],
            "名册一致": facts["名册一致"],
            "全量事件": receipt is not None and bool(receipt_no),
            "审计证据数": audit_n,
            "切满差额": max(0, FULL_RATIO - ratio),
            "未过原因": probe_reasons if not probe_passed else [],
        },
        **{k: v for k, v in facts.items() if k != "实例"},
    }


def replay_release(conn, release_id: int, *, fix: bool = True) -> dict:
    """按同一套完成定义重放/收口一笔发版单。

    已放行单的读侧事实若与真源分叉，按真源比例回正标/快照/回执（不回改真源比例）：
    - 比例未满无标：补挂未满百标；
    - 比例满百仍挂标：清掉未满百标；
    - 全量时比例快照缺失或与真源分叉：回正到真源比例；
    - 回执绿但未切满（假全量回执）：写更正回执并改指回执号，审计留“揭穿”证据；
    - 名册/实例缺口：收敛到终态。
    在途但名册+实例已被全切（典型“回执失败却已全切”）：补完终态与回执。
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
        target_early = 1 if is_early_ratio(ratio) else 0
        fully_switched_evidence = before["名册一致"] or (
            before["实例总数"] > 0 and before["全切"]
        )

        if r["status"] == "已放行":
            # 1) 标 / 快照 / 落成时间按真源回正（真源比例本身不写）
            sets, params = [], []
            if int(r["early_full"]) != target_early:
                sets.append("early_full=?")
                params.append(target_early)
                actions.append("补挂未满百全量标" if target_early else "清除满百仍挂的未满百标")
            if r["full_ratio"] is None:
                sets.append("full_ratio=?")
                params.append(ratio)
                actions.append("快照全量时比例")
            elif int(r["full_ratio"]) != ratio:
                sets.append("full_ratio=?")
                params.append(ratio)
                actions.append("全量时比例快照回正到真源")
            if r["full_cutover_at"] is None:
                sets.append("full_cutover_at=?")
                params.append(r["released_at"] or now)
            if sets:
                params.append(release_id)
                conn.execute(f"UPDATE releases SET {', '.join(sets)} WHERE id=?", params)

            # 2) 名册 / 实例收敛到终态
            if not before["名册一致"]:
                set_app_env_version(conn, r["app_id"], prod["id"], r["target_version"])
                actions.append("名册产版本补切")
            conv = converge_prod_instances(
                conn, r["app_id"], prod["id"], r["target_version"], release_id, now
            )
            if not before["全切"]:
                actions.append("实例补全切")

            # 3) 全量事件：缺失补证，假全量则揭穿并更正
            receipt = latest_receipt(conn, release_id)
            inconsistent = receipt is not None and (
                int(receipt["ratio_at_cutover"]) != ratio
                or bool(receipt["early_full"]) != bool(target_early)
                or (receipt["roster_version"] or "") != r["target_version"]
            )
            if receipt is None:
                receipt_no = r["full_receipt_no"] or new_receipt_no(release_id)
                _insert_receipt(
                    conn,
                    receipt_no=receipt_no,
                    release_id=release_id,
                    ratio=ratio,
                    early=bool(target_early),
                    roster_version=r["target_version"],
                    conv=conv,
                    now=now,
                    status="重放补证",
                    detail={"replay": True, "actions": actions},
                )
                conn.execute(
                    "UPDATE releases SET full_receipt_no=? WHERE id=?",
                    (receipt_no, release_id),
                )
                actions.append("写重放补证回执")
            elif inconsistent:
                corrected_no = new_receipt_no(release_id)
                _insert_receipt(
                    conn,
                    receipt_no=corrected_no,
                    release_id=release_id,
                    ratio=ratio,
                    early=bool(target_early),
                    roster_version=r["target_version"],
                    conv=conv,
                    now=now,
                    status="重放更正",
                    detail={
                        "replay": True,
                        "纠正回执号": receipt["receipt_no"],
                        "纠正原因": "回执绿但未切满：当时回执与比例真源/未满百标分叉",
                        "actions": actions,
                    },
                )
                conn.execute(
                    "UPDATE releases SET full_receipt_no=? WHERE id=?",
                    (corrected_no, release_id),
                )
                actions.append("揭穿未切满假全量回执并更正")
            elif not r["full_receipt_no"]:
                conn.execute(
                    "UPDATE releases SET full_receipt_no=? WHERE id=?",
                    (receipt["receipt_no"], release_id),
                )
                actions.append("补全量回执号")

            # 4) 审计时序：所有修复落库之后再记一条可重放证据
            if actions:
                audit(
                    conn,
                    "全量",
                    release_id,
                    "全量重放",
                    "；".join(actions) + f"（真源比例 {ratio}%，切满差额 {FULL_RATIO - ratio}）",
                )

        elif r["status"] in ACTIVE_FOR_FULL and fully_switched_evidence:
            # 终态事实已经切出去了，只差单子/回执：按乙策补完成，比例事实原样挂标
            execute_full_cutover(conn, r, note="读侧重放：按已全切事实补完成",
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
        "修复后全量格": after["全量格"] if after else None,
        "修复后切满探针": after["切满探针"] if after else None,
        "after": after,
    }

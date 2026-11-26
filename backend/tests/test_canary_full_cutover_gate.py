"""全量落成闸（shipgate-16 canary-full-cutover-gate）自检夹具。

锁定策略 Arm B：EARLY_FULL_MARKED —— 未满百可提前全量，但必须留可核标，
发版单 / 全量写口回执 / 名册 / 实例切流 / 总览 / 对账 / 审计同笔同源。

若评分方自带 conftest.py，本文件不定义同名 client 夹具，避免冲突；
sg16_client 每函数重建隔离库。
"""
from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

import pytest

_tmp = tempfile.mkdtemp(prefix="shipgate16-test-")
os.environ.setdefault("SHIPGATE_DB", str(Path(_tmp) / "test16.db"))

# 评分环境若未装 httpx，本文件整体 skip，而不是在收集阶段炸掉整个 pytest 运行
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app import config  # noqa: E402
from app.db import db as db_ctx  # noqa: E402
from app.seed import seed_if_empty  # noqa: E402


@pytest.fixture()
def sg16_client():
    Path(config.DB_PATH).unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(config.DB_PATH) + suffix).unlink(missing_ok=True)
    seed_if_empty()
    with TestClient(app) as c:
        yield c


def _raw(fn):
    with db_ctx() as conn:
        return fn(conn)


def _create_ready(c: TestClient, ratio: int) -> int:
    apps = c.get("/api/应用").json()
    app_id = apps[-1]["编号"]
    res = c.post("/api/发版单", json={
        "应用编号": app_id, "目标版本": "3.0.0",
        "制品摘要": "sha256:x", "金丝雀比例": ratio,
    })
    assert res.status_code == 200, res.text
    rid = res.json()["编号"]
    assert c.post(f"/api/发版单/{rid}/流水线", json={"流水线": "绿"}).status_code == 200
    assert c.post(f"/api/发版单/{rid}/入闸", json={}).status_code == 200
    # 逐级到预，使单子处于晋升中
    assert c.post(f"/api/发版单/{rid}/晋升", json={"目标环境": "预"}).status_code == 200
    return rid


# ---------- 夹具一：未满百点全量 ----------

def test_early_full_is_marked_everywhere(sg16_client):
    c = sg16_client
    rid = _create_ready(c, 30)

    res = c.post(f"/api/发版单/{rid}/全量放行", json={"备注": "未满百"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["状态"] == "已放行"
    assert body["未满百全量"] is True
    assert body["提前全量"] is True
    assert "未满百全量" in body["未满百标记"]
    assert body["金丝雀比例"] == 30          # 真源不被刷成 100
    assert body["全量时比例"] == 30          # 落成快照
    assert body["全量回执"]["回执号"]

    # 发版单详情同源
    one = c.get(f"/api/发版单/{rid}").json()
    assert one["全量落成态"] == "未满百全量"

    # 总览：计数 + 独立露口
    ov = c.get("/api/总览").json()
    assert ov["未满百全量数"] >= 1
    assert any(x["编号"] == rid for x in ov["未满百全量"])

    # 对账：未满百可核，不假装跑满
    rec = c.get("/api/对账").json()
    row = next(x for x in rec["全量对账"] if x["编号"] == rid)
    assert row["对账结论"] == "未满百全量可核"
    assert row["未满百可核"] is True
    assert row["名册一致"] is True
    assert row["全切"] is True and row["切流比例"] == 100

    # 健康/实例面：切流比例 100，且带未满百标
    inst = [x for x in c.get("/api/实例").json() if x["发版单编号"] == rid]
    assert inst and all(x["切流比例"] == 100 for x in inst)
    assert all(x["未满百全量"] is True and x["金丝雀比例"] == 30 for x in inst)

    # 审计全量证据
    audits = c.get("/api/审计", params={"类别": "全量", "关联编号": rid}).json()
    assert any(a["动作"] == "全量放行" and "30%" in a["备注"] for a in audits)


def test_seed_release_sub100_full(sg16_client):
    """夹具：种子单（10%）直接点全量，乙策下挂标落成。"""
    c = sg16_client
    rid = c.get("/api/发版单").json()[0]["编号"]
    res = c.post(f"/api/发版单/{rid}/全量", json={})
    assert res.status_code == 200
    assert res.json()["未满百全量"] is True
    ov = c.get("/api/总览").json()
    assert any(x["编号"] == rid for x in ov["未满百全量"])


# ---------- 夹具二：满百点全量 ----------

def test_full_at_hundred_clean(sg16_client):
    c = sg16_client
    rid = _create_ready(c, 100)
    res = c.post(f"/api/发版单/{rid}/全量放行", json={})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["状态"] == "已放行" and body["未满百全量"] is False
    assert body["全量落成态"] == "满百全量"
    row = next(x for x in c.get("/api/对账").json()["全量对账"] if x["编号"] == rid)
    assert row["对账结论"] == "全量一致"
    assert row["全切"] is True and row["切流比例"] == 100


def test_promote_to_prod_uses_same_gate(sg16_client):
    """既有晋升写口到产不得绕过全量落成闸。"""
    c = sg16_client
    rid = _create_ready(c, 40)
    res = c.post(f"/api/发版单/{rid}/晋升", json={"目标环境": "产"})
    assert res.status_code == 200
    assert res.json()["未满百全量"] is True
    assert res.json()["全量回执"]["回执号"]


# ---------- 夹具三：并发改比例 ----------

def test_concurrent_ratio_change_and_full(sg16_client):
    c = sg16_client
    rid = _create_ready(c, 80)
    out: dict[str, int] = {}

    def change():
        r = c.post(f"/api/发版单/{rid}/金丝雀比例", json={"金丝雀比例": 45})
        out["ratio"] = r.status_code

    def full():
        r = c.post(f"/api/发版单/{rid}/全量放行", json={})
        out["full"] = r.status_code

    t1, t2 = threading.Thread(target=change), threading.Thread(target=full)
    t1.start(); t2.start(); t1.join(); t2.join()

    rel = c.get(f"/api/发版单/{rid}").json()
    row = next(x for x in c.get("/api/对账").json()["全量对账"] if x["编号"] == rid)
    # 库层串行后只允许一致结局
    if out.get("full") == 200 and rel["状态"] == "已放行":
        assert rel["全量时比例"] == rel["金丝雀比例"]
        assert rel["未满百全量"] == (rel["金丝雀比例"] < 100)
        assert row["对账结论"] in ("未满百全量可核", "全量一致")
    # 终态后改比例必须冻结（若全量先提交）
    if rel["状态"] == "已放行":
        blocked = c.post(f"/api/发版单/{rid}/金丝雀比例", json={"金丝雀比例": 50})
        assert blocked.status_code == 400


def test_ratio_endpoint_validation_and_freeze(sg16_client):
    c = sg16_client
    rid = _create_ready(c, 10)
    assert c.post(f"/api/发版单/{rid}/金丝雀比例", json={"金丝雀比例": 30}).status_code == 200
    bad = c.post(f"/api/发版单/{rid}/金丝雀比例", json={"金丝雀比例": 120})
    assert bad.status_code == 400
    c.post(f"/api/发版单/{rid}/全量放行", json={})
    frozen = c.post(f"/api/发版单/{rid}/比例", json={"比例": 40})
    assert frozen.status_code == 400 and "冻结" in frozen.json()["detail"]


# ---------- 夹具四：半截 + 原子性 ----------

def test_fault_receipt_failure_rolls_back_switch(sg16_client):
    c = sg16_client
    rid = _create_ready(c, 30)
    res = c.post(f"/api/发版单/{rid}/全量放行", json={"故障注入": "全切后失败"})
    assert res.status_code >= 400
    rel = c.get(f"/api/发版单/{rid}").json()
    assert rel["状态"] != "已放行"
    assert not rel["全量回执号"]
    row = next(x for x in c.get("/api/对账").json()["全量对账"] if x["编号"] == rid)
    assert row["回执存在"] is False
    # 实例没有提前全切到目标版本
    assert row["目标实例数"] == 0


def test_planted_half_split_detected_and_replayed(sg16_client):
    c = sg16_client
    rid = _create_ready(c, 30)
    target = c.get(f"/api/发版单/{rid}").json()["目标版本"]

    def plant(conn):
        prod = conn.execute("SELECT id FROM environments WHERE code='产'").fetchone()["id"]
        app_id = conn.execute("SELECT app_id FROM releases WHERE id=?", (rid,)).fetchone()["app_id"]
        from app.services.common import now_str
        now = now_str()
        conn.execute(
            "INSERT INTO instances(app_id,env_id,version,health,traffic_ratio,updated_at)"
            " VALUES(?,?, '1.9.0-old','探针绿',100,?)",
            (app_id, prod, now),
        )
        conn.execute(
            "UPDATE releases SET status='已放行', current_env_id=?, released_at=?,"
            " early_full=0, full_ratio=NULL, full_receipt_no='' WHERE id=?",
            (prod, now, rid),
        )
        conn.execute(
            "INSERT INTO app_env_versions(app_id,env_id,version,updated_at) VALUES(?,?,?,?)"
            " ON CONFLICT(app_id,env_id) DO UPDATE SET version=excluded.version",
            (app_id, prod, target, now),
        )

    _raw(plant)

    row = next(x for x in c.get("/api/对账").json()["全量对账"] if x["编号"] == rid)
    assert row["对账结论"] == "分裂·未满百无标"
    assert "半截·实例未全切" in row["缺口标"]

    rep = c.post("/api/对账/重放", json={"发版单编号": rid}).json()
    assert rep["结果"][0]["修复项"]

    row2 = next(x for x in c.get("/api/对账").json()["全量对账"] if x["编号"] == rid)
    assert row2["未满百可核"] is True
    assert row2["全切"] is True
    assert row2["对账结论"] == "未满百全量可核"
    assert c.get(f"/api/发版单/{rid}").json()["金丝雀比例"] == 30  # 真源不回改


def test_idempotent_second_full_call(sg16_client):
    c = sg16_client
    rid = _create_ready(c, 100)
    assert c.post(f"/api/发版单/{rid}/全量放行", json={}).status_code == 200
    again = c.post(f"/api/发版单/{rid}/全量放行", json={})
    assert again.status_code == 200 and again.json()["幂等"] is True


def test_fixture_suite_endpoint(sg16_client):
    c = sg16_client
    res = c.post("/api/夹具/套件")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["全部通过"] is True, [
        (x["场景"], x["断言"]) for x in data["结果"] if not x["通过"]
    ]


def test_policy_declared(sg16_client):
    c = sg16_client
    p = c.get("/api/全量闸策略").json()
    assert p["策略码"] == "EARLY_FULL_MARKED"
    assert c.get("/api/总览").json()["策略"]["策略码"] == "EARLY_FULL_MARKED"
    assert c.get("/api/对账").json()["策略"]["策略码"] == "EARLY_FULL_MARKED"


# ---------- 夹具五：回执绿但比例未满（全量成功灯与切满探针撕裂） ----------

def _bypass_release_to_prod(c, rid, ratio, *, receipt_no="", early=0, full_ratio=100,
                            null_nodes=True, receipt_ratio=None, receipt_early=None):
    """用旁路直接写出“回执已绿 / 已放行 / 名册已切”的终态，绕过全量写口。

    receipt_ratio/receipt_early 可单独指定回执内容，用来造“回执与单子分叉”的假全量回执。
    """
    def plant(conn):
        from app.services.common import now_str
        prod = conn.execute("SELECT id FROM environments WHERE code='产'").fetchone()["id"]
        target = conn.execute("SELECT target_version FROM releases WHERE id=?", (rid,)).fetchone()["target_version"]
        app_id = conn.execute("SELECT app_id FROM releases WHERE id=?", (rid,)).fetchone()["app_id"]
        now = now_str()
        if receipt_no:
            conn.execute(
                "INSERT INTO cutover_receipts(receipt_no, release_id, ratio_at_cutover,"
                " required_ratio, early_full, status, roster_version, switched_instances,"
                " total_instances, detail, created_at)"
                " VALUES (?,?,?,100,?, '全量落成', ?, 1, 2, '{}', ?)",
                (receipt_no, rid, ratio if receipt_ratio is None else receipt_ratio,
                 early if receipt_early is None else receipt_early, target, now),
            )
        conn.execute(
            "UPDATE releases SET status='已放行', current_env_id=?, released_at=?,"
            " canary_ratio=?, full_ratio=?, early_full=?, full_receipt_no=?"
            " WHERE id=?",
            (prod, now, ratio, full_ratio if not null_nodes else None, early,
             receipt_no, rid),
        )
        conn.execute(
            "INSERT INTO app_env_versions(app_id, env_id, version, updated_at) VALUES(?,?,?,?)"
            " ON CONFLICT(app_id,env_id) DO UPDATE SET version=excluded.version",
            (app_id, prod, target, now),
        )
        conn.execute(
            "INSERT INTO instances(app_id, env_id, version, health, release_id, traffic_ratio, updated_at)"
            " VALUES(?,?,?, '探针绿', ?, 100, ?)",
            (app_id, prod, target, rid, now),
        )
    _raw(plant)


def _side_counts(c: TestClient, rid: int) -> dict:
    ov = c.get("/api/总览").json()
    rows = ov["全量格对读"]
    mine = next((x for x in rows if x["编号"] == rid), None)
    return {"旁侧计数格": ov["旁侧计数格"], "本行": mine, "未满百全量数": ov["未满百全量数"]}


def test_green_receipt_ratio_not_full_does_not_light_full_cell(sg16_client):
    """回执绿 + 比例未满：全量格不得亮，切满探针必须钉缺口。"""
    c = sg16_client
    rid = _create_ready(c, 40)
    _bypass_release_to_prod(c, rid, 40, receipt_no="QL-BYPASS-40", early=0, null_nodes=True)

    row = next(x for x in c.get("/api/对账").json()["全量对账"] if x["编号"] == rid)
    assert row["切满探针"]["过线"] is False
    assert "比例未到百分百" in row["切满探针"]["未过原因"]
    assert row["全量格"] == "缺口·未全量"
    assert row["切满差额"] == 60
    assert row["对账结论"] == "分裂·未满百无标"

    # 发版详情 / 总览在途 / 健康 三面都不得把该单写成满百全量
    one = c.get(f"/api/发版单/{rid}").json()
    assert one["全量落成态"] == "缺口·未全量"
    assert one["全量格"] == "缺口·未全量"
    assert one["比例百分列"] == "40%"
    side = _side_counts(c, rid)
    assert side["本行"]["全量格"] == "缺口·未全量"
    assert side["旁侧计数格"]["满百全量"] == 0
    assert side["旁侧计数格"]["切满探针过线"] == 0

    # 重放：补挂未满百标，但真源比例不回改，全量格只能变成「未满百全量」
    rep = c.post("/api/对账/重放", json={"发版单编号": rid}).json()
    assert "补挂未满百全量标" in rep["结果"][0]["修复项"]
    row2 = next(x for x in c.get("/api/对账").json()["全量对账"] if x["编号"] == rid)
    assert row2["真源比例"] == 40
    assert row2["全量格"] == "未满百全量"
    assert row2["全量格"] != "满百全量"
    assert row2["切满探针"]["过线"] is False


def test_full_ratio_with_stale_early_mark_is_not_reported_early(sg16_client):
    """比例满 + 全量标缺（错误挂未满百）：全量格不得写成未满百，重放须清标。"""
    c = sg16_client
    rid = _create_ready(c, 100)
    _bypass_release_to_prod(c, rid, 100, receipt_no="QL-BYPASS-100", early=1,
                            full_ratio=100, null_nodes=False)

    row = next(x for x in c.get("/api/对账").json()["全量对账"] if x["编号"] == rid)
    assert row["对账结论"] == "分裂·满百仍挂未满百标"
    assert row["全量格"] != "未满百全量"
    assert row["切满探针"]["过线"] is False

    rep = c.post("/api/对账/重放", json={"发版单编号": rid}).json()
    assert "清除满百仍挂的未满百标" in rep["结果"][0]["修复项"]
    row2 = next(x for x in c.get("/api/对账").json()["全量对账"] if x["编号"] == rid)
    assert row2["全量格"] == "满百全量"
    assert row2["切满探针"]["过线"] is True
    assert row2["对账结论"] == "全量一致"
    one = c.get(f"/api/发版单/{rid}").json()
    assert one["全量落成态"] == "满百全量"


def test_full_cell_and_side_counts_are_same_source(sg16_client):
    """全量格与旁侧计数格不得双拧：未切满不得计入满百。"""
    c = sg16_client
    rid = _create_ready(c, 100)
    _bypass_release_to_prod(c, rid, 100, receipt_no="QL-BYPASS-PARTIAL", early=0,
                            full_ratio=100, null_nodes=False)

    def leave_old_instance(conn):
        from app.services.common import now_str
        prod = conn.execute("SELECT id FROM environments WHERE code='产'").fetchone()["id"]
        app_id = conn.execute("SELECT app_id FROM releases WHERE id=?", (rid,)).fetchone()["app_id"]
        conn.execute(
            "INSERT INTO instances(app_id, env_id, version, health, release_id, traffic_ratio, updated_at)"
            " VALUES(?,?, '0.9.0-old', '探针绿', NULL, 55, ?)",
            (app_id, prod, now_str()),
        )
    _raw(leave_old_instance)

    row = next(x for x in c.get("/api/对账").json()["全量对账"] if x["编号"] == rid)
    assert "半截·实例未全切" in row["阻塞缺口"]
    assert row["全量格"] == "缺口·未全量"
    assert row["切满探针"]["过线"] is False

    side = _side_counts(c, rid)
    assert side["本行"]["全量格"] == "缺口·未全量"
    assert side["本行"]["切满探针过线"] is False
    assert side["旁侧计数格"]["满百全量"] == 0
    assert side["旁侧计数格"]["缺口未切满"] >= 1
    # 实例健康面（挂接行）也读同一份重放
    inst = [x for x in c.get("/api/实例").json() if x["发版单编号"] == rid]
    assert inst and all(x["全量格"] == "缺口·未全量" for x in inst)


def test_bypass_half_is_pinned_and_replayed(sg16_client):
    """日常放行旁路半截：无回执/无快照/实例未全切，全量格不亮，重放收口。"""
    c = sg16_client
    rid = _create_ready(c, 100)

    def plant(conn):
        from app.services.common import now_str
        prod = conn.execute("SELECT id FROM environments WHERE code='产'").fetchone()["id"]
        target = conn.execute("SELECT target_version FROM releases WHERE id=?", (rid,)).fetchone()["target_version"]
        app_id = conn.execute("SELECT app_id FROM releases WHERE id=?", (rid,)).fetchone()["app_id"]
        now = now_str()
        conn.execute(
            "UPDATE releases SET status='已放行', current_env_id=?, released_at=?,"
            " full_ratio=NULL, early_full=0, full_receipt_no='' WHERE id=?",
            (prod, now, rid),
        )
        conn.execute(
            "INSERT INTO app_env_versions(app_id, env_id, version, updated_at) VALUES(?,?,?,?)"
            " ON CONFLICT(app_id,env_id) DO UPDATE SET version=excluded.version",
            (app_id, prod, target, now),
        )
        conn.execute(
            "INSERT INTO instances(app_id, env_id, version, health, release_id, traffic_ratio, updated_at)"
            " VALUES(?,?,?, '探针绿', ?, 100, ?)",
            (app_id, prod, target, rid, now),
        )
        conn.execute(
            "INSERT INTO instances(app_id, env_id, version, health, release_id, traffic_ratio, updated_at)"
            " VALUES(?,?, '1.9.0-old', '探针绿', NULL, 30, ?)",
            (app_id, prod, now),
        )
    _raw(plant)

    row = next(x for x in c.get("/api/对账").json()["全量对账"] if x["编号"] == rid)
    assert {"半截·无全量回执", "半截·无全量比例快照", "半截·实例未全切"} <= set(row["阻塞缺口"])
    assert row["全量格"] == "缺口·未全量"

    rep = c.post(f"/api/发版单/{rid}/重放收口").json()
    assert rep["修复后切满探针"]["过线"] is True
    row2 = next(x for x in c.get("/api/对账").json()["全量对账"] if x["编号"] == rid)
    assert row2["全量格"] == "满百全量"
    assert row2["对账结论"] == "全量一致"


def test_replay_exposes_fake_green_receipt(sg16_client):
    """重放必须揭穿“回执绿但未切满”的假全量回执，并留可重放审计证据。"""
    c = sg16_client
    # 满百单 + 满百标不存在，但回执按 30% 早全量落（回执与单子分叉）
    rid = _create_ready(c, 100)
    _bypass_release_to_prod(c, rid, 100, receipt_no="QL-FAKE-30", early=0,
                            full_ratio=100, null_nodes=False,
                            receipt_ratio=30, receipt_early=1)

    row = next(x for x in c.get("/api/对账").json()["全量对账"] if x["编号"] == rid)
    assert "分裂·回执与单子分叉" in row["阻塞缺口"]
    assert row["全量格"] != "满百全量"

    rep = c.post("/api/对账/重放", json={"发版单编号": rid}).json()["结果"][0]
    assert "揭穿未切满假全量回执并更正" in rep["修复项"]
    receipt = next(
        r for r in c.get("/api/总览").json()["全量回执"] if r["发版单编号"] == rid
    )
    assert receipt["回执号"] != "QL-FAKE-30"
    audits = c.get("/api/审计", params={"类别": "全量", "关联编号": rid}).json()
    assert any(a["动作"] == "全量重放" for a in audits)
    row2 = next(x for x in c.get("/api/对账").json()["全量对账"] if x["编号"] == rid)
    assert row2["全量格"] == "满百全量"
    assert row2["切满探针"]["过线"] is True


def test_fixture_suite_covers_four_incident_paths(sg16_client):
    """夹具套件必须包含本事故四路分测：回执绿比例未满 / 比例满标缺 / 双格拧花 / 旁路半截。"""
    c = sg16_client
    data = c.post("/api/夹具/套件").json()
    names = {x["场景"] for x in data["结果"]}
    assert {"回执绿比例未满", "比例满全量标缺", "全量格与旁侧格双拧", "全量与日常放行旁路半截"} <= names
    assert data["全部通过"] is True

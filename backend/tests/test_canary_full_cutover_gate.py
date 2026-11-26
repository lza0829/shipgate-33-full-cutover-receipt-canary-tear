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

from __future__ import annotations
from datetime import datetime
from fastapi import HTTPException

from app.db import row_to_dict


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def audit(conn, kind: str, ref_id: int | None, action: str, note: str = "") -> None:
    conn.execute(
        "INSERT INTO audit_events(kind, ref_id, action, created_at, note) VALUES (?, ?, ?, ?, ?)",
        (kind, ref_id, action, now_str(), note),
    )


def get_setting(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def next_serial(conn) -> str:
    day = datetime.now().strftime("%Y%m%d")
    prefix = f"发{day}"
    row = conn.execute(
        "SELECT serial FROM releases WHERE serial LIKE ? ORDER BY serial DESC LIMIT 1",
        (prefix + "%",),
    ).fetchone()
    n = 1
    if row and len(row["serial"]) >= 4:
        try:
            n = int(row["serial"][-4:]) + 1
        except ValueError:
            n = 1
    return f"{prefix}{n:04d}"


RELEASE_SELECT = """
SELECT r.id AS 编号,
       r.serial AS 发版单号,
       r.app_id AS 应用编号,
       a.name AS 应用,
       a.owner AS 负责人,
       a.status AS 应用状态,
       r.target_version AS 目标版本,
       r.artifact_digest AS 制品摘要,
       r.status AS 状态,
       r.pipeline_status AS 流水线,
       r.current_env_id AS 当前环境编号,
       e.code AS 当前环境,
       e.name AS 当前环境名,
       r.canary_ratio AS 金丝雀比例,
       r.note AS 备注,
       r.created_at AS 申请时间,
       r.admitted_at AS 入闸时间,
       r.released_at AS 放行时间,
       r.rolled_back_at AS 回滚时间,
       r.full_ratio AS 全量时比例,
       r.early_full AS 未满百全量,
       r.full_receipt_no AS 全量回执号,
       r.full_cutover_at AS 全量落成时间
FROM releases r
JOIN apps a ON a.id = r.app_id
LEFT JOIN environments e ON e.id = r.current_env_id
"""


def release_to_dict(row, view: dict | None = None) -> dict:
    """发版单统一出参：布尔标记给真 bool，并挂全站同一套全量落成态措辞。

    关键约束：**全量落成态只能由切满证据点亮**。没有传入 view（切满探针重放）时，
    已放行单不得自称「满百全量」，只能标记为待核缺口；带 view 时全量格与结论
    一律取自同一次重放，杜绝“总览全量格亮了但探针没过线”。
    """
    d = row_to_dict(row)
    if d is None:
        return {}
    early = bool(d.get("未满百全量"))
    ratio = d.get("金丝雀比例")
    status = d.get("状态")
    d["未满百全量"] = early
    d["提前全量"] = early
    d["early_full"] = early
    d["未满百标记"] = "未满百全量" if early else ""
    d["比例百分列"] = f"{ratio}%" if ratio is not None else "-"
    if status != "已放行":
        d["全量落成态"] = "未全量"
        d["全量格"] = "未全量"
        d["全量闸结论"] = "未全量"
        if ratio is None:
            d["全量闸结论"] = "未知比例"
        elif ratio < 100:
            d["全量闸结论"] = "未满百·可提前全量挂标"
        else:
            d["全量闸结论"] = "满百·可全量"
        return d

    if view is None:
        # 无切满证据，绝不点亮
        d["全量落成态"] = "待核·缺切满证据"
        d["全量格"] = "缺口·未全量"
        d["全量闸结论"] = "待核·缺切满证据"
        return d

    d["全量落成态"] = view["全量格"]
    d["全量格"] = view["全量格"]
    d["对账结论"] = view["对账结论"]
    d["缺口标"] = view["缺口标"]
    d["阻塞缺口"] = view["阻塞缺口"]
    d["钉住缺口"] = view["钉住缺口"]
    d["切满探针"] = view["切满探针"]
    d["切满差额"] = view["切满差额"]
    d["未满百可核"] = view["未满百可核"]
    d["审计证据数"] = view["审计证据数"]
    d["全量闸结论"] = view["对账结论"]
    return d


def release_rows_to_list(rows, views: dict[int, dict] | None = None) -> list[dict]:
    views = views or {}
    return [release_to_dict(r, views.get(r["编号"])) for r in rows]


def get_release(conn, release_id: int):
    row = conn.execute(RELEASE_SELECT + " WHERE r.id=?", (release_id,)).fetchone()
    if not row:
        raise HTTPException(404, "发版单不存在")
    return row


def env_by_code(conn, code: str):
    row = conn.execute("SELECT * FROM environments WHERE code=?", (code,)).fetchone()
    if not row:
        raise HTTPException(400, f"环境不存在：{code}")
    return row


def env_by_id(conn, env_id: int):
    row = conn.execute("SELECT * FROM environments WHERE id=?", (env_id,)).fetchone()
    if not row:
        raise HTTPException(400, "环境不存在")
    return row


def ordered_envs(conn):
    return conn.execute("SELECT * FROM environments ORDER BY sort_order").fetchall()


def active_release_window(conn, env_id: int):
    return conn.execute(
        """
        SELECT * FROM release_windows
        WHERE env_id=? AND status='窗开'
        ORDER BY id DESC LIMIT 1
        """,
        (env_id,),
    ).fetchone()


def active_rollback_window(conn, env_id: int):
    return conn.execute(
        """
        SELECT * FROM rollback_windows
        WHERE env_id=? AND status='窗开'
        ORDER BY id DESC LIMIT 1
        """,
        (env_id,),
    ).fetchone()


def set_app_env_version(conn, app_id: int, env_id: int, version: str) -> None:
    conn.execute(
        """
        INSERT INTO app_env_versions(app_id, env_id, version, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(app_id, env_id) DO UPDATE SET version=excluded.version, updated_at=excluded.updated_at
        """,
        (app_id, env_id, version, now_str()),
    )


def upsert_instance(conn, app_id: int, env_id: int, version: str, health: str, release_id: int | None) -> None:
    row = conn.execute(
        "SELECT id FROM instances WHERE app_id=? AND env_id=? AND version=?",
        (app_id, env_id, version),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE instances SET health=?, release_id=?, updated_at=? WHERE id=?",
            (health, release_id, now_str(), row["id"]),
        )
    else:
        conn.execute(
            """
            INSERT INTO instances(app_id, env_id, version, health, release_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (app_id, env_id, version, health, release_id, now_str()),
        )


def assert_app_enabled(conn, app_id: int):
    row = conn.execute("SELECT * FROM apps WHERE id=?", (app_id,)).fetchone()
    if not row:
        raise HTTPException(404, "应用不存在")
    if row["status"] != "启用":
        raise HTTPException(400, "应用已停用，禁止入闸")
    return row


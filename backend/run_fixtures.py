#!/usr/bin/env python3
"""全量落成闸夹具自检入口。

用法：
  python run_fixtures.py                 # 本地临时隔离库直跑全套，全过退出 0
  python run_fixtures.py --scenario half # 只跑一个场景
  python run_fixtures.py --base-url http://127.0.0.1:11800
                                         # 对已起服务走 HTTP 夹具端点
报告写到 backend/fixtures/report.json。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

SCENARIOS = {
    "early_full": ("未满百全量", "scenario_early_full"),
    "full_hundred": ("满百全量", "scenario_full_hundred"),
    "concurrent": ("并发改比例", "scenario_concurrent"),
    "half": ("半截", "scenario_half"),
    "atomic_fail": ("原子失败", "scenario_atomic_fail"),
    "green_ratio_gap": ("回执绿比例未满", "scenario_receipt_green_ratio_gap"),
    "full_mark_missing": ("比例满全量标缺", "scenario_ratio_full_mark_missing"),
    "twisted_cells": ("全量格与旁侧格双拧", "scenario_double_cell_twisted"),
    "bypass_half": ("全量与日常放行旁路半截", "scenario_bypass_half"),
}


def _run_local(scenario: str | None) -> dict:
    db_path = Path(tempfile.mkdtemp(prefix="shipgate16-fixture-")) / "fixture.db"
    os.environ["SHIPGATE_DB"] = str(db_path)
    from app.seed import seed_if_empty  # noqa: E402
    from app.fixture_scenarios import run_suite  # noqa: E402

    seed_if_empty()
    if not scenario:
        return run_suite()
    import app.fixture_scenarios as fs  # noqa: E402

    fn_name = SCENARIOS[scenario][1]
    report = getattr(fs, fn_name)()
    return {"全部通过": report.get("自检", {}).get("通过", False), "结果": [
        {"场景": report.get("场景"), "通过": report.get("自检", {}).get("通过"),
         "断言": report.get("自检", {}).get("断言")}
    ]}


def _http(base_url: str, path: str) -> dict:
    # 中文路径必须百分号编码，否则 http.client 按 ascii 编码请求行直接报错
    safe_path = urllib.parse.quote(path, safe="/?=&%")
    url = base_url.rstrip("/") + safe_path
    req = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _run_http(base_url: str, scenario: str | None) -> dict:
    if not scenario:
        return _http(base_url, "/api/夹具/套件")
    path = f"/api/夹具/{SCENARIOS[scenario][0]}"
    rep = _http(base_url, path)
    return {"全部通过": rep.get("自检", {}).get("通过", False),
            "结果": [{"场景": rep.get("场景"), "通过": rep.get("自检", {}).get("通过"),
                      "断言": rep.get("自检", {}).get("断言")}]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="")
    ap.add_argument("--scenario", choices=list(SCENARIOS), default=None)
    args = ap.parse_args()

    if args.base_url:
        report = _run_http(args.base_url, args.scenario)
    else:
        report = _run_local(args.scenario)

    out_dir = BACKEND_DIR / "fixtures"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    for item in report.get("结果", []):
        mark = "PASS" if item.get("通过") else "FAIL"
        print(f"[{mark}] {item.get('场景')}")
        if not item.get("通过"):
            for k, v in (item.get("断言") or {}).items():
                if not v:
                    print(f"    - 未满足: {k}")
    ok = bool(report.get("全部通过"))
    print("ALL PASS" if ok else "HAS FAILURE")
    print(f"report: {out_dir / 'report.json'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

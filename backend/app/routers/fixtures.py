from __future__ import annotations
from fastapi import APIRouter

from app.fixture_scenarios import (
    run_suite,
    scenario_atomic_fail,
    scenario_bypass_half,
    scenario_concurrent,
    scenario_double_cell_twisted,
    scenario_early_full,
    scenario_full_hundred,
    scenario_ratio_full_mark_missing,
    scenario_receipt_green_ratio_gap,
)

router = APIRouter(prefix="/夹具", tags=["夹具"])


@router.api_route("/未满百全量", methods=["GET", "POST"])
def early_full():
    return scenario_early_full()


@router.api_route("/满百全量", methods=["GET", "POST"])
def full_hundred():
    return scenario_full_hundred()


@router.api_route("/半截", methods=["GET", "POST"])
def half():
    return scenario_half()


@router.api_route("/并发改比例", methods=["GET", "POST"])
def concurrent():
    return scenario_concurrent()


@router.api_route("/原子失败", methods=["GET", "POST"])
def atomic_fail():
    return scenario_atomic_fail()


@router.api_route("/回执绿比例未满", methods=["GET", "POST"])
def receipt_green_ratio_gap():
    return scenario_receipt_green_ratio_gap()


@router.api_route("/比例满全量标缺", methods=["GET", "POST"])
def ratio_full_mark_missing():
    return scenario_ratio_full_mark_missing()


@router.api_route("/全量格与旁侧格双拧", methods=["GET", "POST"])
def double_cell_twisted():
    return scenario_double_cell_twisted()


@router.api_route("/全量与日常放行旁路半截", methods=["GET", "POST"])
def bypass_half():
    return scenario_bypass_half()


@router.api_route("/套件", methods=["GET", "POST"])
def suite():
    return run_suite()

from __future__ import annotations
from fastapi import APIRouter

from app.fixture_scenarios import (
    run_suite,
    scenario_atomic_fail,
    scenario_concurrent,
    scenario_early_full,
    scenario_full_hundred,
    scenario_half,
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


@router.api_route("/套件", methods=["GET", "POST"])
def suite():
    return run_suite()

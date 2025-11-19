import os
from datetime import date, timedelta

import numpy as np

from pm_agent.analysis import (
    build_focus_board,
    compute_critical_path,
    run_monte_carlo_simulation,
    summarize_artifacts,
)
from pm_agent.schema import ProjectPlan, Task


def test_compute_critical_path_identifies_chain_and_warnings():
    plan = ProjectPlan(
        tasks=[
            Task(id=1, title="Design", start_date=date(2024, 1, 1), due_date=date(2024, 1, 3)),
            Task(id=2, title="Backend", start_date=date(2024, 1, 3), due_date=date(2024, 1, 8), dependencies=[1]),
            Task(
                id=3,
                title="Docs",
                start_date=date(2024, 1, 3),
                due_date=date(2024, 1, 4),
                dependencies=[1, 99],
            ),
            Task(id=4, title="Launch", start_date=date(2024, 1, 8), due_date=date(2024, 1, 10), dependencies=[2, 3]),
        ]
    )

    result = compute_critical_path(plan)

    assert result.total_duration == 9
    critical_ids = [task.task_id for task in result.tasks if task.is_critical]
    assert critical_ids == [1, 2, 4]
    assert any("missing dependency" in warning for warning in result.warnings)


def test_run_monte_carlo_simulation_returns_probabilities(sample_plan):
    np.random.seed(0)
    result = run_monte_carlo_simulation(
        sample_plan,
        iterations=500,
        cost_rate=800.0,
        deadline=date(2024, 2, 15),
        milestone_deadline=date(2024, 1, 15),
        milestone_label="Beta",
    )

    assert result.iterations == 500
    p10 = result.finish_percentiles["p10"].toordinal()
    p50 = result.finish_percentiles["p50"].toordinal()
    p90 = result.finish_percentiles["p90"].toordinal()
    assert p10 <= p50 <= p90
    assert 0.0 <= result.probability_on_time <= 1.0
    assert result.milestone_label == "Beta"
    assert result.cost_percentiles is not None
    assert result.risk_drivers  # non-empty


def test_build_focus_board_prioritizes_overdue_then_blocked():
    today = date.today()
    plan = ProjectPlan(
        tasks=[
            Task(id=1, title="Overdue", due_date=today - timedelta(days=1)),
            Task(id=2, title="Blocked", status="blocked", due_date=today + timedelta(days=5)),
            Task(id=3, title="Priority gap", priority="P1"),
        ]
    )

    board = build_focus_board(plan, horizon_days=7, limit=3)

    reasons = [item.reason for item in board.items]
    assert reasons[0] == "overdue"
    assert "blocked" in reasons
    assert "priority_gap" in reasons


def test_summarize_artifacts_orders_by_modified_time(project_root, artifacts_root):
    newer = artifacts_root / "analysis" / "report.txt"
    older = artifacts_root / "exports" / "summary.md"
    older.parent.mkdir(parents=True, exist_ok=True)
    newer.parent.mkdir(parents=True, exist_ok=True)

    older.write_text("old", encoding="utf-8")
    newer.write_text("new", encoding="utf-8")

    os.utime(older, (100, 100))
    os.utime(newer, (200, 200))

    artifacts = summarize_artifacts(project_root, max_items=5)
    assert artifacts[0].path == "artifacts/analysis/report.txt"
    assert artifacts[1].path == "artifacts/exports/summary.md"

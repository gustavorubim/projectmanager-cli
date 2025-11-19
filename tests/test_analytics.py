from datetime import date, datetime, timedelta

from pm_agent.analytics import (
    StatusSnapshot,
    TaskStatusRecord,
    compute_burnup_series,
    compute_cycle_time_stats,
    compute_slip_stats,
    compute_throughput_series,
    derive_task_timelines,
    filter_history_by_days,
    load_status_history,
    record_status_snapshot,
)
def test_record_and_load_status_history_round_trip(project_root, sample_plan):
    snapshot = record_status_snapshot(sample_plan)
    history = load_status_history()

    assert len(history) == 1
    assert history[0].status_counts == snapshot.status_counts
    assert history[0].total_tasks == len(sample_plan.tasks)


def test_filter_history_by_days_prunes_entries():
    base = datetime.utcnow()

    def build_snapshot(offset_days: int) -> StatusSnapshot:
        captured = base - timedelta(days=offset_days)
        record = TaskStatusRecord(
            task_id=1,
            status="done" if offset_days == 1 else "todo",
            due_date=date(2024, 1, 5),
            title="Task",
            assignee=None,
        )
        return StatusSnapshot(
            captured_at=captured,
            total_tasks=1,
            status_counts={record.status: 1},
            tasks={1: record},
        )

    history = [build_snapshot(3), build_snapshot(2), build_snapshot(1), build_snapshot(0)]
    recent = filter_history_by_days(history, days=2)

    assert len(recent) == 3
    assert all(snapshot.captured_at >= base - timedelta(days=2) for snapshot in recent)


def test_time_series_helpers_compute_expected_shapes():
    base = datetime(2024, 1, 1, 12, 0, 0)

    def snapshot(day_offset: int, records: list[TaskStatusRecord]) -> StatusSnapshot:
        captured = base + timedelta(days=day_offset)
        status_counts = {}
        for rec in records:
            status_counts[rec.status] = status_counts.get(rec.status, 0) + 1
        return StatusSnapshot(
            captured_at=captured,
            total_tasks=len(records),
            status_counts=status_counts,
            tasks={rec.task_id: rec for rec in records},
        )

    task1_due = date(2024, 1, 5)
    task2_due = date(2024, 1, 2)

    history = [
        snapshot(
            0,
            [
                TaskStatusRecord(1, "in_progress", task1_due, "Spec", "Alex"),
                TaskStatusRecord(2, "todo", task2_due, "API", None),
            ],
        ),
        snapshot(
            1,
            [
                TaskStatusRecord(1, "done", task1_due, "Spec", "Alex"),
                TaskStatusRecord(2, "in_progress", task2_due, "API", "Bailey"),
            ],
        ),
        snapshot(
            2,
            [
                TaskStatusRecord(1, "done", task1_due, "Spec", "Alex"),
                TaskStatusRecord(2, "done", task2_due, "API", "Bailey"),
            ],
        ),
    ]

    burnup = compute_burnup_series(history)
    assert len(burnup) == 3
    assert burnup[-1]["done"] == 2

    timeline = derive_task_timelines(history)
    throughput = compute_throughput_series(history, timeline)
    assert throughput[-1]["completed"] == 1

    cycle = compute_cycle_time_stats(history, timeline)
    assert cycle["count"] == 2
    assert cycle["mean"] is not None

    slips = compute_slip_stats(history, timeline)
    assert slips["on_time"] == 1
    assert slips["late"] == 1
    assert slips["records"][0]["task_id"] in {1, 2}

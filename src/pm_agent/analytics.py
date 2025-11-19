"""Project health analytics and time-series helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from .schema import ProjectPlan
from .tools import get_project_root


@dataclass
class TaskStatusRecord:
    """Task status information captured in a snapshot."""

    task_id: int
    status: str
    due_date: Optional[date]
    title: str
    assignee: Optional[str]


@dataclass
class StatusSnapshot:
    """Recorded totals and per-task data at a given moment."""

    captured_at: datetime
    total_tasks: int
    status_counts: Dict[str, int]
    tasks: Dict[int, TaskStatusRecord]

    @classmethod
    def from_dict(cls, payload: Dict) -> "StatusSnapshot":
        captured_at = datetime.fromisoformat(payload["captured_at"])
        tasks: Dict[int, TaskStatusRecord] = {}
        for task in payload.get("tasks", []):
            due = date.fromisoformat(task["due_date"]) if task.get("due_date") else None
            tasks[task["id"]] = TaskStatusRecord(
                task_id=task["id"],
                status=task.get("status", "todo"),
                due_date=due,
                title=task.get("title", ""),
                assignee=task.get("assignee"),
            )
        total_tasks = payload.get("total_tasks", len(tasks))
        return cls(
            captured_at=captured_at,
            total_tasks=total_tasks,
            status_counts=payload.get("status_counts", {}),
            tasks=tasks,
        )


@dataclass
class TaskTimeline:
    """Derived lifecycle info for each task."""

    first_seen: Dict[int, datetime]
    completion_times: Dict[int, datetime]
    due_dates: Dict[int, date]
    titles: Dict[int, str]
    assignees: Dict[int, Optional[str]]


def _history_path() -> Path:
    root = get_project_root()
    history_dir = root / ".pm" / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    return history_dir / "status_history.jsonl"


def record_status_snapshot(plan: ProjectPlan) -> StatusSnapshot:
    """Append the current plan state to the history log."""
    path = _history_path()
    now = datetime.utcnow()

    status_counts: Dict[str, int] = {}
    tasks_payload: List[Dict] = []
    for task in plan.tasks:
        status_counts[task.status] = status_counts.get(task.status, 0) + 1
        tasks_payload.append(
            {
                "id": task.id,
                "status": task.status,
                "due_date": task.due_date.isoformat() if task.due_date else None,
                "title": task.title,
                "assignee": task.assignee,
            }
        )

    payload = {
        "captured_at": now.isoformat(),
        "status_counts": status_counts,
        "total_tasks": len(plan.tasks),
        "tasks": tasks_payload,
    }

    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")

    return StatusSnapshot.from_dict(payload)


def load_status_history(limit: Optional[int] = None) -> List[StatusSnapshot]:
    """Load all stored status history entries."""
    path = _history_path()
    if not path.exists():
        return []

    snapshots: List[StatusSnapshot] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            snapshots.append(StatusSnapshot.from_dict(payload))

    snapshots.sort(key=lambda snap: snap.captured_at)
    if limit:
        snapshots = snapshots[-limit:]
    return snapshots


def filter_history_by_days(history: List[StatusSnapshot], days: int) -> List[StatusSnapshot]:
    """Return only history within the last `days` days."""
    if days <= 0:
        return history
    cutoff = datetime.utcnow() - timedelta(days=days)
    return [snap for snap in history if snap.captured_at >= cutoff]


def compute_burnup_series(history: List[StatusSnapshot]) -> List[Dict]:
    """Aggregate burnup/burndown friendly points by day."""
    daily: Dict[date, Dict] = {}
    for snapshot in history:
        day = snapshot.captured_at.date()
        done = snapshot.status_counts.get("done", 0)
        total = snapshot.total_tasks
        remaining = max(0, total - done)
        blocked = snapshot.status_counts.get("blocked", 0)
        daily[day] = {
            "date": day,
            "done": done,
            "total": total,
            "remaining": remaining,
            "blocked": blocked,
        }
    return [daily[day] for day in sorted(daily.keys())]


def derive_task_timelines(history: List[StatusSnapshot]) -> TaskTimeline:
    """Compute first-seen, completion, and due-date tracking for tasks."""
    first_seen: Dict[int, datetime] = {}
    completion_times: Dict[int, datetime] = {}
    due_dates: Dict[int, date] = {}
    titles: Dict[int, str] = {}
    assignees: Dict[int, Optional[str]] = {}

    for snapshot in history:
        for task_id, record in snapshot.tasks.items():
            if task_id not in first_seen:
                first_seen[task_id] = snapshot.captured_at
            if record.due_date:
                due_dates[task_id] = record.due_date
            if record.title:
                titles[task_id] = record.title
            if task_id not in assignees or record.assignee:
                assignees[task_id] = record.assignee
            if record.status == "done" and task_id not in completion_times:
                completion_times[task_id] = snapshot.captured_at

    return TaskTimeline(
        first_seen=first_seen,
        completion_times=completion_times,
        due_dates=due_dates,
        titles=titles,
        assignees=assignees,
    )


def compute_throughput_series(
    history: List[StatusSnapshot],
    timeline: Optional[TaskTimeline] = None,
) -> List[Dict]:
    """Return number of tasks completed per day."""
    if timeline is None:
        timeline = derive_task_timelines(history)

    counts: Dict[date, int] = {}
    for completed_at in timeline.completion_times.values():
        day = completed_at.date()
        counts[day] = counts.get(day, 0) + 1

    return [
        {"date": day, "completed": counts[day]}
        for day in sorted(counts.keys())
    ]


def compute_cycle_time_stats(
    history: List[StatusSnapshot],
    timeline: Optional[TaskTimeline] = None,
) -> Dict:
    """Return cycle-time samples and percentiles."""
    if timeline is None:
        timeline = derive_task_timelines(history)

    values: List[float] = []
    samples: List[Dict] = []
    for task_id, completed_at in timeline.completion_times.items():
        start = timeline.first_seen.get(task_id)
        if not start:
            continue
        days = max(0.0, (completed_at - start).days)
        values.append(days)
        samples.append(
            {
                "task_id": task_id,
                "title": timeline.titles.get(task_id, ""),
                "cycle_days": days,
                "completed_at": completed_at.date().isoformat(),
            }
        )

    percentiles: Dict[str, float] = {}
    mean = None
    if values:
        arr = np.array(values, dtype=float)
        percentiles = {
            "p50": float(np.percentile(arr, 50)),
            "p80": float(np.percentile(arr, 80)),
            "p90": float(np.percentile(arr, 90)),
        }
        mean = float(np.mean(arr))

    return {
        "count": len(values),
        "mean": mean,
        "percentiles": percentiles,
        "samples": sorted(samples, key=lambda item: item["cycle_days"], reverse=True),
    }


def compute_slip_stats(
    history: List[StatusSnapshot],
    timeline: Optional[TaskTimeline] = None,
) -> Dict:
    """Return planned vs actual completion deltas."""
    if timeline is None:
        timeline = derive_task_timelines(history)

    records: List[Dict] = []
    values: List[int] = []
    on_time = late = 0

    for task_id, completed_at in timeline.completion_times.items():
        due = timeline.due_dates.get(task_id)
        if not due:
            continue
        slip_days = (completed_at.date() - due).days
        values.append(slip_days)
        if slip_days > 0:
            late += 1
        else:
            on_time += 1
        records.append(
            {
                "task_id": task_id,
                "title": timeline.titles.get(task_id, ""),
                "slip_days": slip_days,
                "completed_at": completed_at.date().isoformat(),
                "due_date": due.isoformat(),
            }
        )

    percentiles: Dict[str, float] = {}
    if values:
        arr = np.array(values, dtype=float)
        percentiles = {
            "p10": float(np.percentile(arr, 10)),
            "p50": float(np.percentile(arr, 50)),
            "p90": float(np.percentile(arr, 90)),
        }

    return {
        "on_time": on_time,
        "late": late,
        "records": sorted(records, key=lambda item: item["slip_days"], reverse=True),
        "percentiles": percentiles,
    }

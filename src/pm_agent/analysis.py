"""Advanced analytics helpers for the PM Agent."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from .schema import FocusBoard, FocusItem, ProjectPlan, Task


@dataclass
class CriticalPathTask:
    """Represents scheduling data for a single task."""

    task_id: int
    title: str
    duration: int
    earliest_start: date
    earliest_finish: date
    latest_start: date
    latest_finish: date
    slack: int
    is_critical: bool


@dataclass
class CriticalPathResult:
    """Aggregate critical path analysis."""

    tasks: List[CriticalPathTask]
    total_duration: int
    project_start: date
    warnings: List[str]


@dataclass
class RiskDriver:
    """Information about a task that strongly influences schedule risk."""

    task_id: int
    title: str
    correlation: float
    avg_duration: float


@dataclass
class MonteCarloResult:
    """Monte Carlo schedule simulation summary."""

    iterations: int
    finish_percentiles: Dict[str, date]
    duration_percentiles: Dict[str, float]
    risk_drivers: List[RiskDriver]
    project_start: date
    cost_percentiles: Optional[Dict[str, float]] = None
    probability_on_time: Optional[float] = None
    milestone_probability: Optional[float] = None
    milestone_label: Optional[str] = None
    effort_percentiles: Optional[Dict[str, float]] = None
    finish_samples: Optional[np.ndarray] = None
    effort_samples: Optional[np.ndarray] = None
    cost_samples: Optional[np.ndarray] = None


@dataclass
class ArtifactInfo:
    """Metadata about files stored under artifacts/."""

    path: str
    size_kb: float
    modified: datetime


def _project_start_date(plan: ProjectPlan) -> date:
    dates = []
    for task in plan.tasks:
        if task.start_date:
            dates.append(task.start_date)
        if task.due_date:
            dates.append(task.due_date)
    return min(dates) if dates else date.today()


def _duration_from_dates(task: Task) -> Optional[int]:
    if task.start_date and task.due_date:
        return max(1, (task.due_date - task.start_date).days or 1)
    if task.due_date:
        # Assume at least one day if only a due date exists
        return 1
    return None


def _estimated_duration(task: Task) -> int:
    duration = _duration_from_dates(task)
    if duration:
        return duration
    if task.likely_days:
        return max(1, task.likely_days)
    if task.optimistic_days and task.pessimistic_days:
        return max(1, int(round((task.optimistic_days + task.pessimistic_days) / 2)))
    return 5


def _duration_estimates_for_risk(task: Task) -> tuple[int, int, int]:
    likely = task.likely_days or _duration_from_dates(task) or 5
    optimistic = task.optimistic_days or max(1, int(round(likely * 0.7)))
    pessimistic = task.pessimistic_days or max(likely + 1, int(round(likely * 1.5)))

    optimistic = max(1, min(optimistic, likely))
    pessimistic = max(likely + 1, pessimistic)

    return optimistic, max(1, likely), pessimistic


def compute_critical_path(plan: ProjectPlan) -> CriticalPathResult:
    """Compute earliest/latest dates and slack for each task."""
    tasks = plan.tasks
    project_start = _project_start_date(plan)
    warnings: List[str] = []

    if not tasks:
        return CriticalPathResult([], 0, project_start, ["No tasks defined"])

    tasks_by_id: Dict[int, Task] = {task.id: task for task in tasks}
    durations = {task.id: _estimated_duration(task) for task in tasks}

    successors: Dict[int, List[int]] = {task.id: [] for task in tasks}
    indegree: Dict[int, int] = {task.id: 0 for task in tasks}

    for task in tasks:
        for dep in task.dependencies:
            if dep not in successors:
                warnings.append(f"Task #{task.id} references missing dependency #{dep}.")
                continue
            successors[dep].append(task.id)
            indegree[task.id] += 1

    # Kahn topological sort
    order: List[int] = []
    queue = [task_id for task_id, deg in indegree.items() if deg == 0]

    while queue:
        current = queue.pop(0)
        order.append(current)
        for neighbor in successors[current]:
            indegree[neighbor] -= 1
            if indegree[neighbor] == 0:
                queue.append(neighbor)

    if len(order) != len(tasks):
        warnings.append("Dependency graph may contain a cycle; results could be inaccurate.")

    earliest_start: Dict[int, int] = {task_id: 0 for task_id in tasks_by_id}
    earliest_finish: Dict[int, int] = {task_id: durations[task_id] for task_id in tasks_by_id}

    for task_id in order:
        deps = tasks_by_id[task_id].dependencies
        if deps:
            valid_deps = [earliest_finish.get(dep, 0) for dep in deps]
            start_offset = max(valid_deps) if valid_deps else 0
        else:
            start_offset = 0

        earliest_start[task_id] = start_offset
        earliest_finish[task_id] = start_offset + durations[task_id]

    total_duration = max(earliest_finish.values()) if earliest_finish else 0

    latest_finish: Dict[int, int] = {task_id: total_duration for task_id in tasks_by_id}
    latest_start: Dict[int, int] = {
        task_id: total_duration - durations[task_id] for task_id in tasks_by_id
    }

    for task_id in reversed(order):
        succs = successors.get(task_id, [])
        if succs:
            min_latest_start = min(latest_start[s] for s in succs)
            latest_finish[task_id] = min_latest_start
            latest_start[task_id] = min_latest_start - durations[task_id]

    schedule: List[CriticalPathTask] = []
    for task_id in order:
        task = tasks_by_id[task_id]
        es_days = earliest_start[task_id]
        ef_days = earliest_finish[task_id]
        ls_days = latest_start[task_id]
        lf_days = latest_finish[task_id]
        slack = max(0, ls_days - es_days)

        schedule.append(
            CriticalPathTask(
                task_id=task_id,
                title=task.title,
                duration=durations[task_id],
                earliest_start=project_start + timedelta(days=es_days),
                earliest_finish=project_start + timedelta(days=ef_days),
                latest_start=project_start + timedelta(days=ls_days),
                latest_finish=project_start + timedelta(days=lf_days),
                slack=slack,
                is_critical=slack == 0,
            )
        )

    schedule.sort(key=lambda item: item.earliest_start)
    return CriticalPathResult(schedule, total_duration, project_start, warnings)


def run_monte_carlo_simulation(
    plan: ProjectPlan,
    iterations: int = 2000,
    cost_rate: Optional[float] = None,
    deadline: Optional[date] = None,
    milestone_deadline: Optional[date] = None,
    shock_days: int = 0,
    milestone_label: Optional[str] = None
) -> MonteCarloResult:
    """Run a Monte Carlo simulation to understand schedule risk."""
    tasks = plan.tasks
    project_start = _project_start_date(plan)

    if not tasks:
        today = date.today()
        finish = {"p10": today, "p50": today, "p90": today}
        duration = {"p10": 0.0, "p50": 0.0, "p90": 0.0}
        zero_array = np.zeros(iterations)
        return MonteCarloResult(
            iterations=iterations,
            finish_percentiles=finish,
            duration_percentiles=duration,
            risk_drivers=[],
            project_start=project_start,
            cost_percentiles=None,
            probability_on_time=None,
            milestone_probability=None,
            milestone_label=milestone_label,
            effort_percentiles={"p10": 0.0, "p50": 0.0, "p90": 0.0},
            finish_samples=zero_array,
            effort_samples=zero_array.copy(),
            cost_samples=None,
        )

    tasks_by_id = {task.id: task for task in tasks}
    durations = {task.id: _duration_estimates_for_risk(task) for task in tasks}

    successors: Dict[int, List[int]] = {task.id: [] for task in tasks}
    indegree: Dict[int, int] = {task.id: 0 for task in tasks}

    for task in tasks:
        for dep in task.dependencies:
            if dep in successors:
                successors[dep].append(task.id)
                indegree[task.id] += 1

    order: List[int] = []
    queue = [task_id for task_id, deg in indegree.items() if deg == 0]
    while queue:
        current = queue.pop(0)
        order.append(current)
        for neighbor in successors[current]:
            indegree[neighbor] -= 1
            if indegree[neighbor] == 0:
                queue.append(neighbor)

    # Fallback: if graph not connected, add remaining tasks
    for task_id in tasks_by_id:
        if task_id not in order:
            order.append(task_id)

    finish_samples: Dict[int, np.ndarray] = {}
    duration_samples: Dict[int, np.ndarray] = {}

    for task_id in order:
        optimistic, likely, pessimistic = durations[task_id]
        samples = np.random.triangular(optimistic, likely, pessimistic, size=iterations)
        samples = np.clip(np.round(samples), 1, None)
        duration_samples[task_id] = samples

        start_samples = np.zeros(iterations)
        for dep in tasks_by_id[task_id].dependencies:
            if dep in finish_samples:
                start_samples = np.maximum(start_samples, finish_samples[dep])
        if shock_days > 0 and tasks_by_id[task_id].dependencies:
            extra_delay = np.random.uniform(0, shock_days, size=iterations)
        else:
            extra_delay = 0
        finish_samples[task_id] = start_samples + samples + extra_delay

    project_finish = np.max(np.vstack(list(finish_samples.values())), axis=0)

    percentiles_days = np.percentile(project_finish, [10, 50, 90])
    finish_percentiles = {
        "p10": project_start + timedelta(days=int(percentiles_days[0])),
        "p50": project_start + timedelta(days=int(percentiles_days[1])),
        "p90": project_start + timedelta(days=int(percentiles_days[2])),
    }
    duration_percentiles = {
        "p10": float(percentiles_days[0]),
        "p50": float(percentiles_days[1]),
        "p90": float(percentiles_days[2]),
    }

    risk_drivers: List[RiskDriver] = []
    finish_array = project_finish

    for task_id, samples in finish_samples.items():
        if np.std(samples) == 0:
            continue
        corr_matrix = np.corrcoef(samples, finish_array)
        corr = corr_matrix[0, 1]
        if np.isnan(corr):
            continue
        risk_drivers.append(
            RiskDriver(
                task_id=task_id,
                title=tasks_by_id[task_id].title,
                correlation=float(corr),
                avg_duration=float(np.mean(duration_samples[task_id])),
            )
        )

    risk_drivers.sort(key=lambda driver: abs(driver.correlation), reverse=True)

    total_person_days = np.sum(np.vstack(list(duration_samples.values())), axis=0)
    effort_percentiles = {
        "p10": float(np.percentile(total_person_days, 10)),
        "p50": float(np.percentile(total_person_days, 50)),
        "p90": float(np.percentile(total_person_days, 90)),
    }

    cost_percentiles = None
    cost_samples = None
    if cost_rate:
        cost_samples = total_person_days * cost_rate
        cost_percentiles = {
            "p10": float(np.percentile(cost_samples, 10)),
            "p50": float(np.percentile(cost_samples, 50)),
            "p90": float(np.percentile(cost_samples, 90)),
        }

    probability_on_time = None
    if deadline:
        horizon_days = (deadline - project_start).days
        probability_on_time = float(np.mean(project_finish <= horizon_days))

    milestone_probability = None
    if milestone_deadline:
        milestone_tasks = [
            task_id for task_id, task in tasks_by_id.items()
            if task.due_date and task.due_date <= milestone_deadline
        ]
        if milestone_tasks:
            milestone_finish = np.max(
                np.vstack([finish_samples[task_id] for task_id in milestone_tasks]),
                axis=0
            )
            milestone_horizon = (milestone_deadline - project_start).days
            milestone_probability = float(np.mean(milestone_finish <= milestone_horizon))

    return MonteCarloResult(
        iterations=iterations,
        finish_percentiles=finish_percentiles,
        duration_percentiles=duration_percentiles,
        risk_drivers=risk_drivers,
        project_start=project_start,
        cost_percentiles=cost_percentiles,
        probability_on_time=probability_on_time,
        milestone_probability=milestone_probability,
        milestone_label=milestone_label,
        effort_percentiles=effort_percentiles,
        finish_samples=project_finish,
        effort_samples=total_person_days,
        cost_samples=cost_samples,
    )


def summarize_monte_carlo(result: MonteCarloResult) -> Dict[str, object]:
    """Return a JSON-ready summary of Monte Carlo outcomes."""

    def _format_percentiles(percentiles: Dict[str, date]) -> Dict[str, str]:
        return {key: value.isoformat() for key, value in percentiles.items()}

    finish_stats: Dict[str, float] = {}
    if result.finish_samples is not None and len(result.finish_samples) > 0:
        finish_stats = {
            "mean_days": float(np.mean(result.finish_samples)),
            "std_days": float(np.std(result.finish_samples)),
            "min_days": float(np.min(result.finish_samples)),
            "max_days": float(np.max(result.finish_samples)),
        }

    cost_stats: Optional[Dict[str, float]] = None
    if result.cost_samples is not None and len(result.cost_samples) > 0:
        cost_stats = {
            "mean": float(np.mean(result.cost_samples)),
            "std": float(np.std(result.cost_samples)),
            "min": float(np.min(result.cost_samples)),
            "max": float(np.max(result.cost_samples)),
        }

    summary: Dict[str, object] = {
        "iterations": result.iterations,
        "generated_at": datetime.utcnow().isoformat(),
        "project_start": result.project_start.isoformat(),
        "finish_percentiles": _format_percentiles(result.finish_percentiles),
        "duration_percentiles": result.duration_percentiles,
        "effort_percentiles": result.effort_percentiles,
        "finish_distribution_stats": finish_stats,
        "probability_on_time": result.probability_on_time,
        "milestone_probability": result.milestone_probability,
        "milestone_label": result.milestone_label,
        "risk_drivers": [
            {
                "task_id": driver.task_id,
                "title": driver.title,
                "correlation": driver.correlation,
                "avg_duration": driver.avg_duration,
            }
            for driver in result.risk_drivers
        ],
    }

    if result.cost_percentiles:
        summary["cost_percentiles"] = result.cost_percentiles
    if cost_stats:
        summary["cost_distribution_stats"] = cost_stats

    return summary


def build_focus_board(
    plan: ProjectPlan,
    horizon_days: int = 14,
    limit: int = 10,
) -> FocusBoard:
    """Create a prioritized list of near-term or blocked tasks."""
    today = date.today()
    items: List[FocusItem] = []

    reason_priority = {"overdue": 0, "blocked": 1, "due_soon": 2}

    for task in plan.tasks:
        reason: Optional[str] = None
        days_remaining: Optional[int] = None

        if task.due_date:
            days_remaining = (task.due_date - today).days
            if days_remaining < 0:
                reason = "overdue"
            elif days_remaining <= horizon_days:
                reason = "due_soon"
        if task.status == "blocked":
            reason = "blocked"

        if reason:
            items.append(
                FocusItem(
                    task_id=task.id,
                    title=task.title,
                    due_date=task.due_date,
                    status=task.status,
                    assignee=task.assignee,
                    days_remaining=days_remaining,
                    reason=reason,
                )
            )

    items.sort(
        key=lambda item: (
            reason_priority.get(item.reason, 99),
            item.days_remaining if item.days_remaining is not None else 999,
        )
    )

    if limit:
        items = items[:limit]

    return FocusBoard(
        generated_at=datetime.utcnow(),
        horizon_days=horizon_days,
        max_items=limit,
        items=items,
    )


def summarize_artifacts(root: Path, max_items: int = 20) -> List[ArtifactInfo]:
    """Return up to `max_items` recent artifact files."""
    artifacts_dir = root / "artifacts"
    if not artifacts_dir.exists():
        return []

    artifacts: List[ArtifactInfo] = []
    for path in artifacts_dir.rglob("*"):
        if not path.is_file():
            continue
        stat = path.stat()
        artifacts.append(
            ArtifactInfo(
                path=str(path.relative_to(root)).replace("\\", "/"),
                size_kb=stat.st_size / 1024,
                modified=datetime.fromtimestamp(stat.st_mtime),
            )
        )

    artifacts.sort(key=lambda art: art.modified, reverse=True)
    return artifacts[:max_items]

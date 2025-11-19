"""Shared pytest fixtures for the PM agent test suite."""

from datetime import date

import pytest

import pm_agent.analytics as analytics
import pm_agent.tools as tools
from pm_agent.schema import ProjectPlan, Task


@pytest.fixture(autouse=True)
def stub_gantt(monkeypatch):
    """Prevent matplotlib work during tests."""
    monkeypatch.setattr("pm_agent.viz.generate_gantt", lambda: None)


@pytest.fixture
def project_root(tmp_path, monkeypatch):
    """Force all filesystem helpers to use an isolated directory."""
    monkeypatch.setattr(tools, "get_project_root", lambda: tmp_path)
    monkeypatch.setattr(analytics, "get_project_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def sample_plan():
    """Provide a reusable in-memory plan with dependencies and timelines."""
    return ProjectPlan(
        tasks=[
            Task(
                id=1,
                title="Architecture",
                assignee="Alex",
                status="in_progress",
                start_date=date(2024, 1, 2),
                due_date=date(2024, 1, 5),
            ),
            Task(
                id=2,
                title="API",
                assignee="Bailey",
                dependencies=[1],
                start_date=date(2024, 1, 5),
                due_date=date(2024, 1, 12),
                likely_days=5,
            ),
            Task(
                id=3,
                title="QA",
                dependencies=[2],
                status="todo",
                start_date=date(2024, 1, 12),
                due_date=date(2024, 1, 18),
                optimistic_days=3,
                pessimistic_days=6,
            ),
        ]
    )


@pytest.fixture
def persisted_plan(project_root, sample_plan):
    """Save the shared plan to disk for tools that read/write files."""
    tools.ensure_project_files()
    tools.save_plan(sample_plan)
    return sample_plan


@pytest.fixture
def artifacts_root(project_root):
    """Utility fixture to quickly access the artifacts directory."""
    path = project_root / "artifacts"
    path.mkdir(parents=True, exist_ok=True)
    return path

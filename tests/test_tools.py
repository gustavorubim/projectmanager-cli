from datetime import date

import numpy as np

from pm_agent import tools
from pm_agent.schema import ProjectPlan, Task


def test_ensure_project_files_creates_required_documents(project_root):
    tools.ensure_project_files()

    expected_files = [
        "00_project_charter.md",
        "01_plan.json",
        "02_followups.json",
        "03_risks_issues.md",
        "04_decisions.log",
        "05_focus.json",
    ]
    for rel in expected_files:
        assert (project_root / rel).exists(), f"{rel} was not created"

    for rel in ["artifacts/analysis", "artifacts/documentation", ".pm"]:
        assert (project_root / rel).exists()


def test_plan_round_trip_and_csv_import(project_root):
    tools.ensure_project_files()
    plan = ProjectPlan(
        tasks=[
            Task(id=1, title="Spec", start_date=date(2024, 1, 2), due_date=date(2024, 1, 5)),
            Task(id=2, title="Build", dependencies=[1], status="in_progress"),
        ]
    )

    tools.save_plan(plan)
    loaded = tools.load_plan()
    assert len(loaded.tasks) == 2
    assert loaded.get_task(2).dependencies == [1]

    csv_plan = tools.import_plan_from_csv()
    assert csv_plan.to_json_list() == loaded.to_json_list()


def test_add_and_update_task_flow(project_root):
    tools.ensure_project_files()

    add_msg = tools.add_task.invoke(
        {
            "title": "Kickoff",
            "assignee": "Alex",
            "start_date": "2024-01-01",
            "due_date": "2024-01-05",
            "status": "todo",
        }
    )
    assert "added successfully" in add_msg

    update_msg = tools.update_task.invoke(
        {
            "task_id": 1,
            "status": "done",
            "assignee": "Jordan",
            "dependencies": [2, 3],
        }
    )
    assert "updated successfully" in update_msg

    plan = tools.load_plan()
    task = plan.get_task(1)
    assert task.status == "done"
    assert task.assignee == "Jordan"
    assert task.dependencies == [2, 3]


def test_append_log_and_read_helpers(project_root, persisted_plan):
    log_msg = tools.append_log("Aligned on architecture.")
    assert "added successfully" in log_msg

    context = tools.read_context.invoke({})
    assert "# Project Context" in context
    assert "Architecture" in context
    assert "Aligned on architecture." in context

    plan_text = tools.read_plan.invoke({})
    assert "Architecture" in plan_text


def test_write_file_guardrails(project_root):
    tools.ensure_project_files()

    ok = tools.write_file.invoke({"filename": "docs/notes.md", "content": "hello"})
    assert "created successfully" in ok

    err = tools.write_file.invoke({"filename": "01_plan.json", "content": "bad"})
    assert "Error" in err


def test_focus_and_reporting_tools(project_root, persisted_plan):
    focus_summary = tools.focus_overview.invoke({"horizon_days": 30, "limit": 5})
    assert "Focus horizon" in focus_summary

    cp_snapshot = tools.critical_path_snapshot.invoke({})
    assert "Critical path length" in cp_snapshot

    np.random.seed(0)
    risk_report = tools.schedule_risk_report.invoke({"iterations": 100})
    assert "Monte Carlo" in risk_report

    artifacts_dir = project_root / "artifacts" / "analysis"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "report.txt").write_text("data", encoding="utf-8")
    artifacts_list = tools.list_project_artifacts.invoke({})
    assert "artifacts/analysis/report.txt" in artifacts_list

from datetime import date

from pm_agent.schema import ProjectPlan, Task


def test_project_plan_helpers_and_lookup():
    plan = ProjectPlan(
        tasks=[
            Task(id=1, title="Kickoff"),
            Task(id=2, title="Specs", assignee="Alex", status="in_progress"),
        ]
    )

    assert plan.get_next_id() == 3
    assert plan.get_task(2).title == "Specs"
    assert plan.get_task(99) is None


def test_project_plan_json_serialization_handles_dates_and_lists():
    start = date(2024, 2, 1)
    due = date(2024, 2, 10)
    plan = ProjectPlan(
        tasks=[
            Task(
                id=4,
                title="Prototype",
                start_date=start,
                due_date=due,
                dependencies=[1, 2],
                tags=["backend", "v1"],
            )
        ]
    )

    payload = plan.to_json_list()
    assert payload[0]["start_date"] == start.isoformat()
    assert payload[0]["due_date"] == due.isoformat()
    assert payload[0]["dependencies"] == [1, 2]
    assert payload[0]["tags"] == ["backend", "v1"]

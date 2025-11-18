"""Data models for the PM Agent."""

from datetime import date, datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class Task(BaseModel):
    """A single task in the project plan."""

    id: int
    title: str
    assignee: Optional[str] = None
    start_date: Optional[date] = None
    due_date: Optional[date] = None
    status: str = "todo"  # todo, in_progress, done, blocked
    dependencies: List[int] = Field(default_factory=list)
    optimistic_days: Optional[int] = None
    likely_days: Optional[int] = None
    pessimistic_days: Optional[int] = None

    class Config:
        json_encoders = {
            date: lambda v: v.isoformat() if v else None
        }


class ProjectPlan(BaseModel):
    """The complete project plan."""

    tasks: List[Task] = Field(default_factory=list)

    def get_next_id(self) -> int:
        """Get the next available task ID."""
        if not self.tasks:
            return 1
        return max(task.id for task in self.tasks) + 1

    def get_task(self, task_id: int) -> Optional[Task]:
        """Find a task by ID."""
        for task in self.tasks:
            if task.id == task_id:
                return task
        return None

    def to_json_list(self) -> List[dict]:
        """Convert to JSON-serializable list format."""
        return [
            {
                "id": task.id,
                "title": task.title,
                "assignee": task.assignee,
                "start_date": task.start_date.isoformat() if task.start_date else None,
                "due_date": task.due_date.isoformat() if task.due_date else None,
                "status": task.status,
                "dependencies": task.dependencies,
                "optimistic_days": task.optimistic_days,
                "likely_days": task.likely_days,
                "pessimistic_days": task.pessimistic_days
            }
            for task in self.tasks
        ]


class FocusItem(BaseModel):
    """A near-term focus item derived from the plan."""

    task_id: Optional[int] = None
    title: str
    due_date: Optional[date] = None
    status: Optional[str] = None
    assignee: Optional[str] = None
    days_remaining: Optional[int] = None
    reason: str = "due_soon"  # due_soon, overdue, blocked

    class Config:
        json_encoders = {
            date: lambda v: v.isoformat() if v else None
        }


class FocusBoard(BaseModel):
    """Represents the near-term focus list."""

    generated_at: Optional[datetime] = None
    horizon_days: int = 14
    max_items: int = 10
    items: List[FocusItem] = Field(default_factory=list)

    class Config:
        json_encoders = {
            date: lambda v: v.isoformat() if v else None,
            datetime: lambda v: v.isoformat() if v else None
        }

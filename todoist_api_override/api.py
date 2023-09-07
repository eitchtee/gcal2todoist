from dataclasses import dataclass
from typing import List, Dict, Any

from todoist_api_python.api import TodoistAPI
from todoist_api_python.endpoints import (
    TASKS_ENDPOINT,
    get_rest_url,
)
from todoist_api_python.http_requests import get, post
from todoist_api_python.models import (
    Due,
)


@dataclass
class Duration(object):
    amount: int
    unit: str

    @classmethod
    def from_dict(cls, obj):
        return cls(
            amount=obj["amount"],
            unit=obj["unit"],
        )

    def to_dict(self):
        return {
            "amount": self.amount,
            "unit": self.unit,
        }


@dataclass
class TaskPatched(object):
    assignee_id: str | None
    assigner_id: str | None
    comment_count: int
    is_completed: bool
    content: str
    created_at: str
    creator_id: str
    description: str
    due: Due | None
    id: str
    labels: List[str]
    order: int
    parent_id: str | None
    priority: int
    project_id: str
    section_id: str | None
    url: str
    duration: Duration | None

    sync_id: str | None = None

    @classmethod
    def from_dict(cls, obj):
        due: Due | None = None
        duration: Duration | None = None

        if obj.get("due"):
            due = Due.from_dict(obj["due"])

        if obj.get("duration"):
            duration = Duration.from_dict(obj["duration"])

        return cls(
            assignee_id=obj.get("assignee_id"),
            assigner_id=obj.get("assigner_id"),
            comment_count=obj["comment_count"],
            is_completed=obj["is_completed"],
            content=obj["content"],
            created_at=obj["created_at"],
            creator_id=obj["creator_id"],
            description=obj["description"],
            due=due,
            id=obj["id"],
            labels=obj.get("labels"),
            order=obj.get("order"),
            parent_id=obj.get("parent_id"),
            priority=obj["priority"],
            project_id=obj["project_id"],
            section_id=obj["section_id"],
            url=obj["url"],
            duration=duration,
        )

    def to_dict(self):
        due: dict | None = None

        if self.due:
            due = self.due.to_dict()

        return {
            "assignee_id": self.assignee_id,
            "assigner_id": self.assigner_id,
            "comment_count": self.comment_count,
            "is_completed": self.is_completed,
            "content": self.content,
            "created_at": self.created_at,
            "creator_id": self.creator_id,
            "description": self.description,
            "due": due,
            "id": self.id,
            "labels": self.labels,
            "order": self.order,
            "parent_id": self.parent_id,
            "priority": self.priority,
            "project_id": self.project_id,
            "section_id": self.section_id,
            "sync_id": self.sync_id,
            "url": self.url,
            "duration": self.duration,
        }


class TodoistAPIPatched(TodoistAPI):
    def get_tasks(self, **kwargs) -> List[TaskPatched]:
        ids = kwargs.pop("ids", None)

        if ids:
            kwargs.update({"ids": ",".join(str(i) for i in ids)})

        endpoint = get_rest_url(TASKS_ENDPOINT)
        tasks = get(self._session, endpoint, self._token, kwargs)
        return [TaskPatched.from_dict(obj) for obj in tasks]

    def add_task(self, content: str, **kwargs) -> TaskPatched:
        endpoint = get_rest_url(TASKS_ENDPOINT)
        data: Dict[str, Any] = {"content": content}
        data.update(kwargs)
        task = post(self._session, endpoint, self._token, data=data)
        return TaskPatched.from_dict(task)

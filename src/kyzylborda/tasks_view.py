import os.path
import logging
import datetime
from dataclasses import dataclass
from typing import Tuple, List, Dict, Any
from copy import copy
from jinja2 import Template

from .utils import utc_now
from .generate import get_or_generate_task
from .utils import list_files
from .cache import TasksCache, TaskCache, task_can_submit
from .tasks import Hint, Task
from .db import User, SubmittedFlag, GrantedHint


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UserTask:
    solved: bool
    can_submit: bool
    hints: List[Tuple[Hint, bool]]
    attachments: List[str]
    urls: List[str]
    bullets: List[str]
    description: str


@dataclass(frozen=True)
class UserTaskSummary:
    solved: bool
    solved_recently: bool
    can_submit: bool


class TaskNotReadyError(Exception):
    pass


class TaskNotFoundError(Exception):
    pass


def get_task_for_user(db, dynamic_attachments_path: str, tasks_cache: TasksCache, user: User, task_cache: TaskCache, substitutions: Dict[str, Any]) -> UserTask:
    task = task_cache.task

    attachments = []

    if task.generator is None:
        urls = task.urls
        bullets = task.bullets
    else:
        generated_task = get_or_generate_task(db, dynamic_attachments_path, tasks_cache, user, task_cache)
        if generated_task.substitutions is None:
            # Task is still being generated.
            raise TaskNotReadyError()
        substitutions = copy(substitutions)
        substitutions.update(generated_task.substitutions)

        try:
            full_path = os.path.join(dynamic_attachments_path, str(user.id), task.name, "attachments")
            dirnames, filenames = list_files(full_path)
            attachments.extend(filenames)
        except FileNotFoundError:
            pass

        urls = task.urls + generated_task.urls
        bullets = task.bullets + generated_task.bullets

    if task.attachments_path is not None:
        try:
            dirnames, filenames = list_files(task.attachments_path)
            attachments.extend(filenames)
        except FileNotFoundError:
            logger.warn(f"Attachments path '{task.attachments_path}' for task '{task.name}' doesn't exist")

    flag_count = db.query(SubmittedFlag).filter_by(task_name=task.name, submitter_id=user.id).count()
    granted = {x for x, in db.query(GrantedHint.hint_name).filter_by(task_name=task.name, requester_id=user.id)}
    hints = [(hint, hint.points == 0 or hint.name in granted) for hint in task_cache.hints.values()]

    description = task_cache.description_template.render(**substitutions)
    urls = [url.format(**substitutions) for url in urls]
    bullets = [Template(bullet).render(**substitutions) for bullet in bullets]

    return UserTask(
        solved=flag_count > 0,
        can_submit=task_can_submit(utc_now(), task),
        hints=hints,
        attachments=attachments,
        urls=urls,
        bullets=bullets,
        description=description,
    )


def get_task_summaries_for_user(db, tasks_cache: TasksCache, user: User) -> List[Tuple[Task, UserTaskSummary]]:
    time = utc_now()
    recent_time = time - datetime.timedelta(seconds=15)
    solved = {x for x, in db.query(SubmittedFlag.task_name).filter_by(submitter_id=user.id)}
    solved_recently = {x for x, in db.query(SubmittedFlag.task_name).filter(SubmittedFlag.accept_time > recent_time,
                                                                            SubmittedFlag.submitter_id == user.id)}

    def make_summary(task_cache):
        return UserTaskSummary(
            solved=task_cache.task.name in solved,
            solved_recently=task_cache.task.name in solved_recently,
            can_submit=task_can_submit(time, task_cache.task),
        )

    return sorted(
        [(task_cache.task, make_summary(task_cache)) for task_name, task_cache in tasks_cache.get_tasks(user=user)],
        key=lambda task: task[0].name
    )


def get_dummy_task_summaries(tasks_cache: TasksCache) -> List[Tuple[Task, UserTaskSummary]]:
    time = utc_now()

    def make_summary(task_cache):
        return UserTaskSummary(
            solved=False,
            solved_recently=False,
            can_submit=task_can_submit(time, task_cache.task),
        )

    return [(task_cache.task, make_summary(task_cache)) for task_name, task_cache in tasks_cache.get_tasks()]

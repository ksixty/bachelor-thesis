import logging
from typing import Optional
from sqlalchemy.exc import IntegrityError

from .utils import utc_now
from .cache import TasksCache, task_can_submit
from .tasks import TaskName, Task
from .db import User, SubmittedFlag, GeneratedTask, GeneratedFlag


logger = logging.getLogger(__name__)


class FlagExistsError(Exception):
    pass


class FlagStolenError(Exception):
    def __init__(self, user_id):
        super().__init__(user_id)
        self.user_id = user_id


class FlagNotFoundError(Exception):
    pass


class FlagForWrongTaskError(Exception):
    def __init__(self, task_name):
        super().__init__(task_name)
        self.task_name = task_name


class FlagTooLateError(Exception):
    pass


def submit_flag(db, tasks_cache: TasksCache, user: User, flag: str, accept_only_task: Optional[TaskName]=None) -> str:
    if flag in tasks_cache.static_flags:
        task_name = tasks_cache.static_flags[flag]
    else:
        task_row = db.query(GeneratedFlag).join(GeneratedTask).filter(GeneratedFlag.flag == flag).with_entities(GeneratedTask.task_name, GeneratedTask.user_id).one_or_none()
        if task_row is None:
            raise FlagNotFoundError()
        task_name = task_row[0]
        task_user = task_row[1]
        if task_user != user.id:
            raise FlagStolenError(task_user)

    try:
        # Check that task is not invisible.
        task_cache = tasks_cache.get_task(task_name, user=user)
    except KeyError:
        raise FlagNotFoundError()

    if accept_only_task and task_name != accept_only_task:
        raise FlagForWrongTaskError(task_name)

    time = utc_now()
    flag_entry = SubmittedFlag(
        task_name=task_name,
        flag=flag,
        submitter_id=user.id,
        accept_time=time,
    )
    db.add(flag_entry)

    try:
        db.commit()
    except IntegrityError as e:
        logger.warn(f"Error while submitting flag '{flag}' for task '{task_name}' by user '{user.login}'", exc_info=e)
        db.rollback()
        raise FlagExistsError()

    if not task_can_submit(time, task_cache.task):
        logger.warn(f"Flag '{flag}' for task '{task_name}' by user '{user.login}' is submitted too late (at {time}, after {task_cache.task.submit_not_after})")
        raise FlagTooLateError()

    return task_name

import logging
from sqlalchemy.exc import IntegrityError

from .utils import utc_now
from .cache import TasksCache, task_can_submit
from .tasks import Hint
from .db import SubmittedFlag, User, GrantedHint


logger = logging.getLogger(__name__)


class HintTakenError(Exception):
    pass


class HintNotFoundError(Exception):
    pass


class HintNotNeededError(Exception):
    pass



def grant_hint(db, tasks_cache: TasksCache, user: User, task_name: str, hint_name: str) -> Hint:
    try:
        task_cache = tasks_cache.get_task(task_name, user=user)
        hint = task_cache.hints[hint_name]
    except KeyError:
        raise HintNotFoundError()

    if hint.points == 0:
        raise HintTakenError()

    flag_count = db.query(SubmittedFlag).filter_by(task_name=task_name, submitter_id=user.id).count()
    if flag_count > 0:
        raise HintNotNeededError()

    hint_entry = GrantedHint(
        task_name=task_name,
        hint_name=hint.name,
        requester_id=user.id,
        request_time=utc_now(),
    )
    db.add(hint_entry)

    try:
        db.commit()
    except IntegrityError as e:
        logger.warn(f"Error while granting hint '{hint_name}' for task '{task_name}' to user {user.login}", exc_info=e)
        db.rollback()
        raise HintTakenError()

    return hint

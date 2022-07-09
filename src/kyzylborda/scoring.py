from typing import Set, List, Dict, Set, Optional, Iterator
from datetime import MINYEAR, datetime, timezone
from dataclasses import dataclass, field
import logging

from .db import User, SubmittedFlag, GrantedHint
from .tasks import TaskName, HintName
from .cache import TasksCache


logger = logging.getLogger(__name__)


@dataclass
class UserTaskScore:
    flag_time: datetime
    points: int
    hints: Set[HintName] = field(default_factory=set)


@dataclass
class UserScore:
    login: str
    name: str
    is_organizer: bool
    total_rank: Optional[int]
    tag_ranks: Dict[str, Optional[int]]
    tasks: Dict[TaskName, UserTaskScore] = field(default_factory=dict)
    last_flag_time: Optional[datetime] = None
    points: int = 0


# We need timezone-aware datetime here.
LAST_MOMENT = datetime.max.replace(tzinfo=timezone.utc)

def user_score_sort_key(score):
    # We place people who didn't solve any tasks last.
    last_flag_time = score.last_flag_time if score.last_flag_time is not None else LAST_MOMENT
    return (-score.points, last_flag_time)


def score_users(cache: TasksCache, db, tags: Optional[List[str]]=None, is_organizer: Optional[bool]=None, filter_zero=False) -> List[UserScore]:
    scores: Dict[int, UserScore] = {}

    # This whole query is crazy ineffective.
    user_query = db.query(User.id, User.login, User.name, User.tags, User.is_organizer, User.is_disqualified)
    if tags is not None:
        user_query = user_query.filter(User.tags.overlap(tags))
    if is_organizer is not None:
        user_query = user_query.filter_by(is_organizer=is_organizer)

    for id, login, name, user_tags, user_is_organizer, user_is_disqualified in user_query.order_by(User.signup_time.desc()):
        initial_rank = None if user_is_disqualified else 0
        tag_ranks = {tag: initial_rank for tag in user_tags}
        scores[id] = UserScore(login=login, name=name, total_rank=initial_rank, tag_ranks=tag_ranks, is_organizer=user_is_organizer)

    for id, submitter_id, task_name, raw_accept_time in db.query(SubmittedFlag.id, SubmittedFlag.submitter_id, SubmittedFlag.task_name, SubmittedFlag.accept_time).filter(SubmittedFlag.submitter_id.in_(scores.keys())):
        task_cache = cache.tasks.get(task_name)
        if task_cache is None:
            logger.warn(f"Flag for unknown task '{task_name}' with id {id} found; skipping")
            continue
        user_score = scores[submitter_id]
        accept_time = raw_accept_time.replace(tzinfo=timezone.utc)
        if task_cache.task.submit_not_after and accept_time > task_cache.task.submit_not_after:
            continue
        if user_score.last_flag_time is None:
            user_score.last_flag_time = accept_time
        else:
            user_score.last_flag_time = max(user_score.last_flag_time, accept_time)
        if task_name in user_score.tasks:
            raise RuntimeError("Impossible 'double' flag")
        task_score = UserTaskScore(flag_time=accept_time, points=task_cache.task.points)
        user_score.tasks[task_name] = task_score

    for id, requester_id, task_name, hint_name in db.query(GrantedHint.id, GrantedHint.requester_id, GrantedHint.task_name, GrantedHint.hint_name).filter(GrantedHint.requester_id.in_(scores.keys())):
        task_cache = cache.tasks.get(task_name)
        if task_cache is None:
            logger.warn(f"Hint for unknown task '{task_name}' with id {id} found; skipping")
            continue
        hint = task_cache.hints.get(hint_name)
        if hint is None:
            logger.warn(f"Hint with unknown name '{hint_name}' for task '{task_name}' with id {id} found; skipping")
            continue
        user_score = scores[requester_id]
        if task_name in user_score.tasks:
            task_score = user_score.tasks[task_name]
            task_score.hints.add(hint_name)
            task_score.points = max(0, task_score.points - hint.points)

    for user_score in scores.values():
        user_score.points = sum(map(lambda x: x.points, user_score.tasks.values()))

    # Why is type annotation needed here???
    score_values_i: Iterator[UserScore] = scores.values() # type: ignore
    if filter_zero:
        score_values_i = filter(lambda x: x.points > 0, score_values_i)
    score_values = sorted(score_values_i, key=user_score_sort_key)

    current_tag_ranks: Dict[str, int] = {}
    for total_rank, user_score in enumerate(filter(lambda x: x.total_rank is not None, score_values), 1):
        user_score.total_rank = total_rank
        for tag in user_score.tag_ranks:
            rank = current_tag_ranks.get(tag, 1)
            user_score.tag_ranks[tag] = rank
            current_tag_ranks[tag] = rank + 1

    return score_values

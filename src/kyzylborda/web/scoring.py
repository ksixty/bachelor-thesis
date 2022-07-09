from datetime import datetime
import logging
from typing import Dict, Optional, List
from flask import current_app, g, jsonify, abort
from flask_login import current_user
from dataclasses import dataclass, field
from dataclasses_json import dataclass_json

from ..scoring import UserScore, score_users
from ..tasks import TaskName


logger = logging.getLogger(__name__)


@dataclass_json
@dataclass(frozen=True)
class TaskResponse:
    name: str
    title: str
    category: str
    points: int


@dataclass_json
@dataclass(frozen=True)
class UserTaskScoreResponse:
    points: int
    flag_time: datetime


@dataclass_json
@dataclass(frozen=True)
class UserScoreResponse:
    name: str
    tasks: Dict[TaskName, UserTaskScoreResponse]
    points: int
    last_flag_time: Optional[datetime]
    total_rank: Optional[int]
    tag_ranks: Dict[str, Optional[int]]


@dataclass_json
@dataclass(frozen=True)
class ScoreboardResponse:
    tasks: Dict[TaskName, TaskResponse]
    users: List[UserScoreResponse]


@dataclass_json
@dataclass(frozen=True)
class NamedScoreboard:
    caption: str
    tags: Optional[List[str]] = None
    filter_zero: bool = False


def convert_task(task):
    return TaskResponse(
        name=task.name,
        title=task.title,
        category=task.category,
        points=task.points,
    )


def convert_user_task_score(task_score):
    return UserTaskScoreResponse(
        points=task_score.points,
        flag_time=task_score.flag_time,
    )


def convert_user_score(user_score: UserScore):
    return UserScoreResponse(
        name=user_score.name,
        tasks={task_name: convert_user_task_score(task_score) for task_name, task_score in user_score.tasks.items()},
        last_flag_time=user_score.last_flag_time,
        points=user_score.points,
        total_rank=user_score.total_rank,
        tag_ranks=user_score.tag_ranks,
    )


class KyzylScoreboards:
    named_scoreboards: Dict[str, NamedScoreboard]

    def __init__(self, app, named_scoreboards: Optional[Dict[str, NamedScoreboard]]=None):
        if named_scoreboards is None:
            self.named_scoreboards = {}
        else:
            self.named_scoreboards = named_scoreboards

    def scoreboard_route(self, success_view, filter_zero: bool=False, tags: Optional[List[str]]=None):
        tasks = [x.task for name, x in g.tasks_cache.get_tasks(user=current_user.user if current_user.is_authenticated else None)]
        is_organizer = False if not (current_user.is_authenticated and current_user.user.is_organizer) else None
        scores = score_users(g.tasks_cache, current_app.db, is_organizer=is_organizer, filter_zero=filter_zero, tags=tags)
        named_scoreboards = [(name, board.caption) for name, board in self.named_scoreboards.items()]
        return success_view(scores=scores, tasks=tasks, named_scoreboards=named_scoreboards)

    def named_scoreboard_route(self, success_view, name: str):
        if name not in self.named_scoreboards:
            logger.warn(f"Named scoreboard {name} not found")
            abort(404)
        scoreboard = self.named_scoreboards[name]
        wrapped_success_view = lambda **kwargs: success_view(current_named_scoreboard=name, **kwargs)
        return self.scoreboard_route(wrapped_success_view, filter_zero=scoreboard.filter_zero, tags=scoreboard.tags)

    def convert_scoreboard_api(self, scores, tasks, **kwargs):
        tasks = [convert_task(task) for task in tasks]
        users = [convert_user_score(user) for user in scores]
        response = ScoreboardResponse(
            tasks=tasks,
            users=users,
        )
        return jsonify(response)

    def get_scoreboard_api(self, **kwargs):
            return self.scoreboard_route(self.convert_scoreboard_api, **kwargs)

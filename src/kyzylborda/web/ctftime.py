from flask import jsonify
from typing import Optional, List
from dataclasses import dataclass
from dataclasses_json import dataclass_json

from .scoring import KyzylScoreboards

@dataclass_json
@dataclass(frozen=True)
class StandingResponse:
    pos: int
    team: str
    score: int
    lastAccept: Optional[int]


@dataclass_json
@dataclass(frozen=True)
class StandingsResponse:
    standings: List[StandingResponse]


def convert_standing(user_score):
    last_accept = None if user_score.last_flag_time is None else int(user_score.last_flag_time.timestamp())
    return StandingResponse(
        pos=user_score.total_rank,
        team=user_score.name,
        score=user_score.points,
        lastAccept=last_accept,
    )


def convert_scoreboard_ctftime_api(scores, tasks):
    standings = [convert_standing(user_score) for user_score in scores if user_score.total_rank is not None]
    response = StandingsResponse(
        standings=standings,
    )
    return jsonify(response)


def get_scoreboard_ctftime_api(scoreboards: KyzylScoreboards, **kwargs):
    return scoreboards.scoreboard_route(lambda scores, tasks, **kwargs: convert_scoreboard_ctftime_api(scores, tasks), **kwargs)

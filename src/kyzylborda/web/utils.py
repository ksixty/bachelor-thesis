from functools import wraps
from datetime import date, datetime, time
from flask import after_this_request
from flask.json import JSONEncoder


def hide_referrer_this_request():
    @after_this_request
    def do_hide_referrer(response):
        response.headers["Referrer-Policy"] = "no-referrer"
        return response


def hide_referrer(handler):
    @wraps(handler)
    def new_handler(*args, **kwargs):
        hide_referrer_this_request()
        return handler(*args, **kwargs)

    return new_handler


class DateTimeJSONEncoder(JSONEncoder):
    def default(self, obj):
        if isinstance(obj, date):
            return obj.isoformat()
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, time):
            return obj.isoformat()
        return super().default(obj)

import sys
import os
import logging
import yaml
import pytz
from typing import Optional
from datetime import datetime, timezone
import dateutil.parser
from functools import wraps
from werkzeug.middleware.proxy_fix import ProxyFix
from flask import Flask, render_template, redirect, url_for, current_app, request, g, abort
from flask_login import current_user, login_required, logout_user
from flask_babel import Babel, format_datetime, lazy_gettext as _
from flask_accept import accept_fallback
from flask_cors import CORS
from sqlalchemy.exc import IntegrityError

from ..mail import SMTPServer
from ..utils import get_factory
from ..tasks_view import get_task_summaries_for_user, get_dummy_task_summaries
from .users import AppUser, KyzylUsers
from .tasks import KyzylTasks
from .utils import DateTimeJSONEncoder
from .db import set_up_database
from .files import get_attachment_route, get_static_route
from .scoring import NamedScoreboard, KyzylScoreboards
from .ctftime import get_scoreboard_ctftime_api


logger = logging.getLogger(__name__)


def make_app(config_path: str):
    app = Flask(__name__)
    app.json_encoder = DateTimeJSONEncoder
    app.wsgi_app = ProxyFix(app.wsgi_app, x_host=1) # type: ignore
    with open(config_path) as f:
        app.config.from_mapping(**yaml.load(f, Loader=yaml.FullLoader))

    @app.before_first_request
    def init_stuff():
        logging.basicConfig(level=logging.INFO)
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    babel = Babel(app)
    cors = CORS(app, resources={"/scores": {"origins": "*"}})

    tz = pytz.timezone(app.config["TZ"])
    app.config["BABEL_DEFAULT_TIMEZONE"] = app.config["TZ"]
    app.jinja_env.globals["tz"] = tz

    is_anonymous_allowed = app.config.get("ALLOW_ANONYMOUS", False)
    set_up_database(app, app.config["DATABASE"])

    def allow_anonymous(func):
        def wrapped(*args, **kwargs):
            if not is_anonymous_allowed and not current_user.is_authenticated:
                return current_app.login_manager.unauthorized()
            return func(*args, **kwargs)
        wrapped.__name__ = func.__name__
        return wrapped

    if "SMTP_HOST" not in app.config:
        smtp_server = None
        from_address = None
    else:
        smtp_use_ssl = app.config.get("SMTP_USE_SSL", False)
        smtp_server = SMTPServer(
            host=app.config["SMTP_HOST"],
            port=app.config.get("SMTP_PORT", 465 if smtp_use_ssl else 587),
            ssl=smtp_use_ssl,
            starttls=app.config.get("SMTP_USE_STARTTLS", False),
            login=app.config.get("SMTP_LOGIN"),
            password=app.config.get("SMTP_PASSWORD"),
        )
        from_address = app.config["EMAIL_FROM"]

    title = app.config["TITLE"]

    external_key_path = app.config.get("EXTERNAL_KEY_PATH", None)
    if external_key_path:
        with open(external_key_path) as f:
            external_key: Optional[str] = f.read()
    else:
        external_key = None

    users = KyzylUsers(
        app,
        smtp_server=smtp_server,
        from_address=from_address,
        reset_token_timeout=app.config.get("RESET_PASSWORDS_TIMEOUT", 600),
        registration_enabled=app.config.get("REGISTRATION_ENABLED", False),
        external_key=external_key,
    )
    users.login_manager.unauthorized_handler(lambda: redirect(url_for("password_login"), code=303))

    tasks = KyzylTasks(
        app,
        tasks_path=app.config["TASKS_PATH"],
        dynamic_attachments_path=app.config["DYNAMIC_ATTACHMENTS_PATH"],
        default_attrs=app.config.get("DEFAULT_TASK_ATTRS", {}),
    )

    filter_zero_scores = app.config.get("FILTER_ZERO_SCORES", False)
    raw_named_scoreboards = app.config.get("NAMED_SCOREBOARDS", {})
    if isinstance(raw_named_scoreboards, dict):
        named_scoreboards: Dict[str, NamedScoreboard] = {name: NamedScoreboard.from_dict(dict) for name, dict in raw_named_scoreboards.items()} # type: ignore
    else:
        named_scoreboards = {dict["name"]: NamedScoreboard.from_dict(dict) for dict in raw_named_scoreboards} # type: ignore
    if "default" not in named_scoreboards:
        named_scoreboards["default"] = NamedScoreboard(_("Default"))

    scoreboards = KyzylScoreboards(
        app,
        named_scoreboards=named_scoreboards,
    )

    @app.template_filter()
    def format_locale_datetime(value):
        return format_datetime(value, format='dd MMMM HH:mm:ss')

    @app.context_processor
    def inject_args():
        return {"title": title}

    @app.route("/")
    @allow_anonymous
    def root():
        if current_user.is_authenticated:
            tasks = get_task_summaries_for_user(current_app.db, g.tasks_cache, current_user.user)
        else:
            tasks = None

        success_view = lambda **kwargs: render_template("board.html", **dict(kwargs, tasks=tasks, redirect=None))

        name = request.args.get("name", "default")
        if name not in named_scoreboards:
            abort(404)
        return scoreboards.named_scoreboard_route(
            success_view,
            name=name,
        )

    @app.route("/token_login")
    def token_login():
        return users.token_login_route(lambda: redirect(url_for("root"), code=303))

    @app.route("/password_login", methods=["GET", "POST"])
    def password_login():
        def error_view(**kwargs):
            return render_template("password_login.html", **kwargs)
        return users.password_login_route(url_for("root"), error_view)

    @app.route("/external_login")
    def external_login():
        return users.external_login_route(lambda: redirect(url_for("root"), code=303))

    @app.route("/signup", methods=["GET", "POST"])
    def signup():
        def error_view(**kwargs):
            return render_template("signup.html", **kwargs)
        return users.signup_route(url_for("signup_sent"), error_view, "token_email.txt", "token_login")

    @app.route("/signup_sent")
    def signup_sent():
        return render_template("signup_sent.html")

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        return redirect(url_for("root"), code=303)

    @app.route("/reset_token", methods=["GET", "POST"])
    def reset_token():
        def error_view(**kwargs):
            return render_template("reset_token.html", **kwargs)
        return users.reset_token_route(url_for("reset_token_sent"), error_view, "reset_token_email.txt")

    @app.route("/reset_token_sent")
    def reset_token_sent():
        return render_template("reset_token_sent.html")

    @app.route("/do_reset_token")
    def do_reset_token():
        return users.do_reset_token_route(url_for("reset_token_sent"), "token_email.txt", "token_login")

    @app.route("/scores")
    @accept_fallback
    @allow_anonymous
    def scoreboard():
        tags_arg = request.args.get("tags")
        tags = None if tags_arg is None else [tag for raw_tag in tags_arg.split(",") if len((tag := raw_tag.strip())) != 0]
        filter_zero = bool(request.args.get("filter_zero", False))
        return scoreboards.scoreboard_route(lambda **kwargs: render_template("scores.html", **kwargs), filter_zero=filter_zero, tags=tags)

    @scoreboard.support("application/json")
    @allow_anonymous
    def scoreboard_api():
        tags_arg = request.args.get("tags")
        tags = None if tags_arg is None else [tag for raw_tag in tags_arg.split(",") if len((tag := raw_tag.strip())) != 0]
        if "ctftime" in request.args:
            return get_scoreboard_ctftime_api(scoreboards, filter_zero=False, tags=tags)
        else:
            return scoreboards.get_scoreboard_api(filter_zero=filter_zero_scores, tags=tags)

    @app.route("/tasks/")
    @allow_anonymous
    def tasks_list():
        if current_user.is_authenticated:
            tasks = get_task_summaries_for_user(current_app.db, g.tasks_cache, current_user.user)
        else:
            tasks = get_dummy_task_summaries(g.tasks_cache)
        return render_template("tasks.html", tasks=tasks)

    @app.route("/tasks/<task_name>/")
    @login_required
    def get_task(task_name):
        def success_view(**kwargs):
            return render_template("task.html", **kwargs)
        return tasks.get_task_route(success_view, task_name)

    @app.route("/tasks/<task_name>/flush", methods=["POST"])
    @login_required
    def flush_task(task_name):
        def error_view(**kwargs):
            return render_template("ask_hint_error.html", **kwargs)

        return tasks.flush_task_route(url_for("root"), error_view, task_name)

    @app.route("/tasks/<task_name>/pregenerate", methods=["POST"])
    @login_required
    def pregenerate_task(task_name):
        def error_view(**kwargs):
            return render_template("ask_hint_error.html", **kwargs)

        return tasks.pregenerate_task_route(url_for("root"), error_view, task_name)

    @app.route("/tasks/<task_name>/hints/<hint_name>/ask", methods=["POST"])
    def ask_hint(task_name, hint_name):
        referrer = get_factory(request.headers, "Referer", lambda: url_for("get_task", task_name=task_name))

        def error_view(**kwargs):
            return render_template("ask_hint_error.html", redirect=referrer, **kwargs)

        return tasks.ask_hint_route(referrer, error_view, task_name, hint_name)

    @app.route("/tasks/<task_name>/attachments/<file_name>")
    @login_required
    def get_attachment(task_name, file_name):
        return get_attachment_route(tasks.dynamic_attachments_path, task_name, file_name)

    @app.route("/tasks/<task_name>/static/<path:file_path>")
    @login_required
    def get_static(task_name, file_path):
        return get_static_route(tasks.dynamic_attachments_path, task_name, file_path)

    @app.route("/flags/send", methods=["POST"])
    @login_required
    def send_flag():
        referrer = get_factory(request.headers, "Referer", lambda: url_for("tasks_list"))
        def error_view(**kwargs):
            return render_template("send_flag_error.html", redirect=referrer, **kwargs)
        return tasks.send_flag_route(
            success_url=url_for("root"),
            error_view=error_view
        )

    return app


def main():
    debug = os.getenv("DEBUG") == "F"
    app = make_app(sys.argv[1])
    app.run(port=8070, debug=debug)


if __name__ == "__main__":
    main()

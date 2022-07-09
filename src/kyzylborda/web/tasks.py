from typing import Optional, Any, Dict
import os
import logging
import os.path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from flask import current_app, g, request, abort, redirect
from flask_wtf import FlaskForm
from flask_login import current_user, login_required
from flask_babel import lazy_gettext as _
from wtforms import StringField, IntegerField
from wtforms.validators import InputRequired

from ..db import GeneratedTask, User
from ..utils import debounce
from ..tasks import read_tasks
from ..cache import build_tasks_cache, db_sanity_check
from ..tasks_view import TaskNotReadyError, UserTask, get_task_for_user
from ..flags import FlagStolenError, FlagExistsError, FlagNotFoundError, FlagForWrongTaskError, FlagTooLateError, submit_flag
from ..hints import HintNotFoundError, HintTakenError, HintNotNeededError, grant_hint
from ..generate import generate_task, flush_task


logger = logging.getLogger(__name__)


class AskHintForm(FlaskForm):
    pass


class FlushTaskForm(FlaskForm):
    pass


class PregenerateTaskForm(FlaskForm):
    count = IntegerField(_("Count"), validators=[InputRequired()])


class SendFlagForm(FlaskForm):
    flag = StringField(_("Flag"), validators=[InputRequired()])


class KyzylTasks:
    dynamic_attachments_path: str

    def __init__(self, app, tasks_path: str, dynamic_attachments_path: str, default_attrs: Optional[Dict[str, Any]]=None):
        # We need abspath here; this path is passed to generators which run from different cwd.
        self.dynamic_attachments_path = os.path.abspath(dynamic_attachments_path)

        # Be careful - this variable is updated from another thread.
        tasks_cache = None
        def reload_tasks():
            nonlocal tasks_cache
            new_tasks = build_tasks_cache(read_tasks(tasks_path, default_attrs=default_attrs))
            logger.info(f"Tasks reloaded, {len(new_tasks.tasks)} tasks total")
            db_sanity_check(new_tasks, app.db)
            tasks_cache = new_tasks

        class ReloadTasksEventHandler(FileSystemEventHandler):
            @debounce(2)
            def on_any_event(self, event):
                reload_tasks()

        @app.before_first_request
        def init_stuff():
            os.makedirs(self.dynamic_attachments_path, exist_ok=True)

            reload_tasks()
            tasks_observer = Observer()
            tasks_observer.schedule(ReloadTasksEventHandler(), tasks_path, recursive=True)
            tasks_observer.start()

        @app.before_request
        def set_tasks():
            g.tasks_cache = tasks_cache
            if current_user.is_authenticated:
                # Add it to session context because it's used in different views.
                g.flag_form = SendFlagForm()

    @login_required
    def get_task_route(self, success_view, task_name: str):
        try:
            task_cache = g.tasks_cache.get_task(task_name, user=current_user.user)
        except KeyError:
            logger.warn(f"Trying to access task '{task_name}' which is not available yet")
            abort(404)

        redirect = None
        try:
            substitutions = {
                "hostname": request.host,
            }
            user_task: Optional[UserTask] = get_task_for_user(current_app.db, self.dynamic_attachments_path, g.tasks_cache, current_user.user, task_cache, substitutions)
        except TaskNotReadyError:
            user_task = None
            redirect = ""

        params = {
            "task": task_cache.task,
            "redirect": redirect,
            "summary": user_task,
            "hint_form": AskHintForm(),
        }
        if current_user.user.is_organizer:
            params["flush_task_form"] = FlushTaskForm()
            params["pregenerate_task_form"] = PregenerateTaskForm()
            params["pregenerated_count"] = current_app.db.query(GeneratedTask).filter_by(user_id=None, task_name=task_name).count()

        return success_view(**params)

    @login_required
    def send_flag_route(self, success_url, error_view, accept_only_task=None):
        form = g.flag_form
        if not form.validate_on_submit():
            logger.warn(f"Flag posted by user '{current_user.user.login}' didn't pass validation")
            return error_view(form=form)

        try:
            task_name = submit_flag(current_app.db, g.tasks_cache, current_user.user, form.flag.data.strip().lower(), accept_only_task=accept_only_task)
        except FlagStolenError as e:
            victim_user = current_app.db.query(User).get(e.user_id)
            logger.warn(f"Flag '{form.flag.data}' posted by user '{current_user.user.login}' is stolen from user '{victim_user.login}'")
            return error_view(errors=[_("Invalid flag.")], form=form)
        except FlagNotFoundError:
            logger.warn(f"Unknown flag '{form.flag.data}' posted by user '{current_user.user.login}'")
            return error_view(errors=[_("Invalid flag.")], form=form)
        except FlagExistsError:
            return error_view(errors=[_("Task has already been solved.")], form=form)
        except FlagForWrongTaskError as e:
            logger.warn(f"Flag '{form.flag.data}' posted by user '{current_user.user.login}' is from task '{e.task_name}', not '{accept_only_task}'")
            return error_view(errors=[_("Your task is in another castle, go submit there.")], form=form)
        except FlagTooLateError:
            return error_view(errors=[_("The flag is correct, but the contest has ended.")], form=form)

        logger.info(f"Flag '{form.flag.data}' for task '{task_name}' has been submitted by user '{current_user.user.login}'")
        return redirect(success_url, code=303)

    @login_required
    def ask_hint_route(self, success_url, error_view, task_name, hint_name):
        form = AskHintForm()
        if not form.validate_on_submit():
            logger.warn(f"Hint request request posted by user '{current_user.user.login}' didn't pass validation: {repr(form.errors)}")
            return error_view(form=form)

        try:
            grant_hint(current_app.db, g.tasks_cache, current_user.user, task_name, hint_name)
        except HintNotFoundError:
            logger.warn(f"Unknown hint '{hint_name}' for task '{task_name}' requested by user '{current_user.user.login}'")
            return error_view(errors=[_("Hint not found.")], form=form)
        except HintTakenError:
            return error_view(errors=[_("Hint has already been granted.")], form=form)
        except HintNotNeededError:
            return error_view(errors=[_("You can't get hints for solved tasks.")], form=form)

        logger.info(f"Hint '{hint_name}' for task '{task_name}' requested by user '{current_user.user.login}' has been granted")
        return redirect(success_url, code=303)

    @login_required
    def flush_task_route(self, success_url, error_view, task_name):
        form = FlushTaskForm()
        if not form.validate_on_submit():
            logger.warn(f"Flush task request posted by user '{current_user.user.login}' didn't pass validation: {repr(form.errors)}")
            return error_view(form=form)

        if not current_user.user.is_organizer:
            logger.warn(f"Trying to flush task '{task_name}' while not being an organizer")
            abort(403)

        try:
            task_cache = g.tasks_cache.get_task(task_name, user=current_user.user)
        except KeyError:
            logger.warn(f"Trying to access task '{task_name}' which is not available yet")
            abort(404)

        flush_task(current_app.db, self.dynamic_attachments_path, g.tasks_cache, task_cache)
        logger.info(f"Task {task_name}' has been flushed by user '{current_user.user.login}")
        return redirect(success_url, code=303)

    @login_required
    def pregenerate_task_route(self, success_url, error_view, task_name):
        form = PregenerateTaskForm()
        if not form.validate_on_submit():
            logger.warn(f"Pregenerate task request posted by user '{current_user.user.login}' didn't pass validation: {repr(form.errors)}")
            return error_view(form=form)

        if not current_user.user.is_organizer:
            logger.warn(f"Trying to pregenerate task '{task_name}' while not being an organizer")
            abort(403)

        try:
            task_cache = g.tasks_cache.get_task(task_name, user=current_user.user)
        except KeyError:
            logger.warn(f"Trying to access task '{task_name}' which is not available yet")
            abort(404)

        for _ in range(form.count.data):
            generate_task(current_app.db, self.dynamic_attachments_path, g.tasks_cache, None, task_cache)
        logger.info(f"Task {task_name}' has been pregenerated {form.count.data} times by user '{current_user.user.login}")
        return redirect(success_url, code=303)

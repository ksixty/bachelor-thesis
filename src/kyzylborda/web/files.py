import os
import os.path
from flask import abort, send_file, current_app, g
from flask_login import current_user, login_required

from ..generate import get_or_generate_task


@login_required
def generic_get_file_route(get_files_path, dynamic_subdir, dynamic_attachments_path, task_name, file_path, as_attachment=True):
    norm_path = os.path.normpath(file_path)
    if norm_path != file_path or norm_path.split(os.sep)[0] == os.pardir:
        abort(404)

    try:
        task_cache = g.tasks_cache.tasks[task_name]
        task = task_cache.task
    except KeyError:
        abort(404)

    if task.generator is not None:
        # Generate task if it's not there yet. Ignore the result.
        get_or_generate_task(current_app.db, dynamic_attachments_path, g.tasks_cache, current_user.user, task_cache)
        full_path = os.path.abspath(os.path.join(dynamic_attachments_path, str(current_user.user.id), task.name, dynamic_subdir, file_path))
        if os.path.isfile(full_path):
            return send_file(full_path, as_attachment=True)

    attachments_path = get_files_path(task)
    if attachments_path is not None:
        full_path = os.path.abspath(os.path.join(attachments_path, file_path))
        if os.path.isfile(full_path):
            return send_file(full_path, as_attachment=as_attachment)

    abort(404)

def get_static_route(dynamic_attachments_path, task_name, file_path):
    return generic_get_file_route(lambda task: task.static_path, "static", dynamic_attachments_path, task_name, file_path, as_attachment=False)

def get_attachment_route(dynamic_attachments_path, task_name, file_name):
    return generic_get_file_route(lambda task: task.attachments_path, "attachments", dynamic_attachments_path, task_name, file_name)

import os
import os.path
import logging
import asyncio
from typing import Optional, Union, Callable, Set, List, Dict, Awaitable
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from .supervisor import Supervisor, SupervisorProgram
from ..utils import debounce, run_task_threadsafe
from ..tasks import Task, read_tasks
from ..cache import TasksCache, TaskCache, build_tasks_cache, empty_tasks_cache
from .supervisor import Supervisor
from .redirect import SocketRedirect, SocketSpec


logger = logging.getLogger(__name__)


def task_spec(task_cache: TaskCache, *, tmp_dir: str, state_dir: str) -> SupervisorProgram:
    task = task_cache.task
    assert task.daemon is not None

    # Don't change HOME; we might need it for Podman or other stuff.
    extra_env = {
        "TMPDIR": tmp_dir,
    }

    return SupervisorProgram(
        exec=task.daemon.exec + [state_dir, task.name],
        cwd=task.daemon.cwd,
        extra_env=extra_env,
        auto_restart="true",
    )


class ReloadTasksEventHandler(FileSystemEventHandler):
    _loop: asyncio.AbstractEventLoop
    _reload: Callable[[Set[str]], Awaitable[None]]
    _touched_paths: Set[str]

    def __init__(self, loop: asyncio.AbstractEventLoop, reload: Callable[[Set[str]], Awaitable[None]]):
        self._loop = loop
        # https://github.com/python/mypy/issues/2427
        self._reload = reload # type: ignore
        self._touched_paths = set()

    def _add_path(self, path):
        self._touched_paths.add(os.path.normpath(path))

    @debounce(2)
    def _flush_events(self):
        run_task_threadsafe(self._loop, lambda: self._reload(self._touched_paths))
        self._touched_paths = set()

    def on_created(self, event):
        self._add_path(event.src_path)
        self._flush_events()

    def on_deleted(self, event):
        self._add_path(event.src_path)
        self._flush_events()

    def on_modified(self, event):
        self._add_path(event.src_path)
        self._flush_events()

    def on_moved(self, event):
        self._add_path(event.src_path)
        self._add_path(event.dest_path)
        self._flush_events()


class TasksSupervisor:
    _tasks_path: str
    _state_path: str
    _http_listen: Optional[Union[str, int]]
    _tasks: TasksCache
    _daemon_specs: Dict[str, SupervisorProgram]
    _supervisor: Supervisor
    _observer: Observer
    _redirect: Optional[SocketRedirect] = None

    def __init__(self, tasks_path: str, state_path: str, http_listen: Optional[Union[str, int]] = None):
        self._tasks_path = tasks_path
        self._state_path = state_path
        self._http_listen = http_listen

        self._tasks = empty_tasks_cache
        self._daemon_specs = {}

        self._supervisor = Supervisor()
        self._observer = Observer()

    async def start_and_wait(self):
        await self._supervisor.start()
        logger.info(f"Running supervisor with state directory {self._supervisor.tmp_dir}")
        self._redirect = SocketRedirect(self._supervisor, http_listen=self._http_listen)
        await self._reload_tasks(set())
        loop = asyncio.get_running_loop()
        handler = ReloadTasksEventHandler(loop, self._reload_tasks)
        self._observer.schedule(handler, self._tasks_path, recursive=True)
        self._observer.start()
        try:
            await self._supervisor.wait()
        finally:
            self._redirect = None
            self._observer.stop()
            self._observer.unschedule_all()

    def _socket_spec(self, task_cache: TaskCache) -> SocketSpec:
        task = task_cache.task
        assert task.daemon is not None and task.daemon.socket is not None

        path = os.path.join(self._supervisor.tmp_dir, "tasks", task.name, task.daemon.socket)
        http_hostnames = None if task.daemon.http_hostnames is None else task.daemon.http_hostnames
        return SocketSpec(
            type=task.daemon.socket_type,
            path=path,
            http_hostnames=http_hostnames,
            tcp_port=task.daemon.tcp_port,
        )

    def _daemon_spec(self, task_cache: TaskCache) -> SupervisorProgram:
        task = task_cache.task

        task_tmp_dir = os.path.join(self._supervisor.tmp_dir, "tasks", task.name)
        os.makedirs(task_tmp_dir, exist_ok=True)

        task_state_dir = os.path.join(self._state_path, task.name)
        os.makedirs(task_state_dir, exist_ok=True)

        return task_spec(
            task_cache,
            tmp_dir=task_tmp_dir,
            state_dir=task_state_dir,
        )

    @property
    def redirect(self) -> SocketRedirect:
        assert self._redirect is not None
        return self._redirect

    def get_tasks(self) -> List[Task]:
        return read_tasks(self._tasks_path)

    async def post_reload(self):
        pass

    async def _reload_tasks(self, touched_paths: Set[str]):
        assert self._redirect is not None

        new_tasks = build_tasks_cache(self.get_tasks())
        logger.info(f"Tasks reloaded, {len(new_tasks.tasks)} tasks total")

        new_daemon_specs = {
            name: self._daemon_spec(task_cache)
            for name, task_cache in new_tasks.tasks.items()
            if task_cache.task.daemon is not None
        }

        touched_tasks_set = set()
        for name in new_daemon_specs:
            task_path = os.path.dirname(new_tasks.tasks[name].task.path)
            if any(map(lambda touched_path: os.path.commonpath([task_path, touched_path]) == task_path, touched_paths)):
                touched_tasks_set.add(name)
        touched_programs = {f"task_{name}" for name in touched_tasks_set}

        old_tasks_set = {path for path in self._daemon_specs}
        new_tasks_set = {path for path in new_daemon_specs}
        added_tasks_set = new_tasks_set.difference(old_tasks_set)

        new_programs = {f"task_{name}": spec for name, spec in new_daemon_specs.items()}
        new_programs["nginx"] = self._redirect.spec

        new_socks = [
            self._socket_spec(task_cache)
            for name, task_cache in new_tasks.tasks.items()
            if name in new_daemon_specs and task_cache.task.daemon is not None and task_cache.task.daemon.socket is not None
        ]

        # Set sockets before starting nginx for the first time.
        await self._redirect.set_sockets(new_socks)

        added, changed, removed = await self._supervisor.set_programs(new_programs)
        restarted = touched_programs - added - changed - removed

        for name in restarted:
            task_name = name.strip("task_")
            logger.info(f"Restarting daemon for {task_name}")
        await asyncio.gather(*[self._supervisor.restart(name) for name in restarted])

        self._tasks = new_tasks
        self._daemon_specs = new_daemon_specs
        await self.post_reload()

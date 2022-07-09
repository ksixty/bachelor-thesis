from typing import List, Dict, Set, Optional, Iterable
from datetime import datetime
from dataclasses import dataclass, field
from jinja2 import Template
import logging

from .utils import utc_now, set_factory
from .tasks import TaskName, Flag, HintName, Hint, Task, SocketType, TaskDaemon, TaskGenerator, MultiGeneratorKey
from .db import User, GeneratedTask, GeneratedFlag


logger = logging.getLogger(__name__)


def task_is_visible(time: datetime, task: Task, user: Optional[User]=None):
    if user is not None and user.is_organizer:
        return True
    else:
        return not task.hidden and not (task.not_before is not None and task.not_before > time)


def task_can_submit(time: datetime, task: Task):
    return not (task.submit_not_after is not None and task.submit_not_after < time)


def task_sort_key(task: Task):
    if task.sort_key is not None:
        return (-1, task.sort_key)
    else:
        return (task.points, task.name)


@dataclass(frozen=True)
class MultiGeneratorCache:
    generator: TaskGenerator
    tasks: Set[TaskName] = field(default_factory=set)


@dataclass(frozen=True)
class TaskCache:
    task: Task
    hints: Dict[HintName, Hint]
    description_template: Template


GeneratorPath = str


@dataclass(frozen=True)
class TasksCache:
    tasks: Dict[TaskName, TaskCache]
    static_flags: Dict[Flag, TaskName]
    multi_generators: Dict[MultiGeneratorKey, MultiGeneratorCache]

    def get_task(self, task_name: TaskName, user: Optional[User]=None):
        task_cache = self.tasks[task_name]
        if not task_is_visible(utc_now(), task_cache.task, user):
            raise KeyError
        return task_cache

    def get_tasks(self, user: Optional[User]=None):
        time = utc_now()
        return filter(lambda pair: task_is_visible(time, pair[1].task, user), self.tasks.items())


empty_tasks_cache = TasksCache({}, {}, {})


def build_task_cache(task: Task) -> TaskCache:
    hints = {hint.name: hint for hint in task.hints}
    if len(hints) < len(task.hints):
        raise RuntimeError("Conflicting hint names")

    return TaskCache(
        task=task,
        description_template=Template(task.description),
        hints=hints,
    )


# We remove a task if any error happens. This ensures the problem will be noticed.
def validate_tasks_cache(tasks: Iterable[Task]) -> Dict[TaskName, TaskCache]:
    tasks_dict: Dict[TaskName, TaskCache] = {}
    for raw_task in tasks:
        try:
            task = build_task_cache(raw_task)
        except Exception as e:
            logger.error(f"Error during validating task {raw_task.name}", exc_info=e)
            continue
        if task.task.name in tasks_dict:
            prev_task = tasks_dict[task.task.name]
            logger.error(f"Conflicting task name: {task.task.name}, in {task.task.path} and {prev_task.task.path}")
            continue
        tasks_dict[task.task.name] = task

    new_tasks_dict: Dict[TaskName, TaskCache] = {}
    static_flags: Dict[Flag, TaskName] = {}
    for name, task_cache in tasks_dict.items():
        conflicting = False
        local_flags = set()
        for raw_flag in task_cache.task.flags:
            flag = raw_flag.lower()
            if flag != raw_flag:
                logger.warn(f"Flag '{raw_flag}' for task {name} contains uppercase letters; flags are case-insensitive")
            if flag in local_flags:
                logger.warn(f"Repeating flag '{flag}' for task {name}")
                continue
            if flag in static_flags:
                prev_name = static_flags[flag]
                logger.error(f"Conflicting flag '{flag}', in {name} and {prev_name}")
                conflicting = True
                break
            local_flags.add(flag)
        if conflicting:
            continue
        new_tasks_dict[name] = task_cache
        for raw_flag in task_cache.task.flags:
            static_flags[raw_flag.lower()] = name
    tasks_dict = new_tasks_dict

    new_tasks_dict = {}
    multi_generators: Dict[MultiGeneratorKey, MultiGeneratorCache] = {}
    for name, task_cache in tasks_dict.items():
        if task_cache.task.generator is not None and task_cache.task.generator.multi_generator_key is not None:
            generator_cache = set_factory(multi_generators, task_cache.task.generator.multi_generator_key, lambda: MultiGeneratorCache(generator=task_cache.task.generator))
            if task_cache.task.generator != generator_cache.generator:
                prev_names = ", ".join(generator_cache.tasks)
                logger.error(f"Multi-generator options for task {name} are different from tasks {prev_names}:\nCurrent task: {task_cache.task.generator}\nOther tasks:  {generator_cache.generator}")
                continue
            generator_cache.tasks.add(name)
        new_tasks_dict[name] = task_cache
    tasks_dict = new_tasks_dict

    new_tasks_dict = {}
    daemons_http_hostnames: Dict[str, TaskName] = {}
    daemons_tcp_ports: Dict[int, TaskName] = {}
    for name, task_cache in tasks_dict.items():
        daemon = task_cache.task.daemon
        if daemon is not None:
            if daemon.socket_type == SocketType.HTTP:
                assert daemon.http_hostnames is not None
                local_hostnames: Set[str] = set()
                conflicting = False
                for hostname in daemon.http_hostnames:
                    if hostname in local_hostnames:
                        logger.error(f"Repeating HTTP hostname '{hostname}' in a daemon of task {name}")
                        conflicting = True
                        break
                    if hostname in daemons_http_hostnames:
                        prev_name = daemons_http_hostnames[hostname]
                        logger.error(f"Conflicting HTTP hostname '{hostname}', in {name} and {prev_name}")
                        conflicting = True
                        break
                    local_hostnames.add(hostname)
                if conflicting:
                    continue
                for hostname in daemon.http_hostnames:
                    daemons_http_hostnames[hostname] = name
            elif daemon.socket_type == SocketType.TCP:
                assert daemon.tcp_port is not None
                if daemon.tcp_port in daemons_tcp_ports:
                    prev_name = daemons_tcp_ports[daemon.tcp_port]
                    logger.error(f"Conflicting TCP port {daemon.tcp_port}, in {name} and {prev_name}")
                    continue
                daemons_tcp_ports[daemon.tcp_port] = name
        new_tasks_dict[name] = task_cache
    tasks_dict = new_tasks_dict

    return tasks_dict


def finalize_tasks_cache(tasks_dict: Dict[TaskName, TaskCache]) -> TasksCache:
    tasks_list = list(tasks_dict.values())
    # We sort the list here because Python 3.6+ guarantees order in dictionaries;
    # hence we get a default sort.
    tasks_list.sort(key=lambda x: task_sort_key(x.task))
    tasks_dict = {task.task.name: task for task in tasks_list}

    static_flags: Dict[Flag, TaskName] = {}
    for name, task_cache in tasks_dict.items():
        for raw_flag in task_cache.task.flags:
            static_flags[raw_flag.lower()] = name

    multi_generators: Dict[MultiGeneratorKey, MultiGeneratorCache] = {}
    for name, task_cache in tasks_dict.items():
        if task_cache.task.generator is not None and task_cache.task.generator.multi_generator_key is not None:
            generator_cache = set_factory(multi_generators, task_cache.task.generator.multi_generator_key, lambda: MultiGeneratorCache(generator=task_cache.task.generator))
            generator_cache.tasks.add(name)

    return TasksCache(
        tasks=tasks_dict,
        static_flags=static_flags,
        multi_generators=multi_generators,
    )


def build_tasks_cache(tasks: Iterable[Task]) -> TasksCache:
    tasks_dict = validate_tasks_cache(tasks)
    return finalize_tasks_cache(tasks_dict)


def db_sanity_check(tasks: TasksCache, db):
    for flag, dynamic_task in db.query(GeneratedFlag.flag, GeneratedTask.task_name).filter(GeneratedFlag.task_id == GeneratedTask.id, GeneratedFlag.flag.in_(tasks.static_flags.keys())):
        static_task = tasks.static_flags[flag]
        logger.error(f"Generated flag for {dynamic_task} clashes with static one for {static_task}: {flag}")

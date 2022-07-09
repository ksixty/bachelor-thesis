from datetime import datetime, timezone
import os.path
import glob
import logging
import yaml
import shutil
import re
import dateutil.parser
from copy import copy
from enum import Enum
from dataclasses import dataclass, field
from dataclasses_json import dataclass_json
from typing import List, Set, Optional, Dict, Any, Union

from .utils import abspath_from


logger = logging.getLogger(__name__)


NAME_REGEX = re.compile(r"[a-zA-Z0-9_.-]+")


Flag = str
HintName = str
TaskName = str
MultiGeneratorKey = str


@dataclass_json
@dataclass(frozen=True)
class Hint:
    name: HintName
    points: int
    text: str


class SocketType(Enum):
    HTTP = "http"
    TCP = "tcp"


@dataclass_json
@dataclass(frozen=True)
class TaskDaemon:
    exec: List[str]
    cwd: str
    socket: Optional[str] = None
    socket_type: SocketType = SocketType.HTTP
    http_hostnames: Optional[Set[str]] = None
    tcp_port: Optional[int] = None

    def __post_init__(self):
        if self.socket_type == SocketType.TCP and self.tcp_port is None:
            raise RuntimeError("TCP port should be specified for TCP sockets")
        if self.socket_type == SocketType.HTTP and (self.http_hostnames is None or len(self.http_hostnames) == 0):
            raise RuntimeError("No hostnames specified for HTTP socket")


@dataclass_json
@dataclass(frozen=True)
class TaskGenerator:
    exec: List[str]
    cwd: str
    multi_generator_key: Optional[MultiGeneratorKey] = None


@dataclass_json
@dataclass(frozen=True)
class Task:
    path: str
    name: TaskName
    title: str
    category: str
    points: int
    author: str
    description: str
    bullets: List[str] = field(default_factory=list)
    urls: List[str] = field(default_factory=list)
    flags: Set[Flag] = field(default_factory=set)
    hints: List[Hint] = field(default_factory=list)
    attachments_path: Optional[str] = None
    static_path: Optional[str] = None
    generator: Optional[TaskGenerator] = None
    daemon: Optional[TaskDaemon] = None
    not_before: Optional[datetime] = None
    submit_not_after: Optional[datetime] = None
    hidden: bool = False
    sort_key: Optional[str] = None

    def __post_init__(self):
        if NAME_REGEX.fullmatch(self.name) is None:
            raise RuntimeError("Invalid name")
        if self.generator is None and len(self.flags) == 0:
            raise RuntimeError("Non-dynamic tasks should have flags defined")


def convert_args(base_dir: str, cwd: Optional[str], raw_args: Union[str, List[str]]) -> List[str]:
    if isinstance(raw_args, str):
        args = raw_args.split(" ")
    else:
        args = copy(raw_args)
    if len(args) == 0:
        raise RuntimeError("Executable should be a string or non-empty list of arguments")
    raw_daemon_path = args[0]
    if os.path.dirname(raw_daemon_path) == "":
        which_daemon_path = shutil.which(raw_daemon_path)
        if which_daemon_path is None:
            raise RuntimeError(f"Executable not found: {raw_daemon_path}")
        daemon_path = which_daemon_path
    else:
        if cwd is not None:
            daemon_path = abspath_from(cwd, raw_daemon_path)
        else:
            daemon_path = abspath_from(base_dir, raw_daemon_path)
        if not os.path.isfile(daemon_path):
            raise RuntimeError(f"Executable not found: {raw_daemon_path}")
    args[0] = daemon_path
    return args


def convert_task_daemon(name: str, base_dir: str, daemon_data: Any) -> Dict[str, Any]:
    out_data = copy(daemon_data)
    if out_data.get("cwd") is not None:
        out_data["cwd"] = abspath_from(base_dir, out_data["cwd"])
    else:
        out_data["cwd"] = base_dir
    out_data["exec"] = convert_args(base_dir, out_data.get("cwd"), out_data["exec"])
    if out_data.get("http_hostnames") is None:
        out_data["http_hostnames"] = [name]
    return out_data


def convert_task_generator(base_dir: str, generator_data: Any) -> Dict[str, Any]:
    if isinstance(generator_data, dict):
        out_data = copy(generator_data)
    else:
        out_data = {"exec": generator_data}
    if out_data.get("cwd") is not None:
        out_data["cwd"] = abspath_from(base_dir, out_data["cwd"])
    else:
        out_data["cwd"] = base_dir
    out_data["exec"] = convert_args(base_dir, out_data.get("cwd"), out_data["exec"])
    return out_data


def do_read_task(path: str, default_attrs: Optional[Dict[str, Any]]=None) -> Optional[Task]:
    with open(path) as f:
        task_data = {}
        if default_attrs is not None:
            task_data.update(default_attrs)
        task_data.update(yaml.load(f, Loader=yaml.FullLoader))
    name, _ = os.path.splitext(os.path.basename(path))
    base_dir = os.path.dirname(os.path.abspath(path))

    if task_data.get("disabled", False):
        return None

    task_data["path"] = os.path.normpath(path)
    if "name" not in task_data:
        # Use name from file name.
        task_data["name"] = name
    if task_data.get("generator") is not None:
        task_data["generator"] = convert_task_generator(base_dir, task_data["generator"])
    if task_data.get("daemon") is not None:
        task_data["daemon"] = convert_task_daemon(name, base_dir, task_data["daemon"])
    if task_data.get("attachments_path") is not None:
        task_data["attachments_path"] = abspath_from(base_dir, task_data["attachments_path"])
    if task_data.get("static_path") is not None:
        task_data["static_path"] = abspath_from(base_dir, task_data["static_path"])
    not_before = task_data.get("not_before")
    if not_before is not None:
        task_data["not_before"] = dateutil.parser.isoparse(not_before).astimezone(timezone.utc)
    submit_not_after = task_data.get("submit_not_after")
    if submit_not_after is not None:
        task_data["submit_not_after"] = dateutil.parser.isoparse(submit_not_after).astimezone(timezone.utc)

    return Task.from_dict(task_data) # type: ignore


def read_task(path: str, default_attrs: Optional[Dict[str, Any]]=None) -> Optional[Task]:
    try:
        return do_read_task(path, default_attrs)
    except Exception as e:
        logger.error(f"Error while reading task {path}", exc_info=e)
        return None


def read_tasks(dir_path: str, default_attrs: Optional[Dict[str, Any]]=None) -> List[Task]:
    tasks = []
    for path, dirs, files in os.walk(dir_path):
        task_files = [file for file in files if file.endswith(".yaml") and not file.startswith(".")]
        if len(task_files) > 0:
            dirs.clear()
            for task_file in task_files:
                task = read_task(os.path.join(path, task_file), default_attrs=default_attrs)
                if task is not None:
                    tasks.append(task)
    return tasks

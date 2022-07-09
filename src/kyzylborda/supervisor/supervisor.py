import os.path
import sys
import time
from typing import Any, Optional, List, Union, Tuple, Dict, Mapping, Set, Callable, Iterable, Awaitable, cast
from dataclasses import dataclass
from tempfile import TemporaryDirectory
import asyncio
from aiohttp import UnixConnector
from aiohttp_xmlrpc.client import ServerProxy
from aiohttp_xmlrpc.exceptions import ServerError

from .scripts import supervisord as supervisord_script
from .scripts import logger as logger_script


def quote_arg(arg):
    quoted = arg.replace('"', '\\"')
    return f'"{quoted}"'


@dataclass(frozen=True)
class SupervisorProgram:
    exec: List[str]
    cwd: Optional[str] = None
    extra_env: Optional[Dict[str, str]] = None
    start_secs: Optional[int] = None
    stop_wait_secs: Optional[int] = None
    auto_restart: Optional[str] = None

    @property
    def config(self):
        command = " ".join([quote_arg(arg) for arg in self.exec])

        cfg = f"""
        command = {command}
        stdout_events_enabled = true
        stderr_events_enabled = true
        """

        if self.extra_env is not None:
            extra_env = ",".join([f"{name}={quote_arg(value)}" for name, value in self.extra_env.items()])
            cfg += f"environment = {extra_env}\n"

        if self.cwd is not None:
            cfg += f"directory = {self.cwd}\n"

        if self.start_secs is not None:
            cfg += f"startsecs = {self.start_secs}\n"

        if self.stop_wait_secs is not None:
            cfg += f"stopwaitsecs = {self.stop_wait_secs}\n"

        if self.auto_restart is not None:
            cfg += f"autorestart = {self.auto_restart}\n"

        return cfg


class Supervisor:
    _programs: Dict[str, SupervisorProgram]
    _tmp: Optional[TemporaryDirectory] = None
    _supervisord: Optional[asyncio.subprocess.Process] = None
    _proxy: Optional[ServerProxy] = None

    def __init__(self, programs=None):
        if programs is None:
            programs = {}
        self._programs = programs

    async def start(self):
        if self._supervisord is not None:
            return
        self._tmp = TemporaryDirectory(prefix="supervisor_")
        self._write_config()
        supervisord_path = os.path.abspath(supervisord_script.__file__)
        self._supervisord = await asyncio.create_subprocess_exec(
            sys.executable, supervisord_path, "-c", "supervisord.conf",
            cwd=self._tmp.name,
            stdin=asyncio.subprocess.DEVNULL,
        )
        sock_path = os.path.join(self._tmp.name, "supervisor.sock")
        conn = UnixConnector(path=sock_path)
        self._proxy = ServerProxy("http://dummy:dummy@127.0.0.1/RPC2", connector=conn)
        for delay in range(20):
            try:
                await self._proxy["supervisor.getAPIVersion"]()
                exc = None
                break
            except Exception as e:
                exc = e
                await asyncio.sleep(0.1)
        if exc is not None:
            raise exc

    async def terminate(self):
        if self._supervisord is None:
            return
        self._supervisord.terminate()
        await self._supervisord.wait()
        self._tmp.cleanup()
        self._supervisord = None
        self._tmp = None
        await self._proxy.close()
        self._proxy = None

    def _write_config(self):
        with open(os.path.join(self.tmp_dir, "supervisord.conf"), "w") as c:
            logger_path = os.path.abspath(logger_script.__file__)
            # See https://github.com/Supervisor/supervisor/issues/717 for explanation on username/password.
            c.write(f"""
            [supervisord]
            nodaemon = true
            logfile = /dev/null
            logfile_maxbytes = 0

            [rpcinterface:supervisor]
            supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface

            [unix_http_server]
            file = %(here)s/supervisor.sock
            chmod = 0700
            username = dummy
            password = dummy

            [eventlistener:logger]
            command = {sys.executable} {logger_path}
            buffer_size = 128
            events = PROCESS_STATE_EXITED,PROCESS_LOG
            result_handler = kyzylborda.supervisor.logger_handler:event_handler
            """)

            for name, program in self._programs.items():
                c.write(f"\n[program:{name}]\n{program.config}\n")

    async def _add_one(self, program: str):
        assert self._proxy is not None
        await self._proxy["supervisor.addProcessGroup"](program)

    async def _change_one(self, program: str):
        assert self._proxy is not None
        await self._proxy["supervisor.addProcessGroup"](program)
        await self._stop(program)

    async def _remove_one(self, program: str):
        assert self._proxy is not None
        await self._stop(program)
        await self._proxy["supervisor.removeProcessGroup"](program)

    async def set_programs(self, programs: Dict[str, SupervisorProgram]) -> Tuple[Set[str], Set[str], Set[str]]:
        self._programs = programs
        if self._supervisord is not None:
            assert self._proxy is not None
            self._write_config()
            ret = await self._proxy["supervisor.reloadConfig"]()
            added = set(ret[0][0])
            changed = set(ret[0][1])
            removed = set(ret[0][2])
            await asyncio.gather(
                *[self._add_one(name) for name in added],
                *[self._change_one(name) for name in changed],
                *[self._remove_one(name) for name in removed],
            )
            await self._proxy["supervisor.startAllProcesses"](True)
            return added, changed, removed
        else:
            return set(), set(), set()

    async def signal(self, program: str, signal: int):
        assert self._proxy is not None
        await self._proxy["supervisor.signalProcess"](program, int(signal))

    async def _start(self, program: str):
        assert self._proxy is not None
        await self._proxy["supervisor.startProcess"](program, True)

    async def _stop(self, program: str):
        assert self._proxy is not None
        try:
            await self._proxy["supervisor.stopProcess"](program, True)
        except ServerError:
            pass

    async def restart(self, program: str):
        await self._stop(program)
        await self._start(program)

    @property
    def tmp_dir(self) -> str:
        assert self._tmp is not None
        return self._tmp.name

    @property
    def programs(self) -> Dict[str, SupervisorProgram]:
        return self._programs

    async def wait(self):
        assert self._supervisord is not None
        try:
            await self._supervisord.wait()
        finally:
            await self.terminate()

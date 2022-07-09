import os
import os.path
import sys
import logging
from typing import Optional, Union, List
from argparse import ArgumentParser
from tempfile import TemporaryDirectory
import asyncio

from ..supervisor.daemon import TasksSupervisor
from ..tasks import SocketType, Task, read_task


logger = logging.getLogger(__name__)


arg_parser = ArgumentParser(description="Run a daemon in the same environment Kyzylborda does.")
arg_parser.add_argument("-p", "--port", type=int, default=8080, help="incoming HTTP port")
arg_parser.add_argument("task", metavar="TASK", help="path to task definition")


class SingleTaskSupervisor(TasksSupervisor):
    _task_path: str

    def __init__(self, task_path: str, state_path: str, http_listen: Optional[Union[str, int]] = None):
        tasks_path = os.path.dirname(task_path)
        super().__init__(tasks_path, state_path, http_listen)
        self._task_path = task_path

    def get_tasks(self) -> List[Task]:
        task = read_task(self._task_path)
        if task is None:
            logger.info("Task is disabled or no daemon is specified")
            return []
        else:
            return [task]

    async def post_reload(self):
        socks = self.redirect.sockets
        if len(socks) == 0 or socks[0].type != SocketType.HTTP:
            await self.redirect.set_default_http_socket(None)
        else:
            await self.redirect.set_default_http_socket(socks[0].path)


async def amain():
    logging.basicConfig(level=logging.INFO)

    args = arg_parser.parse_args()
    task_path = os.path.abspath(args.task)

    with TemporaryDirectory(prefix="kyzylborda_run_daemon_") as state_path:
        logger.info(f"Created new state directory {state_path}, running with HTTP port {args.port}")
        supervisor = SingleTaskSupervisor(task_path, state_path, http_listen=args.port)
        await supervisor.start_and_wait()


def main():
    asyncio.run(amain())


if __name__ == "__main__":
    main()

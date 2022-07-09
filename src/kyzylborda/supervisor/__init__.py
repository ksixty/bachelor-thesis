import os
import os.path
import sys
import logging
import yaml
import asyncio

from .daemon import TasksSupervisor


logger = logging.getLogger(__name__)


async def amain():
    logging.basicConfig(level=logging.INFO)

    config_path = sys.argv[1]
    with open(config_path) as f:
        config = yaml.load(f, Loader=yaml.FullLoader)
    tasks_path = config["TASKS_PATH"]
    raw_daemons_state_path = os.path.abspath(config["DAEMONS_STATE_PATH"])
    http_listen_sock = config.get("HTTP_REDIRECT_SOCK")

    if http_listen_sock is not None:
        try:
            os.remove(http_listen_sock)
        except:
            pass
        http_listen = f"unix:{os.path.abspath(http_listen_sock)}"
    else:
        http_listen = None

    daemons_state_path = os.path.abspath(raw_daemons_state_path)
    os.makedirs(daemons_state_path, exist_ok=True)

    supervisor = TasksSupervisor(tasks_path, daemons_state_path, http_listen=http_listen)
    await supervisor.start_and_wait()


def main():
    asyncio.run(amain())


if __name__ == "__main__":
    main()

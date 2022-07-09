import os
from argparse import ArgumentParser
import sys
import uuid
import logging
import tempfile

from ..tasks import read_tasks
from ..cache import build_tasks_cache
from ..generate import GeneratedTaskOutput, run_task_generator


logger = logging.getLogger(__name__)


arg_parser = ArgumentParser(description='Run a task generator.')
arg_parser.add_argument("-d", "--out-dir", default=tempfile.gettempdir(), help="path to task output directory")
arg_parser.add_argument("tasks_path", metavar="TASKS_PATH", help="path to task definitions directory")
arg_parser.add_argument("name", metavar="NAME", help="task name")


def main():
    logging.basicConfig(level=logging.INFO)

    args = arg_parser.parse_args()

    tasks_cache = build_tasks_cache(read_tasks(args.tasks_path))

    if args.name not in tasks_cache.tasks:
        logger.error(f"Task {args.name} doesn't exist")
        sys.exit(1)

    def add_task_result(name: str, output: GeneratedTaskOutput):
        print(f"{name}: {output}")

    random_seed = uuid.uuid4()
    out_dir = tempfile.mkdtemp(prefix="kyzylborda_test_generator_", dir=args.out_dir)
    logger.info(f"Generating task with random seed {random_seed} and output {out_dir}")

    run_task_generator(
        add_task_result=add_task_result,
        tasks_cache=tasks_cache,
        initial_task_cache=tasks_cache.tasks[args.name],
        random_seed=random_seed,
        parent_dir=out_dir,
    )


if __name__ == "__main__":
    main()

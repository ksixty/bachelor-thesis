import os
from argparse import ArgumentParser
import sys
import uuid
import logging
import tempfile

from ..tasks import read_tasks
from ..cache import build_tasks_cache


logger = logging.getLogger(__name__)


arg_parser = ArgumentParser(description='Validate and display tasks cache.')
arg_parser.add_argument("tasks", metavar="TASKS", help="path to task definitions directory")


def main():
    logging.basicConfig(level=logging.INFO)

    args = arg_parser.parse_args()

    tasks = read_tasks(args.tasks)
    tasks_cache = build_tasks_cache(tasks)
    for task in tasks:
        print(task)


if __name__ == "__main__":
    main()

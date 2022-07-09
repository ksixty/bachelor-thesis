import os
from argparse import ArgumentParser
import sys
import logging
import sqlalchemy

from ..db import Base

logger = logging.getLogger(__name__)


arg_parser = ArgumentParser(description='Initialize database.')
arg_parser.add_argument("-d", "--database", required=True, help="database URL")


def main():
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    args = arg_parser.parse_args()

    db_engine = sqlalchemy.create_engine(args.database)
    Base.metadata.create_all(db_engine)


if __name__ == "__main__":
    main()

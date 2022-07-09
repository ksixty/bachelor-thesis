import os

from . import make_app

app = make_app(os.environ.get("CONFIG", "config.yaml"))

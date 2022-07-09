import sys


def event_handler(event, response):
    response = response.decode()
    sys.stderr.write(response)
    sys.stderr.flush()

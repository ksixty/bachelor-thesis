from typing import Tuple, List, Callable, Awaitable, TypeVar
import os
import os.path
import errno
from datetime import datetime, timezone
from tempfile import TMP_MAX
from threading import Timer
from concurrent.futures import Future
import asyncio


def abspath_from(prefix, path):
    """ Convert to normalized absolute path based on given prefix. """
    return os.path.normpath(os.path.join(prefix, path))


def get_factory(dictionary, key, factory):
    """ Get value from dictionary, contructing it with factory if it doesn't exist. """
    try:
        return dictionary[key]
    except KeyError:
        return factory()


def set_factory(dictionary, key, factory):
    """ Construct and insert value into dictionary if it doesn't exist and return it. """
    try:
        return dictionary[key]
    except KeyError:
        ret = factory()
        dictionary[key] = ret
        return ret


def list_files(path) -> Tuple[List[str], List[str]]:
    """ List files and directories separately in a directory. """
    for (dirpath, dirnames, filenames) in os.walk(path):
        return dirnames, filenames
    raise FileNotFoundError()


# https://gist.github.com/walkermatt/2871026
def debounce(wait):
    """ Decorator that will postpone a functions
        execution until after wait seconds
        have elapsed since the last time it was invoked. """
    def decorator(fn):
        t = None

        def debounced(*args, **kwargs):
            nonlocal t
            if t is not None:
                t.cancel()
            t = Timer(wait, lambda: fn(*args, **kwargs))
            t.start()

        return debounced
    return decorator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


T = TypeVar('T')
def run_task_threadsafe(loop: asyncio.AbstractEventLoop, coro: Callable[[], Awaitable[T]]) -> T:
    future: Future[T] = Future()
    async def wrapper():
        try:
            ret = await coro()
            future.set_result(ret)
        except Exception as e:
            future.set_exception(e)
    loop.call_soon_threadsafe(loop.create_task, wrapper())
    return future.result()

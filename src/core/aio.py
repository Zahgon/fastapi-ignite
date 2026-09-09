"""
Background event loop used to drive coroutines from Flask's synchronous request path

The data, cache and scheduler layers are written with asyncio.  Flask serves requests
on worker threads, so a single long-lived event loop is started next to the WSGI
application and every coroutine is submitted to it.  Keeping one loop for the whole
process also keeps the SQLAlchemy async engine and the asyncpg connection pool bound
to a single loop, which is what they require.
"""
import asyncio
import logging
import threading
from typing import Any, Coroutine, Optional, TypeVar


logger = logging.getLogger(__name__)

T = TypeVar("T")

_loop: Optional[asyncio.AbstractEventLoop] = None
_thread: Optional[threading.Thread] = None
_lock = threading.Lock()


def start_event_loop() -> asyncio.AbstractEventLoop:
    """
    Start the background event loop if it is not running yet
    """
    global _loop, _thread

    with _lock:
        if _loop is not None and not _loop.is_closed():
            return _loop

        loop = asyncio.new_event_loop()
        ready = threading.Event()

        def _run() -> None:
            asyncio.set_event_loop(loop)
            loop.call_soon(ready.set)
            loop.run_forever()

        thread = threading.Thread(target=_run, name="asyncio-bridge", daemon=True)
        thread.start()
        ready.wait()

        _loop = loop
        _thread = thread
        logger.info("Background event loop started")
        return loop


def stop_event_loop() -> None:
    """
    Stop the background event loop and join its thread
    """
    global _loop, _thread

    with _lock:
        loop, thread = _loop, _thread
        _loop, _thread = None, None

    if loop is None:
        return

    loop.call_soon_threadsafe(loop.stop)
    if thread is not None:
        thread.join(timeout=5)
    loop.close()
    logger.info("Background event loop stopped")


def get_event_loop() -> asyncio.AbstractEventLoop:
    """
    Return the background event loop, starting it on first use
    """
    if _loop is None or _loop.is_closed():
        return start_event_loop()
    return _loop


def run_async(coro: Coroutine[Any, Any, T]) -> T:
    """
    Run a coroutine on the background event loop and wait for its result
    """
    return asyncio.run_coroutine_threadsafe(coro, get_event_loop()).result()

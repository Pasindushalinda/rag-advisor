"""Time-based cache decorator for Yahoo Finance calls."""

import time
from collections.abc import Callable, Hashable
from functools import wraps


def ttl_cache[**P, R](seconds: int) -> Callable[[Callable[P, R]], Callable[P, R]]:

    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        store: dict[Hashable, tuple[float, R]] = {}

        @wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            key = (args, tuple(sorted(kwargs.items())))
            hit = store.get(key)
            if hit and time.monotonic() - hit[0] < seconds:
                return hit[1]
            result = fn(*args, **kwargs)
            store[key] = (time.monotonic(), result)
            return result

        return wrapper

    return decorator

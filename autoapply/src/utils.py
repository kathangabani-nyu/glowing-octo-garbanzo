"""
Shared utilities for AutoApply V2.2.

Provides: structured logger, retry decorator, rate limiter, timer.
Module owner: Codex
"""

import logging
import os
import time
import functools
from contextlib import contextmanager
from typing import Optional


# ── Structured Logger ──

def get_logger(name: str, log_dir: str = "logs") -> logging.Logger:
    """Create a logger that writes to both console and file."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    # Console handler (INFO+)
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S"
    ))
    logger.addHandler(console)

    # File handler (DEBUG+)
    os.makedirs(log_dir, exist_ok=True)
    file_handler = logging.FileHandler(
        os.path.join(log_dir, f"{name}.log"), encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    logger.addHandler(file_handler)

    return logger


# ── Retry Decorator ──

def retry(max_attempts: int = 3, base_delay: float = 1.0,
          max_delay: float = 60.0, exceptions: tuple = (Exception,)):
    """Retry decorator with exponential backoff."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt < max_attempts - 1:
                        delay = min(base_delay * (2 ** attempt), max_delay)
                        time.sleep(delay)
            raise last_exc
        return wrapper
    return decorator


# ── Rate Limiter (Token Bucket) ──

class RateLimiter:
    """Simple token bucket rate limiter."""

    def __init__(self, rate: float, capacity: int = 1):
        """
        Args:
            rate: Tokens per second to add
            capacity: Maximum tokens in the bucket
        """
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_refill = time.time()

    def acquire(self):
        """Block until a token is available."""
        while True:
            self._refill()
            if self.tokens >= 1:
                self.tokens -= 1
                return
            sleep_time = (1 - self.tokens) / self.rate
            time.sleep(sleep_time)

    def _refill(self):
        now = time.time()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last_refill = now


# ── Timer Context Manager ──

@contextmanager
def timer(label: str, logger: Optional[logging.Logger] = None):
    """Context manager that logs elapsed time."""
    start = time.time()
    yield
    elapsed = time.time() - start
    msg = f"{label}: {elapsed:.2f}s"
    if logger:
        logger.info(msg)
    else:
        print(msg)

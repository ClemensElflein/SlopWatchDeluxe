import json
import signal
import sys

from .adapters import ADAPTERS
from .config import load_config
from .transport import request


def handle(provider, config_file=None):
    """Fail open, including bad input, missing config, DNS stalls, and server errors."""
    alarm = hasattr(signal, "setitimer")
    previous = None

    def expired(signum, frame):
        raise TimeoutError("Hook time budget exhausted")

    try:
        if alarm:
            previous = signal.signal(signal.SIGALRM, expired)
            signal.setitimer(signal.ITIMER_REAL, 1.2)
        raw = sys.stdin.buffer.read(1048577)
        if len(raw) > 1048576:
            return 0
        event = ADAPTERS[provider].normalize(json.loads(raw))
        if event:
            request(load_config(config_file), "/api/v1/events", event)
    except (Exception, KeyboardInterrupt):
        pass
    finally:
        if alarm and previous is not None:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
    return 0

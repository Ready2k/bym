"""Test configuration.

**Network access is blocked for the entire ordinary test suite**, at the
socket layer, so "no test makes a network call" is a guarantee rather than
a convention someone remembers to honour. A missing mock now fails loudly
here instead of quietly contacting a real API — or quietly costing credits.

Live tests opt back in with SUPER6_LIVE_TESTS=1.
"""

import os
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

LIVE = os.environ.get("SUPER6_LIVE_TESTS") == "1"

_real_socket = socket.socket
_real_create_connection = socket.create_connection


class NetworkAccessAttempted(RuntimeError):
    """Raised when a non-live test tries to open a network connection."""


def _blocked(*args, **kwargs):
    raise NetworkAccessAttempted(
        "A test attempted a real network connection. Ordinary tests must "
        "mock every external call; run live checks with SUPER6_LIVE_TESTS=1."
    )


@pytest.fixture(autouse=True, scope="session")
def block_network():
    if LIVE:
        yield
        return
    socket.socket = _blocked
    socket.create_connection = _blocked
    try:
        yield
    finally:
        socket.socket = _real_socket
        socket.create_connection = _real_create_connection

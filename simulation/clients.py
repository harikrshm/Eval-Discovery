"""Counting wrapper around a Groq-style client, and the seam that swaps it into the app."""

import threading
from contextlib import contextmanager


class _Completions:
    def __init__(self, owner: "CountingClient"):
        self._owner = owner

    def create(self, **kwargs):
        return self._owner._create(**kwargs)


class _Chat:
    def __init__(self, owner: "CountingClient"):
        self.completions = _Completions(owner)


class CountingClient:
    """Delegates to ``inner`` and counts calls and ``usage.total_tokens`` per model.

    ``with_options`` returns a CountingClient over ``inner.with_options(...)`` that shares this
    client's counters, so retry-bounded calls are counted too. A call is counted when it is
    made, even if it then raises; tokens are added only when the reply carries an int total."""

    def __init__(self, inner, _shared: "CountingClient | None" = None):
        self._inner = inner
        if _shared is None:
            self.calls: dict[str, int] = {}
            self.tokens: dict[str, int] = {}
            self._lock = threading.Lock()
        else:
            self.calls = _shared.calls
            self.tokens = _shared.tokens
            self._lock = _shared._lock
        self.chat = _Chat(self)

    def with_options(self, **kwargs) -> "CountingClient":
        return CountingClient(self._inner.with_options(**kwargs), _shared=self)

    def _create(self, **kwargs):
        model = str(kwargs.get("model", "unknown"))
        with self._lock:
            self.calls[model] = self.calls.get(model, 0) + 1
            self.tokens.setdefault(model, 0)
        response = self._inner.chat.completions.create(**kwargs)
        total = getattr(getattr(response, "usage", None), "total_tokens", None)
        if isinstance(total, int) and not isinstance(total, bool):
            with self._lock:
                self.tokens[model] += total
        return response


@contextmanager
def install_client(client):
    """Make ``client`` the model client behind ``backend.groq_client.groq_chat``; restore on exit."""
    import backend.groq_client as groq_client

    original = groq_client.client
    groq_client.client = client
    try:
        yield client
    finally:
        groq_client.client = original

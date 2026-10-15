"""A stand-in for the SimWorld that a cached tool registry resolves at CALL time (#280).

Building the FastMCP servers (registering ~245 tools) is the slow part of a scenario rebuild, so
they are built once per process. They must never hold a world: `WorldRef` forwards every attribute
to the world of the run that opened the session (a ContextVar set by `acme.sim.client`), and
`lazy()` defers anything a builder used to take from the world at build time.
"""

from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

from acme.sim.world import SimWorld

CURRENT: ContextVar[SimWorld] = ContextVar("acme_sim_world")


class WorldRef:
    def current(self) -> SimWorld:
        try:
            return CURRENT.get()
        except LookupError:
            raise RuntimeError(
                "no sim world: a shared registry was used outside a session"
            ) from None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.current(), name)


class _Lazy:
    def __init__(self, getter: Callable[[], Any]) -> None:
        self._getter = getter

    def __getattr__(self, name: str) -> Any:
        return getattr(self._getter(), name)


def resolve(sim: Any) -> SimWorld:
    """The real world behind `sim`, whether it is a SimWorld or a `WorldRef`."""
    return sim.current() if isinstance(sim, WorldRef) else sim


def lazy(getter: Callable[[], Any]) -> Any:
    """An object whose attributes come from `getter()` on every access (never cached)."""
    return _Lazy(getter)

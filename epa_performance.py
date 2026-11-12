"""Session-private derived results. Nothing is cached globally or written to disk.

The archive is a read-only snapshot for rendering. Loads and successful writes
install a new snapshot and clear this cache. Write transactions and Data Hub
approval/download validation deliberately bypass this module.
"""
from __future__ import annotations

import copy
import json
import sys
from collections import OrderedDict
from collections.abc import Callable, MutableMapping
from typing import Any

CACHE_KEY = "_epa_private_results_v1"
CACHE_VERSION = "fast-interface-2026-11-12"
MAX_ENTRIES = 16
MAX_BYTES = 64 * 1024 * 1024


def _marker(state: dict) -> tuple:
    return (state.get("updated_at"), len(state.get("evaluations", {})),
            len(state.get("forms", {})), len(state.get("conflicts", {})),
            id(state.get("settings")), id(state.get("forms")), id(state.get("evaluations")))


def invalidate(session: MutableMapping[str, Any]) -> None:
    """Call after replacing or intentionally editing the active archive."""
    session.pop(CACHE_KEY, None)


def _cache(session: MutableMapping[str, Any], state: dict) -> dict:
    cache = session.get(CACHE_KEY)
    marker = _marker(state)
    if (not isinstance(cache, dict) or cache.get("version") != CACHE_VERSION
            or cache.get("source") is not state or cache.get("marker") != marker):
        cache = {"version": CACHE_VERSION, "source": state, "marker": marker,
                 "items": OrderedDict(), "sizes": {}, "bytes": 0}
        session[CACHE_KEY] = cache
    return cache


def _key(name: str, parameters: Any) -> tuple[str, str]:
    # Parameters are small filter/config objects, not the resident archive.
    return name, json.dumps(parameters, sort_keys=True, default=str, ensure_ascii=False)


def _clone(value: Any) -> Any:
    # deepcopy also protects nested lists/dictionaries and DataFrame object cells.
    return copy.deepcopy(value)


def _size(value: Any, seen: set[int] | None = None) -> int:
    """Conservative memory estimate used to bound each session's cache."""
    seen = set() if seen is None else seen
    identity = id(value)
    if identity in seen:
        return 0
    seen.add(identity)
    if hasattr(value, "memory_usage"):
        usage = value.memory_usage(deep=True)
        return int(usage.sum() if hasattr(usage, "sum") else usage)
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        return size + sum(_size(k, seen) + _size(v, seen) for k, v in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return size + sum(_size(item, seen) for item in value)
    return size


def cached_result(session: MutableMapping[str, Any], state: dict, name: str,
                  parameters: Any, factory: Callable[[], Any], *, clone: bool = True) -> Any:
    """Compute once for this archive and selection. Callers receive safe copies.

    clone=False is reserved for internal immutable download snapshots. A callback
    must capture the returned snapshot before leaving the Streamlit script thread.
    """
    cache = _cache(session, state)
    items = cache["items"]
    key = _key(name, parameters)
    if key not in items:
        # Failed computations are not cached. Oversized values are freshly
        # computed for this caller, but do not evict every useful small result.
        value = factory()
        size = _size(value)
        if size > MAX_BYTES:
            return value
        items[key] = value
        cache["sizes"][key] = size
        cache["bytes"] += size
        while len(items) > MAX_ENTRIES or cache["bytes"] > MAX_BYTES:
            oldest, _ = items.popitem(last=False)
            cache["bytes"] -= cache["sizes"].pop(oldest, 0)
    else:
        items.move_to_end(key)
    return _clone(items[key]) if clone else items[key]


def cached_value(session: MutableMapping[str, Any], state: dict, name: str,
                 parameters: Any) -> Any:
    """Read a prepared download without doing work just to display a button."""
    items = _cache(session, state)["items"]
    key = _key(name, parameters)
    if key not in items:
        return None
    items.move_to_end(key)
    return _clone(items[key])

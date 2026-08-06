"""Hoster strategy registry.

Every Hoster subclass registers itself here so that strategy selection can
resolve a canonical strategy key to a class and its aliases.
"""

from demo_ghostprovider.hoster.strategies.base import Hoster

STRATEGY_REGISTRY: dict[str, type[Hoster]] = {}
_ALIAS_MAP: dict[str, str] = {}


def register(cls: type[Hoster]) -> type[Hoster]:
    """Class decorator: add a Hoster subclass to the strategy registry."""
    if cls.name:
        STRATEGY_REGISTRY[cls.name] = cls
        _ALIAS_MAP[cls.name.lower()] = cls.name
        for alias in cls.aliases:
            _ALIAS_MAP[alias.lower()] = cls.name
    return cls


def canonical_strategy(strategy: str) -> str:
    """Normalize a strategy name to the canonical registry key."""
    if not strategy:
        return ""
    key = str(strategy).strip()
    if key in STRATEGY_REGISTRY:
        return key
    return _ALIAS_MAP.get(key.lower(), "")


def get_strategy(name: str) -> type[Hoster] | None:
    """Return the Hoster class for a canonical or aliased strategy name."""
    return STRATEGY_REGISTRY.get(canonical_strategy(name))


def available_strategies() -> list[str]:
    """Return canonical strategy names in registry order."""
    return list(STRATEGY_REGISTRY)

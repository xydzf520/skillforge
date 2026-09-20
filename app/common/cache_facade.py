"""Namespace-oriented cache facade built on top of app.common.cache."""

from __future__ import annotations

from typing import Any

from app.common.cache import (
    cache_delete,
    cache_delete_pattern,
    cached_namespace_get,
    cached_namespace_set,
    invalidate_namespace,
)


def _join_parts(*parts: object) -> str:
    return ":".join(str(part) for part in parts if part not in (None, ""))


class NamespaceCache:
    def __init__(self, namespace: str, ttl: int | None = None):
        self.namespace = namespace
        self.ttl = ttl

    def build_key(self, *parts: object) -> str:
        return _join_parts(*parts)

    async def get(self, *parts: object) -> Any | None:
        return await cached_namespace_get(self.namespace, self.build_key(*parts), ttl=self.ttl)

    async def set(self, *parts: object, value: Any) -> None:
        await cached_namespace_set(self.namespace, self.build_key(*parts), value, ttl=self.ttl)

    async def delete(self, *parts: object) -> None:
        key = self.build_key(*parts)
        full_key = f"{self.namespace}:{key}" if key else self.namespace
        await cache_delete(full_key)

    async def invalidate(self) -> int:
        return await invalidate_namespace(self.namespace)

    async def invalidate_prefix(self, *parts: object) -> int:
        prefix = self.build_key(*parts)
        if not prefix:
            return await self.invalidate()
        return await cache_delete_pattern(f"{self.namespace}:{prefix}*")

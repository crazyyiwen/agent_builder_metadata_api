"""MongoDB client lifecycle using the PyMongo Async API.

Motor was deprecated in favor of ``pymongo.AsyncMongoClient`` and reaches
end-of-life on 2026-05-14. This module uses the new unified async client.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError

if TYPE_CHECKING:
    from pymongo.asynchronous.database import AsyncDatabase

log = logging.getLogger("app.db.mongo")


class MongoManager:
    """Owns the AsyncMongoClient. The client itself is lazy — instantiation
    does not open a network connection; the first operation does.
    """

    def __init__(
        self,
        uri: str,
        db_name: str,
        *,
        connect_timeout_ms: int = 5000,
        server_selection_timeout_ms: int = 5000,
    ) -> None:
        self._uri = uri
        self._db_name = db_name
        self._client: AsyncMongoClient = AsyncMongoClient(
            uri,
            connectTimeoutMS=connect_timeout_ms,
            serverSelectionTimeoutMS=server_selection_timeout_ms,
            tz_aware=True,
        )
        log.debug("MongoManager initialized db=%s", db_name)

    @property
    def client(self) -> AsyncMongoClient:
        return self._client

    @property
    def db(self) -> AsyncDatabase:
        return self._client[self._db_name]

    @property
    def db_name(self) -> str:
        return self._db_name

    async def ping(self) -> dict[str, Any]:
        """Force a round-trip to the server. Raises PyMongoError on failure."""
        result = await self._client.admin.command("ping")
        return dict(result)

    async def server_info(self) -> dict[str, Any]:
        info = await self._client.server_info()
        return {
            "version": info.get("version"),
            "modules": info.get("modules", []),
        }

    async def close(self) -> None:
        log.info("closing mongodb connection db=%s", self._db_name)
        try:
            await self._client.close()
        except PyMongoError as e:
            log.warning("error closing mongodb client: %s", e)

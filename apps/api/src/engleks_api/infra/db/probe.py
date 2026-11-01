from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class DatabaseProbe:
    name = "database"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def check(self) -> None:
        async with self._engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

import asyncio

from sqlalchemy import text

from batch.models.external import AsyncSessionLocal


async def create_tmp_duplicates(session: AsyncSessionLocal) -> None:
    statements = [
        """
        DROP TABLE IF EXISTS tmp_duplicates
        """,
        """
        CREATE TABLE tmp_duplicates AS
        WITH image_embeddings AS (
            SELECT i.id, e.embedding, i.created_at
            FROM images i
            JOIN embeddings e ON i.id = e.image_id
        )
        SELECT ROW_NUMBER() OVER () AS id,
               ie1.id AS image_id1,
               ie2.id AS image_id2,
               ie1.created_at AS created_at,
               ie1.embedding <=> ie2.embedding AS distance
        FROM image_embeddings ie1
        JOIN image_embeddings ie2 ON true
        """,
        "ALTER TABLE tmp_duplicates ADD PRIMARY KEY (id)",
        "CREATE INDEX idx_tmp_duplicates_id_distance ON tmp_duplicates (id DESC, distance)",
        "CREATE INDEX idx_tmp_duplicates_distance ON tmp_duplicates (distance)",
        "CREATE INDEX idx_tmp_duplicates_id ON tmp_duplicates (id DESC)",
    ]

    for stmt in statements:
        print(stmt)
        await session.execute(text(stmt))

    await session.commit()


async def main():

    async with AsyncSessionLocal() as session:
        await create_tmp_duplicates(session)



if __name__ == "__main__":
    asyncio.run(main())



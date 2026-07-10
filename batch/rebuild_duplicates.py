import asyncio

from sqlalchemy import text

from Storage.db import AsyncSessionLocal


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
        # CREATE TABLE AS SELECT carries no constraints, so these need to be
        # (re)added on every rebuild — otherwise a deleted image's rows
        # linger here forever instead of cascading like tmp_clusters does.
        "ALTER TABLE tmp_duplicates ADD CONSTRAINT tmp_duplicates_image_id1_fkey "
        "FOREIGN KEY (image_id1) REFERENCES images(id) ON DELETE CASCADE",
        "ALTER TABLE tmp_duplicates ADD CONSTRAINT tmp_duplicates_image_id2_fkey "
        "FOREIGN KEY (image_id2) REFERENCES images(id) ON DELETE CASCADE",
    ]

    for stmt in statements:
        print(stmt)
        await session.execute(text(stmt))


async def main():

    async with AsyncSessionLocal() as session:
        await create_tmp_duplicates(session)
        await session.commit()



if __name__ == "__main__":
    asyncio.run(main())



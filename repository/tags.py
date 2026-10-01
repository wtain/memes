from collections import defaultdict

from sqlalchemy import delete
from sqlalchemy.orm import aliased

from Storage.models import ImageTag

_DELETE_CHUNK_SIZE = 10_000

NOTE_TAG_SOURCE = "Note"
DESCRIPTION_TAG_SOURCE = "Ollama"


class TagsRepository:

    def __init__(self, session):
        self.tags = aliased(ImageTag)
        self.session = session

    async def delete_tags(self, source):
        print(f"Deleting all {source} tags...")
        await self.session.execute(
            delete(
                ImageTag
            )
            .where(
                ImageTag.source == source
            )
        )
        await self.session.commit()
        print("Done")

    async def delete_tags_for_images(self, source, image_ids):
        """Removes `source` tags for just these images (no commit; the caller's session
        commits together with the replacement tags)."""
        image_ids = list(image_ids)
        # Chunked: asyncpg caps a statement at 32767 bind parameters, and a full corpus is bigger.
        for start in range(0, len(image_ids), _DELETE_CHUNK_SIZE):
            chunk = image_ids[start:start + _DELETE_CHUNK_SIZE]
            await self.session.execute(
                delete(ImageTag).where(ImageTag.source == source, ImageTag.image_id.in_(chunk))
            )


class TagsSaver:

    def __init__(self, session):
        self.session = session
        self.image_tags = defaultdict(set)

    def add_tag(self, image_id, tag_name, value, source):
        dedup_key = f"{tag_name}:{value}"
        if dedup_key not in self.image_tags[image_id]:
            self.image_tags[image_id].add(dedup_key)
            self.session.add(ImageTag(key=tag_name,
                                 value=value,
                                 source=source,
                                 image_id=image_id))

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        print(f"Total images tagged: {len(self.image_tags.keys())}")
        print("Committing...")
        await self.session.commit()
        print("Done")
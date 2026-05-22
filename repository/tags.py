from collections import defaultdict

from sqlalchemy import delete
from sqlalchemy.orm import aliased

from Storage.models import ImageTag


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
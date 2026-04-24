from batch.repository.images import ImagesRepository
from batch.source.fs import LocalFileSystemFolder


class UnregisterNonExisting:

    def __init__(self, session, base_path):
        self.repo = ImagesRepository(session)
        self.source = LocalFileSystemFolder(base_path)

    async def run(self):
        images = await self.repo.get_all_images()
        non_existent = set()
        for (filename, id,) in images:
            if not self.source.check_exists(filename):
                print(f"Doesn't exist: {filename}")
                non_existent.add(id)

        print(f"Non existing: {len(non_existent)}")

        await self.repo.delete_images(non_existent)
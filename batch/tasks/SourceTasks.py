
from repository.images import ImagesRepository
from batch.source.fs import LocalFileSystemFolder
from Storage.models import ImageStatus


class UnregisterNonExisting:

    def __init__(self, session, base_path):
        self.repo = ImagesRepository(session)
        self.source = LocalFileSystemFolder(base_path)

    async def run(self):
        images = list(await self.repo.get_all_images())
        # A rejected recut cut is flagged and moved by move_flagged; an undecided one (not flagged) is never
        # unregistered, so an unplugged drive cannot wipe the review queue.
        images += list(await self.repo.get_flagged_images(ImageStatus.CUT_PROPOSED))
        non_existent = set()
        for (filename, id,) in images:
            if self.source.check_is_directory(filename):
                print(f"Directory: {filename}")
                non_existent.add(id)
            if not self.source.check_exists(filename):
                print(f"Doesn't exist: {filename}")
                non_existent.add(id)

        print(f"Non existing: {len(non_existent)}")

        await self.repo.delete_images(non_existent)
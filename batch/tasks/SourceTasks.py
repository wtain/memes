import os

from config.settings import settings
from repository.images import ImagesRepository
from batch.source.fs import LocalFileSystemFolder
from Storage.models import ImageStatus

_DEFAULT_MAX_DELETE_FRACTION = 0.2
_DEFAULT_MIN_GUARDED_COUNT = 50


class UnregisterNonExisting:
    """Deletes (cascading) the rows of images whose file is gone from BASE_PATH.

    Guards against an unmounted or emptied BASE_PATH wiping the library: it aborts, deleting nothing, when
    BASE_PATH is missing or empty, or when at least `min_guarded_count` rows (settings
    `general.unregister_min_guarded_count`) AND more than `max_delete_fraction` of the checked rows
    (`general.unregister_max_delete_fraction`) would go. `allow_mass_delete` (--allow-mass-delete) bypasses
    the fraction check for a deliberate large cleanup; a missing or empty BASE_PATH is never bypassed."""

    def __init__(self, session, base_path, allow_mass_delete=False,
                 max_delete_fraction=None, min_guarded_count=None):
        self.repo = ImagesRepository(session)
        self.base_path = base_path
        self.source = LocalFileSystemFolder(base_path)
        self.allow_mass_delete = allow_mass_delete
        self.max_delete_fraction = (
            max_delete_fraction if max_delete_fraction is not None
            else settings.get("general.unregister_max_delete_fraction", _DEFAULT_MAX_DELETE_FRACTION))
        self.min_guarded_count = (
            min_guarded_count if min_guarded_count is not None
            else settings.get("general.unregister_min_guarded_count", _DEFAULT_MIN_GUARDED_COUNT))

    def _check_base_path(self):
        if not os.path.isdir(self.base_path):
            raise RuntimeError(f"BASE_PATH {self.base_path!r} is not a directory (unmounted?); nothing deleted.")
        if not any(True for _ in os.scandir(self.base_path)):
            raise RuntimeError(f"BASE_PATH {self.base_path!r} is empty (unmounted?); nothing deleted.")

    async def run(self):
        self._check_base_path()
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

        total = max(len(images), 1)
        if (not self.allow_mass_delete and len(non_existent) >= self.min_guarded_count
                and len(non_existent) / total > self.max_delete_fraction):
            raise RuntimeError(
                f"Refusing to delete {len(non_existent)} of {len(images)} images "
                f"(> {self.max_delete_fraction:.0%}); is BASE_PATH mounted? "
                f"Nothing deleted. Re-run with --allow-mass-delete for a deliberate large cleanup.")

        await self.repo.delete_images(non_existent)

import os
import shutil

MAX_FILENAME_LENGTH = 255  # conservative common denominator across NTFS/ext4
_SUFFIX_RESERVE = 6        # room for "_" + up to 4 digits, plus a little headroom


def move_without_overwrite(src_path: str, dest_dir: str) -> str:
    """Move src_path into dest_dir. If a file with the same name already exists there,
    renames with a numeric suffix (name_1.ext, name_2.ext, ...) instead of silently
    overwriting it. Returns the actual filename used at the destination -- callers that
    register this filename elsewhere (e.g. a DB row) must use the returned value, not
    the original.

    The stem is truncated up front, before any collision check, to leave room for a
    numeric suffix -- an already-near-the-filesystem-limit name would otherwise become
    unwritable the moment a suffix is appended. Short names are unaffected (a no-op
    slice).
    """
    filename = os.path.basename(src_path)
    stem, ext = os.path.splitext(filename)
    max_stem_length = max(1, MAX_FILENAME_LENGTH - len(ext) - _SUFFIX_RESERVE)
    stem = stem[:max_stem_length]

    candidate = f"{stem}{ext}"
    counter = 0
    while os.path.exists(os.path.join(dest_dir, candidate)):
        counter += 1
        candidate = f"{stem}_{counter}{ext}"

    shutil.move(src_path, os.path.join(dest_dir, candidate))
    return candidate

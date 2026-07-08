import hashlib

CHUNK_SIZE = 65_536  # 64 KB


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            h.update(chunk)
    return h.hexdigest()


def files_are_identical(paths: list) -> bool:
    """Stream-compare all files chunk by chunk; True if all are byte-identical."""
    handles = [open(p, "rb") for p in paths]
    try:
        while True:
            chunks = [fh.read(CHUNK_SIZE) for fh in handles]
            if len(set(chunks)) > 1:
                return False
            if not chunks[0]:  # all handles reached EOF simultaneously
                return True
    finally:
        for fh in handles:
            fh.close()
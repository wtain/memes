"""io_producer skips hidden (dot-prefixed) files, e.g. a leftover .comics-build-* temp file (no DB)."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import batch.extract_text_from_memes as ocr_module


def test_io_producer_skips_dot_files(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"x")
    (tmp_path / ".comics-build-x.jpg").write_bytes(b"x")

    image = MagicMock(status="active")
    image_repo = MagicMock()
    image_repo.find_image_by_filename = AsyncMock(return_value=image)
    status_repo = MagicMock()
    status_repo.should_process = AsyncMock(return_value=True)
    status_repo.mark_started = AsyncMock()
    metrics = MagicMock()
    queue = asyncio.Queue()

    with patch.object(ocr_module, "AsyncSessionLocal") as session_cls, \
            patch.object(ocr_module, "ImageProcessingStatusRepository", return_value=status_repo), \
            patch.object(ocr_module, "ImagesRepository", return_value=image_repo):
        session_cls.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        asyncio.run(ocr_module.io_producer(str(tmp_path), queue, "p", metrics, MagicMock()))

    queued = []
    while not queue.empty():
        queued.append(queue.get_nowait())
    assert [item[0] for item in queued if item] == ["a.jpg"]
    image_repo.find_image_by_filename.assert_awaited_once_with("a.jpg")
    metrics.increment.assert_any_call("skipped.hidden")
    assert [c.args[0] for c in metrics.increment.call_args_list].count("skipped.hidden") == 1

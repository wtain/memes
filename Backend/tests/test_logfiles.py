"""Bounded log files (O5): never fatal, bounded per slot, request id in the line, sampled lines. No database."""
import errno
import logging
import logging.handlers
import os
import re

import pytest

import Backend.app.main as main
from Backend.app.observability import logfiles


class FakeSettings:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


@pytest.fixture(autouse=True)
def clean_sampling():
    logfiles.reset_sampling()
    yield
    logfiles.reset_sampling()


@pytest.fixture
def isolated_logging():
    """_configure_logging reformats every handler of every logger (pytest's own included): put all of it back afterwards."""
    root = logging.getLogger()
    loggers = [root] + [lgr for lgr in logging.Logger.manager.loggerDict.values() if isinstance(lgr, logging.Logger)]
    saved = [(lgr, list(lgr.handlers)) for lgr in loggers]
    states = [(h, h.formatter, list(h.filters)) for lgr, hs in saved for h in hs]
    level = root.level
    yield
    for lgr, hs in saved:
        for h in list(lgr.handlers):
            if h not in hs:
                lgr.removeHandler(h)
        for h in hs:
            if h not in lgr.handlers:
                lgr.addHandler(h)
    for h, formatter, filters in states:
        h.setFormatter(formatter)
        h.filters[:] = filters
    root.setLevel(level)


def test_install_survives_missing_unwritable_and_full_directory(tmp_path, monkeypatch, capsys):
    # missing
    assert logfiles.install(str(tmp_path / "nope"), "w0", 1000, 2) == []
    assert "log directory does not exist" in capsys.readouterr().out
    # a file where the directory should be
    plain = tmp_path / "plain"
    plain.write_text("x")
    assert logfiles.install(str(plain), "w0", 1000, 2) == []
    # unset: simply off, silent
    assert logfiles.install(None, "w0", 1000, 2) == []
    assert logfiles.install("", "w0", 1000, 2) == []
    # unwritable: opening the file raises
    def denied(*args, **kwargs):
        raise PermissionError(errno.EACCES, "denied")
    with monkeypatch.context() as m:
        m.setattr(logfiles, "SafeRotatingFileHandler", denied)
        assert logfiles.install(str(tmp_path), "w0", 1000, 2) == []
    assert "log files off (PermissionError)" in capsys.readouterr().out

    # full: the open works, the first write fails with ENOSPC
    class Full:
        def write(self, _data):
            raise OSError(errno.ENOSPC, "No space left on device")

        def flush(self):
            pass

        def close(self):
            pass

    with monkeypatch.context() as m:
        m.setattr(logging.handlers.RotatingFileHandler, "_open", lambda self: Full())
        assert logfiles.install(str(tmp_path), "w1", 1000, 2) == []
    assert "log files off (OSError)" in capsys.readouterr().out
    # a bad slot name never reaches the file system
    assert logfiles.install(str(tmp_path), "../evil", 1000, 2) == []


def test_backend_logging_setup_survives_a_missing_log_directory(tmp_path, monkeypatch, capsys, isolated_logging):
    monkeypatch.setattr(main, "settings", FakeSettings({"observability.log_file.dir": str(tmp_path / "missing")}))
    root = logging.getLogger()
    before = list(root.handlers)
    level = root.level
    try:
        main._configure_logging()  # must not raise
        assert not any(getattr(h, logfiles.FILE_HANDLER_MARK, False) for h in root.handlers)
        logging.getLogger("t").warning("still logs")
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
        root.setLevel(level)
    assert "log directory does not exist" in capsys.readouterr().out


def test_backend_logging_setup_writes_the_slot_file_with_request_id(tmp_path, monkeypatch, isolated_logging):
    monkeypatch.setattr(main, "settings", FakeSettings({"observability.log_file.dir": str(tmp_path)}))
    monkeypatch.setenv("MEMES_LOG_SLOT", "w1")
    root = logging.getLogger()
    before = list(root.handlers)
    level = root.level
    try:
        main._configure_logging()
        main._configure_logging()  # a second lifespan must not stack file handlers
        assert sum(1 for h in root.handlers if getattr(h, logfiles.FILE_HANDLER_MARK, False)) == 1
        token = logfiles.request_id_var.set("abc123")
        try:
            logging.getLogger("t.file").warning("hello file")
        finally:
            logfiles.request_id_var.reset(token)
        logging.getLogger("t.file").warning("outside")
        for h in root.handlers:
            h.flush()
    finally:
        logfiles.remove_installed([root])
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
        root.setLevel(level)
    text = (tmp_path / "backend-w1.log").read_text(encoding="utf-8")
    assert re.search(r"\[abc123\] t\.file: hello file", text)
    assert re.search(r"\[-\] t\.file: outside", text)


def test_rotation_bounds_files_per_slot(tmp_path):
    handlers = logfiles.install(str(tmp_path), "w0", 2000, 3)
    assert len(handlers) == 1
    logger = logging.getLogger("rotation.test")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(handlers[0])
    try:
        for i in range(2000):
            logger.info("line %d %s", i, "x" * 80)
    finally:
        logger.removeHandler(handlers[0])
        handlers[0].close()
    files = sorted(os.listdir(tmp_path))
    assert files == ["backend-w0.log", "backend-w0.log.1", "backend-w0.log.2", "backend-w0.log.3"]
    for name in files:
        assert os.path.getsize(tmp_path / name) <= 2000 + 200  # one record may overshoot, never a second one


def test_log_sampled_is_once_per_interval_per_key(caplog):
    caplog.set_level(logging.INFO)
    logger = logging.getLogger("sample.test")
    assert logfiles.log_sampled(logger, "k", "line %s", "a") is True
    assert logfiles.log_sampled(logger, "k", "line %s", "b") is False
    assert logfiles.log_sampled(logger, "other", "line %s", "c") is True
    assert logfiles.log_sampled(logger, "k", "line %s", "d", interval_s=0) is True
    assert "(+1 similar suppressed)" in caplog.records[-1].getMessage()


def test_safe_text_escapes_and_bounds():
    out = logfiles.safe_text("a\nb\r\x1b[31m" + "z" * 500, 40)
    assert "\n" not in out and "\r" not in out and "\x1b" not in out and len(out) < 60

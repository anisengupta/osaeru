"""Unit tests for osaeru.logging_config."""

import logging

import pytest

from osaeru import logging_config


@pytest.fixture
def clean_root_logger(monkeypatch):
    """basicConfig() does nothing if the root logger already has handlers.
    pytest attaches its own capture handler just before each test runs, so
    configure() is wrapped to clear the root logger first, and everything is
    restored afterwards."""
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    real_configure = logging_config.configure

    def configure_on_clean_root():
        root.handlers = []
        return real_configure()

    configure_on_clean_root.real = real_configure
    monkeypatch.setattr(logging_config, "configure", configure_on_clean_root)
    yield root
    for h in root.handlers:
        h.close()
    root.handlers = saved_handlers
    root.setLevel(saved_level)


def test_returns_log_path_under_home(isolated, clean_root_logger):
    assert logging_config.configure() == isolated.home / ".osaeru" / "osaeru.log"


def test_creates_the_log_directory(isolated, clean_root_logger):
    assert not (isolated.home / ".osaeru").exists()
    logging_config.configure()
    assert (isolated.home / ".osaeru").is_dir()


def test_sets_debug_level(clean_root_logger):
    logging_config.configure()
    assert clean_root_logger.level == logging.DEBUG


def test_adds_file_and_console_handlers(clean_root_logger):
    path = logging_config.configure()
    kinds = {type(h) for h in clean_root_logger.handlers}
    assert kinds == {logging.FileHandler, logging.StreamHandler}
    (file_handler,) = [
        h for h in clean_root_logger.handlers if isinstance(h, logging.FileHandler)
    ]
    assert file_handler.baseFilename == str(path)
    assert file_handler.mode == "a"


def test_writes_formatted_messages_to_file(clean_root_logger):
    path = logging_config.configure()
    logging.getLogger("osaeru.test").info("hello from the test")
    for h in clean_root_logger.handlers:
        h.flush()
    line = path.read_text().strip().splitlines()[-1]
    assert line.endswith("[INFO] osaeru.test: hello from the test")


def test_appends_rather_than_overwrites(isolated, clean_root_logger):
    log = isolated.home / ".osaeru" / "osaeru.log"
    log.parent.mkdir()
    log.write_text("earlier line\n")
    logging_config.configure()
    logging.getLogger("osaeru.test").warning("later line")
    for h in clean_root_logger.handlers:
        h.flush()
    text = log.read_text()
    assert text.startswith("earlier line\n") and "later line" in text


def test_calling_twice_does_not_duplicate_handlers(clean_root_logger):
    logging_config.configure()
    first = clean_root_logger.handlers[:]
    logging_config.configure.real()  # second call, root not cleared this time
    assert clean_root_logger.handlers == first

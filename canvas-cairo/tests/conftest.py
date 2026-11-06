from core_support import core, guard_finish, guard_start  # noqa: F401  (core: module-scoped fixture shared by test_terminal.py and test_sticky_note.py)


def pytest_configure(config):
    guard_start()   # OPEN_MAESTRI_DATA_DIR -> throwaway dir before any test module is imported


def pytest_sessionfinish(session, exitstatus):
    guard_finish(session)   # fails the run if the real ~/.open-maestri changed

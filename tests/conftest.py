"""Test setup: the app runs from a temporary COPY of the code.

Every module keeps its data next to itself (BASE_DIR): tracker.db, config.json, auth.json, photos/, .env.
Copying only the code (*.py, locales.json, templates/, static/) into a temp folder means the tests can never
read or change the real database, photos, settings or secrets (.env is not copied, so the bot token never loads).

Each test starts with an empty database and a fresh config.json (see the `client` fixture).
"""
import json
import os
import shutil
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE_DIRS = ("templates", "static")
SKIP_IN_STATIC = ("backgrounds",)      # large images are not needed by the API

# Settings every test starts from: enough tokens to create projects, no DEV_MODE.
BASE_CONFIG = {"INITIAL_TOKENS": 100, "PROJECT_COST": 10, "DEV_MODE": False, "LANGUAGE": "ru-RU"}


def _copy_code(dst: str) -> None:
    for name in os.listdir(REPO):
        src = os.path.join(REPO, name)
        if os.path.isfile(src) and (name.endswith(".py") or name == "locales.json"):
            shutil.copy2(src, dst)
    for name in CODE_DIRS:
        shutil.copytree(os.path.join(REPO, name), os.path.join(dst, name),
                        ignore=shutil.ignore_patterns(*SKIP_IN_STATIC, "__pycache__"))
    shutil.copytree(os.path.join(REPO, "tools"), os.path.join(dst, "tools"),
                    ignore=shutil.ignore_patterns("__pycache__"))


# Runs once, before any test module is imported: the copied code goes first on sys.path.
APP_DIR = tempfile.mkdtemp(prefix="project-hub-test-")
_copy_code(APP_DIR)
sys.path.insert(0, APP_DIR)
os.environ.pop("TELEGRAM_BOT_TOKEN", None)      # a token exported in the shell must not reach the tests either

import app as app_module          # noqa: E402  (imported from APP_DIR)
import common                     # noqa: E402
import database                   # noqa: E402

assert os.path.dirname(app_module.__file__) == APP_DIR, "tests must import the copy, not the real app"
assert os.path.dirname(database.DB_PATH) == APP_DIR


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(APP_DIR, ignore_errors=True)


def write_config(**overrides) -> None:
    """Replaces config.json of the test copy (BASE_CONFIG + overrides) and drops the config cache."""
    with open(common.CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({**BASE_CONFIG, **overrides}, f, ensure_ascii=False)
    common._config_cache["stamp"] = None


def clear_database() -> None:
    with database.engine.begin() as conn:
        for table in reversed(database.Base.metadata.sorted_tables):
            conn.execute(table.delete())


@pytest.fixture
def client():
    """HTTP client for the app with an empty database and default test settings.
    The app's lifespan (bot, scheduler) is not started: TestClient is used without `with`."""
    from fastapi.testclient import TestClient
    clear_database()
    write_config()
    return TestClient(app_module.app)


@pytest.fixture
def config():
    """Change settings inside a test: config(DEV_MODE=True)."""
    return write_config

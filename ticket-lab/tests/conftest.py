from pathlib import Path
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def migrated_url(tmp_path):
    # A separate disposable SQLite file for each test; never DATABASE_URL.
    url = "sqlite:///" + str(tmp_path / "test.sqlite3").replace("\\", "/")
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    return url


@pytest.fixture
def client(migrated_url):
    with TestClient(create_app(migrated_url)) as test_client:
        yield test_client

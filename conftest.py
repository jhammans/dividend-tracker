"""
Pytest configuration and shared fixtures.

Fixtures
--------
db_session  — an in-memory SQLite session scoped to each test function.
              Sufficient for any unit test that needs ORM objects.
              Integration tests that require PostgreSQL should be marked
              with @pytest.mark.integration and are excluded from CI.
tmp_csv     — yields a factory callable that writes rows to a temp CSV
              and returns the file path. The file is removed after the test.
"""
import csv
import os
import pytest
from tempfile import NamedTemporaryFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture
def tmp_csv(tmp_path):
    """Factory fixture: call it with a list of row lists to get a CSV path."""
    created = []

    def _make(rows, suffix=".csv"):
        path = tmp_path / f"test{len(created)}{suffix}"
        with open(path, "w", newline="") as f:
            csv.writer(f).writerows(rows)
        created.append(path)
        return str(path)

    return _make

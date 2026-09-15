import psycopg
import pytest

from app.config import get_settings


def test_seeded_database_is_reachable() -> None:
    try:
        with psycopg.connect(get_settings().database_url) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM studies")
                row = cursor.fetchone()
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL is not running")
    assert row is not None
    assert row[0] >= 1

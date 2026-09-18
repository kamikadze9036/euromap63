"""Pytest fixtures/setup shared by all collector tests.

collector.py performs a few import-time side effects that a pure unit-test
run shouldn't have to satisfy for real:

  - it reads DATABASE_URL from the environment (os.environ["DATABASE_URL"]),
    which would otherwise raise KeyError before a single test runs;
  - it does an unconditional `import psycopg2` - a real Postgres driver
    isn't needed to test pure parsing/reconstruction logic, and in some
    environments (e.g. no pg_config, very new Python not yet supported by
    psycopg2-binary wheels) it may not even be installable.

Neither of these touches actual runtime behaviour: production still installs
psycopg2-binary per requirements.txt and gets a real DATABASE_URL from
docker-compose.yml. This is test-only scaffolding.
"""
import os
import sys
import types

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")

# Make `import collector` resolve to collector/collector.py regardless of
# the directory pytest was invoked from.
_COLLECTOR_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _COLLECTOR_DIR not in sys.path:
    sys.path.insert(0, _COLLECTOR_DIR)

if "psycopg2" not in sys.modules:
    try:
        import psycopg2 as _real_psycopg2  # noqa: F401 - real driver available (e.g. test_integration_real_db.py), nothing to stub.
    except ImportError:
        fake_psycopg2 = types.ModuleType("psycopg2")

        class _Error(Exception):
            pass

        class _OperationalError(_Error):
            pass

        def _connect(*_args, **_kwargs):
            raise NotImplementedError(
                "psycopg2 is stubbed out for unit tests - no real DB connection "
                "is available or needed here."
            )

        fake_psycopg2.Error = _Error
        fake_psycopg2.OperationalError = _OperationalError
        fake_psycopg2.connect = _connect
        # test_integration_real_db.py's `pytest.importorskip("psycopg2")`
        # would otherwise succeed against this stub (it IS importable) and
        # os.environ.setdefault() above means DATABASE_URL is never falsy
        # either - without this marker, that whole module errors instead of
        # cleanly skipping whenever no real driver is installed.
        fake_psycopg2._is_stub = True
        sys.modules["psycopg2"] = fake_psycopg2

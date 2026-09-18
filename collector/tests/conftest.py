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
    fake_psycopg2 = types.ModuleType("psycopg2")

    class _Error(Exception):
        pass

    def _connect(*_args, **_kwargs):
        raise NotImplementedError(
            "psycopg2 is stubbed out for unit tests - no real DB connection "
            "is available or needed here."
        )

    fake_psycopg2.Error = _Error
    fake_psycopg2.connect = _connect
    sys.modules["psycopg2"] = fake_psycopg2

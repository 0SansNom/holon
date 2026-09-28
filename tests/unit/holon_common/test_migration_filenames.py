"""A misnamed migration file fails before the runner touches Postgres."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "libs"))

from holon_common.migrations import MigrationFilenameError, run_migrations  # noqa: E402


class _Pool:
    def acquire(self):
        raise AssertionError("invalid filenames must fail before a connection")


def test_invalid_sql_filename_raises_and_names_the_file(tmp_path: Path) -> None:
    (tmp_path / "Not_Valid.sql").write_text("SELECT 1;")
    (tmp_path / "0001_ok.sql").write_text("SELECT 1;")

    async def run() -> None:
        with pytest.raises(MigrationFilenameError, match="Not_Valid.sql"):
            await run_migrations(_Pool(), tmp_path)  # type: ignore[arg-type]

    asyncio.run(run())

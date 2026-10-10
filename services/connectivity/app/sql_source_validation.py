"""SQL source query validation (SELECT-only gate)."""
from __future__ import annotations

import re

from app import sql_drivers
from app.source_registry_base import SourceConfigError

_FORBIDDEN_STMT = re.compile(
    r"\b(insert|update|delete|truncate|alter|drop|create|grant|revoke|call|execute)\b",
    re.IGNORECASE,
)
_COPY_STMT = re.compile(r"(^\s*copy\b|\bcopy\s+\S+\s+(from|to)\b)", re.IGNORECASE)
_FORBIDDEN_FUNCS = re.compile(
    r"\b(pg_read_\w+|pg_ls_\w+|pg_file_\w+|pg_write_\w+|lo_import|lo_export|lo_get|lo_put|"
    r"lo_from_bytea|lo_create|lo_unlink|dblink\w*|pg_sleep|crdb_internal\.\w+)\s*\(",
    re.IGNORECASE,
)
_SELECT_INTO = re.compile(
    r"\binto\s+(temp(orary)?\s+)?(table\s+)?[\"']?[A-Za-z_]",
    re.IGNORECASE,
)
_FOR_LOCK = re.compile(r"\bfor\s+(update|share|no\s+key\s+update|key\s+share)\b", re.IGNORECASE)
# MySQL / MariaDB exfiltration and side-effect helpers.
_MYSQL_FORBIDDEN = re.compile(
    r"\b(load_file\s*\(|into\s+outfile\b|into\s+dumpfile\b|benchmark\s*\(|sleep\s*\()",
    re.IGNORECASE,
)
# SQL Server linked-server / extended-proc helpers. OPENROWSET is separate:
# dedicated pools forbid it; serverless SQL pools use it to read storage files.
_MSSQL_FORBIDDEN = re.compile(
    r"\b(opendatasource\s*\(|openquery\s*\(|xp_\w+|sp_oacreate\b)",
    re.IGNORECASE,
)
_MSSQL_OPENROWSET = re.compile(r"\bopenrowset\s*\(", re.IGNORECASE)
# Snowflake stage / system helpers with side effects or file access.
# Role grants remain the real control plane; this mirrors MySQL/MSSQL denylists.
_SNOWFLAKE_FORBIDDEN = re.compile(
    r"("
    r"\bfrom\s+@"  # SELECT … FROM @stage[/path]
    r"|directory\s*\(\s*@"  # DIRECTORY(@stage) / TABLE(DIRECTORY(@stage))
    r"|\bsystem\$\w+"  # SYSTEM$CANCEL_ALL_QUERIES, etc.
    r"|\bget_presigned_url\s*\("
    r"|\bbuild_scoped_file_url\s*\("
    r")",
    re.IGNORECASE,
)


def _require_select_only(query: str, dialect: str = "postgres") -> None:
    stripped = query.strip().rstrip(";").strip()
    if ";" in stripped:
        raise SourceConfigError("query must be a single SELECT statement — no semicolons")
    head = stripped.split(None, 1)[0].upper() if stripped else ""
    if head not in {"SELECT", "WITH"}:
        raise SourceConfigError("query must start with SELECT or WITH — this connector is read-only")
    if (
        _FORBIDDEN_STMT.search(stripped)
        or _COPY_STMT.search(stripped)
        or _FORBIDDEN_FUNCS.search(stripped)
        or _FOR_LOCK.search(stripped)
        or _SELECT_INTO.search(stripped)
    ):
        raise SourceConfigError("query must be a read-only SELECT — writes, locks, and file helpers are not allowed")
    try:
        stored = sql_drivers.normalize_dialect(dialect)
    except ValueError as exc:
        raise SourceConfigError(str(exc)) from exc
    d = sql_drivers.wire_dialect(stored)
    if d == "mysql" and _MYSQL_FORBIDDEN.search(stripped):
        raise SourceConfigError("query must be a read-only SELECT — MySQL file helpers are not allowed")
    if d == "mssql" and (
        _MSSQL_FORBIDDEN.search(stripped)
        or (stored != "azure_synapse_serverless" and _MSSQL_OPENROWSET.search(stripped))
    ):
        raise SourceConfigError("query must be a read-only SELECT — SQL Server file helpers are not allowed")
    if d == "snowflake" and _SNOWFLAKE_FORBIDDEN.search(stripped):
        raise SourceConfigError(
            "query must be a read-only SELECT — Snowflake stage and SYSTEM$ helpers are not allowed"
        )

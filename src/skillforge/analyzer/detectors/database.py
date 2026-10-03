"""Cross-cutting database and migration detector.

Language-specific detectors report ORMs and migration tools they can see from
their own manifests; this detector catches standalone SQL migrations and
database configuration that no single ecosystem owns.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Final

from skillforge.analyzer.detectors.base import Detection, DetectionContext, fact
from skillforge.analyzer.languages import FileCategory
from skillforge.models import (
    Certainty,
    Database,
    DetectedTechnology,
    Risk,
    Severity,
    TechnologyKind,
    Unknown,
)
from skillforge.models.workflow import DatabaseEngine, RiskCategory

_SQL_FILE_RE = re.compile(r"^(?P<number>\d+)[_-].*\.sql$", re.IGNORECASE)
_SCHEME_RE = re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*=\s*[\"']?(?P<scheme>[a-z][a-z0-9+.\-]*)://")

_SCHEME_TO_ENGINE: Final[dict[str, DatabaseEngine]] = {
    "postgres": DatabaseEngine.POSTGRESQL,
    "postgresql": DatabaseEngine.POSTGRESQL,
    "mysql": DatabaseEngine.MYSQL,
    "mariadb": DatabaseEngine.MARIADB,
    "mongodb": DatabaseEngine.MONGODB,
    "mongodb+srv": DatabaseEngine.MONGODB,
    "redis": DatabaseEngine.REDIS,
    "mssql": DatabaseEngine.MSSQL,
    "sqlite": DatabaseEngine.SQLITE,
    "clickhouse": DatabaseEngine.CLICKHOUSE,
    "neo4j": DatabaseEngine.NEO4J,
}

_DB_CONFIG_FILES: Final[dict[str, DatabaseEngine]] = {
    "flyway.conf": DatabaseEngine.UNKNOWN,
    "liquibase.properties": DatabaseEngine.UNKNOWN,
    "knexfile.js": DatabaseEngine.UNKNOWN,
    "knexfile.ts": DatabaseEngine.UNKNOWN,
    "database.yml": DatabaseEngine.UNKNOWN,
    "ormconfig.json": DatabaseEngine.UNKNOWN,
}


class DatabaseDetector:
    """Detects SQL migration layouts and connection-string schemes in templates."""

    id = "database"

    def applies(self, context: DetectionContext) -> bool:
        return True

    def detect(self, context: DetectionContext) -> Detection:
        detection = Detection()
        sql_files = [
            record for record in context.scan.files if record.name.lower().endswith(".sql")
        ]
        numbered = [record for record in sql_files if _SQL_FILE_RE.match(record.name)]
        migration_dirs = sorted(
            {
                str(PurePosixPath(record.path).parent)
                for record in sql_files
                if any(
                    part in {"migrations", "migration", "db", "sql"}
                    for part in record.path.lower().split("/")
                )
            }
        )
        if numbered or migration_dirs:
            evidence = [
                fact(record.path, "numbered migration", weight=0.7) for record in numbered[:3]
            ] or [fact(migration_dirs[0], weight=0.6)]
            detection.databases.append(
                Database(
                    engine=DatabaseEngine.UNKNOWN,
                    migration_tool="SQL migration files",
                    migration_dirs=migration_dirs[:10],
                    evidence=evidence,
                    certainty=Certainty.FACT if numbered else Certainty.INFERENCE,
                    confidence=0.7,
                )
            )
            detection.technologies.append(
                DetectedTechnology(
                    kind=TechnologyKind.DATABASE_TOOL,
                    name="SQL migrations",
                    certainty=Certainty.FACT,
                    confidence=0.75,
                    evidence=evidence,
                    notes=[f"{len(numbered)} numbered SQL migration(s)"] if numbered else [],
                )
            )
        for name, _engine in _DB_CONFIG_FILES.items():
            if not context.exists(name):
                continue
            detection.databases.append(
                Database(
                    engine=DatabaseEngine.UNKNOWN,
                    migration_tool=name.split(".")[0],
                    config_files=[name],
                    evidence=[fact(name, weight=0.7)],
                    certainty=Certainty.FACT,
                    confidence=0.7,
                )
            )
        # Connection schemes in env templates reveal the intended engine without
        # ever storing the (possibly secret) value.
        for record in context.scan.records_in(_env_category()):
            text = context.read(record.path)
            if not text:
                continue
            schemes = {match.group("scheme").lower() for match in _SCHEME_RE.finditer(text)}
            for scheme in sorted(schemes):
                engine = _SCHEME_TO_ENGINE.get(scheme)
                if engine is None:
                    continue
                detection.databases.append(
                    Database(
                        engine=engine,
                        config_files=[record.path],
                        evidence=[fact(record.path, f"{scheme}:// URL scheme", weight=0.7)],
                        certainty=Certainty.INFERENCE,
                        confidence=0.7,
                    )
                )
            if "DATABASE_URL" in text or "POSTGRES" in text.upper():
                detection.unknowns.append(_connection_unknown(record.path))
        if sql_files and not migration_dirs and not numbered:
            detection.risks.append(
                Risk(
                    id="unstructured-sql",
                    title="SQL files without a migration structure",
                    description=(
                        "SQL files exist outside a migration directory. Applying them manually can "
                        "leave the database in an inconsistent state."
                    ),
                    severity=Severity.LOW,
                    category=RiskCategory.DATA,
                    evidence=[fact(sql_files[0].path, weight=0.6)],
                    mitigation="Prefer the project's migration tool, or review each script before applying it.",
                )
            )
        return detection


def _env_category() -> FileCategory:
    from skillforge.analyzer.languages import FileCategory

    return FileCategory.ENV_TEMPLATE


def _connection_unknown(path: str) -> Unknown:
    from skillforge.models import Unknown

    return Unknown(
        id="dev-database",
        question="Which database does the project expect in development, and how is it reached?",
        why=f"{path} declares database configuration but a development instance was not detected.",
        suggested_check="Check docker compose services or ask a maintainer for the local connection settings.",
        evidence=[fact(path, weight=0.6)],
    )

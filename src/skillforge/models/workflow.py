"""Workflow, command, and infrastructure models."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from skillforge.models.common import Certainty, Evidence, RiskLevel, Severity


class CommandSource(StrEnum):
    """Where a discovered command came from."""

    PACKAGE_SCRIPT = "package_script"
    MAKEFILE = "makefile"
    TASKFILE = "taskfile"
    COMPOSE = "compose"
    DOCKERFILE = "dockerfile"
    CI = "ci"
    README = "readme"
    DOCS = "docs"
    PROCFILE = "procfile"
    JUSTFILE = "justfile"
    CONFIG = "config"
    OTHER = "other"

    @property
    def label(self) -> str:
        return {
            CommandSource.PACKAGE_SCRIPT: "package script",
            CommandSource.MAKEFILE: "Makefile",
            CommandSource.TASKFILE: "Taskfile",
            CommandSource.COMPOSE: "Docker Compose",
            CommandSource.DOCKERFILE: "Dockerfile",
            CommandSource.CI: "CI pipeline",
            CommandSource.README: "README",
            CommandSource.DOCS: "documentation",
            CommandSource.PROCFILE: "Procfile",
            CommandSource.JUSTFILE: "Justfile",
            CommandSource.CONFIG: "configuration",
            CommandSource.OTHER: "repository",
        }[self]


class CommandPurpose(StrEnum):
    """What a command is for."""

    SETUP = "setup"
    RUN = "run"
    TEST = "test"
    BUILD = "build"
    LINT = "lint"
    FORMAT = "format"
    TYPECHECK = "typecheck"
    MIGRATE = "migrate"
    SEED = "seed"
    DEPLOY = "deploy"
    VERIFY = "verify"
    CLEAN = "clean"
    DOCS = "docs"
    RELEASE = "release"
    OTHER = "other"

    @property
    def label(self) -> str:
        return {
            CommandPurpose.SETUP: "Setup",
            CommandPurpose.RUN: "Run",
            CommandPurpose.TEST: "Test",
            CommandPurpose.BUILD: "Build",
            CommandPurpose.LINT: "Lint",
            CommandPurpose.FORMAT: "Format",
            CommandPurpose.TYPECHECK: "Type check",
            CommandPurpose.MIGRATE: "Database migration",
            CommandPurpose.SEED: "Seed data",
            CommandPurpose.DEPLOY: "Deploy",
            CommandPurpose.VERIFY: "Verification",
            CommandPurpose.CLEAN: "Cleanup",
            CommandPurpose.DOCS: "Documentation",
            CommandPurpose.RELEASE: "Release",
            CommandPurpose.OTHER: "Other",
        }[self]


class Command(BaseModel):
    """A command discovered in the repository, with provenance."""

    model_config = ConfigDict(extra="forbid")

    command: str
    source: CommandSource = CommandSource.OTHER
    purpose: CommandPurpose = CommandPurpose.OTHER
    cwd: str = "."
    component: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    certainty: Certainty = Certainty.FACT
    risk: RiskLevel = RiskLevel.REVIEW
    risk_reasons: list[str] = Field(default_factory=list)
    placeholders: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    script_body: str | None = None

    @property
    def provenance(self) -> str:
        return self.evidence[0].label if self.evidence else "unknown"

    def with_evidence(self, evidence: Evidence) -> Command:
        return self.model_copy(update={"evidence": [*self.evidence, evidence]})


class WorkflowCategory(StrEnum):
    SETUP = "setup"
    RUN = "run"
    TEST = "test"
    BUILD = "build"
    LINT = "lint"
    MIGRATE = "migrate"
    DEPLOY = "deploy"
    VERIFY = "verify"
    OTHER = "other"

    @property
    def label(self) -> str:
        return {
            WorkflowCategory.SETUP: "Setup",
            WorkflowCategory.RUN: "Development server",
            WorkflowCategory.TEST: "Testing",
            WorkflowCategory.BUILD: "Build",
            WorkflowCategory.LINT: "Lint and type check",
            WorkflowCategory.MIGRATE: "Database migration",
            WorkflowCategory.DEPLOY: "Deployment",
            WorkflowCategory.VERIFY: "Verification",
            WorkflowCategory.OTHER: "Other",
        }[self]


class Workflow(BaseModel):
    """A named group of related commands with shared evidence."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    category: WorkflowCategory = WorkflowCategory.OTHER
    description: str = ""
    component: str | None = None
    commands: list[Command] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    certainty: Certainty = Certainty.FACT
    notes: list[str] = Field(default_factory=list)

    def primary_command(self) -> Command | None:
        return self.commands[0] if self.commands else None


class ServiceKind(StrEnum):
    WEB = "web"
    WORKER = "worker"
    DATABASE = "database"
    CACHE = "cache"
    QUEUE = "queue"
    STORAGE = "storage"
    PROXY = "proxy"
    OTHER = "other"


class ServiceOrigin(StrEnum):
    """Where a service definition came from (CI-only services are not local deps)."""

    COMPOSE = "compose"
    CI = "ci"
    CONFIG = "config"
    OTHER = "other"


class Service(BaseModel):
    """A service discovered from Docker Compose, CI services, or config."""

    model_config = ConfigDict(extra="forbid")

    name: str
    kind: ServiceKind = ServiceKind.OTHER
    origin: ServiceOrigin = ServiceOrigin.OTHER
    image: str | None = None
    ports: list[str] = Field(default_factory=list)
    environment_keys: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    certainty: Certainty = Certainty.FACT
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)

    @property
    def is_local_dependency(self) -> bool:
        return self.origin is not ServiceOrigin.CI


class DatabaseEngine(StrEnum):
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    MARIADB = "mariadb"
    SQLITE = "sqlite"
    MONGODB = "mongodb"
    REDIS = "redis"
    MSSQL = "mssql"
    ORACLE = "oracle"
    CLICKHOUSE = "clickhouse"
    ELASTICSEARCH = "elasticsearch"
    DYNAMODB = "dynamodb"
    NEO4J = "neo4j"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return {
            DatabaseEngine.POSTGRESQL: "PostgreSQL",
            DatabaseEngine.MYSQL: "MySQL",
            DatabaseEngine.MARIADB: "MariaDB",
            DatabaseEngine.SQLITE: "SQLite",
            DatabaseEngine.MONGODB: "MongoDB",
            DatabaseEngine.REDIS: "Redis",
            DatabaseEngine.MSSQL: "SQL Server",
            DatabaseEngine.ORACLE: "Oracle",
            DatabaseEngine.CLICKHOUSE: "ClickHouse",
            DatabaseEngine.ELASTICSEARCH: "Elasticsearch",
            DatabaseEngine.DYNAMODB: "DynamoDB",
            DatabaseEngine.NEO4J: "Neo4j",
            DatabaseEngine.UNKNOWN: "Unknown database",
        }[self]


class Database(BaseModel):
    """A database dependency and how the project manages its schema."""

    model_config = ConfigDict(extra="forbid")

    engine: DatabaseEngine = DatabaseEngine.UNKNOWN
    orm: str | None = None
    driver: str | None = None
    migration_tool: str | None = None
    migration_dirs: list[str] = Field(default_factory=list)
    config_files: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    certainty: Certainty = Certainty.FACT
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)

    @property
    def display(self) -> str:
        if self.orm:
            return f"{self.engine.label} ({self.orm})"
        return self.engine.label


class APIKind(StrEnum):
    REST = "rest"
    GRAPHQL = "graphql"
    GRPC = "grpc"
    WEBSOCKET = "websocket"
    CLI = "cli"
    UNKNOWN = "unknown"


class API(BaseModel):
    """An application interface surface (HTTP API, GraphQL, gRPC)."""

    model_config = ConfigDict(extra="forbid")

    kind: APIKind = APIKind.UNKNOWN
    framework: str | None = None
    router_dirs: list[str] = Field(default_factory=list)
    spec_paths: list[str] = Field(default_factory=list)
    entrypoints: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    certainty: Certainty = Certainty.FACT
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)


class RiskCategory(StrEnum):
    SECURITY = "security"
    DATA = "data"
    RELIABILITY = "reliability"
    MAINTENANCE = "maintenance"
    DOCUMENTATION = "documentation"
    UNKNOWN = "unknown"


class Risk(BaseModel):
    """A repository-level risk or caveat worth surfacing to an agent."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    description: str = ""
    severity: Severity = Severity.MEDIUM
    category: RiskCategory = RiskCategory.UNKNOWN
    evidence: list[Evidence] = Field(default_factory=list)
    mitigation: str = ""

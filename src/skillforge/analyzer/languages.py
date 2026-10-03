"""File classification: language and category by path.

Pure functions, no I/O. Kept intentionally simple and explicit: a wrong
classification should be visible in a test, not hidden behind heuristics.
"""

from __future__ import annotations

import posixpath
from enum import StrEnum
from typing import Final

LANGUAGE_BY_EXTENSION: Final[dict[str, str]] = {
    ".py": "python",
    ".pyi": "python",
    ".pyx": "cython",
    ".ipynb": "jupyter",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".vue": "vue",
    ".svelte": "svelte",
    ".go": "go",
    ".cs": "csharp",
    ".vb": "vbnet",
    ".fs": "fsharp",
    ".dart": "dart",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".m": "objc",
    ".mm": "objc",
    ".scala": "scala",
    ".pl": "perl",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".clj": "clojure",
    ".hs": "haskell",
    ".lua": "lua",
    ".r": "r",
    ".jl": "julia",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".fish": "shell",
    ".ps1": "powershell",
    ".psm1": "powershell",
    ".bat": "batch",
    ".cmd": "batch",
    ".sql": "sql",
    ".graphql": "graphql",
    ".gql": "graphql",
    ".proto": "protobuf",
    ".tf": "terraform",
    ".tfvars": "terraform",
    ".hcl": "hcl",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".jsonc": "json",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "ini",
    ".env": "dotenv",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".less": "less",
    ".md": "markdown",
    ".mdx": "markdown",
    ".rst": "rst",
    ".txt": "text",
    ".tex": "latex",
    ".csv": "csv",
    ".xml": "xml",
    ".plist": "xml",
    ".svg": "svg",
    ".makefile": "make",
    ".mk": "make",
    ".dockerfile": "dockerfile",
}

LANGUAGE_BY_FILENAME: Final[dict[str, str]] = {
    "dockerfile": "dockerfile",
    "containerfile": "dockerfile",
    "makefile": "make",
    "gnumakefile": "make",
    "rakefile": "ruby",
    "gemfile": "ruby",
    "procfile": "procfile",
    "justfile": "just",
    "jenkinsfile": "groovy",
    "vagrantfile": "ruby",
    "cmakelists.txt": "cmake",
}


class FileCategory(StrEnum):
    """Coarse category used for budgets, context selection, and reporting."""

    SOURCE = "source"
    TEST = "test"
    MANIFEST = "manifest"
    LOCKFILE = "lockfile"
    CONFIG = "config"
    CI = "ci"
    INFRASTRUCTURE = "infrastructure"
    DATABASE = "database"
    DOCS = "docs"
    ENV_TEMPLATE = "env_template"
    LICENSE = "license"
    BUILD_SCRIPT = "build_script"
    DATA = "data"
    SECRET = "secret"
    UNKNOWN = "unknown"


_MANIFEST_NAMES: Final[frozenset[str]] = frozenset(
    {
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "requirements.txt",
        "requirements-dev.txt",
        "requirements-test.txt",
        "pipfile",
        "package.json",
        "go.mod",
        "cargo.toml",
        "composer.json",
        "pubspec.yaml",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
        "project.clj",
        "mix.exs",
        "gemfile",
        "cmakelists.txt",
    }
)

_MANIFEST_SUFFIXES: Final[tuple[str, ...]] = (
    ".csproj",
    ".vbproj",
    ".fsproj",
    ".sln",
    ".slnf",
    ".nuspec",
)

_LOCKFILE_NAMES: Final[frozenset[str]] = frozenset(
    {
        "uv.lock",
        "poetry.lock",
        "pdm.lock",
        "pipfile.lock",
        "package-lock.json",
        "npm-shrinkwrap.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "bun.lockb",
        "go.sum",
        "cargo.lock",
        "composer.lock",
        "pubspec.lock",
        "gemfile.lock",
        "packages.lock.json",
        "gradle.lockfile",
    }
)

_CI_PATH_PARTS: Final[tuple[str, ...]] = (
    ".github/workflows/",
    ".gitlab/",
    ".circleci/",
    ".buildkite/",
    ".teamcity/",
    ".azuredevops/",
    ".woodpecker/",
)

_CI_FILENAMES: Final[frozenset[str]] = frozenset(
    {
        ".gitlab-ci.yml",
        ".gitlab-ci.yaml",
        "jenkinsfile",
        "azure-pipelines.yml",
        "azure-pipelines.yaml",
        "bitbucket-pipelines.yml",
        "appveyor.yml",
        ".travis.yml",
        "drone.yml",
        "woodpecker.yml",
        "buildkite.yml",
        ".drone.yml",
        "cloudbuild.yaml",
        "buildspec.yml",
    }
)

_INFRA_NAMES: Final[frozenset[str]] = frozenset(
    {
        "dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "compose.yaml",
        "compose.override.yml",
        "compose.override.yaml",
        "docker-compose.override.yml",
        "makefile",
        "gnumakefile",
        "taskfile.yml",
        "taskfile.yaml",
        "justfile",
        "procfile",
        "nginx.conf",
        "caddyfile",
        "skaffold.yaml",
        "tiltfile",
        "chart.yaml",
        "kustomization.yaml",
        "kustomization.yml",
        "serverless.yml",
        "fly.toml",
        "render.yaml",
        "vercel.json",
        "netlify.toml",
        "railway.json",
        "renovate.json",
        ".dockerignore",
        ".editorconfig",
        "ansible.cfg",
        "inventory.ini",
    }
)

_INFRA_PATH_PARTS: Final[tuple[str, ...]] = (
    "k8s/",
    "kubernetes/",
    "helm/",
    "charts/",
    "terraform/",
    "infra/",
    "infrastructure/",
    "deploy/",
    "deployment/",
    "ansible/",
    "migrations/",
    "alembic/",
    "prisma/",
    "db/migrate/",
    "database/migrations/",
    "flyway/",
    "liquibase/",
)

_DATABASE_NAMES: Final[frozenset[str]] = frozenset(
    {
        "alembic.ini",
        "prisma.schema",
        "schema.prisma",
        "flyway.conf",
        "liquibase.properties",
        "knexfile.js",
        "knexfile.ts",
        "ormconfig.json",
        "ormconfig.js",
        "sequelize.config.js",
        "database.yml",
        "atlas.hcl",
    }
)

_CONFIG_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        ".toml",
        ".ini",
        ".cfg",
        ".json",
        ".jsonc",
        ".yaml",
        ".yml",
        ".properties",
        ".conf",
        ".editorconfig",
    }
)

_DOC_NAMES: Final[tuple[str, ...]] = (
    "readme",
    "contributing",
    "changelog",
    "changes",
    "architecture",
    "security",
    "code_of_conduct",
    "license",
    "notice",
    "authors",
    "governance",
    "roadmap",
)

_TEST_PATH_PARTS: Final[tuple[str, ...]] = (
    "test/",
    "tests/",
    "testing/",
    "__tests__/",
    "spec/",
    "specs/",
    "e2e/",
)

_TEST_FILENAME_MARKERS: Final[tuple[str, ...]] = (
    "test_",
    "_test.",
    ".test.",
    ".spec.",
    "_spec.",
    "tests.",
    "test.",
)


def language_for(path: str) -> str:
    """Best-effort language name for a repository-relative path."""
    name = posixpath.basename(path).lower()
    if name in LANGUAGE_BY_FILENAME:
        return LANGUAGE_BY_FILENAME[name]
    if name.startswith("dockerfile.") or name.endswith(".dockerfile"):
        return "dockerfile"
    extension = posixpath.splitext(name)[1]
    return LANGUAGE_BY_EXTENSION.get(extension, "unknown")


def is_test_path(path: str) -> bool:
    """True when a path lives in a test directory or has a test-style name."""
    lowered = path.lower()
    name = posixpath.basename(lowered)
    directory = lowered[: -len(name)]
    if any(part in directory for part in _TEST_PATH_PARTS):
        return True
    return any(marker in name for marker in _TEST_FILENAME_MARKERS)


def is_doc_path(path: str) -> bool:
    lowered = path.lower()
    name = posixpath.basename(lowered)
    stem = posixpath.splitext(name)[0]
    if "docs/" in lowered or lowered.startswith("docs"):
        return True
    if any(stem == doc or (stem.startswith(doc) and lowered.endswith(".md")) for doc in _DOC_NAMES):
        return True
    return lowered.endswith((".md", ".mdx", ".rst"))


def is_ci_path(path: str) -> bool:
    lowered = path.lower()
    if any(lowered.startswith(part) or f"/{part}" in f"/{lowered}" for part in _CI_PATH_PARTS):
        return True
    return posixpath.basename(lowered) in _CI_FILENAMES


def is_manifest_path(path: str) -> bool:
    name = posixpath.basename(path).lower()
    if name in _MANIFEST_NAMES:
        return True
    if name.startswith("requirements") and name.endswith(".txt"):
        return True
    if name.startswith("dockerfile") or name.endswith(".dockerfile"):
        return False
    return name.endswith(_MANIFEST_SUFFIXES)


def is_lockfile_path(path: str) -> bool:
    return posixpath.basename(path).lower() in _LOCKFILE_NAMES


def is_env_template_path(path: str) -> bool:
    name = posixpath.basename(path).lower()
    if name == ".env.example" or name.startswith(".env."):
        return name.endswith((".example", ".sample", ".template", ".dist"))
    return name in {"env.example", "env.sample", "env.template", "environment.example"}


def is_infrastructure_path(path: str) -> bool:
    lowered = path.lower()
    name = posixpath.basename(lowered)
    if name in _INFRA_NAMES or name.startswith("dockerfile."):
        return True
    if any(part in f"{lowered}/" for part in _INFRA_PATH_PARTS):
        return True
    if lowered.endswith((".tf", ".tfvars", ".hcl")):
        return True
    return lowered.endswith((".service", ".timer", ".conf")) and (
        "deploy" in lowered or "systemd" in lowered
    )


def is_database_path(path: str) -> bool:
    lowered = path.lower()
    name = posixpath.basename(lowered)
    if name in _DATABASE_NAMES:
        return True
    if lowered.endswith(".sql"):
        return True
    return any(
        part in f"{lowered}/"
        for part in ("migrations/", "alembic/", "prisma/", "db/migrate/", "database/migrations/")
    )


def is_license_path(path: str) -> bool:
    stem = posixpath.splitext(posixpath.basename(path).lower())[0]
    return stem in {"license", "licence", "copying", "notice", "unlicense"}


def category_for(path: str) -> FileCategory:
    """Classify a path into a single :class:`FileCategory`."""
    if is_env_template_path(path):
        return FileCategory.ENV_TEMPLATE
    if is_license_path(path):
        return FileCategory.LICENSE
    if is_ci_path(path):
        return FileCategory.CI
    if is_lockfile_path(path):
        return FileCategory.LOCKFILE
    if is_manifest_path(path):
        return FileCategory.MANIFEST
    if is_database_path(path):
        return FileCategory.DATABASE
    if is_infrastructure_path(path):
        return FileCategory.INFRASTRUCTURE
    if is_doc_path(path):
        return FileCategory.DOCS
    if is_test_path(path):
        return FileCategory.TEST
    language = language_for(path)
    if language in {"unknown", "text"}:
        return FileCategory.DATA if path.lower().endswith(".csv") else FileCategory.UNKNOWN
    if path.lower().endswith(tuple(_CONFIG_EXTENSIONS)):
        return FileCategory.CONFIG
    if language in {"shell", "batch", "powershell"}:
        return FileCategory.BUILD_SCRIPT
    return FileCategory.SOURCE

"""Environment template detector.

Only variable *names* and URL schemes are extracted from ``.env.example``-style
files. Values are never retained, logged, or copied into generated skills.
"""

from __future__ import annotations

import re
from typing import Final

from skillforge.analyzer.detectors.base import Detection, DetectionContext, fact
from skillforge.analyzer.languages import FileCategory
from skillforge.models import Certainty, DetectedTechnology, Risk, Severity, TechnologyKind
from skillforge.models.workflow import RiskCategory
from skillforge.security.secrets import find_secrets

_KEY_RE = re.compile(r"^(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=")
_SENSITIVE_SUFFIXES: Final[tuple[str, ...]] = (
    "_KEY",
    "_TOKEN",
    "_SECRET",
    "_PASSWORD",
    "_CREDENTIALS",
)

SECRET_FILE_NAMES_FOR_RISK: Final[tuple[str, ...]] = (
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
)


class EnvDetector:
    """Extracts environment variable names and flags secret-handling risks."""

    id = "env"

    def applies(self, context: DetectionContext) -> bool:
        return True

    def detect(self, context: DetectionContext) -> Detection:
        detection = Detection()
        for record in context.scan.records_in(_env_category()):
            text = context.read(record.path)
            if not text:
                continue
            keys: list[str] = []
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                match = _KEY_RE.match(stripped)
                if match:
                    keys.append(match.group("key"))
            detection.env_keys.extend(keys)
            detection.env_files.append(record.path)
            findings = find_secrets(text)
            if findings:
                detection.risks.append(
                    Risk(
                        id=f"template-secret-{record.path.replace('/', '-')}",
                        title=f"Possible real credential in {record.path}",
                        description=(
                            "An environment template appears to contain a value that matches a secret "
                            "pattern. Templates are committed to version control and must contain "
                            "placeholders only."
                        ),
                        severity=Severity.HIGH,
                        category=RiskCategory.SECURITY,
                        evidence=[
                            fact(
                                record.path,
                                f"L{finding.line}",
                                f"{finding.description} ({finding.preview})",
                            )
                            for finding in findings[:5]
                        ],
                        mitigation="Replace the value with a placeholder and rotate the leaked credential.",
                    )
                )
            sensitive = sorted({key for key in keys if key.upper().endswith(_SENSITIVE_SUFFIXES)})
            if sensitive:
                detection.notes.append(
                    f"{record.path}: {len(sensitive)} sensitive variable(s) declared "
                    f"({', '.join(sensitive[:5])}{'…' if len(sensitive) > 5 else ''})"
                )
        if detection.env_files:
            detection.technologies.append(
                DetectedTechnology(
                    kind=TechnologyKind.CONFIG,
                    name="Environment templates",
                    certainty=Certainty.FACT,
                    confidence=0.85,
                    evidence=[fact(detection.env_files[0], weight=0.8)],
                    notes=[f"{len(detection.env_keys)} variable name(s) declared"],
                )
            )
        for candidate in SECRET_FILE_NAMES_FOR_RISK:
            if any(item.path == candidate for item in context.scan.skipped):
                detection.risks.append(
                    Risk(
                        id="committed-env-file",
                        title=f"{candidate} exists in the working tree",
                        description=(
                            "A real environment file was found. SkillForge never reads its contents, "
                            "but it must not be committed to version control."
                        ),
                        severity=Severity.HIGH,
                        category=RiskCategory.SECURITY,
                        evidence=[],
                        mitigation=f"Add {candidate} to .gitignore and rotate any secrets it contains.",
                    )
                )
        return detection


def _env_category() -> FileCategory:
    from skillforge.analyzer.languages import FileCategory

    return FileCategory.ENV_TEMPLATE

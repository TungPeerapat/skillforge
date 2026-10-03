"""Documentation detector: README/docs, documented commands, and injection signals."""

from __future__ import annotations

from typing import Final

from skillforge.analyzer.detectors.base import Detection, DetectionContext, fact
from skillforge.analyzer.languages import FileCategory
from skillforge.discovery.commands import commands_from_markdown
from skillforge.models import (
    Certainty,
    DetectedTechnology,
    DocFile,
    DocKind,
    Risk,
    Severity,
    TechnologyKind,
    Unknown,
)
from skillforge.models.workflow import CommandSource, RiskCategory
from skillforge.security.injection import InjectionSeverity, scan_for_injection
from skillforge.utils.markdown import headings

_MAX_DOC_COMMANDS = 40
_MAX_DOCS = 60

_KIND_BY_PREFIX: Final[tuple[tuple[str, DocKind], ...]] = (
    ("readme", DocKind.README),
    ("contributing", DocKind.CONTRIBUTING),
    ("architecture", DocKind.ARCHITECTURE),
    ("changelog", DocKind.CHANGELOG),
    ("changes", DocKind.CHANGELOG),
    ("api", DocKind.API),
)

_INJECTION_TO_RISK: Final[dict[InjectionSeverity, Severity]] = {
    InjectionSeverity.LOW: Severity.LOW,
    InjectionSeverity.MEDIUM: Severity.MEDIUM,
    InjectionSeverity.HIGH: Severity.HIGH,
}


class DocsDetector:
    """Extracts documentation structure and documented commands."""

    id = "docs"

    def applies(self, context: DetectionContext) -> bool:
        return bool(self._docs(context))

    def detect(self, context: DetectionContext) -> Detection:
        detection = Detection()
        docs = self._docs(context)
        if not docs:
            detection.risks.append(
                Risk(
                    id="missing-readme",
                    title="No README found",
                    description="Repository documentation could not be located, so documented workflows may exist only in CI or scripts.",
                    severity=Severity.LOW,
                    category=RiskCategory.DOCUMENTATION,
                    evidence=[],
                    mitigation="Add a README with setup and test commands.",
                )
            )
            return detection
        detection.technologies.append(
            DetectedTechnology(
                kind=TechnologyKind.TOOL,
                name="Project documentation",
                certainty=Certainty.FACT,
                confidence=0.8,
                evidence=[fact(docs[0].path, weight=0.7)],
                notes=[f"{len(docs)} documentation file(s)"],
            )
        )
        emitted = 0
        for record in docs[:_MAX_DOCS]:
            text = context.read(record.path)
            if not text:
                continue
            kind = _doc_kind(record.path)
            title = _title(text) or record.stem
            detection.docs.append(
                DocFile(path=record.path, kind=kind, title=title, bytes=record.size)
            )
            signals = scan_for_injection(text)
            high = [signal for signal in signals if signal.severity is InjectionSeverity.HIGH]
            if high:
                detection.risks.append(
                    Risk(
                        id=f"prompt-injection-{record.path.replace('/', '-')}",
                        title=f"Instruction-like text in {record.path}",
                        description=(
                            "Documentation contains text that looks like an attempt to give instructions "
                            "to an automated reader. SkillForge treats repository text as data, but the "
                            "content should be reviewed before it is shared with an agent."
                        ),
                        severity=_INJECTION_TO_RISK[InjectionSeverity.HIGH],
                        category=RiskCategory.SECURITY,
                        evidence=[
                            fact(record.path, f"L{signal.line}", signal.description)
                            for signal in high[:5]
                        ],
                        mitigation="Review the file; do not let repository text override agent instructions.",
                    )
                )
            if emitted >= _MAX_DOC_COMMANDS:
                continue
            remaining = _MAX_DOC_COMMANDS - emitted
            commands = commands_from_markdown(
                record.path,
                text,
                source=CommandSource.README if kind is DocKind.README else CommandSource.DOCS,
                max_commands=remaining,
            )
            detection.commands.extend(commands)
            emitted += len(commands)
            if kind is DocKind.README and not commands:
                detection.unknowns.append(_missing_setup_unknown(record.path, detection))
        return detection

    def _docs(self, context: DetectionContext) -> list:
        records = [
            record for record in context.scan.records_in(_doc_category()) if not record.is_generated
        ]
        records.sort(key=lambda record: (record.path.count("/"), record.path.lower()))
        return records


def _doc_category() -> FileCategory:
    from skillforge.analyzer.languages import FileCategory

    return FileCategory.DOCS


def _doc_kind(path: str) -> DocKind:
    name = path.rsplit("/", 1)[-1].lower()
    stem = name.rsplit(".", 1)[0]
    for prefix, kind in _KIND_BY_PREFIX:
        if stem.startswith(prefix):
            return kind
    if path.lower().startswith("docs/"):
        return DocKind.GUIDE
    return DocKind.OTHER


def _title(text: str) -> str | None:
    for level, title, _line in headings(text):
        if level == 1:
            return title
        break
    return None


def _missing_setup_unknown(path: str, detection: Detection) -> Unknown:
    from skillforge.models import Unknown

    return Unknown(
        id="readme-setup-steps",
        question="How do maintainers set up and run this project locally?",
        why=f"{path} does not contain recognizable, copy-pasteable tooling commands.",
        suggested_check="Check CI configuration, scripts/, and CONTRIBUTING.md.",
        evidence=[fact(path, weight=0.5)],
    )

"""Global knowledge store tests (K4): round trip, locks, corruption, staleness."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from skillforge.knowledge.staleness import (
    KnowledgeStalenessStatus,
    check_knowledge_ref,
)
from skillforge.knowledge.store import (
    KnowledgeEntry,
    KnowledgeStore,
    StoreCorruptedError,
    StoreLockedError,
    knowledge_home,
)
from skillforge.models import (
    KnowledgeManifest,
    KnowledgeRef,
    KnowledgeSource,
    KnowledgeTarget,
)
from skillforge.security.paths import PathViolation


def _target(
    ecosystem: str = "pypi", technology: str = "fastapi", version: str = "0.115.0"
) -> KnowledgeTarget:
    return KnowledgeTarget(ecosystem=ecosystem, technology=technology, version=version)


def _manifest(**overrides: object) -> KnowledgeManifest:
    payload: dict[str, object] = {
        "technology": "fastapi",
        "ecosystem": "pypi",
        "resolved_version": "0.115.0",
        "generated_at": datetime.now(UTC),
        "tool_version": "0.2.0",
    }
    payload.update(overrides)
    return KnowledgeManifest.model_validate(payload)


def _source() -> KnowledgeSource:
    return KnowledgeSource(
        id="src-1",
        technology="fastapi",
        version="0.115.0",
        url="https://fastapi.tiangolo.com/",
        accepted=True,
    )


def _entry(*, generated_at: datetime | None = None) -> KnowledgeEntry:
    return KnowledgeEntry(
        manifest=_manifest(generated_at=generated_at or datetime.now(UTC)),
        sources=[_source()],
        capabilities=[{"id": "cap-2", "name": "b"}, {"id": "cap-1", "name": "a"}],
        relationships=[{"id": "rel-1", "source": "cap-1"}],
        domains={"core": {"count": 2}},
        coverage={"public_api": 3},
        conflicts=[{"id": "conf-1"}],
    )


def _store(tmp_path: Path) -> KnowledgeStore:
    return KnowledgeStore(tmp_path / "knowledge")


# ------------------------------------------------------------------- home


def test_knowledge_home_override_wins(tmp_path: Path) -> None:
    assert knowledge_home({"SKILLFORGE_KNOWLEDGE_HOME": str(tmp_path)}) == tmp_path


def test_store_refuses_untrusted_key_traversal(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(PathViolation):
        store.entry_dir(_target(version="../../escape"))
    with pytest.raises(PathViolation):
        store.entry_dir(_target(ecosystem="../../escape"))


def test_store_refuses_manifest_file_escape(tmp_path: Path) -> None:
    store = _store(tmp_path)
    target = _target()
    store.write_entry(target, _entry())
    with pytest.raises(PathViolation):
        store.read_json_file(target, "../../../secret.json")


def test_knowledge_home_platform_default() -> None:
    if os.name == "nt":
        home = knowledge_home({"LOCALAPPDATA": "C:/data"})
        assert home == Path("C:/data") / "skillforge" / "knowledge"
    else:
        home = knowledge_home({"XDG_DATA_HOME": "/data"})
        assert home == Path("/data") / "skillforge" / "knowledge"


def test_mac_knowledge_home_uses_application_support() -> None:
    assert knowledge_home({}, platform="darwin") == (
        Path.home() / "Library" / "Application Support" / "skillforge" / "knowledge"
    )


# -------------------------------------------------------------- round trip


def test_write_and_read_round_trip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    target = _target()
    manifest = store.write_entry(target, _entry())

    assert manifest.capability_count == 2
    assert manifest.domain_count == 1
    assert set(manifest.source_hashes) == {
        "sources.json",
        "capabilities.jsonl",
        "relationships.jsonl",
        "domains.json",
        "coverage.json",
        "conflicts.json",
    }
    assert store.exists(target)
    assert store.validate_entry(target).ok

    loaded = store.read_entry(target)
    assert loaded is not None
    assert loaded.manifest.store_hash == manifest.store_hash
    assert [record["id"] for record in loaded.capabilities] == ["cap-1", "cap-2"]
    assert loaded.sources[0].url == "https://fastapi.tiangolo.com/"
    assert loaded.domains == {"core": {"count": 2}}
    assert loaded.coverage == {"public_api": 3}

    # The compiled reuse marker carries no machine paths.
    marker = next((store.entry_dir(target) / "compiled").glob("*.json"))
    text = marker.read_text(encoding="utf-8")
    assert str(tmp_path) not in text
    assert "C:\\" not in text and "/home/" not in text


def test_read_entry_returns_none_when_missing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.read_entry(_target(technology="missing")) is None
    assert store.read_manifest(_target(technology="missing")) is None


def test_version_keys_are_normalized(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.key_for(_target("npm", "next", "v1.2.3")) == "npm/next/1.2.3"
    assert store.key_for(_target("pypi", "demo", "01.2")) == "pypi/demo/1.2"
    assert store.key_for(_target("npm", "@prisma/client", "6.1.0")) == "npm/prisma-client/6.1.0"


# ------------------------------------------------------------- dedupe/reuse


def test_rewriting_same_content_reuses_the_entry(tmp_path: Path) -> None:
    store = _store(tmp_path)
    target = _target()
    first = store.write_entry(target, _entry())
    second = store.write_entry(target, _entry(generated_at=datetime.now(UTC) + timedelta(hours=1)))
    assert first.store_hash == second.store_hash
    registry = store.read_registry()
    matching = [entry for entry in registry.entries if entry.key == store.key_for(target)]
    assert len(matching) == 1
    assert matching[0].store_hash == first.store_hash
    assert store.is_reusable(target, store_hash=first.store_hash)
    assert not store.is_reusable(target, store_hash="kh-deadbeef")
    compiled = list((store.entry_dir(target) / "compiled").glob("*.json"))
    assert len(compiled) == 1


# -------------------------------------------------------------------- locks


def test_lock_is_exclusive_and_bounded(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with (
        store.lock("shared", timeout=0.2),
        pytest.raises(StoreLockedError),
        store.lock("shared", timeout=0.1),
    ):
        pass  # pragma: no cover - must not acquire
    # released cleanly
    with store.lock("shared", timeout=0.5):
        pass
    assert not (store.home / "locks" / "shared.lock").exists()


def test_stale_lock_is_reclaimed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    lock_dir = store.home / "locks" / "shared.lock"
    lock_dir.mkdir(parents=True)
    old = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    (lock_dir / "owner.json").write_text(
        json.dumps({"name": "shared", "pid": 999999, "created_at": old}), encoding="utf-8"
    )
    with store.lock("shared", timeout=0.5):
        pass


def test_lock_without_owner_and_old_mtime_is_reclaimed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    lock_dir = store.home / "locks" / "shared.lock"
    lock_dir.mkdir(parents=True)
    old_epoch = time.time() - 7200
    os.utime(lock_dir, (old_epoch, old_epoch))
    with store.lock("shared", timeout=0.5):
        pass


# --------------------------------------------------------------- corruption


def test_corruption_is_detected_and_quarantined(tmp_path: Path) -> None:
    store = _store(tmp_path)
    target = _target()
    store.write_entry(target, _entry())
    (store.entry_dir(target) / "capabilities.jsonl").write_text("not json\n", encoding="utf-8")

    validation = store.validate_entry(target)
    assert validation.ok is False
    assert any("capabilities.jsonl" in issue for issue in validation.issues)
    with pytest.raises(StoreCorruptedError):
        store.read_entry(target)

    quarantined = store.quarantine_entry(target)
    assert quarantined is not None
    assert ".corrupt-" in quarantined.name
    assert not store.entry_dir(target).exists()
    assert store.lookup(target) is None
    assert store.read_manifest(target) is None


def test_corrupt_registry_is_tolerated(tmp_path: Path) -> None:
    store = _store(tmp_path)
    target = _target()
    store.write_entry(target, _entry())
    registry_path = store.home / "registry.json"
    registry_path.write_text("{{{", encoding="utf-8")

    registry = store.read_registry()
    assert registry.entries == []
    assert any(path.name.startswith("registry.json.corrupt-") for path in store.home.iterdir())


def test_missing_file_is_reported(tmp_path: Path) -> None:
    store = _store(tmp_path)
    target = _target()
    store.write_entry(target, _entry())
    (store.entry_dir(target) / "sources.json").unlink()
    validation = store.validate_entry(target)
    assert validation.ok is False
    assert validation.issues == ["missing file: sources.json"]


# ----------------------------------------------------------------- staleness


def test_staleness_missing_hash_mismatch_and_fresh(tmp_path: Path) -> None:
    store = _store(tmp_path)
    target = _target()
    assert (
        check_knowledge_ref(KnowledgeRef(technology="fastapi", version="0.115.0"), store).status
        is KnowledgeStalenessStatus.MISSING
    )

    manifest = store.write_entry(target, _entry())
    fresh = check_knowledge_ref(
        KnowledgeRef(
            technology="fastapi",
            version="0.115.0",
            store_hash=manifest.store_hash,
        ),
        store,
    )
    assert fresh.status is KnowledgeStalenessStatus.FRESH
    assert fresh.entry is not None

    mismatched = check_knowledge_ref(
        KnowledgeRef(technology="fastapi", version="0.115.0", store_hash="kh-wrong"),
        store,
    )
    assert mismatched.status is KnowledgeStalenessStatus.HASH_MISMATCH

    no_hash = check_knowledge_ref(KnowledgeRef(technology="fastapi", version="0.115.0"), store)
    assert no_hash.status is KnowledgeStalenessStatus.FRESH
    assert no_hash.reasons


def test_staleness_reports_corruption(tmp_path: Path) -> None:
    store = _store(tmp_path)
    target = _target()
    store.write_entry(target, _entry())
    (store.entry_dir(target) / "domains.json").write_text("{broken", encoding="utf-8")
    report = check_knowledge_ref(KnowledgeRef(technology="fastapi", version="0.115.0"), store)
    assert report.status is KnowledgeStalenessStatus.CORRUPT
    assert report.reasons

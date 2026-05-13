"""Integration tests for ``WorkflowRepository``. Skipped without TEST_MONGODB_URI."""

from __future__ import annotations

import os
import re
import secrets

import pytest

from app.db.indexes import ensure_indexes
from app.db.mongo import MongoManager
from app.db.repos.workflow_repo import WorkflowRepository, generate_workflow_id
from app.errors import ConflictError, NotFoundError
from app.models.meta import WorkflowStatus
from app.models.workflow import WorkflowDoc
from app.services.validation_service import extract_summary

_URI = os.environ.get("TEST_MONGODB_URI")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_URI is None, reason="set TEST_MONGODB_URI to run"),
]


# --------------------------- fixtures + helpers ---------------------------


@pytest.fixture
async def db():
    name = f"abm_repo_wf_{secrets.token_hex(4)}"
    mgr = MongoManager(uri=_URI, db_name=name)
    try:
        await ensure_indexes(mgr.db)
        yield mgr.db
    finally:
        await mgr.client.drop_database(name)
        await mgr.close()


def _doc(workflow_id: str = "wf_seed") -> WorkflowDoc:
    return WorkflowDoc.model_validate(
        {
            "id": workflow_id,
            "name": "Seed",
            "nodes": [
                {
                    "id": "s",
                    "type": "start",
                    "position": {"x": 0, "y": 0},
                    "data": {"name": "S", "config": {}},
                },
                {
                    "id": "o",
                    "type": "output",
                    "position": {"x": 100, "y": 0},
                    "data": {"name": "O", "config": {}},
                },
            ],
            "edges": [
                {
                    "id": "e1",
                    "source": "s",
                    "target": "o",
                    "sourceHandle": None,
                    "targetHandle": None,
                }
            ],
        }
    )


def _summary():
    return extract_summary(_doc())


async def _seed(repo: WorkflowRepository, **overrides) -> str:
    """Create a workflow and return its id."""
    args = dict(name="Seed", doc=_doc(), summary=_summary())
    args.update(overrides)
    meta = await repo.create(**args)
    return meta.workflow_id


# --------------------------- create ---------------------------


_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


async def test_generate_workflow_id_format():
    wid = generate_workflow_id()
    assert _UUID4_RE.match(wid), f"not a UUID4: {wid!r}"


async def test_create_assigns_generated_id_and_v1(db):
    repo = WorkflowRepository(db)
    meta = await repo.create(name="Demo", doc=_doc(), summary=_summary())
    assert _UUID4_RE.match(meta.workflow_id)
    assert meta.current_version == 1
    assert meta.status is WorkflowStatus.DRAFT


async def test_create_with_explicit_id(db):
    repo = WorkflowRepository(db)
    meta = await repo.create(
        name="Demo",
        doc=_doc("wf_explicit"),
        summary=_summary(),
        workflow_id="wf_explicit",
    )
    assert meta.workflow_id == "wf_explicit"


async def test_create_duplicate_id_raises_conflict(db):
    repo = WorkflowRepository(db)
    await repo.create(name="A", doc=_doc("wf_dup"), summary=_summary(), workflow_id="wf_dup")
    with pytest.raises(ConflictError):
        await repo.create(
            name="B", doc=_doc("wf_dup"), summary=_summary(), workflow_id="wf_dup"
        )


async def test_create_stamps_doc_id_to_match_workflow_id(db):
    repo = WorkflowRepository(db)
    meta = await repo.create(
        name="Test",
        doc=_doc("wf_old"),  # caller passed wrong id
        summary=_summary(),
        workflow_id="wf_new",
    )
    response = await repo.get(meta.workflow_id)
    assert response is not None
    assert response.doc.id == "wf_new"


# --------------------------- read ---------------------------


async def test_get_returns_meta_and_doc(db):
    repo = WorkflowRepository(db)
    wid = await _seed(repo, name="Foo")
    response = await repo.get(wid)
    assert response is not None
    assert response.meta.workflow_id == wid
    assert response.doc.id == wid
    assert len(response.doc.nodes) == 2


async def test_get_returns_none_when_missing(db):
    repo = WorkflowRepository(db)
    assert await repo.get("wf_missing") is None
    assert await repo.get_meta("wf_missing") is None


async def test_get_meta_strips_doc_payload(db):
    """get_meta projects only the meta fields. The latest_doc field should
    not be reachable via the typed return."""
    repo = WorkflowRepository(db)
    wid = await _seed(repo)
    meta = await repo.get_meta(wid)
    # WorkflowMeta.model_fields proves no 'latest_doc' field is reachable.
    assert "latest_doc" not in type(meta).model_fields
    assert meta.workflow_id == wid


async def test_exists(db):
    repo = WorkflowRepository(db)
    wid = await _seed(repo)
    assert await repo.exists(wid) is True
    assert await repo.exists("wf_missing") is False


# --------------------------- update_head + OCC ---------------------------


async def test_update_head_bumps_version_with_correct_occ(db):
    repo = WorkflowRepository(db)
    wid = await _seed(repo)
    meta = await repo.update_head(
        workflow_id=wid,
        doc=_doc(wid),
        summary=_summary(),
        new_version=2,
        expected_current_version=1,
    )
    assert meta.current_version == 2


async def test_update_head_with_stale_version_raises_conflict(db):
    repo = WorkflowRepository(db)
    wid = await _seed(repo)
    await repo.update_head(
        workflow_id=wid,
        doc=_doc(wid),
        summary=_summary(),
        new_version=2,
        expected_current_version=1,
    )
    with pytest.raises(ConflictError):
        await repo.update_head(
            workflow_id=wid,
            doc=_doc(wid),
            summary=_summary(),
            new_version=3,
            expected_current_version=1,  # stale
        )


async def test_update_head_with_missing_workflow_raises_notfound(db):
    repo = WorkflowRepository(db)
    with pytest.raises(NotFoundError):
        await repo.update_head(
            workflow_id="wf_missing",
            doc=_doc("wf_missing"),
            summary=_summary(),
            new_version=2,
            expected_current_version=1,
        )


# --------------------------- patch_metadata ---------------------------


async def test_patch_metadata_partial(db):
    repo = WorkflowRepository(db)
    wid = await _seed(repo, name="Old")
    meta = await repo.patch_metadata(
        wid, {"name": "New", "tags": ["a", "b"]}
    )
    assert meta.name == "New"
    assert meta.tags == ["a", "b"]


async def test_patch_metadata_status_enum_accepted(db):
    repo = WorkflowRepository(db)
    wid = await _seed(repo)
    meta = await repo.patch_metadata(wid, {"status": WorkflowStatus.PUBLISHED})
    assert meta.status is WorkflowStatus.PUBLISHED


async def test_patch_metadata_ignores_unknown_keys(db):
    """Unknown fields silently dropped — defensive against misuse."""
    repo = WorkflowRepository(db)
    wid = await _seed(repo, name="Same")
    meta = await repo.patch_metadata(wid, {"name": "Renamed", "fake_field": "ignored"})
    assert meta.name == "Renamed"


async def test_patch_metadata_missing_raises_notfound(db):
    repo = WorkflowRepository(db)
    with pytest.raises(NotFoundError):
        await repo.patch_metadata("wf_missing", {"name": "X"})


async def test_patch_metadata_does_not_bump_version(db):
    repo = WorkflowRepository(db)
    wid = await _seed(repo)
    before = await repo.get_meta(wid)
    await repo.patch_metadata(wid, {"name": "Renamed"})
    after = await repo.get_meta(wid)
    assert after.current_version == before.current_version


# --------------------------- archive / restore / delete ---------------------------


async def test_archive_then_unarchive(db):
    repo = WorkflowRepository(db)
    wid = await _seed(repo)
    archived = await repo.archive(wid)
    assert archived.status is WorkflowStatus.ARCHIVED
    restored = await repo.unarchive(wid)
    assert restored.status is WorkflowStatus.DRAFT


async def test_soft_delete_is_archive_alias(db):
    repo = WorkflowRepository(db)
    wid = await _seed(repo)
    meta = await repo.soft_delete(wid)
    assert meta.status is WorkflowStatus.ARCHIVED


async def test_restore_is_unarchive_alias(db):
    repo = WorkflowRepository(db)
    wid = await _seed(repo)
    await repo.archive(wid)
    meta = await repo.restore(wid, target_status=WorkflowStatus.PUBLISHED)
    assert meta.status is WorkflowStatus.PUBLISHED


async def test_hard_delete_removes_record(db):
    repo = WorkflowRepository(db)
    wid = await _seed(repo)
    await repo.hard_delete(wid)
    assert await repo.get(wid) is None


async def test_hard_delete_missing_raises(db):
    repo = WorkflowRepository(db)
    with pytest.raises(NotFoundError):
        await repo.hard_delete("wf_missing")


# --------------------------- duplicate ---------------------------


async def test_duplicate_creates_fresh_id_at_v1(db):
    repo = WorkflowRepository(db)
    wid = await _seed(repo, name="Original")
    await repo.update_head(
        workflow_id=wid, doc=_doc(wid), summary=_summary(),
        new_version=2, expected_current_version=1,
    )
    copy = await repo.duplicate(wid)
    assert copy.workflow_id != wid
    assert copy.current_version == 1
    assert copy.name.endswith("(copy)")
    assert copy.status is WorkflowStatus.DRAFT


async def test_duplicate_with_explicit_name_and_owner(db):
    repo = WorkflowRepository(db)
    wid = await _seed(repo, owner_id="alice")
    copy = await repo.duplicate(wid, new_name="Custom", owner_id="bob")
    assert copy.name == "Custom"
    assert copy.owner_id == "bob"


async def test_duplicate_missing_raises(db):
    repo = WorkflowRepository(db)
    with pytest.raises(NotFoundError):
        await repo.duplicate("wf_missing")


async def test_duplicate_stamps_new_id_into_doc(db):
    repo = WorkflowRepository(db)
    wid = await _seed(repo)
    copy = await repo.duplicate(wid)
    response = await repo.get(copy.workflow_id)
    assert response.doc.id == copy.workflow_id


# --------------------------- list / search ---------------------------


async def test_list_excludes_archived_by_default(db):
    repo = WorkflowRepository(db)
    a = await _seed(repo, name="A")
    b = await _seed(repo, name="B")
    await repo.archive(b)
    items, total = await repo.list()
    ids = {m.workflow_id for m in items}
    assert a in ids
    assert b not in ids
    assert total == 1


async def test_list_includes_archived_when_requested(db):
    repo = WorkflowRepository(db)
    await _seed(repo, name="A")
    b = await _seed(repo, name="B")
    await repo.archive(b)
    items, total = await repo.list(include_archived=True)
    assert total == 2


async def test_list_pagination(db):
    repo = WorkflowRepository(db)
    for i in range(5):
        await _seed(repo, name=f"N{i}")
    page1, total = await repo.list(limit=2, offset=0)
    page2, _ = await repo.list(limit=2, offset=2)
    assert total == 5
    assert len(page1) == 2 and len(page2) == 2
    assert {m.workflow_id for m in page1} & {m.workflow_id for m in page2} == set()


async def test_list_filter_by_owner(db):
    repo = WorkflowRepository(db)
    await _seed(repo, owner_id="alice")
    await _seed(repo, owner_id="bob")
    items, total = await repo.list(owner_id="alice")
    assert total == 1
    assert items[0].owner_id == "alice"


async def test_list_filter_by_status(db):
    repo = WorkflowRepository(db)
    await _seed(repo, name="A")
    pub_id = await _seed(repo, name="B")
    await repo.patch_metadata(pub_id, {"status": WorkflowStatus.PUBLISHED})
    items, total = await repo.list(status=WorkflowStatus.PUBLISHED)
    assert total == 1
    assert items[0].status is WorkflowStatus.PUBLISHED


async def test_list_filter_by_tag(db):
    repo = WorkflowRepository(db)
    await _seed(repo, tags=["alpha", "beta"])
    await _seed(repo, tags=["gamma"])
    items, _ = await repo.list(tag="alpha")
    assert len(items) == 1


async def test_list_filter_by_category(db):
    repo = WorkflowRepository(db)
    await _seed(repo, category="support")
    await _seed(repo, category="ops")
    items, _ = await repo.list(category="ops")
    assert len(items) == 1


async def test_list_filter_by_node_type(db):
    repo = WorkflowRepository(db)
    await _seed(repo)  # contains 'start' and 'output'
    items, _ = await repo.list(node_type="start")
    assert len(items) == 1
    no_match, _ = await repo.list(node_type="nonexistent_type")
    assert no_match == []


async def test_list_filter_by_name_contains_case_insensitive(db):
    repo = WorkflowRepository(db)
    await _seed(repo, name="Customer Support Bot")
    await _seed(repo, name="Sales Pipeline")
    items, _ = await repo.list(name_contains="customer")
    assert len(items) == 1
    assert items[0].name == "Customer Support Bot"


async def test_list_sorted_by_updated_at_desc(db):
    repo = WorkflowRepository(db)
    first = await _seed(repo, name="first")
    second = await _seed(repo, name="second")
    items, _ = await repo.list()
    # newest first
    assert items[0].workflow_id == second
    assert items[1].workflow_id == first

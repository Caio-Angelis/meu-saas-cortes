"""Store SQLite da playlist web."""

from __future__ import annotations

import pytest

from app.web.store import (
    PIPELINE_DONE,
    WORKFLOW_DISCARDED,
    WORKFLOW_PENDING,
    WORKFLOW_PUBLISHED,
    JobStore,
)


@pytest.fixture
def store(tmp_path):
    return JobStore(db_path=tmp_path / "test.sqlite")


def test_add_and_list(store: JobStore) -> None:
    ids = store.add_urls(["https://example.com/v1"], pipeline_options={"target_language": "pt"})
    assert len(ids) == 1
    items = store.list_items()
    assert len(items) == 1
    assert items[0].workflow_status == WORKFLOW_PENDING
    assert items[0].can_process


def test_pipeline_done_then_workflow(store: JobStore) -> None:
    iid = store.add_urls(["https://a.test/v"])[0]
    store.mark_running(iid)
    store.update_progress(iid, 0.5)
    store.mark_pipeline_done(iid, ["/tmp/out.mp4"])
    item = store.get(iid)
    assert item is not None
    assert item.pipeline_status == PIPELINE_DONE
    assert item.can_mark_workflow

    updated = store.set_workflow(iid, WORKFLOW_PUBLISHED)
    assert updated is not None
    assert updated.workflow_status == WORKFLOW_PUBLISHED

    with pytest.raises(ValueError):
        store.set_workflow(iid, WORKFLOW_DISCARDED)


def test_discard_after_done(store: JobStore) -> None:
    iid = store.add_urls(["https://b.test/v"])[0]
    store.mark_pipeline_done(iid, [])
    store.set_workflow(iid, WORKFLOW_DISCARDED)
    assert store.get(iid).workflow_status == WORKFLOW_DISCARDED

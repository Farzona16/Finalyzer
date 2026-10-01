"""U3: encryption at rest (S10), persistence, hard delete and idle purge (S17), DuckDB cache."""

from __future__ import annotations

import base64
import logging
import os
from datetime import timedelta
from pathlib import Path

import pandas as pd
import pytest
from app.config import BackendSettings
from app.db import session_scope
from app.models import AuditRecord, Dataset, Workspace
from app.security import audit_chain
from app.services import datasets as service
from app.storage import crypto
from app.storage.handles import HandleCache
from app.storage.store import DatasetStore, StorageError
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import clock
from tests.conftest import (
    customers_csv,
    first_admin,
    login,
    new_session,
    sales_csv,
    upload_ready,
    wait_job,
)

CANARY = "CANARY-9f3a1c77-do-not-store"
MASTER = base64.b64encode(os.urandom(32)).decode()
OTHER_MASTER = base64.b64encode(os.urandom(32)).decode()


def all_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


# ---------------------------------------------------------------- crypto


def test_encryption_round_trip_and_randomness() -> None:
    key = os.urandom(32)
    blob = crypto.encrypt(key, b"hello table", b"ctx")
    assert b"hello table" not in blob
    assert crypto.decrypt(key, blob, b"ctx") == b"hello table"
    assert crypto.encrypt(key, b"hello table", b"ctx") != blob  # fresh key and nonce every time


def test_wrong_key_changed_bytes_and_moved_files_are_refused() -> None:
    key = os.urandom(32)
    blob = crypto.encrypt(key, b"secret rows", b"abc/t0")
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(os.urandom(32), blob, b"abc/t0")
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(key, blob, b"abc/t1")  # same bytes under another file's name
    tampered = bytearray(blob)
    tampered[-3] ^= 1
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(key, bytes(tampered), b"abc/t0")
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(key, b"not an encrypted file at all, just text", b"abc/t0")
    with pytest.raises(ValueError):
        crypto.encrypt(b"short", b"x", b"c")


def test_rewrap_changes_the_master_key_but_not_the_data() -> None:
    old, new = os.urandom(32), os.urandom(32)
    blob = crypto.encrypt(old, b"rows", b"c")
    moved = crypto.rewrap(old, new, blob)
    assert crypto.decrypt(new, moved, b"c") == b"rows"
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(old, moved, b"c")
    assert moved[crypto.HEADER_LEN :] == blob[crypto.HEADER_LEN :]


# ---------------------------------------------------------------- file store


def store_settings(make_settings, **extra) -> BackendSettings:
    values = {"storage_mode": "persistent", "app_master_key": MASTER, **extra}
    return make_settings(**values)


def test_the_store_refuses_names_that_are_not_its_own(make_settings) -> None:
    store = DatasetStore(make_settings())
    for bad in ("../x", "a/b", "C:\\x", "", "UPPER", "x" * 70):
        with pytest.raises(StorageError):
            store.folder(bad)


def test_persistent_files_are_ciphertext_and_load_back(make_settings) -> None:
    settings = store_settings(make_settings)
    store = DatasetStore(settings)
    frame = pd.DataFrame({"city": [CANARY, "Tashkent"], "amount": [1.5, 2.5]})
    name = "ab" * 16
    store.save(name, {"orders": frame})
    raw = b"".join(p.read_bytes() for p in all_files(settings.data_dir))
    assert CANARY.encode() not in raw and b"PAR1" not in raw and b"Tashkent" not in raw
    restored = store.load(name, ["orders"])["orders"]
    pd.testing.assert_frame_equal(restored, frame)


def test_a_wrong_master_key_cannot_read_the_files(make_settings) -> None:
    store = DatasetStore(store_settings(make_settings))
    store.save("cd" * 16, {"t": pd.DataFrame({"a": [1]})})
    other = DatasetStore(store_settings(make_settings, app_master_key=OTHER_MASTER))
    with pytest.raises(StorageError):
        other.load("cd" * 16, ["t"])


def test_master_key_rotation_keeps_data_readable(make_settings) -> None:
    settings = store_settings(make_settings)
    store = DatasetStore(settings)
    store.save("ef" * 16, {"t": pd.DataFrame({"a": [1, 2, 3]})})
    new_key = os.urandom(32)
    assert store.rotate_master_key(new_key) == 1
    assert store.load("ef" * 16, ["t"])["t"]["a"].tolist() == [1, 2, 3]
    rotated_settings = store_settings(
        make_settings, app_master_key=base64.b64encode(new_key).decode()
    )
    assert DatasetStore(rotated_settings).load("ef" * 16, ["t"])["t"]["a"].tolist() == [1, 2, 3]


def test_persistent_mode_will_not_start_without_a_master_key(make_settings) -> None:
    with pytest.raises(ValueError):
        make_settings(storage_mode="persistent")


# ---------------------------------------------------------------- persistent round trip (API)


def test_persistent_round_trip_across_a_restart(make_client, make_settings) -> None:
    settings = dict(storage_mode="persistent", app_master_key=MASTER)
    first = make_client(**settings)
    first_admin(first)
    dataset_id = upload_ready(first, [("sales.csv", sales_csv(80, canary=CANARY))])
    before = first.get(f"/api/v1/datasets/{dataset_id}/profile").json()
    data_dir = make_settings().data_dir
    raw = b"".join(p.read_bytes() for p in all_files(data_dir))
    assert raw and CANARY.encode() not in raw  # the canary is in a column of the uploaded file
    first.close()

    second = make_client(**settings)  # a fresh process: new caches, same database and files
    assert login(second, "admin@example.com") == 200
    build = second.post(f"/api/v1/datasets/{dataset_id}/build")
    assert wait_job(second, build.json()["id"])["status"] == "done"  # decrypted and rebuilt
    after = second.get(f"/api/v1/datasets/{dataset_id}/profile").json()
    assert after["dataset"]["status"] == "ready"
    assert after["dropped_pii"] == before["dropped_pii"]
    assert after["roles"] == before["roles"]


def test_a_wrong_key_after_restart_fails_the_build_without_leaking(make_client) -> None:
    first = make_client(storage_mode="persistent", app_master_key=MASTER)
    first_admin(first)
    dataset_id = upload_ready(first)
    first.close()
    second = make_client(storage_mode="persistent", app_master_key=OTHER_MASTER)
    assert login(second, "admin@example.com") == 200
    job = wait_job(second, second.post(f"/api/v1/datasets/{dataset_id}/build").json()["id"])
    assert job["status"] == "failed"
    assert second.get(f"/api/v1/datasets/{dataset_id}").json()["status"] == "failed"


def test_persistent_datasets_survive_logout_and_restart(make_client) -> None:
    client = make_client(storage_mode="persistent", app_master_key=MASTER)
    first_admin(client)
    dataset_id = upload_ready(client)
    assert client.post("/api/v1/auth/logout").status_code == 204
    again = make_client(storage_mode="persistent", app_master_key=MASTER)
    login(again, "admin@example.com")
    assert [d["id"] for d in again.get("/api/v1/datasets").json()] == [dataset_id]


# ---------------------------------------------------------------- hard delete


def test_delete_removes_files_rows_cache_and_is_audited(client: TestClient, make_settings) -> None:
    first_admin(client)
    dataset_id = upload_ready(
        client, [("sales.csv", sales_csv()), ("customers.csv", customers_csv())]
    )
    client.post(f"/api/v1/datasets/{dataset_id}/build")
    data_dir = make_settings().data_dir
    assert all_files(data_dir)
    assert client.delete(f"/api/v1/datasets/{dataset_id}").status_code == 204
    assert all_files(data_dir) == []
    assert not [p for p in data_dir.rglob("*") if p.is_dir() and p.name != "ephemeral"]
    assert client.get(f"/api/v1/datasets/{dataset_id}").status_code == 404
    assert client.app.state.services.handles.open_ids() == []
    with session_scope(client.app.state.session_factory) as db:
        assert db.scalar(select(Dataset).where(Dataset.id == dataset_id)) is None
        events = db.scalars(
            select(AuditRecord.event).where(AuditRecord.workspace_id.is_not(None))
        ).all()
        assert audit_chain.UPLOAD in events and audit_chain.DELETE in events
    assert client.get("/api/v1/admin/audit/verify").json()["ok"] is True


# ---------------------------------------------------------------- ephemeral: logout, idle, restart


def test_ephemeral_datasets_are_deleted_at_logout(client: TestClient, make_settings) -> None:
    first_admin(client)
    upload_ready(client)
    assert all_files(make_settings().data_dir)
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert all_files(make_settings().data_dir) == []
    login(client, "admin@example.com")
    assert client.get("/api/v1/datasets").json() == []


def test_idle_ephemeral_datasets_are_purged(client: TestClient, make_settings) -> None:
    first_admin(client)
    old = upload_ready(client)
    services = client.app.state.services
    with session_scope(services.factory) as db:
        assert service.purge_expired(services, db, clock.now()) == 0  # fresh data stays
    later = clock.now() + timedelta(minutes=make_settings().session_idle_minutes + 1)
    with session_scope(services.factory) as db:
        assert service.purge_expired(services, db, later) == 1
    assert client.get(f"/api/v1/datasets/{old}").status_code == 404
    assert all_files(make_settings().data_dir) == []


def test_using_a_dataset_keeps_it_alive(make_client, monkeypatch) -> None:
    client = make_client(session_idle_minutes=10)
    first_admin(client)
    dataset_id = upload_ready(client)
    services = client.app.state.services
    start = clock.now()
    monkeypatch.setattr(clock, "now", lambda: start + timedelta(minutes=8))
    assert client.get(f"/api/v1/datasets/{dataset_id}").status_code == 200  # touched at +8 min
    with session_scope(services.factory) as db:
        assert service.purge_expired(services, db, start + timedelta(minutes=16)) == 0
        assert service.purge_expired(services, db, start + timedelta(minutes=19)) == 1


def test_a_restart_clears_ephemeral_data(make_client, make_settings) -> None:
    first = make_client()
    first_admin(first)
    upload_ready(first)
    first.close()
    second = make_client()
    login(second, "admin@example.com")
    assert second.get("/api/v1/datasets").json() == []
    assert all_files(make_settings().data_dir) == []


def test_files_without_a_record_are_removed(client: TestClient, make_settings) -> None:
    first_admin(client)
    services = client.app.state.services
    services.store.save("0f" * 16, {"t": pd.DataFrame({"a": [1]})})
    assert all_files(make_settings().data_dir)
    with session_scope(services.factory) as db:
        assert service.remove_orphans(services, db) == 1
    assert all_files(make_settings().data_dir) == []


def test_retention_deletes_old_persistent_datasets(make_client) -> None:
    client = make_client(storage_mode="persistent", app_master_key=MASTER)
    first_admin(client)
    dataset_id = upload_ready(client)
    services = client.app.state.services
    with session_scope(services.factory) as db:
        assert (
            service.purge_expired(services, db, clock.now() + timedelta(days=400)) == 0
        )  # no policy
        db.scalars(select(Workspace)).first().retention_days = 30
    with session_scope(services.factory) as db:
        assert service.purge_expired(services, db, clock.now() + timedelta(days=10)) == 0
        assert service.purge_expired(services, db, clock.now() + timedelta(days=31)) == 1
    assert client.get(f"/api/v1/datasets/{dataset_id}").status_code == 404


def test_the_background_sweep_runs_on_a_timer(make_client) -> None:
    import time

    client = make_client(purge_interval_seconds=1, session_idle_minutes=0)
    first_admin(client)
    dataset_id = upload_ready(client)
    for _ in range(60):
        time.sleep(0.1)
        with session_scope(client.app.state.session_factory) as db:
            if db.get(Dataset, dataset_id) is None:
                break
    else:
        pytest.fail("the sweep did not delete the idle dataset")


# ---------------------------------------------------------------- DuckDB handle cache


def test_the_cache_keeps_only_a_few_datasets_open(make_client) -> None:
    client = make_client(max_open_datasets=2)
    first_admin(client)
    ids = [upload_ready(client) for _ in range(3)]
    services = client.app.state.services
    with session_scope(services.factory) as db:
        for dataset_id in ids:
            row = service.get_dataset(db, db.scalars(select(Workspace)).first().id, dataset_id)
            with services.handles.use(row.id, row.storage_name, service.stored_model(row)) as h:
                count = h.workspace.con.execute("SELECT COUNT(*) FROM base").fetchone()[0]
                assert count == 120
    assert services.handles.open_ids() == ids[1:]  # the oldest was evicted and closed


def test_an_evicted_dataset_is_reloaded_from_the_store(make_client) -> None:
    client = make_client(max_open_datasets=1)
    first_admin(client)
    first, second = upload_ready(client), upload_ready(client)
    services = client.app.state.services
    with session_scope(services.factory) as db:
        wid = db.scalars(select(Workspace)).first().id
        for dataset_id in (first, second, first):
            row = service.get_dataset(db, wid, dataset_id)
            with services.handles.use(row.id, row.storage_name, service.stored_model(row)) as h:
                assert h.model.row_count == 120
    assert services.handles.open_ids() == [first]


def test_handle_cache_closes_connections_on_shutdown(make_settings) -> None:
    settings = make_settings()
    store = DatasetStore(settings)
    cache = HandleCache(
        store, lambda: __import__("core.config", fromlist=["Settings"]).Settings(_env_file=None), 2
    )
    cache.close_all()
    assert cache.open_ids() == []


# ---------------------------------------------------------------- logs and per-user cleanup


def test_logs_never_contain_uploaded_values(client: TestClient, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    first_admin(client)
    upload_ready(client, [("sales.csv", sales_csv(40, canary=CANARY))])
    broken = b"a,b\n" + CANARY.encode() + b",1,2,3,4\n"
    job_id = client.post(
        "/api/v1/datasets", files=[("files", ("broken.csv", broken, "text/csv"))]
    ).json()["id"]
    wait_job(client, job_id)
    assert CANARY not in caplog.text


def test_two_users_logging_out_only_lose_their_own_ephemeral_data(client: TestClient) -> None:
    from tests.conftest import add_user

    first_admin(client)
    mine = upload_ready(client)
    add_user(client, "analyst@example.com", "analyst")
    analyst = new_session(client)
    login(analyst, "analyst@example.com")
    theirs = upload_ready(analyst)
    assert analyst.post("/api/v1/auth/logout").status_code == 204
    ids = {d["id"] for d in client.get("/api/v1/datasets").json()}
    assert ids == {mine} and theirs not in ids

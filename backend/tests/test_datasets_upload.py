"""U3: upload validation (S9), the ingest job, profile, model edits, build, roles, isolation."""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from tests.conftest import (
    STRONG_PASSWORD,
    add_user,
    customers_csv,
    first_admin,
    insert_workspace_with_admin,
    login,
    new_session,
    sales_csv,
    upload,
    upload_ready,
    wait_job,
    with_csrf,
)


def xlsx_bytes() -> bytes:
    """A genuine small workbook made by openpyxl."""
    import openpyxl

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["order_date", "store", "amount"])
    for i in range(30):
        sheet.append([f"2026-02-{1 + i % 28:02d}", f"S{i % 3}", 10 + i])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def fake_xlsx(member: bytes, name: str = "xl/media/big.bin") -> bytes:
    """A ZIP that looks like a workbook but holds one highly compressible member."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
        archive.writestr(name, member)
    return out.getvalue()


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    first_admin(client)
    return client


# ---------------------------------------------------------------- happy path


def test_a_csv_upload_becomes_a_profiled_dataset(admin: TestClient) -> None:
    response = upload(admin, [("My Sales 2026.csv", sales_csv())])
    assert response.status_code == 202
    job = wait_job(admin, response.json()["id"])
    assert job["status"] == "done" and job["progress"] == 100
    dataset_id = job["result"]["dataset_id"]

    listing = admin.get("/api/v1/datasets").json()
    assert [d["id"] for d in listing] == [dataset_id]
    item = listing[0]
    assert item["name"] == "My Sales 2026"  # a display label, never a path
    assert item["status"] == "profiled" and item["row_count"] == 120

    profile = admin.get(f"/api/v1/datasets/{dataset_id}/profile").json()
    assert profile["fact_table"] == "my_sales_2026"
    assert profile["roles"]["date"] == "order_date"
    assert profile["roles"]["amount"] == "amount"
    assert any(name.endswith("email") for name in profile["dropped_pii"])
    assert profile["roles_automatic"] is True


def test_an_excel_upload_works(admin: TestClient) -> None:
    dataset_id = upload_ready(admin, [("book.xlsx", xlsx_bytes())])
    assert admin.get(f"/api/v1/datasets/{dataset_id}").json()["row_count"] == 30


def test_two_files_are_joined_when_the_keys_match(admin: TestClient) -> None:
    dataset_id = upload_ready(
        admin, [("sales.csv", sales_csv()), ("customers.csv", customers_csv())]
    )
    profile = admin.get(f"/api/v1/datasets/{dataset_id}/profile").json()
    assert [(r["dim_table"], r["dim_col"]) for r in profile["relationships"]] == [
        ("customers", "customer_id")
    ]
    flat = {c["name"] for c in profile["columns"]}
    assert "region" in flat
    assert profile["dataset"]["tables"] == {"sales": 120, "customers": 10}
    asked = admin.post(
        f"/api/v1/datasets/{dataset_id}/ask",
        json={"question": "Region bo'yicha jami daromad", "lang": "uz"},
    )
    assert asked.status_code == 200, asked.text
    body = asked.json()
    assert body["status"] == "ok"
    text = body["headline"] + body["explanation"] + str(body.get("table"))
    assert "Region" in text


def test_build_marks_the_dataset_ready(admin: TestClient) -> None:
    dataset_id = upload_ready(admin)
    response = admin.post(f"/api/v1/datasets/{dataset_id}/build")
    assert response.status_code == 202
    assert wait_job(admin, response.json()["id"])["status"] == "done"
    assert admin.get(f"/api/v1/datasets/{dataset_id}").json()["status"] == "ready"


def test_progress_is_streamed_as_server_sent_events(admin: TestClient) -> None:
    job_id = upload(admin, [("sales.csv", sales_csv())]).json()["id"]
    with admin.stream("GET", f"/api/v1/jobs/{job_id}/events") as stream:
        assert stream.headers["content-type"].startswith("text/event-stream")
        body = "".join(stream.iter_text())
    events = [block for block in body.split("\n\n") if block.strip()]
    assert events[-1].startswith("event: done")
    last = json.loads(events[-1].split("data: ", 1)[1])
    assert last["status"] == "done" and "dataset_id" in last["result"]
    assert all(block.startswith(("event: progress", "event: done")) for block in events)


# ---------------------------------------------------------------- model edits


def test_roles_can_be_changed_and_bad_ones_are_refused(admin: TestClient) -> None:
    dataset_id = upload_ready(admin)
    url = f"/api/v1/datasets/{dataset_id}/model"
    ok = admin.patch(url, json={"roles": {"date": "order_date", "amount": "amount"}})
    assert ok.status_code == 200
    assert ok.json()["roles_automatic"] is False
    bad = admin.patch(url, json={"roles": {"amount": "store"}})  # text column as amount
    assert bad.status_code == 422 and bad.json()["code"] == "model_invalid"
    unknown = admin.patch(url, json={"roles": {"amount": "no_such_column"}})
    assert unknown.json()["code"] == "model_invalid"
    # the stored choice survived the failed attempts
    assert admin.get(f"/api/v1/datasets/{dataset_id}/profile").json()["roles"]["amount"] == "amount"


def test_domain_can_be_set_and_unknown_domains_are_refused(admin: TestClient) -> None:
    dataset_id = upload_ready(admin)
    url = f"/api/v1/datasets/{dataset_id}/model"
    assert admin.patch(url, json={"domain": "retail"}).json()["domain"] == "retail"
    assert admin.patch(url, json={"domain": "astrology"}).status_code == 422
    assert admin.patch(url, json={"unexpected": 1}).status_code == 422


def test_a_join_can_be_removed_and_added_back(admin: TestClient) -> None:
    dataset_id = upload_ready(
        admin, [("sales.csv", sales_csv()), ("customers.csv", customers_csv())]
    )
    url = f"/api/v1/datasets/{dataset_id}/model"
    off = admin.patch(url, json={"relationships": []}).json()
    assert off["relationships"] == []
    join = {"fact_col": "customer_id", "dim_table": "customers", "dim_col": "customer_id"}
    on = admin.patch(url, json={"relationships": [join]}).json()
    assert len(on["relationships"]) == 1


def test_an_unknown_fact_table_is_refused(admin: TestClient) -> None:
    dataset_id = upload_ready(admin)
    response = admin.patch(f"/api/v1/datasets/{dataset_id}/model", json={"fact_table": "nope"})
    assert response.status_code == 422 and response.json()["code"] == "model_invalid"


# ---------------------------------------------------------------- upload validation (S9)


def code_of(response) -> str:
    return response.json()["code"]


def test_the_extension_must_be_csv_or_xlsx(admin: TestClient) -> None:
    for name in ("malware.exe", "page.html", "data.csv.exe", "noextension", "x.xlsm", "x.pdf"):
        response = upload(admin, [(name, b"a,b\n1,2\n")])
        assert response.status_code == 415, name
        assert code_of(response) == "unsupported_media"


def test_a_binary_file_renamed_to_csv_is_refused(admin: TestClient) -> None:
    for payload in (
        xlsx_bytes(),
        b"%PDF-1.7\n" + b"a,b\n" * 20,
        b"MZ\x90\x00" + b"a,b\n" * 20,
        b"a,b\n\x00\x01\x02\n",
    ):
        response = upload(admin, [("data.csv", payload)])
        assert response.status_code == 422
        assert code_of(response) == "upload_unsafe"


def test_a_text_file_renamed_to_xlsx_is_refused(admin: TestClient) -> None:
    response = upload(admin, [("data.xlsx", sales_csv())])
    assert response.status_code == 422 and code_of(response) == "upload_invalid"
    plain_zip = io.BytesIO()
    with zipfile.ZipFile(plain_zip, "w") as archive:
        archive.writestr("hello.txt", "hi")
    response = upload(admin, [("data.xlsx", plain_zip.getvalue())])
    assert response.status_code == 422 and code_of(response) == "upload_invalid"


def test_a_zip_bomb_is_refused_before_it_is_opened(admin: TestClient) -> None:
    bomb = fake_xlsx(b"\x00" * (60 * 1024 * 1024))  # ~60 MB of zeros, a few KB compressed
    assert len(bomb) < 200_000
    response = upload(admin, [("bomb.xlsx", bomb)])
    assert response.status_code == 422 and code_of(response) == "upload_unsafe"
    assert admin.get("/api/v1/datasets").json() == []


def test_the_uncompressed_size_limit_is_enforced(make_client) -> None:
    client = make_client(max_uncompressed_mb=1, max_zip_expansion=100000)
    first_admin(client)
    response = upload(client, [("big.xlsx", fake_xlsx(b"x" * (3 * 1024 * 1024)))])
    assert response.status_code == 422 and code_of(response) == "upload_unsafe"


def test_empty_and_whitespace_files_are_refused(admin: TestClient) -> None:
    for payload in (b"", b"   \n\n  "):
        response = upload(admin, [("empty.csv", payload)])
        assert response.status_code == 422 and code_of(response) == "upload_invalid"


def test_a_file_over_the_size_limit_is_refused(make_client) -> None:
    client = make_client(max_upload_mb=1)
    first_admin(client)
    big = b"a,b\n" + b"1,2\n" * (600 * 1024)
    response = upload(client, [("big.csv", big)])
    assert response.status_code == 413 and code_of(response) == "payload_too_large"


def test_the_whole_request_is_limited_before_it_is_read(make_client) -> None:
    client = make_client(max_request_mb=1)
    first_admin(client)
    payload = b"a,b\n" + b"1,2\n" * (400 * 1024)
    response = upload(client, [("a.csv", payload), ("b.csv", payload)])
    assert response.status_code == 413 and code_of(response) == "payload_too_large"
    assert response.headers["content-type"].startswith("application/problem+json")


def test_an_empty_extra_upload_part_is_ignored(admin: TestClient) -> None:
    dataset_id = upload_ready(admin, [("sales.csv", sales_csv(20)), ("empty.csv", b"")])
    assert admin.get(f"/api/v1/datasets/{dataset_id}").json()["row_count"] == 20


def test_too_many_files_are_refused(make_client) -> None:
    client = make_client(max_upload_files=2)
    first_admin(client)
    files = [(f"t{i}.csv", sales_csv(5)) for i in range(3)]
    assert upload(client, files).status_code == 422
    assert upload(client, []).status_code == 422


def test_a_path_like_file_name_is_only_a_label(admin: TestClient, make_settings) -> None:
    dataset_id = upload_ready(admin, [("../../etc/passwd.csv", sales_csv(10))])
    name = admin.get(f"/api/v1/datasets/{dataset_id}").json()["name"]
    assert name == "passwd"
    storage = make_settings().data_dir
    stored = [p for p in storage.rglob("*") if p.is_file()]
    assert stored and all("passwd" not in str(p) for p in stored)
    assert not (storage.parent / "etc").exists()


def test_a_broken_table_fails_the_job_with_a_plain_message(admin: TestClient) -> None:
    ragged = b"a,b,c\n1,2,3\n4,5,6,7,8\n"
    job = wait_job(admin, upload(admin, [("ragged.csv", ragged)]).json()["id"])
    assert job["status"] == "failed"
    assert job["error"]["code"] in {"upload_invalid", "server_error"}
    assert job["error"]["title"] and job["error"]["detail"]
    assert admin.get("/api/v1/datasets").json() == []


def test_job_errors_follow_the_requested_language(admin: TestClient) -> None:
    job_id = upload(admin, [("ragged.csv", b"a,b,c\n1,2,3\n4,5,6,7,8\n")]).json()["id"]
    wait_job(admin, job_id)
    uz = admin.get(f"/api/v1/jobs/{job_id}").json()["error"]["detail"]
    en = admin.get(f"/api/v1/jobs/{job_id}", headers={"Accept-Language": "en"}).json()["error"][
        "detail"
    ]
    assert uz != en


# ---------------------------------------------------------------- roles and isolation


def test_a_viewer_can_read_but_not_upload_build_edit_or_delete(admin: TestClient) -> None:
    dataset_id = upload_ready(admin)
    add_user(admin, "viewer@example.com", "viewer")
    viewer = new_session(admin)
    assert login(viewer, "viewer@example.com") == 200
    assert viewer.get("/api/v1/datasets").status_code == 200
    assert viewer.get(f"/api/v1/datasets/{dataset_id}/profile").status_code == 200
    assert upload(viewer, [("a.csv", sales_csv(5))]).status_code == 403
    assert viewer.post(f"/api/v1/datasets/{dataset_id}/build").status_code == 403
    assert (
        viewer.patch(f"/api/v1/datasets/{dataset_id}/model", json={"domain": "bank"}).status_code
        == 403
    )
    assert viewer.delete(f"/api/v1/datasets/{dataset_id}").status_code == 403
    assert viewer.post("/api/v1/demo/retail/load").status_code == 403
    assert admin.get(f"/api/v1/datasets/{dataset_id}").status_code == 200


def test_an_analyst_can_upload_and_delete(admin: TestClient) -> None:
    add_user(admin, "analyst@example.com", "analyst")
    analyst = new_session(admin)
    assert login(analyst, "analyst@example.com") == 200
    dataset_id = upload_ready(analyst)
    assert analyst.delete(f"/api/v1/datasets/{dataset_id}").status_code == 204


def other_workspace_admin(client: TestClient) -> TestClient:
    insert_workspace_with_admin(client, "boss@other.example")
    other = new_session(client)
    assert login(other, "boss@other.example", STRONG_PASSWORD) == 200
    return other


def test_two_workspaces_cannot_reach_each_others_datasets(admin: TestClient) -> None:
    mine = upload_ready(admin)
    other = other_workspace_admin(admin)
    assert other.get("/api/v1/datasets").json() == []
    base = f"/api/v1/datasets/{mine}"
    assert other.get(base).status_code == 404
    assert other.get(f"{base}/profile").status_code == 404
    assert other.patch(f"{base}/model", json={"domain": "bank"}).status_code == 404
    assert other.post(f"{base}/build").status_code == 404
    assert other.delete(base).status_code == 404
    # nothing happened to it
    assert admin.get(base).json()["domain"] is None
    assert admin.get(base).status_code == 200


def test_jobs_belong_to_their_workspace(admin: TestClient) -> None:
    job_id = upload(admin, [("sales.csv", sales_csv(20))]).json()["id"]
    other = other_workspace_admin(admin)
    assert other.get(f"/api/v1/jobs/{job_id}").status_code == 404
    assert other.get(f"/api/v1/jobs/{job_id}/events").status_code == 404
    wait_job(admin, job_id)


def test_an_anonymous_visitor_cannot_upload(admin: TestClient) -> None:
    anonymous = with_csrf(new_session(admin))
    parts = [("files", ("a.csv", sales_csv(5), "text/csv"))]
    assert anonymous.post("/api/v1/datasets", files=parts).status_code == 401


# ---------------------------------------------------------------- demo loader


def test_the_demo_list_and_loader(admin: TestClient) -> None:
    names = {d["name"] for d in admin.get("/api/v1/demo").json()}
    assert {"retail", "bank"} <= names
    response = admin.post("/api/v1/demo/retail/load")
    assert response.status_code == 202
    job = wait_job(admin, response.json()["id"], timeout=120)
    assert job["status"] == "done"
    profile = admin.get(f"/api/v1/datasets/{job['result']['dataset_id']}/profile").json()
    assert profile["fact_table"] == "sales"
    assert len(profile["relationships"]) >= 2
    assert profile["dataset"]["name"] == "retail"


def test_an_unknown_demo_is_not_found(admin: TestClient) -> None:
    assert admin.post("/api/v1/demo/astrology/load").status_code == 404
    assert admin.post("/api/v1/demo/Retail/load").status_code == 404  # exact names only
    assert admin.post("/api/v1/demo/retail%2F..%2Fbank/load").status_code in (404, 405)

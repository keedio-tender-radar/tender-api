import gzip
import json

from tender_api.config import settings
from tender_api.services import storage
from tests.conftest import make_tender


def test_backup_requires_run_token(client, monkeypatch):
    monkeypatch.setattr(settings, "run_token", "RT")
    assert client.post("/api/admin/backup").status_code == 401
    assert client.post("/api/admin/backup", headers={"X-Run-Token": "wrong"}).status_code == 401


def test_backup_creates_gzip_dump(client, monkeypatch):
    monkeypatch.setattr(settings, "run_token", "RT")
    store: dict[str, bytes] = {}
    monkeypatch.setattr(storage, "is_configured", lambda: True)
    monkeypatch.setattr(
        storage, "put_bytes",
        lambda key, data, ct="application/octet-stream": store.__setitem__(key, data) or key,
    )
    monkeypatch.setattr(storage, "list_objects", lambda prefix: [{"key": k} for k in store])
    monkeypatch.setattr(storage, "delete", lambda key: store.pop(key, None))

    make_tender(client, source_id="BK-1")
    r = client.post("/api/admin/backup", headers={"X-Run-Token": "RT"})
    assert r.status_code == 200
    body = r.json()
    assert body["rows"] >= 1
    assert body["key"].startswith("backups/") and body["key"].endswith(".json.gz")

    dump = json.loads(gzip.decompress(store[body["key"]]))
    assert "tenders" in dump["tables"]
    assert len(dump["tables"]["tenders"]) >= 1
    assert dump["created_at"]


def test_backup_503_without_storage(client, monkeypatch):
    monkeypatch.setattr(settings, "run_token", "RT")
    monkeypatch.setattr(storage, "is_configured", lambda: False)
    r = client.post("/api/admin/backup", headers={"X-Run-Token": "RT"})
    assert r.status_code == 503


def test_prune_requires_token_and_returns_counts(client, monkeypatch):
    monkeypatch.setattr(settings, "run_token", "RT")
    assert client.post("/api/admin/prune").status_code == 401
    r = client.post("/api/admin/prune", headers={"X-Run-Token": "RT"})
    assert r.status_code == 200
    body = r.json()
    assert "run_logs_deleted" in body and "snapshots_deleted" in body

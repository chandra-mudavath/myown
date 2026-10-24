"""storage/ holds public profile pictures and private client documents; only avatars are served by URL."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_avatars_are_public_but_client_documents_are_not(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "storage/u1/avatar").mkdir(parents=True)
    (tmp_path / "storage/u1/avatar/avatar_ab12.png").write_bytes(b"png")
    doc = tmp_path / "storage/client_documents/C1/2025/F1/W2"
    doc.mkdir(parents=True)
    (doc / "w2_v1_abcd.pdf").write_bytes(b"secret")
    (tmp_path / "storage/client_documents/avatar").mkdir()
    (tmp_path / "storage/client_documents/avatar/x.pdf").write_bytes(b"secret")

    client = TestClient(app)
    r = client.get("/storage/u1/avatar/avatar_ab12.png")
    assert r.status_code == 200 and r.content == b"png"
    for url in ("/storage/client_documents/C1/2025/F1/W2/w2_v1_abcd.pdf",
                "/storage/client_documents/avatar/x.pdf",
                "/storage/u1/avatar/..%2F..%2Fclient_documents%2FC1%2F2025%2FF1%2FW2%2Fw2_v1_abcd.pdf",
                "/storage/u1/avatar/missing.png"):
        assert client.get(url).status_code == 404, url

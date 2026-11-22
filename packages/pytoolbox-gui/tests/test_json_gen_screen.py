import json
from pathlib import Path

from fastapi.testclient import TestClient

from pytoolbox.db import HistoryStore
from pytoolbox.locks import target_lock
from pytoolbox_gui.server import create_app

ORIGIN = {"Origin": "http://127.0.0.1:8000"}


def test_json_gen_screen_dry_run_write_overwrite_and_history(tmp_path: Path) -> None:
    state = tmp_path / "state"
    store = HistoryStore(state_root=state)
    out = tmp_path / "records.json"
    schema = '{"id": "uuid", "role": {"type": "custom_list", "values": ["admin"]}}'

    with TestClient(
        create_app(history=store), base_url="http://127.0.0.1:8000", raise_server_exceptions=False
    ) as client:
        assert client.get("/static/icons/ic_fluent_braces_20_regular.svg").status_code == 200
        page = client.get("/tools/json-gen").text
        assert "date_range" in page

        dry = client.post(
            "/tools/json-gen",
            data={
                "output": f'"{out}"',
                "rows": "3",
                "seed": "7",
                "schema": schema,
                "dry_run": "on",
            },
            headers=ORIGIN,
        )
        assert dry.status_code == 200
        assert "Would write UTF-8 (no BOM) JSON array to:" in dry.text
        assert not out.exists()
        assert store.list_runs() == ()

        written = client.post(
            "/tools/json-gen",
            data={"output": str(out), "rows": "3", "seed": "7", "schema": schema},
            headers=ORIGIN,
        )
        assert written.status_code == 200
        assert "Wrote UTF-8 (no BOM) JSON array to:" in written.text
        parsed = json.loads(out.read_text(encoding="utf-8"))
        assert len(parsed) == 3
        assert parsed[0]["role"] == "admin"
        assert len(store.list_runs()) == 1

        conflict = client.post(
            "/tools/json-gen",
            data={"output": str(out), "rows": "2", "schema": schema},
            headers=ORIGIN,
        )
        assert conflict.status_code == 422
        assert "already exists" in conflict.text

        replaced = client.post(
            "/tools/json-gen",
            data={
                "output": str(out),
                "rows": "2",
                "schema": '{"ok": "boolean"}',
                "overwrite": "on",
            },
            headers=ORIGIN,
        )
        assert replaced.status_code == 200
        assert 'hx-swap-oob="true"' in replaced.text
        assert len(store.list_runs()) == 2

        history_pane = client.get("/history", headers={"HX-Request": "true"}).text
        assert "json-gen" in history_pane

        locked = tmp_path / "locked.json"
        with target_lock(locked, state_root=state):
            lock_resp = client.post(
                "/tools/json-gen",
                data={"output": str(locked), "rows": "1", "schema": '{"a": "string"}'},
                headers=ORIGIN,
            )
            assert lock_resp.status_code == 500
            assert "Target is locked by another run" in lock_resp.text


def test_json_gen_screen_validates_fields(tmp_path: Path) -> None:
    store = HistoryStore(state_root=tmp_path / "state")
    with TestClient(
        create_app(history=store), base_url="http://127.0.0.1:8000", raise_server_exceptions=False
    ) as client:
        bad_empty = client.post(
            "/tools/json-gen",
            data={"output": "", "rows": "2", "schema": '{"a": "string"}'},
            headers=ORIGIN,
        )
        assert bad_empty.status_code == 422
        assert "Output file: enter a path." in bad_empty.text

        bad_rel = client.post(
            "/tools/json-gen",
            data={"output": "rel.json", "rows": "2", "schema": '{"a": "string"}'},
            headers=ORIGIN,
        )
        assert bad_rel.status_code == 422
        assert "absolute path" in bad_rel.text

        bad_rows = client.post(
            "/tools/json-gen",
            data={"output": str(tmp_path / "a.json"), "rows": "abc", "schema": '{"a": "string"}'},
            headers=ORIGIN,
        )
        assert bad_rows.status_code == 422
        assert "Rows: enter a whole number" in bad_rows.text

        bad_seed = client.post(
            "/tools/json-gen",
            data={
                "output": str(tmp_path / "a.json"),
                "rows": "2",
                "seed": "nope",
                "schema": '{"a": "string"}',
            },
            headers=ORIGIN,
        )
        assert bad_seed.status_code == 422
        assert "Seed: enter a whole number" in bad_seed.text

        bad_schema = client.post(
            "/tools/json-gen",
            data={"output": str(tmp_path / "a.json"), "rows": "2", "schema": "/etc/passwd"},
            headers=ORIGIN,
        )
        assert bad_schema.status_code == 422
        assert "Schema: enter a JSON object." in bad_schema.text

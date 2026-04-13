from fastapi import FastAPI

import tailevents.main as main_module


def test_main_module_exposes_app():
    assert isinstance(main_module.app, FastAPI)


def test_main_uses_cli_overrides(monkeypatch, tmp_path):
    captured = {}

    def fake_run(app, host: str, port: int) -> None:
        captured["app"] = app
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr(main_module.uvicorn, "run", fake_run)

    db_path = tmp_path / "cli.db"
    main_module.main(
        [
            "--db-path",
            str(db_path),
            "--host",
            "127.0.0.1",
            "--port",
            "9001",
        ]
    )

    assert isinstance(captured["app"], FastAPI)
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9001
    runtime_settings = main_module.build_runtime_settings(
        [
            "--db-path",
            str(db_path),
            "--host",
            "127.0.0.1",
            "--port",
            "9001",
        ]
    )
    assert runtime_settings.db_path == str(db_path)

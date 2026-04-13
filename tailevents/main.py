"""Package entry point for running the TailEvents API server."""

import argparse
from typing import Optional

import uvicorn

from tailevents.api import create_app
from tailevents.config import Settings, get_settings


app = create_app()


def build_runtime_settings(argv: Optional[list[str]] = None) -> Settings:
    """Build settings with optional CLI overrides."""

    parser = argparse.ArgumentParser(description="Run the TailEvents API server.")
    parser.add_argument("--db-path", dest="db_path", default=None)
    parser.add_argument("--host", dest="host", default=None)
    parser.add_argument("--port", dest="port", type=int, default=None)
    args = parser.parse_args(argv)

    base_settings = get_settings()
    updates = {}
    if args.db_path is not None:
        updates["db_path"] = args.db_path
    if args.host is not None:
        updates["api_host"] = args.host
    if args.port is not None:
        updates["api_port"] = args.port
    return base_settings.model_copy(update=updates)


def main(argv: Optional[list[str]] = None) -> None:
    """Start the FastAPI application with uvicorn."""

    settings = build_runtime_settings(argv)
    runtime_app = create_app(settings=settings)
    uvicorn.run(runtime_app, host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()

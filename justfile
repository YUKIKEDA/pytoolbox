# Gates named in docs/conventions.md.
set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

lint:
    uv run ruff format --check .
    uv run ruff check .
    uv run pyright

test:
    uv run pytest

coverage:
    uv run pytest --cov --cov-report=term-missing

format:
    uv run ruff format .
    uv run ruff check --fix .

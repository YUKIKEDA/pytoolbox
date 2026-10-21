"""The FastAPI app: a hypermedia API that returns HTML for HTMX."""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, Environment, FileSystemLoader, select_autoescape

from pytoolbox import registry
from pytoolbox.errors import InputError, OperationError
from pytoolbox.registry import Tool
from pytoolbox_gui import screens
from pytoolbox_gui.screens import Form, Fragment, Screen
from pytoolbox_gui.sse import Runs, event_name, format_event

HOST = "127.0.0.1"
PORT = 8000

_PACKAGE = Path(__file__).parent
_logger = logging.getLogger(__name__)


def _check_screens(tools: Sequence[Tool], all_screens: Sequence[Screen]) -> dict[str, Screen]:
    registry.check_unique(tools)
    by_name = {screen.tool.name: screen for screen in all_screens}
    if len(by_name) != len(all_screens):
        msg = "Two screens share a tool name."
        raise ValueError(msg)
    tool_names = {tool.name for tool in tools}
    if missing := sorted(tool_names - by_name.keys()):
        msg = f"Tools without a screen: {', '.join(missing)}"
        raise ValueError(msg)
    if extra := sorted(by_name.keys() - tool_names):
        msg = f"Screens without a tool: {', '.join(extra)}"
        raise ValueError(msg)
    return by_name


def create_app(
    tools: Sequence[Tool] = registry.TOOLS,
    all_screens: Sequence[Screen] = screens.SCREENS,
    *,
    port: int = PORT,
    template_dirs: Sequence[Path] = (),
) -> FastAPI:
    """Build the GUI app.

    Args:
        tools: The registry. Each tool must have exactly one screen.
        all_screens: The screens of those tools.
        port: The port the server listens on. `Host` and `Origin` must name it.
        template_dirs: Extra template directories, searched before the package's own.

    Returns:
        The app.

    Raises:
        ValueError: A tool has no screen, a screen has no tool, or a name repeats.
    """
    by_name = _check_screens(tools, all_screens)
    sidebar = registry.group(tools)
    runs = Runs()
    hosts = {f"{HOST}:{port}", f"localhost:{port}"}
    origins = {f"http://{host}" for host in hosts}

    loaders = [FileSystemLoader(path) for path in [*template_dirs, _PACKAGE / "templates"]]
    env = Environment(loader=ChoiceLoader(loaders), autoescape=select_autoescape())
    templates = Jinja2Templates(env=env)

    def render(fragment: Fragment) -> str:
        return env.get_template(fragment.template).render(**fragment.context)

    def page(request: Request, template: str, status: int = 200, **context: object) -> Response:
        if request.headers.get("hx-request") != "true":
            context = {"pane": template, "sidebar": sidebar, **context}
            template = "shell.html"
        return templates.TemplateResponse(request, template, context, status_code=status)

    def screen_for(name: str) -> Screen:
        screen = by_name.get(name)
        if screen is None:
            msg = f"Unknown tool: {name}"
            raise InputError(msg)
        return screen

    async def read_form(request: Request) -> Form:
        body = (await request.body()).decode("utf-8")
        return parse_qs(body, keep_blank_values=True)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        runs.cancel_all()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=_PACKAGE / "static"), name="static")

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.headers.get("host") not in hosts:
            return HTMLResponse("Forbidden host.", status_code=403)
        if request.method == "POST" and request.headers.get("origin") not in origins:
            return HTMLResponse("Forbidden origin.", status_code=403)
        return await call_next(request)

    @app.exception_handler(InputError)
    async def input_error(request: Request, error: InputError) -> Response:
        status = 404 if request.method == "GET" else 422
        return page(request, "error.html", status, message=str(error))

    @app.exception_handler(OperationError)
    async def operation_error(request: Request, error: OperationError) -> Response:
        return page(request, "error.html", 500, message=str(error))

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> Response:
        _logger.error("Request %s %s failed", request.method, request.url.path, exc_info=error)
        return page(request, "error.html", 500, message="Unexpected failure. See the server log.")

    @app.get("/")
    async def home(request: Request) -> Response:
        return page(request, "home.html")

    @app.get("/tools/{name}")
    async def tool_page(request: Request, name: str) -> Response:
        screen = screen_for(name)
        return page(request, screen.template, tool=screen.tool)

    @app.post("/tools/{name}")
    async def tool_run(request: Request, name: str) -> Response:
        screen = screen_for(name)
        if screen.run is None:
            msg = f"{name} streams. Start it at /tools/{name}/runs."
            raise InputError(msg)
        form = await read_form(request)
        fragment = await run_in_threadpool(screen.run, form)
        return HTMLResponse(render(fragment))

    @app.post("/tools/{name}/runs")
    async def tool_start(request: Request, name: str) -> Response:
        screen = screen_for(name)
        if screen.stream is None:
            msg = f"{name} does not stream. Run it at /tools/{name}."
            raise InputError(msg)
        stream = screen.stream(await read_form(request))
        run = runs.start(stream)
        return HTMLResponse(render(Fragment("run.html", {"run_id": run.id})))

    @app.get("/runs/{run_id}/events")
    async def run_events(run_id: str) -> Response:
        run = runs.get(run_id)
        if run is None:
            return HTMLResponse("Unknown run.", status_code=404)

        async def events() -> AsyncIterator[str]:
            while (event := await run_in_threadpool(run.next_event)) is not None:
                name = event_name(event)
                yield format_event(name, render(Fragment(f"events/{name}.html", {"event": event})))
            yield format_event("done", "done")

        return StreamingResponse(
            events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    @app.post("/runs/{run_id}/cancel")
    async def run_cancel(run_id: str) -> Response:
        run = runs.get(run_id)
        if run is None:
            return HTMLResponse("Unknown run.", status_code=404)
        run.cancel()
        return Response(status_code=204)

    return app

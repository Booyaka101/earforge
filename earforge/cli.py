"""The earforge command: start the local server and open the browser."""

import socket
import threading
import webbrowser
from pathlib import Path

import typer
import uvicorn

from earforge import __version__
from earforge.library import Library
from earforge.server import MODELS, create_app

app = typer.Typer(
    add_completion=False,
    help="earforge - local learn-by-ear workbench (audio to MIDI, roll, play-along).",
    no_args_is_help=False,
)


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def serve(
    host: str = "127.0.0.1",
    port: int = 8417,
    model: str = "medium",
    open_browser: bool = True,
):
    if model not in MODELS:
        typer.echo(f"unknown model {model!r}; expected one of {MODELS}", err=True)
        raise typer.Exit(1)
    if not _port_free(host, port):
        typer.echo(
            f"port {port} is already in use. Another earforge is probably"
            " running; close it first, or start this one with --port."
            " On Windows, check with: Get-NetTCPConnection -LocalPort"
            f" {port}",
            err=True,
        )
        raise typer.Exit(1)
    url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}"
    if open_browser:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    typer.echo(f"earforge {__version__} listening on {url} (model: {model})")
    library = Library()
    library.init()
    uvicorn.run(create_app(model=model, library=library), host=host, port=port)


@app.callback(invoke_without_command=True)
def default(
    ctx: typer.Context,
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address."),
    port: int = typer.Option(8417, "--port", help="Port to listen on."),
    model: str = typer.Option(
        "medium", "--model", "-m", help="Model size: small, medium or large."
    ),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Do not open a browser tab."
    ),
    version: bool = typer.Option(False, "--version", help="Print version and exit."),
):
    """Start the earforge workbench server and open it in the browser."""
    if version:
        typer.echo(f"earforge {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        serve(host, port, model, open_browser=not no_browser)


@app.command()
def library():
    """List past transcriptions."""
    lib = Library()
    lib.init()
    jobs = lib.list_jobs()
    if not jobs:
        typer.echo(f"no transcriptions yet (storage: {lib.root})")
        return
    for job in jobs:
        dur = job["duration_s"]
        dur_s = f"{int(dur // 60)}:{int(dur % 60):02d}" if dur else "?"
        found = ", ".join(job["instruments_found"]) or "-"
        typer.echo(
            f"{job['created_at']}  {dur_s:>5}  {job['status']:<9}"
            f"  {job['source_name']}  [{found}]"
        )


def main():
    app()


if __name__ == "__main__":
    main()

"""DevOps Guardian CLI."""

import json

import typer
from rich.console import Console
from rich.syntax import Syntax

from devops_guardian.agents.code_analyser.graph import run_analysis

app = typer.Typer(name="devops-guardian", help="Multi-agent DevOps analysis platform.")
console = Console()


@app.command()
def analyse(
    repo_url: str = typer.Argument(..., help="GitHub repository URL to analyse."),
) -> None:
    """Run the Code Analyser agent on a GitHub repository."""
    console.print(f"\n[bold]Analysing:[/bold] {repo_url}\n")

    with console.status("[bold green]Cloning and scanning..."):
        result = run_analysis(repo_url)

    output = json.dumps(result.model_dump(), indent=2)
    syntax = Syntax(output, "json", theme="monokai")
    console.print(syntax)


@app.command()
def run(
    repo_url: str = typer.Argument(..., help="GitHub repository URL."),
) -> None:
    """Run the full DevOps Guardian pipeline (all agents)."""
    from devops_guardian.orchestrator import run_guardian

    console.print(f"\n[bold]Running DevOps Guardian on:[/bold] {repo_url}\n")

    with console.status("[bold green]Running agents..."):
        result = run_guardian(repo_url)

    output = json.dumps(result, indent=2, default=str)
    syntax = Syntax(output, "json", theme="monokai")
    console.print(syntax)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Host to bind to."),
    port: int = typer.Option(8001, help="Port to bind to."),
    reload: bool = typer.Option(False, help="Enable auto-reload for development."),
) -> None:
    """Start the REST API server."""
    import uvicorn

    console.print(f"\n[bold green]Starting API server on {host}:{port}[/bold green]")
    console.print("API docs available at [link]http://{host}:{port}/docs[/link]\n")
    uvicorn.run("devops_guardian.api:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()

"""DevOps Guardian CLI."""

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.syntax import Syntax

from devops_guardian.agents.code_analyser.graph import run_analysis
from devops_guardian.agents.pipeline_generator.graph import run_pipeline_generator
from devops_guardian.models.analysis import RepoAnalysis
from devops_guardian.models.pipeline import PipelineConfig

app = typer.Typer(name="devops-guardian", help="Multi-agent DevOps analysis platform.")
console = Console()

OUTPUT_ROOT = Path("outputs")


def _get_run_dir() -> Path:
    """Return the next run directory: outputs/<date>/run<N>."""
    from datetime import datetime

    date_str = datetime.now().strftime("%d%b%Y").lstrip("0")  # e.g. 7May2026
    date_dir = OUTPUT_ROOT / date_str
    date_dir.mkdir(parents=True, exist_ok=True)

    # Find next run number
    existing = sorted(
        (d for d in date_dir.iterdir() if d.is_dir() and d.name.startswith("run")),
        key=lambda d: int(d.name[3:]) if d.name[3:].isdigit() else 0,
    )
    next_num = int(existing[-1].name[3:]) + 1 if existing else 1
    run_dir = date_dir / f"run{next_num}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


@app.command()
def analyse(
    repo_url: str = typer.Argument(..., help="GitHub repository URL to analyse."),
    branch: str = typer.Option("", "--branch", "-b", help="Branch to clone. Defaults to DEFAULT_BRANCH env var."),
) -> None:
    """Run the Code Analyser agent on a GitHub repository."""
    console.print(f"\n[bold]Analysing:[/bold] {repo_url}\n")

    run_dir = _get_run_dir()
    with console.status("[bold green]Cloning and scanning..."):
        result = run_analysis(repo_url, run_dir=str(run_dir), branch=branch)

    out_path = run_dir / "agent1-code-analyser.json"
    data = result.model_dump()
    out_json = json.dumps(data, indent=2)

    out_path.write_text(out_json)
    console.print(f"[bold green]\u2713[/bold green] Analysis saved to [cyan]{out_path}[/cyan]\n")

    syntax = Syntax(out_json, "json", theme="monokai")
    console.print(syntax)


@app.command()
def generate_pipelines(
    repo_url: str = typer.Argument(None, help="GitHub repository URL (not needed if --analysis-file is given)."),
    analysis_file: str = typer.Option(None, "--analysis-file", "-a", help="Path to a saved Agent 1 JSON output file. Skips re-running Agent 1."),
    no_ci: bool = typer.Option(False, "--no-ci", help="Skip CI pipeline generation."),
    no_coverage: bool = typer.Option(False, "--no-coverage", help="Skip coverage job generation."),
    no_sonarqube: bool = typer.Option(False, "--no-sonarqube", help="Skip SonarQube job generation."),
    no_security: bool = typer.Option(False, "--no-security", help="Skip security pipeline generation."),
    test_categories: list[str] = typer.Option([], "--test-category", "-t", help="Test categories to include (e.g. unit, e2e). Can be repeated. Empty = all discovered."),
) -> None:
    """Run Agent 2 (generate CI/CD pipelines). Optionally skip Agent 1 with --analysis-file."""
    config = PipelineConfig(
        enable_ci=not no_ci,
        enable_coverage=not no_coverage,
        enable_sonarqube=not no_sonarqube,
        enable_security=not no_security,
        test_categories=test_categories,
    )

    if analysis_file:
        path = Path(analysis_file)
        if not path.exists():
            console.print(f"[red]File not found:[/red] {analysis_file}")
            raise typer.Exit(code=1)
        console.print(f"\n[bold]Loading analysis from:[/bold] {analysis_file}\n")
        analysis = RepoAnalysis(**json.loads(path.read_text()))
        # Reuse the same run folder as the analysis file if possible
        run_dir = path.parent if path.parent.name.startswith("run") else _get_run_dir()
    elif repo_url:
        console.print(f"\n[bold]Generating pipelines for:[/bold] {repo_url}\n")
        run_dir = _get_run_dir()
        with console.status("[bold green]Step 1/2 \u2014 Analysing repository..."):
            analysis = run_analysis(repo_url, run_dir=str(run_dir))
        a1_path = run_dir / "agent1-code-analyser.json"
        a1_path.write_text(json.dumps(analysis.model_dump(), indent=2))
        console.print(f"[bold green]\u2713[/bold green] Analysis saved to [cyan]{a1_path}[/cyan]\n")
    else:
        console.print("[red]Provide either a repo URL or --analysis-file.[/red]")
        raise typer.Exit(code=1)

    with console.status("[bold green]Generating pipelines..."):
        result = run_pipeline_generator(analysis, run_dir=str(run_dir), config=config)

    a2_path = run_dir / "agent2-pipeline-generator.json"
    out_json = json.dumps(result.model_dump(), indent=2)
    a2_path.write_text(out_json)
    console.print(f"[bold green]\u2713[/bold green] Pipelines saved to [cyan]{a2_path}[/cyan]\n")

    syntax = Syntax(out_json, "json", theme="monokai")
    console.print(syntax)


@app.command()
def run(
    repo_url: str = typer.Argument(..., help="GitHub repository URL."),
) -> None:
    """Run the full DevOps Guardian pipeline (all agents)."""
    from devops_guardian.orchestrator import run_guardian

    console.print(f"\n[bold]Running DevOps Guardian on:[/bold] {repo_url}\n")

    run_dir = _get_run_dir()
    with console.status("[bold green]Running agents..."):
        result = run_guardian(repo_url, run_dir=str(run_dir))

    # Save Agent 1 output
    if result.get("analysis"):
        a1_path = run_dir / "agent1-code-analyser.json"
        a1_path.write_text(json.dumps(result["analysis"], indent=2))
        console.print(f"[bold green]\u2713[/bold green] Analysis saved to [cyan]{a1_path}[/cyan]")

    # Save Agent 2 output
    if result.get("pipeline_config"):
        a2_path = run_dir / "agent2-pipeline-generator.json"
        a2_path.write_text(json.dumps(result["pipeline_config"], indent=2))
        console.print(f"[bold green]\u2713[/bold green] Pipelines saved to [cyan]{a2_path}[/cyan]")

    console.print(f"\n[bold green]\u2713[/bold green] All outputs saved to [cyan]{run_dir}[/cyan]\n")

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

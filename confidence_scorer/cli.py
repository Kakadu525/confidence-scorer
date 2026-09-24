from __future__ import annotations

import json
import os
import traceback
from pathlib import Path

import click
from rich.console import Console

from confidence_scorer.config import write_default_config
from confidence_scorer.git_diff import WORKTREE, merge_base
from confidence_scorer.i18n import ENV_VAR, SUPPORTED_LANGUAGES, current_language, set_config_language, tr
from confidence_scorer.pipeline import run_pipeline
from confidence_scorer.report.json_report import render_json_report
from confidence_scorer.report.markdown import render_markdown_report
from confidence_scorer.report.terminal import render_terminal_report


@click.group()
@click.version_option(package_name="confidence-scorer")
@click.option(
    "--lang",
    type=click.Choice(SUPPORTED_LANGUAGES),
    default=None,
    help="Output language, overrides `language:` in confidence.yml (also CONFIDENCE_LANG)",
)
def main(lang: str | None) -> None:
    """Confidence score for AI-generated PRs and diffs."""
    if lang:
        # Through the environment so that worker subprocesses inherit it too.
        os.environ[ENV_VAR] = lang


@main.command()
@click.option("--path", default="confidence.yml", show_default=True, help="Where to write the config")
@click.option("--force", is_flag=True, help="Overwrite an existing file")
def init(path: str, force: bool) -> None:
    """Create confidence.yml with default settings and comments."""
    target = Path(path)
    if target.exists() and not force:
        click.echo(
            tr(
                f"{target} already exists. Use --force to overwrite it.",
                f"{target} уже существует. Используйте --force для перезаписи.",
            ),
            err=True,
        )
        raise SystemExit(1)
    write_default_config(target, language=current_language())
    click.echo(tr(f"Created {target}", f"Создан {target}"))


@main.command()
@click.option("--repo", "repo_dir", default=".", show_default=True, help="Path to the git repository")
@click.option("--config", "config_path", default=None, help="Path to confidence.yml (default: looked up in the repo)")
def doctor(repo_dir: str, config_path: str | None) -> None:
    """Show which checks will run with the current keys and installed packages.

    Sends nothing to any API and costs nothing: it only checks whether the
    environment variables are set and the SDKs and Node.js are installed.
    """
    from rich.table import Table
    from rich.text import Text

    from confidence_scorer.config import find_config_file, load_config
    from confidence_scorer.doctor import diagnose

    console = Console()
    try:
        config = load_config(config_path, repo_root=repo_dir)
    except Exception as exc:
        click.echo(tr(f"The config failed validation: {exc}", f"Конфиг не прошёл проверку: {exc}"), err=True)
        raise SystemExit(2) from exc
    set_config_language(config.language)

    found = Path(config_path) if config_path else find_config_file(Path(repo_dir))
    not_found = tr("not found, using default settings", "не найден, используются настройки по умолчанию")
    console.print(f"{tr('Config', 'Конфиг')}: {found if found else not_found}")

    diagnosis = diagnose(config)
    table = Table(title=tr("Check readiness", "Готовность проверок"))
    table.add_column(tr("Check", "Проверка"))
    table.add_column(tr("Status", "Статус"))
    table.add_column(tr("Details", "Детали"))
    for check in diagnosis.checks:
        if check.ready:
            status = Text(tr("ready", "готова"), style="green")
        else:
            status = Text(tr("will not run", "не будет выполнена"), style="yellow")
        label = check.label if check.scored else f"{check.label} *"
        table.add_row(label, status, check.detail)
    console.print(table)
    console.print(
        tr(
            "[dim]* not part of the score directly, but widens property test coverage[/dim]",
            "[dim]* не входит в score напрямую, но расширяет охват property-тестов[/dim]",
        )
    )

    if diagnosis.score_cap is None:
        console.print(
            tr(
                "[green]All checks are ready, the score is not capped by coverage.[/green]",
                "[green]Все проверки готовы, score не ограничен покрытием.[/green]",
            )
        )
    else:
        console.print(
            tr(
                f"[yellow]{diagnosis.coverage * 100:.0f}% of check weight will run, the score will be at most "
                f"{diagnosis.score_cap:.0f}.[/yellow] Missing keys are set through environment variables, "
                f"see \"AI keys and models\" in the README.",
                f"[yellow]Отработает {diagnosis.coverage * 100:.0f}% веса проверок, score будет не выше "
                f"{diagnosis.score_cap:.0f}.[/yellow] Недостающие ключи задаются переменными окружения, "
                f"подробности в разделе README «AI-ключи и модели».",
            )
        )


@main.command()
@click.option("--base", default=None, help="Base ref/commit to compare against (default: merge-base with origin/main)")
@click.option("--head", default=WORKTREE, show_default=True, help="Target ref/commit, or WORKTREE for the working tree")
@click.option("--repo", "repo_dir", default=".", show_default=True, help="Path to the git repository")
@click.option("--config", "config_path", default=None, help="Path to confidence.yml (default: looked up in the repo)")
@click.option("--format", "output_format", type=click.Choice(["terminal", "json", "markdown"]), default="terminal", show_default=True)
@click.option("--json-out", "json_out_path", default=None, help="Also write the JSON report to a file (regardless of --format)")
@click.option("--markdown-out", "markdown_out_path", default=None, help="Also write the Markdown report to a file (regardless of --format)")
@click.option("--fail-under", "fail_under", type=int, default=None, help="Override thresholds.fail_below for the merge gate")
@click.option("--no-gate", is_flag=True, help="Always exit with code 0 (informational only)")
@click.option("--debug", is_flag=True, help="Print the full traceback on a runtime error")
def run(
    base: str | None,
    head: str,
    repo_dir: str,
    config_path: str | None,
    output_format: str,
    json_out_path: str | None,
    markdown_out_path: str | None,
    fail_under: int | None,
    no_gate: bool,
    debug: bool,
) -> None:
    """Run confidence scoring for the diff between --base and --head."""
    console = Console(stderr=False)

    if base is None:
        for candidate in ("origin/main", "origin/master", "main", "master"):
            try:
                base = merge_base(candidate, repo_dir, head=head)
                break
            except Exception:  # noqa: BLE001, S112
                continue
        if base is None:
            click.echo(
                tr(
                    "Could not determine --base automatically. Pass it explicitly.",
                    "Не удалось автоматически определить --base. Укажите его явно.",
                ),
                err=True,
            )
            raise SystemExit(2)

    status_console = Console(stderr=True)
    try:
        with status_console.status(
            tr(
                "[bold cyan]Analyzing the diff: differential tests, semantic diff, AI review…",
                "[bold cyan]Анализ диффа: differential-тесты, semantic diff, AI-ревью…",
            ),
            spinner="dots",
        ):
            result = run_pipeline(base, head, repo_dir=repo_dir, config_path=config_path)
    except Exception as exc:
        click.echo(tr(f"Runtime error: {exc!r}", f"Ошибка выполнения: {exc!r}"), err=True)
        if debug:
            traceback.print_exc()
        else:
            click.echo(
                tr("Run with --debug to see the full traceback.", "Запустите с --debug, чтобы увидеть полную трассировку."),
                err=True,
            )
        raise SystemExit(2) from exc

    json_payload = json.dumps(render_json_report(result), ensure_ascii=False, indent=2)
    markdown_payload = render_markdown_report(result, comment_marker=result.config.github.comment_marker)

    if output_format == "terminal":
        render_terminal_report(result, console)
    elif output_format == "json":
        click.echo(json_payload)
    elif output_format == "markdown":
        click.echo(markdown_payload)

    if json_out_path:
        Path(json_out_path).write_text(json_payload, encoding="utf-8")
    if markdown_out_path:
        Path(markdown_out_path).write_text(markdown_payload, encoding="utf-8")

    if no_gate:
        raise SystemExit(0)

    threshold = fail_under if fail_under is not None else result.config.thresholds.fail_below
    score = result.score
    if score.hard_fail or (score.overall is not None and score.overall < threshold):
        raise SystemExit(1)
    raise SystemExit(0)


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
import traceback
from pathlib import Path

import click
from rich.console import Console

from confidence_scorer.config import write_default_config
from confidence_scorer.git_diff import WORKTREE, merge_base
from confidence_scorer.pipeline import run_pipeline
from confidence_scorer.report.json_report import render_json_report
from confidence_scorer.report.markdown import render_markdown_report
from confidence_scorer.report.terminal import render_terminal_report


@click.group()
@click.version_option(package_name="confidence-scorer")
def main() -> None:
    """Оценка уверенности для AI-сгенерированных PR и диффов."""


@main.command()
@click.option("--path", default="confidence.yml", show_default=True, help="Куда записать конфиг")
@click.option("--force", is_flag=True, help="Перезаписать существующий файл")
def init(path: str, force: bool) -> None:
    """Создать confidence.yml с дефолтными настройками и комментариями."""
    target = Path(path)
    if target.exists() and not force:
        click.echo(f"{target} уже существует. Используйте --force для перезаписи.", err=True)
        raise SystemExit(1)
    write_default_config(target)
    click.echo(f"Создан {target}")


@main.command()
@click.option("--repo", "repo_dir", default=".", show_default=True, help="Путь к git-репозиторию")
@click.option("--config", "config_path", default=None, help="Путь к confidence.yml (по умолчанию ищется в repo)")
def doctor(repo_dir: str, config_path: str | None) -> None:
    """Показать, какие проверки заработают с текущими ключами и установленными пакетами.

    Ничего не отправляет в API и не тратит деньги: только проверяет, заданы ли
    переменные окружения и установлены ли SDK и Node.js.
    """
    from rich.table import Table
    from rich.text import Text

    from confidence_scorer.config import find_config_file, load_config
    from confidence_scorer.doctor import diagnose

    console = Console()
    try:
        config = load_config(config_path, repo_root=repo_dir)
    except Exception as exc:
        click.echo(f"Конфиг не прошёл проверку: {exc}", err=True)
        raise SystemExit(2) from exc

    found = Path(config_path) if config_path else find_config_file(Path(repo_dir))
    console.print(f"Конфиг: {found if found else 'не найден, используются настройки по умолчанию'}")

    diagnosis = diagnose(config)
    table = Table(title="Готовность проверок")
    table.add_column("Проверка")
    table.add_column("Статус")
    table.add_column("Детали")
    for check in diagnosis.checks:
        status = Text("готова", style="green") if check.ready else Text("не будет выполнена", style="yellow")
        label = check.label if check.scored else f"{check.label} *"
        table.add_row(label, status, check.detail)
    console.print(table)
    console.print("[dim]* не входит в score напрямую, но расширяет охват property-тестов[/dim]")

    if diagnosis.score_cap is None:
        console.print("[green]Все проверки готовы, score не ограничен покрытием.[/green]")
    else:
        console.print(
            f"[yellow]Отработает {diagnosis.coverage * 100:.0f}% веса проверок, score будет не выше "
            f"{diagnosis.score_cap:.0f}.[/yellow] Недостающие ключи задаются переменными окружения, "
            f"подробности в разделе README «AI-ключи и модели»."
        )


@main.command()
@click.option("--base", default=None, help="Базовый ref/commit для сравнения (по умолчанию merge-base с origin/main)")
@click.option("--head", default=WORKTREE, show_default=True, help="Целевой ref/commit, или WORKTREE для рабочего дерева")
@click.option("--repo", "repo_dir", default=".", show_default=True, help="Путь к git-репозиторию")
@click.option("--config", "config_path", default=None, help="Путь к confidence.yml (по умолчанию ищется в repo)")
@click.option("--format", "output_format", type=click.Choice(["terminal", "json", "markdown"]), default="terminal", show_default=True)
@click.option("--json-out", "json_out_path", default=None, help="Дополнительно записать JSON-отчёт в файл (независимо от --format)")
@click.option("--markdown-out", "markdown_out_path", default=None, help="Дополнительно записать Markdown-отчёт в файл (независимо от --format)")
@click.option("--fail-under", "fail_under", type=int, default=None, help="Переопределить порог thresholds.fail_below для merge gate")
@click.option("--no-gate", is_flag=True, help="Всегда завершаться с exit code 0 (только информативно)")
@click.option("--debug", is_flag=True, help="Печатать полную трассировку при ошибке выполнения")
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
    """Прогнать confidence-скоринг для диффа между --base и --head."""
    console = Console(stderr=False)

    if base is None:
        for candidate in ("origin/main", "origin/master", "main", "master"):
            try:
                base = merge_base(candidate, repo_dir, head=head)
                break
            except Exception:  # noqa: BLE001, S112
                continue
        if base is None:
            click.echo("Не удалось автоматически определить --base. Укажите его явно.", err=True)
            raise SystemExit(2)

    status_console = Console(stderr=True)
    try:
        with status_console.status(
            "[bold cyan]Анализ диффа: differential-тесты, semantic diff, AI-ревью…", spinner="dots"
        ):
            result = run_pipeline(base, head, repo_dir=repo_dir, config_path=config_path)
    except Exception as exc:
        click.echo(f"Ошибка выполнения: {exc!r}", err=True)
        if debug:
            traceback.print_exc()
        else:
            click.echo("Запустите с --debug, чтобы увидеть полную трассировку.", err=True)
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

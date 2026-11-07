"""Командная строка: python -m web_reader check | read | version."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from . import __version__
from .check import run_checks
from .i18n import t
from .runner import parse_input_lines, run_urls
from .settings import SUPPORTED_LANGUAGES, load_settings


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="config/settings.json", help="файл настроек")
    parser.add_argument("--lang", choices=SUPPORTED_LANGUAGES, help="язык сообщений и отчётов (ru, en, uk)")
    parser.add_argument("--out", default="results", help="папка для результатов")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="web_reader", description="Полное чтение динамических веб-страниц")
    sub = parser.add_subparsers(dest="cmd")
    check = sub.add_parser("check", help="проверка браузера и соединений")
    _common(check)
    check.add_argument("--no-internet", action="store_true", help="не проверять выход в интернет")
    read = sub.add_parser("read", help="прочитать страницы")
    _common(read)
    read.add_argument("urls", nargs="*", help="адреса страниц")
    read.add_argument("--file", help="файл со списком адресов (по одному в строке)")
    read.add_argument("--skip-check", action="store_true", help="не запускать предварительную проверку")
    sub.add_parser("version", help="версия программы")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd in (None, "version"):
        print(f"web_reader {__version__}")
        return 0
    try:
        settings = load_settings(args.config)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    lang = args.lang or settings.language
    out_dir = Path(args.out)

    if args.cmd == "check":
        ok, _ = asyncio.run(run_checks(settings, lang, out_dir, check_internet=not args.no_internet))
        return 0 if ok else 1

    # read
    lines: list[str] = list(args.urls)
    if args.file:
        try:
            lines += Path(args.file).read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            print(f"{args.file}: {exc}", file=sys.stderr)
            return 2
    items, problems = parse_input_lines(lines, lang)
    for problem in problems:
        print(problem, file=sys.stderr)
    if not items:
        print(t(lang, "no_urls"), file=sys.stderr)
        return 2
    if not args.skip_check:
        ok, _ = asyncio.run(run_checks(settings, lang, out_dir))
        if not ok:
            return 1
    summary = asyncio.run(run_urls(items, settings, out_dir, lang, input_problems=problems))
    return 0 if summary.results else 1

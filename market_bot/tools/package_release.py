"""Create a portable source archive, using an explicit set of public files."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=root.parent / "market-analysis-bot-1.0.0.zip"
    )
    args = parser.parse_args()
    names = [
        "README.md",
        "pyproject.toml",
        "constraints.txt",
        ".gitignore",
        ".dockerignore",
        ".env.example",
        "Dockerfile",
        "compose.yaml",
        "MANIFEST.in",
    ]
    files = [root / name for name in names]
    for folder in ["src/market_bot", "tests", "tools", ".github"]:
        files.extend(
            p
            for p in (root / folder).rglob("*")
            if p.is_file() and p.suffix in {".py", ".yml", ".yaml"} and "__pycache__" not in p.parts
        )
    for path in files:
        text = path.read_text(encoding="utf-8")
        if re.search(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b", text):
            raise SystemExit(f"Архив не создан: возможный Telegram-токен в {path.name}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(args.output, "w", ZIP_DEFLATED) as archive:
        for path in sorted(set(files)):
            archive.write(path, "market_bot/" + path.relative_to(root).as_posix())
    print(f"Архив создан: {args.output} ({len(set(files))} файлов)")


if __name__ == "__main__":
    main()

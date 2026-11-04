import json
from pathlib import Path

from app.main import app

TARGETS = (
    Path(__file__).resolve().parents[2] / "openapi.json",
    Path(__file__).resolve().parents[2] / "frontend" / "public" / "openapi.json",
)


def main() -> None:
    content = json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"
    for target in TARGETS:
        target.write_text(content, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()

"""Write the API contract to the repository root."""

import json
from pathlib import Path

from app.main import app


def main() -> None:
    schema = app.openapi()
    target = Path(__file__).resolve().parent.parent.parent / "openapi.json"
    target.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {target} with {len(schema['paths'])} paths")


if __name__ == "__main__":
    main()

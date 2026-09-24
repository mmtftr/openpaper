"""Write the API's OpenAPI schema as JSON (used by the client's `yarn gen:api`).

    uv run python -m app.scripts.export_openapi ../client/src/lib/api/openapi.json
"""

import json
import sys


def openapi_json() -> str:
    from app.main import app

    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "-"
    text = openapi_json()
    if out == "-":
        sys.stdout.write(text)
    else:
        with open(out, "w") as fh:
            fh.write(text)

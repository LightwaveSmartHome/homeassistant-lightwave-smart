"""Fail unless config_flow.py is fully covered and every other module is above 95%."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "coverage.json"
PACKAGE = "custom_components/lightwave_smart/"
CONFIG_FLOW = "config_flow.py"
MINIMUM = 95.0


def main() -> int:
    data = json.loads(REPORT.read_text(encoding="utf-8"))
    files = data.get("files", {})
    failures = []
    seen = False
    for path, payload in sorted(files.items()):
        normalized = path.replace("\\", "/")
        if PACKAGE not in normalized:
            continue
        name = normalized.split(PACKAGE, 1)[1]
        if "/" in name or not name.endswith(".py"):
            continue
        seen = True
        summary = payload["summary"]
        statements = summary["num_statements"]
        missing = payload.get("missing_lines") or []
        if isinstance(missing, int):
            missing = []
        if statements == 0:
            continue
        covered = summary.get("covered_lines")
        if covered is None:
            covered = statements - len(missing)
        percent = 100.0 * covered / statements
        if name == CONFIG_FLOW:
            if missing:
                failures.append(f"{name} is {percent:.2f}% (need 100%). Missing: {missing}")
            continue
        if percent <= MINIMUM:
            failures.append(
                f"{name} is {percent:.2f}% ({covered}/{statements}). Missing: {missing}"
            )
    if not seen:
        failures.append(f"No module coverage found in {REPORT}")
    if failures:
        print("\n".join(failures))
        return 1
    print("Coverage gate passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

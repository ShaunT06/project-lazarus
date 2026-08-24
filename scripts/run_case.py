"""Manual runner: pick one case from data/sample_cases.json by id, run it
through diagnosis -> strategy -> agent -> gate -> tools, print the result.

Requires OPENROUTER_API_KEY in .env - this hits the real API, unlike the
scripted tests. Not part of CI.

Usage: python scripts/run_case.py sub_001
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent import run_case  # noqa: E402
from app.audit import AuditLogger  # noqa: E402
from app.config import settings  # noqa: E402
from app.diagnosis import diagnose  # noqa: E402
from app.models import CaseContext  # noqa: E402
from app.openrouter_client import OpenRouterClient  # noqa: E402
from app.strategy import StrategyEngine  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python scripts/run_case.py <case_id>", file=sys.stderr)
        raise SystemExit(1)

    case_id = sys.argv[1]
    cases = json.loads(Path("data/sample_cases.json").read_text(encoding="utf-8"))
    raw = next((c for c in cases if c["case_id"] == case_id), None)
    if raw is None:
        print(f"no case with id {case_id!r} in data/sample_cases.json", file=sys.stderr)
        raise SystemExit(1)

    case = CaseContext(**raw)
    cause_category = diagnose(case.error_code)
    case.extra["cause_category"] = cause_category

    engine = StrategyEngine.from_file(settings.strategy_config_path)
    strategy = engine.evaluate(case)

    client = OpenRouterClient()
    audit = AuditLogger(Path("data/audit.jsonl"))

    result = run_case(
        case,
        cause_category,
        strategy,
        client,
        audit,
        max_corrections=settings.max_gate_corrections,
        notify_channel=settings.notify_channel,
    )
    client.close()

    print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))
    print(f"\nfull trace: data/audit.jsonl (filter on case_id={case_id!r})")


if __name__ == "__main__":
    main()

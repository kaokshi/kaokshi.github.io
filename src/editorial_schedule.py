"""Fail-closed workflow routing from reviewed, non-secret repository configuration."""
import argparse
import json
import os
from pathlib import Path
import re

from .editorial_memory import read_json
from .editorial_providers import Blocked

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "editorial_production.json"
CRONS = {"0 5 * * *": "daily", "0 8 * * *": "review"}
PRIVATE_REPO = re.compile(r"kaokshi/[a-zA-Z0-9_.-]+")
PUBLIC_KEY = re.compile(r"[0-9a-fA-F]{64}")


def plan(config, *, command, cron="", branch="", isolated=False, test_key="", test_free=False):
    schedule = config["schedule"]
    enabled = schedule["enabled"] is True
    if command == "scheduled":
        if cron not in CRONS or branch != "refs/heads/main":
            raise Blocked("unknown_editorial_schedule")
        if not enabled:
            return {"command": "skip", "schedule": "disabled"}
        command = CRONS[cron]
    if command in {"daily", "retry", "review"} and not isolated:
        if not enabled:
            return {"command": "skip", "schedule": "activation_pending"}
        if branch != "refs/heads/main":
            raise Blocked("production_requires_main")
        if (type(schedule["successful_validation_run"]) is not int
                or schedule["successful_validation_run"] <= 0
                or schedule["owner_access_verified"] is not True
                or schedule["legacy_automation_retired"] is not True
                or schedule["owner_approved"] is not True):
            raise Blocked("activation_evidence_pending")
        repository = schedule["private_repository"]
        if (not PRIVATE_REPO.fullmatch(repository or "") or repository == "kaokshi/kaokshi.github.io"
                or not PUBLIC_KEY.fullmatch(schedule["owner_public_key"] or "")):
            raise Blocked("storage_configuration_pending")
    if isolated and branch != "refs/heads/codex/editorial-production":
        raise Blocked("unsupported_validation_branch")
    repository = "" if isolated else schedule["private_repository"]
    return {"command": command, "schedule": "enabled" if enabled else "disabled",
            "repository": repository or "",
            "public_key": (test_key if isolated else schedule["owner_public_key"]) or "",
            "groq_free": test_free if isolated else schedule["groq_free_only"] is True,
            "klein_free": test_free if isolated else schedule["klein_free_only"] is True}


def verify_rollout_run(run_id):
    from .editorial_delivery import PUBLIC_REPOSITORY, WORKFLOW, gh

    run = json.loads(gh("api", f"repos/{PUBLIC_REPOSITORY}/actions/runs/{run_id}"))
    if (run.get("conclusion") != "success" or run.get("event") != "workflow_dispatch"
            or run.get("path", "").split("@")[0] != ".github/workflows/" + WORKFLOW
            or (run.get("head_repository") or {}).get("full_name") != PUBLIC_REPOSITORY):
        raise Blocked("successful_rollout_run_not_verified")
    jobs = json.loads(gh("api", f"repos/{PUBLIC_REPOSITORY}/actions/runs/{run_id}/jobs?per_page=100"))
    required = {"Execute editorial command", "Seal new checkpoints and available review material",
                "Store only the encrypted state checkpoint", "Store only the encrypted owner review package"}
    if not any(job["name"] == "production" and job["conclusion"] == "success" and
               required <= {step["name"] for step in job["steps"] if step["conclusion"] == "success"}
               for job in jobs.get("jobs", [])):
        raise Blocked("rollout_review_and_checkpoint_steps_unverified")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--github-env", type=Path, required=True)
    args = parser.parse_args()
    try:
        config = read_json(CONFIG)
        isolated = os.environ.get("EDITORIAL_ISOLATED_VALIDATION") == "true"
        result = plan(config, command=os.environ["EDITORIAL_COMMAND"],
                      cron=os.environ.get("EDITORIAL_CRON", ""), branch=os.environ.get("GITHUB_REF", ""),
                      isolated=isolated,
                      test_key=os.environ.get("EDITORIAL_TEST_PUBLIC_KEY", ""),
                      test_free=os.environ.get("EDITORIAL_TEST_FREE_ONLY") == "true")
        if result["command"] in {"daily", "retry", "review"} and not isolated:
            verify_rollout_run(config["schedule"]["successful_validation_run"])
        bindings = {"EDITORIAL_EFFECTIVE_COMMAND": result["command"],
                    "EDITORIAL_DAILY_REPOSITORY": result.get("repository", ""),
                    "EDITORIAL_REVIEW_PUBLIC_KEY": result.get("public_key", ""),
                    "EDITORIAL_GROQ_FREE_ONLY": str(result.get("groq_free", False)).lower(),
                    "EDITORIAL_CF_FREE_ONLY": str(result.get("klein_free", False)).lower()}
        if any("\n" in value or "\r" in value for value in bindings.values()):
            raise Blocked("invalid_workflow_configuration")
        with args.github_env.open("a", encoding="utf-8") as output:
            for key, value in bindings.items():
                output.write(f"{key}={value}\n")
        print(json.dumps({"command": result["command"], "schedule": result["schedule"]}))
    except (Blocked, KeyError, ValueError, OSError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc) if isinstance(exc, Blocked) else type(exc).__name__}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

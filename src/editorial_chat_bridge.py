"""Post factual, content-free GitHub Actions diagnostics to a PRIVATE review issue.

The connected GitHub app can read this issue. This is not a decrypted article
transport. Never include draft prose, source URLs, image bytes or any API key.
"""
import argparse
import json
import os
import re
import subprocess

from .editorial_chat_status import from_state

REPOSITORY = "kaokshi/zen-editorial-review"
ISSUE = 1


def api(token, path, method="GET", data=None):
    command = ["gh", "api", "-X", method, path]
    if data is not None:
        command.extend(["--input", "-"])
    env = {**os.environ, "GH_TOKEN": token, "GIT_TERMINAL_PROMPT": "0"}
    try:
        result = subprocess.run(command, input=json.dumps(data) if data is not None else None,
                                text=True, capture_output=True, timeout=40, env=env,
                                check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("private_status_transport_unavailable") from None
    if result.returncode:
        raise RuntimeError("private_status_transport_rejected") from None
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise RuntimeError("private_status_transport_invalid_json") from None


def publish_status(report, token, *, run_id, call=api):
    if not token or not re.fullmatch(r"[1-9][0-9]{7,14}", str(run_id)):
        raise RuntimeError("private_status_configuration_missing")
    if report.get("schema") != "zen-editorial-chat-status-v1" or report.get(
            "contents_included") is not False or report.get("publication_authorized") is not False:
        raise RuntimeError("private_status_report_not_sanitized")
    repo = call(token, f"repos/{REPOSITORY}")
    if repo.get("full_name") != REPOSITORY or repo.get("visibility") != "private":
        raise RuntimeError("private_status_repo_visibility_failed")
    issue = call(token, f"repos/{REPOSITORY}/issues/{ISSUE}")
    if issue.get("number") != ISSUE or issue.get("state") != "open":
        raise RuntimeError("private_status_issue_unavailable")
    content = json.dumps(report, ensure_ascii=False, indent=2)
    body = ("Автономный диагностический отчёт GitHub Actions. "
            "**Статусы и безопасные коды ошибок, без текстов/изображений.**\n\n"
            f"Run: https://github.com/kaokshi/kaokshi.github.io/actions/runs/{run_id}\n\n"
            "```json\n" + content + "\n```")
    result = call(token, f"repos/{REPOSITORY}/issues/{ISSUE}/comments", method="POST",
                  data={"body": body})
    if not result.get("html_url", "").startswith(
            f"https://github.com/{REPOSITORY}/issues/{ISSUE}#issuecomment-"):
        raise RuntimeError("private_status_write_unconfirmed")
    return {"status": "private_status_sent", "private_issue": ISSUE,
            "run_id": int(run_id), "contents_included": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    report = from_state(args.root, args.date)
    result = publish_status(report, os.environ.get("EDITORIAL_PRIVATE_TOKEN", ""),
                            run_id=os.environ.get("GITHUB_RUN_ID", ""))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

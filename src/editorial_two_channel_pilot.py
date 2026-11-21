#!/usr/bin/env python3
"""Safely run/resume two private Qwen editorial pilots and report actual state.

Does not install model weights, publish, call an external provider, or transmit
article text to GitHub. The local LLM must already be running on loopback.
"""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import http.client
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit

from editorial_length import count_characters, length_finding
from editorial_writer import private_path, topic_key

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "src" / "editorial_pipeline.py"
PROVIDER = ROOT / "src" / "editorial_local_llm.py"


def endpoint_ready(url, timeout=5, backend="llama.cpp"):
    """Check true local endpoint readiness; no remote URL, no body uploads."""
    endpoint = urlsplit(url)
    try:
        valid = (endpoint.scheme == "http" and endpoint.port is not None
                 and ipaddress.ip_address(endpoint.hostname or "").is_loopback
                 and endpoint.username is None and endpoint.password is None
                 and endpoint.path in ("", "/") and not endpoint.query and not endpoint.fragment)
    except ValueError:
        valid = False
    if not valid:
        return {"status": "invalid_endpoint", "reason": "must be a literal loopback HTTP endpoint"}
    conn = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=timeout)
    try:
        conn.request("GET", "/api/tags" if backend == "ollama" else "/health")
        response = conn.getresponse()
        response.read(2048)
        return {"status": "ready" if response.status == 200 else "not_ready",
                "http_status": response.status}
    except (OSError, http.client.HTTPException, TimeoutError):
        return {"status": "not_ready", "reason": "local_server_unavailable"}
    finally:
        conn.close()


def dossier_summary(workspace, channel, topic):
    key = sha256(topic_key(topic).encode("utf-8")).hexdigest()[:16]
    case_dir = workspace / f"{channel}-draft-{key}"
    case_path = case_dir / "case.json"
    if not case_path.is_file():
        status = case_dir / "writer-status.json"
        state = json.loads(status.read_text(encoding="utf-8")) if status.is_file() else {}
        return {"channel": channel, "status": state.get("status", "not_created"),
                "reason": state.get("reason"), "case_dir": str(case_dir),
                "article_ready_for_owner": False}
    try:
        case = json.loads(case_path.read_text(encoding="utf-8"))
        versions = case["versions"]
        first, last = versions[0], versions[-1]
        recommended = case.get("recommended_text_sha256")
        chosen = next((v for v in versions if v["text_sha256"] == recommended), last)
        article = chosen["article"]
        sha = chosen["text_sha256"]
        count = count_characters(article["body_html"])
        pending = chosen.get("report", {}).get("manual_checks_pending") or []
        handoff = case.get("handoff")
        return {"channel": channel, "status": case.get("decision", "unknown"),
                "case_dir": str(case_dir), "versions": len(versions),
                "original_sha256": first["text_sha256"],
                "draft_sha256": sha, "draft_chars_without_whitespace": count,
                "length_ok": length_finding(count) is None,
                "last_checked_sha256": last.get("report", {}).get("text_sha256"),
                "last_checks_completed": last.get("report", {}).get("automated_checks_completed") is True,
                "manual_checks_pending": pending, "handoff_recipient": handoff.get("recipient") if isinstance(handoff, dict) else None,
                "handoff_transport": handoff.get("transport_status") if isinstance(handoff, dict) else None,
                "article_ready_for_owner": (length_finding(count) is None
                    and last.get("report", {}).get("automated_checks_completed") is True
                    and sha == last.get("report", {}).get("text_sha256")
                    and case.get("decision") == "owner_review_pending"),
                "publication_authorized": False}
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {"channel": channel, "status": "invalid_case",
                "case_dir": str(case_dir), "reason": type(exc).__name__,
                "article_ready_for_owner": False}


def run_one(workspace, channel, topic, *, research_query=None, source_documents=None,
            provider=PROVIDER, runner=subprocess.run):
    cmd = [sys.executable, str(PIPELINE), "--channel", channel, "--topic", topic,
           "--provider", str(provider), "--output", str(workspace)]
    if channel == "science":
        if source_documents is not None:
            cmd.extend(["--source-documents", str(source_documents)])
        else:
            cmd.extend(["--europe-pmc", "--research-query", research_query])
    elif channel == "cinema":
        # Film selection is autonomous; do not accept unverified screenplay
        # guesses as an evidence substitute. A supplied private source bundle
        # can be resumed without a network lookup.
        if source_documents is not None:
            cmd.extend(["--source-documents", str(source_documents)])
        else:
            cmd.append("--movie-research")
    try:
        completed = runner(cmd, cwd=ROOT, capture_output=True, text=True,
                           timeout=1800, check=False)
        execution = {"exit_code": completed.returncode,
                     "pipeline_status": "exited" if completed.returncode == 0 else "incomplete_or_error",
                     "stdout_excerpt": completed.stdout[-700:],
                     "stderr_excerpt": completed.stderr[-350:]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        execution = {"pipeline_status": "interrupted", "error": type(exc).__name__}
    return {**dossier_summary(workspace, channel, topic), "execution": execution}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace", type=Path, required=True,
                   help="Private directory outside the public repository")
    p.add_argument("--legends-topic", required=True)
    p.add_argument("--science-topic", required=True)
    p.add_argument("--science-query", help="Search query for public research abstracts")
    p.add_argument("--source-documents", type=Path, help="Private JSON source-document bundle")
    p.add_argument("--provider", type=Path, default=PROVIDER)
    p.add_argument("--status-only", action="store_true")
    args = p.parse_args()
    workspace = private_path(args.workspace)
    workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not topic_key(args.legends_topic) or not topic_key(args.science_topic):
        p.error("Both topics must be nonempty")
    if not args.status_only and not (args.source_documents or args.science_query):
        p.error("Science requires --science-query or --source-documents")
    if args.source_documents and not args.source_documents.is_file():
        p.error("Source documents must exist")
    health = {"status":"not_checked"}
    if not args.status_only:
        backend = os.getenv("ZEN_LLM_BACKEND", "llama.cpp")
        health = endpoint_ready(os.getenv("ZEN_LLM_URL", "http://127.0.0.1:11434" if backend == "ollama" else "http://127.0.0.1:8093"), backend=backend)
    rows = []
    for channel, topic in (("legends", args.legends_topic), ("science", args.science_topic)):
        if args.status_only or health["status"] != "ready":
            result = dossier_summary(workspace, channel, topic)
            result["execution"] = {"pipeline_status": "skipped",
                                   "reason": "status_only" if args.status_only else "llm_not_ready"}
        else:
            result = run_one(workspace, channel, topic, research_query=args.science_query,
                             source_documents=args.source_documents, provider=args.provider)
        rows.append(result)
    manifest = {"schema": "zen-two-channel-pilot-v1",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "llm_health": health, "channels": rows,
                "publication_authorized": False, "rss_authorized": False,
                "delivery_status": "not_connected",
                "note": "Actual local results only; completed checks do not imply owner approval."}
    output = workspace / "two-channel-pilot-status.json"
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(output, 0o600)
    print(json.dumps({"report":str(output),"health":health,
                      "channels":[{"channel":x["channel"],"status":x["status"],
                                   "ready_for_owner":x["article_ready_for_owner"]} for x in rows]},ensure_ascii=False))
    if not args.status_only and health["status"] != "ready":
        return 2
    return 0 if args.status_only or all(x["execution"].get("exit_code") == 0 for x in rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())

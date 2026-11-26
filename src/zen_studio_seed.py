"""Read owner-uploaded native Studio seed packages without promoting their trust."""
import base64
import gzip
import hashlib
import io
import json
import re

CHANNELS = {"cinema", "science", "legends"}
MAX_RAW = 10_000_000
MAX_ENVELOPE = 15_000_000

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")

def decode_seed(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_ENVELOPE:
        raise ValueError("invalid_seed_size")
    env = json.loads(raw)
    if not isinstance(env, dict) or env.get("schema") != "zen-studio-seed-envelope/v1" or env.get("encoding") != "gzip+base64" or env.get("usable_for_editorial_advice") is not False:
        raise ValueError("invalid_seed_envelope")
    digest = env.get("sha256")
    if not isinstance(digest,str) or not re.fullmatch(r"[0-9a-f]{64}",digest):
        raise ValueError("invalid_seed_digest")
    with gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode(env["payload"],validate=True))) as stream:
        data = stream.read(MAX_RAW+1)
    if len(data)>MAX_RAW or hashlib.sha256(data).hexdigest()!=digest:
        raise ValueError("seed_integrity_error")
    seed=json.loads(data)
    if seed.get("schema")!="zen-studio-owner-seed/v1" or seed.get("source")!="owner_uploaded_xlsx" or seed.get("usable_for_editorial_advice") is not False:
        raise ValueError("invalid_seed_payload")
    reports=seed.get("reports")
    if not isinstance(reports,list) or len(reports)!=3 or not all(isinstance(r,dict) for r in reports) or {r.get("channel") for r in reports}!=CHANNELS:
        raise ValueError("invalid_seed_channels")
    ids=set()
    for r in reports:
        if r.get("schema")!="zen-dzen-studio/v1" or r.get("source")!="dzen_studio_xlsx" or r.get("channel_binding")!="title_and_internal_id_only" or r.get("period_basis")!="event_time" or r.get("granularity")!="period":
            raise ValueError("invalid_seed_report")
        if r.get("ctr") is not None or r.get("revenue") is not None:
            raise ValueError("unsupported_metrics")
        body=dict(r)
        key=body.pop("snapshot_id",None)
        file_digest=body.pop("file_sha256",None)
        if key!=hashlib.sha256(canonical(body)).hexdigest() or not isinstance(file_digest,str) or not re.fullmatch(r"[0-9a-f]{64}",file_digest):
            raise ValueError("invalid_report_integrity")
        a,d=r["all_platforms"],r["dzen_platform"]
        if any(a.get(k)!=d.get(k) for k in ("start","end","channel_id")):
            raise ValueError("inconsistent_report_identity")
        cid=a.get("channel_id")
        if not isinstance(cid,str) or not re.fullmatch(r"[a-f0-9]{24}",cid) or cid in ids:
            raise ValueError("invalid_channel_identity")
        ids.add(cid)
    return seed

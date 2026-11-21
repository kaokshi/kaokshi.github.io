"""Private, resumable Cloudflare scene review. A pending request is never replayed."""
from contextlib import contextmanager
import fcntl
from hashlib import sha256
import json
import os
import re

from editorial_cloud_budget import BudgetUnavailable
from editorial_cloudflare import MODEL, generate_scene
from editorial_cloudflare_refine import MODEL as KLEIN, refine_image
from editorial_cloudflare_vision import review_pair
from editorial_image_guardrails import detect_severe_crop
from editorial_revision import save_case
from editorial_scene_contract import controlled_edit_prompt, fingerprint, select_reviewed_variant, structural_verdict
from editorial_visual_style import scene_prompt
from editorial_writer import Pending, private_path


def pending(reason):
    raise Pending("media_production_pending", reason)


@contextmanager
def scene_lock(directory):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (directory / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def checkpoint_call(directory, state, stage, call):
    record = state.get(stage)
    if record:
        if record["status"] != "received":
            pending(f"{stage}: предыдущий запрос требует сверки в {directory}")
        data = (directory / stage).read_bytes()
        if sha256(data).hexdigest() != record["sha256"]:
            pending(f"{stage}: файл не прошёл проверку SHA-256")
        return data, record["metadata"]
    state[stage] = {"status": "requested"}
    save_case(directory / "state.json", state)
    try:
        data, metadata = call()
    except BudgetUnavailable:
        del state[stage]
        save_case(directory / "state.json", state)
        raise
    with (directory / stage).open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    state[stage] = {"status": "received", "sha256": sha256(data).hexdigest(), "metadata": metadata}
    save_case(directory / "state.json", state)
    return data, metadata


def style_anchor(root, digest):
    if digest is None:
        return None
    if not isinstance(digest, str) or not re.fullmatch("[a-f0-9]{64}", digest):
        pending("Недопустимый SHA-256 эталона")
    directory = root / ".scene-anchors" / digest
    path = directory / "reference"
    record = directory / "review.json"
    if (not directory.resolve().is_relative_to(root) or record.is_symlink() or path.is_symlink()
            or not record.is_file() or not path.is_file() or path.stat().st_size > 15_000_000):
        pending("Отсутствует проверенный эталон персонажа или стиля")
    permission = json.loads(record.read_text())
    data = path.read_bytes()
    if not (permission.get("upload_to_cloudflare") is True and permission.get("reviewer")
            and permission.get("rights_basis") and permission.get("sha256") == digest == sha256(data).hexdigest()):
        pending("Права и SHA-256 эталона требуют проверки")
    return data


def prepare_scene(row, root):
    root = private_path(root)
    from editorial_media import continuity_prompt
    prompt = scene_prompt(continuity_prompt(row))
    contract = row.get("scene_contract")
    edit = os.environ.get("ZEN_IMAGE_FINAL_KLEIN", "false").lower() == "true" and contract is not None
    edit_prompt = None
    if edit and contract.get("fix", "").strip():
        if contract.get("scene") != row["prompt"].strip() or not all(
            contract.get(field) for field in ("subject", "location", "action", "must_keep")
        ):
            pending("Для Klein нужен полный контракт именно этой сцены")
        edit_prompt = controlled_edit_prompt(contract)
    anchor_digest = row.get("style_reference_sha256") if edit_prompt else None
    anchor = style_anchor(root, anchor_digest)
    rights = root / "model-rights.json"
    if rights.is_symlink() or not rights.is_file():
        pending("Отсутствует частный реестр проверенных прав model-rights.json")
    entries = json.loads(rights.read_text())
    models = [MODEL, KLEIN] if edit_prompt else [MODEL]
    for model in models:
        entry = entries.get(model, {})
        if not (entry.get("approved") is True and entry.get("reviewer") and
                str(entry.get("source_url", "")).startswith("https://") and entry.get("license")):
            pending("Отсутствует подтверждение лицензии для " + model)
    # The same source remains available when a reviewer adds or corrects a defect contract.
    source_key = sha256(json.dumps([MODEL, row["id"], prompt, row.get("replaces")],
                                   ensure_ascii=False).encode()).hexdigest()
    source_dir = root / ".scene-originals" / source_key
    if not source_dir.resolve().is_relative_to(root):
        pending("Оригинал должен храниться в приватном рабочем каталоге")
    with scene_lock(source_dir):
        path = source_dir / "state.json"
        state = json.loads(path.read_text()) if path.exists() else {"id": row["id"], "scene": row["prompt"]}
        original, metadata = checkpoint_call(source_dir, state, "original", lambda: generate_scene(prompt))
    credit = {**metadata, "visual_style": "zen-legends-visual-profile-v1",
              "license": entries[MODEL]["license"], "model_rights": {m: entries[m] for m in models}}
    credit["original_sha256"] = sha256(original).hexdigest()
    if not edit_prompt:
        return original, credit
    if row.get("edit_original_sha256") != credit["original_sha256"]:
        pending("Привяжите дефект к SHA-256 просмотренного оригинала " + credit["original_sha256"])
    key = sha256(json.dumps([source_key, sha256(original).hexdigest(), fingerprint(contract), KLEIN,
                             edit_prompt, anchor_digest], ensure_ascii=False).encode()).hexdigest()
    directory = root / ".scene-reviews" / key
    if not directory.resolve().is_relative_to(root):
        pending("Кандидат должен храниться в приватном рабочем каталоге")
    with scene_lock(directory):
        path = directory / "state.json"
        state = json.loads(path.read_text()) if path.exists() else {"original_sha256": sha256(original).hexdigest(),
                                                                   "scene_contract": contract}
        if not (directory / "original").exists():
            os.link(source_dir / "original", directory / "original")
        candidate, meta = checkpoint_call(directory, state, "candidate", lambda: refine_image(original, edit_prompt, style_reference=anchor))
        state["style_reference_sha256"] = anchor_digest
        state["scene_contract_sha256"] = fingerprint(contract)
        state["structure"] = structural_verdict(original, candidate)
        state["crop"] = detect_severe_crop(original, candidate)
        save_case(path, state)
        if "reject" in (state["structure"]["status"], state["crop"]["status"]):
            return original, dict(credit, selection="original", reason="technical_rejection")
        if os.environ.get("ZEN_IMAGE_VISION_PILOT") == "true" and "vision" not in state:
            state["vision"] = {"status": "requested"}
            save_case(path, state)
            from editorial_cloudflare_vision import VisionPending
            try:
                state["vision"] = review_pair(original, candidate, contract)
            except BudgetUnavailable:
                del state["vision"]
                save_case(path, state)
                raise
            except VisionPending as exc:
                state["vision"] = {"status": "unavailable", "reason": str(exc)}
            save_case(path, state)
        if state.get("vision", {}).get("status") == "reject_candidate":
            return original, dict(credit, selection="original", reason="vision_rejection")
        decision = directory / "review.json"
        if not decision.is_file():
            pending("Владельцу: сравните original и candidate, сохраните review.json в " + str(directory))
        review = json.loads(decision.read_text())
        if review.get("scene_contract_sha256") != fingerprint(contract) or not review.get("reviewer"):
            pending("Требуется решение владельца для текущего контракта")
        selected, outcome = select_reviewed_variant(original, candidate, review)
        state["review"] = dict(review, **outcome)
        save_case(path, state)
        return selected, dict(credit, **outcome, refinement=meta)

"""Deliver verified private packages to the configured owner for one explicit job."""
import json
import re
from urllib.request import Request
from uuid import uuid4

from src.editorial_memory import digest
from src.editorial_zen_cloud import Conflict
from src.editorial_zen_core import CoreBlocked, STAGES
from src.editorial_zen_transport import bridge_opener


def send_document(token, owner, filename, data):
    boundary = uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{owner}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{filename}"\r\n'
            'Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document\r\n\r\n').encode()
    body += data + f"\r\n--{boundary}--\r\n".encode()
    request = Request(f"https://api.telegram.org/bot{token}/sendDocument", data=body, method="POST",
                      headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with bridge_opener().open(request, timeout=45) as response:
        result = json.loads(response.read(100_001))
    message = result.get("result", {})
    if (result.get("ok") is not True or message.get("chat", {}).get("id") != int(owner)
            or type(message.get("message_id")) is not int or message["message_id"] <= 0):
        raise CoreBlocked("telegram_document_receipt_invalid")
    return message["message_id"]


def deliver_documents(store, articles, job, token, owner, *, send=send_document):
    if (not re.fullmatch(r"agent:[a-f0-9-]{36}", job or "")
            or not re.fullmatch(r"[1-9][0-9]{0,15}", owner or "")
            or not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token or "")):
        raise CoreBlocked("telegram_document_delivery_configuration_invalid")
    counts = {"sent": 0, "unconfirmed": 0}
    for article in articles:
        match = re.fullmatch(re.escape(job) + r"/(cinema|legends|science)/([1-9])", article["id"])
        if not match or [(row["id"], row["status"]) for row in article["stages"]] != [
                (stage, "passed") for stage in STAGES]:
            continue
        prefix = "articles/" + digest(article["id"]) + "/delivery/"
        marker = prefix + "telegram.json"
        recorded, _ = store.read(marker)
        if recorded is not None:
            counts["unconfirmed"] += json.loads(recorded).get("status") != "sent"
            continue
        raw, _ = store.read(prefix + "article.docx")
        manifest, _ = store.read(prefix + "manifest.json")
        receipt = json.loads(manifest) if manifest else {}
        if (not raw or receipt.get("sha256") != digest(raw) or receipt.get("article") != article["id"]
                or receipt.get("publication_authorized") is not False or receipt.get("images") not in (7, 8)):
            raise CoreBlocked("telegram_document_manifest_invalid")
        claim = {"status": "sending", "sha256": receipt["sha256"]}
        try:
            store.write(marker, json.dumps(claim).encode(), None)
        except Conflict:
            continue
        _, revision = store.read(marker)
        try:
            message_id = send(token, owner, f"{match[1]}-{match[2]}.docx", raw)
        except (OSError, ValueError, CoreBlocked):
            # Telegram may have accepted the document before the connection failed.
            # Keep the claim until the owner can reconcile it.
            counts["unconfirmed"] += 1
            continue
        store.write(marker, json.dumps({**claim, "status": "sent", "message_id": message_id}).encode(), revision)
        counts["sent"] += 1
    return counts

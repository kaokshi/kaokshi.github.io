"""Isolated, owner-requested, no-Groq/no-OpenRouter experiment.
Only text adapter changes; article lifecycle comes from editorial_brain.Article.
All owner contents are encrypted to the tester's temporary X25519 public key.
"""
import copy, json, os, sys, traceback, pathlib, zipfile, io, re, secrets
from datetime import datetime
from zoneinfo import ZoneInfo
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey, X25519PrivateKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from src import editorial_brain as brain
from src.editorial_providers import request, authorization, ProviderError
from src.editorial_memory import digest, write

MODEL = "@cf/zai-org/glm-4.7-flash"
original_settings = brain.settings
def trial_settings():
    cfg = copy.deepcopy(original_settings())
    cfg["articles_per_channel"] = {"legends":1,"science":0,"cinema":0}
    cfg["daily_calls"]["cloudflare-text"] = 14
    cfg["per_article_calls"]["cloudflare-text"] = 14
    return cfg
brain.settings = trial_settings

def cf_text(prompt, max_tokens):
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    if not re.fullmatch(r"[0-9a-fA-F]{32}", account):
        raise ProviderError("invalid_cloudflare_account_id", stop=True)
    payload = {"model":MODEL, "max_tokens":min(max_tokens or 3700, 6800),
               "temperature":0.65, "messages":[
                  {"role":"system","content":"Ты редактор российского научно-популярного и художественного издания. Ответь строго по заданию на русском языке. Не добавляй рассуждений о собственном процессе. Для запроса JSON возвращай только JSON."},
                  {"role":"user","content":prompt}]}
    response=request(
       f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1/chat/completions",
       ("api.cloudflare.com",), data=json.dumps(payload,ensure_ascii=False).encode("utf-8"),
       headers={**authorization("CLOUDFLARE_API_TOKEN"),"Content-Type":"application/json"},
       timeout=125)
    value=json.loads(response)
    if not value.get("success", True):
        raise ProviderError("cloudflare_text_unsuccessful_response",stop=True)
    result=value.get("result",value)
    if isinstance(result,dict) and result.get("choices"):
        choice=result["choices"][0]
        content=choice.get("message",{}).get("content") or ""
        if choice.get("finish_reason") in ("length", "max_tokens"):
            raise ProviderError("cloudflare_text_truncated")
    elif isinstance(result,dict):
        content=result.get("response") or result.get("text") or ""
    else:
        content=""
    if not isinstance(content,str) or not content.strip():
        raise ProviderError("cloudflare_text_no_visible_content")
    return {"text":content.strip(),"model":MODEL,"usage":result.get("usage",{}) if isinstance(result,dict) else {}}

def cloudflare_only_model(self, stage, provider, prompt, *, validate=None, tokens=None, metadata=None, repair=False):
    if provider not in ("groq", "openrouter"):
        raise RuntimeError("external_paid_or_unverified_provider_disallowed")
    ceiling = min(tokens or 3700, 6800)
    def run(task):
        existing=self.memory.response(task)
        if existing is not None: return existing
        return self.memory.call(task, self.id, "cloudflare-text", lambda: cf_text(prompt, ceiling))
    result=self.task(stage,"cloudflare-glm-4.7-flash",
        {"model":MODEL,"stage":stage,"source_role":provider,"prompt_sha256":digest(prompt), **(metadata or {})},
        run, minimum_seconds=130,
        validator=(lambda response: validate(brain.json_reply(response))) if validate else None)
    return brain.json_reply(result) if validate else result["text"]

brain.Article.model=cloudflare_only_model
brain.MODELS["groq"]=MODEL
brain.MODELS["openrouter"]=MODEL
os.environ["EDITORIAL_CF_FREE_ONLY"]="true"
os.environ["EDITORIAL_GROQ_FREE_ONLY"]="false"
os.environ.pop("GROQ_API_KEY",None)
os.environ.pop("OPENROUTER_API_KEY",None)
os.environ["GITHUB_ACTIONS"]="true"

root=pathlib.Path(os.environ.get("RUNNER_TEMP","/tmp"))/"zen-cf-only-20261122"
root.mkdir(parents=True,exist_ok=True)
day=datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
summary={"day":day, "model":MODEL,"text_providers_used":["cloudflare-text"],"image_provider":"cloudflare-klein","publication_authorized":False}
try:
    print("Starting existing editorial brain with isolated Cloudflare-only text adapter.",flush=True)
    summary["result"]=brain.execute("daily",root,day=day,validation_window=120,encrypted_mode=True)
except Exception as exc:
    summary["error"]=type(exc).__name__ + ":" + str(exc)
    traceback.print_exc()
finally:
    out=pathlib.Path("benchmark")
    out.mkdir(exist_ok=True)
    data=root/day
    # Share no private prompts, versions or source text in plain GitHub Action logs.
    # Zip only documents, images, review metadata, and the complete state for debugging.
    archive=io.BytesIO()
    with zipfile.ZipFile(archive,"w",zipfile.ZIP_DEFLATED) as z:
        for file in data.rglob("*"):
            if file.is_file():
                z.write(file, file.relative_to(data))
        z.writestr("experiment-summary.json",json.dumps(summary,ensure_ascii=False,indent=2))
    public=X25519PublicKey.from_public_bytes(bytes.fromhex(os.environ["ZEN_TEST_PUBLIC_KEY"]))
    ephemeral=X25519PrivateKey.generate()
    salt=secrets.token_bytes(16)
    key=HKDF(algorithm=hashes.SHA256(), length=32, salt=salt,
             info=b"zen-cloudflare-agent-test-v1").derive(ephemeral.exchange(public))
    nonce=secrets.token_bytes(12)
    encrypted=AESGCM(key).encrypt(nonce,archive.getvalue(),None)
    sealed=b"ZENCF01"+ephemeral.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)+salt+nonce+encrypted
    (out/"owner-review.enc").write_bytes(sealed)
    public_summary={"model":MODEL,"publication_authorized":False,
                    "article_status":summary.get("result",{}).get("articles",{}),
                    "error":summary.get("error"),"encrypted_bytes":len(sealed),
                    "article_count":len(list((data/"review").glob("*/")))}
    (out/"public-status.json").write_text(json.dumps(public_summary,ensure_ascii=False,indent=2),encoding="utf8")
    print("ZEN_CLOUDFLARE_ONLY_PUBLIC_STATUS "+json.dumps(public_summary,ensure_ascii=False),flush=True)

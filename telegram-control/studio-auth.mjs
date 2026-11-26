const PURPOSE = "zen-control:dzen-studio:v1";
const HOSTS = new Set(["dzen.ru", "www.dzen.ru"]);
const MAX_STATE = 90_000;
const encode = new TextEncoder();
const decode = new TextDecoder("utf-8", {fatal: true});
const b64 = bytes => btoa(Array.from(bytes, byte => String.fromCharCode(byte)).join(""));
const unb64 = value => Uint8Array.from(atob(value), char => char.charCodeAt(0));

export function studioRoute(value) {
 const url = new URL(value);
 const match = /^\/profile\/editor\/id\/([a-f0-9]{24})\/publications-stat$/.exec(url.pathname);
 if (url.origin !== "https://dzen.ru" || url.username || url.password || url.search || url.hash || !match)
  throw Error("route_unconfigured");
 return {url: url.href, id: match[1]};
}

export function studioState(raw, missing = "invalid_auth") {
 if (!Array.isArray(raw?.cookies) || !Array.isArray(raw?.origins)) throw Error("invalid_auth");
 const cookies = raw.cookies.filter(cookie => HOSTS.has(cookie?.domain?.replace(/^\./, "")));
 const origins = raw.origins.filter(origin => origin?.origin === "https://dzen.ru" || origin?.origin === "https://www.dzen.ru");
 if (!cookies.some(cookie => cookie.httpOnly === true && cookie.secure === true && cookie.value &&
     (cookie.expires === -1 || cookie.expires > Date.now() / 1000))) throw Error(missing);
 const state = {cookies, origins};
 if (encode.encode(JSON.stringify(state)).length > MAX_STATE) throw Error("invalid_auth");
 return state;
}

export function validEnvelope(value) {
 return value?.version === 1 && typeof value.iv === "string" && /^[A-Za-z0-9+/]{16}$/.test(value.iv)
   && typeof value.ciphertext === "string" && value.ciphertext.length >= 24 && value.ciphertext.length <= 121000
   && /^[A-Za-z0-9+/]+={0,2}$/.test(value.ciphertext) && value.ciphertext.length % 4 === 0;
}

async function authKey(hex) {
 if (!/^[a-f0-9]{64}$/i.test(hex || "")) throw Error("invalid_auth_key");
 return crypto.subtle.importKey("raw", Uint8Array.from(hex.match(/../g), byte => parseInt(byte, 16)),
  {name: "AES-GCM"}, false, ["encrypt", "decrypt"]);
}

export async function sealStudioState(raw, hex, binding) {
 const key = await authKey(hex);
 const iv = crypto.getRandomValues(new Uint8Array(12));
 const additionalData = encode.encode(PURPOSE + ":" + studioRoute(binding).url);
 const bytes = encode.encode(JSON.stringify(studioState(raw)));
 return {version: 1, iv: b64(iv), ciphertext: b64(new Uint8Array(
  await crypto.subtle.encrypt({name: "AES-GCM", iv, additionalData}, key, bytes)))};
}

export async function openStudioState(value, hex, binding) {
 if (!validEnvelope(value)) throw Error("invalid_auth");
 let state;
 try {
  const key = await authKey(hex);
  const bytes = await crypto.subtle.decrypt({name: "AES-GCM", iv: unb64(value.iv),
   additionalData: encode.encode(PURPOSE + ":" + studioRoute(binding).url)}, key, unb64(value.ciphertext));
  state = JSON.parse(decode.decode(bytes));
 } catch { throw Error("invalid_auth"); }
 return studioState(state, "auth_expired");
}

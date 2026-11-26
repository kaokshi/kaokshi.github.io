// Only bounded status codes and booleans may leave this module.
const TRANSIENT = new Set(["HTTP_404", "HTTP_429", "HTTP_502", "HTTP_503", "HTTP_504", "HTTP_TIMEOUT"]);

function terminal(row, sid) {
  return row?.sessionId === sid && Number.isFinite(row.startTime)
    && Number.isFinite(row.endTime) && row.endTime >= row.startTime && row.endTime > 0;
}

async function confirmClose({
  sid, root, call, pause, now = Date.now, expiresAt = now() + 25000,
  latest, observe = () => {}, onDuration = () => {},
}) {
  const detail = root.replace(/\/browser$/, "/session") + "/" + sid;
  const timeout = cap => Math.max(1, Math.min(cap, expiresAt - now()));
  for (let attempt = 0; attempt < 3; attempt++) {
    if (now() >= expiresAt) throw Error("CLOSE_UNCONFIRMED");
    try {
      const data = await call(root + "/" + sid, "DELETE", timeout(3000));
      if (!["closing", "closed"].includes((data.result || data).status)) throw Error("UNKNOWN_CLOSE_STATE");
      observe("delete_acknowledged");
      break;
    } catch (error) {
      if (attempt === 2) throw error;
      await pause(timeout(250));
    }
  }

  function check(row, source) {
    if (!terminal(row, sid)) return false;
    observe(source);
    onDuration(row.endTime - row.startTime);
    if (row.closeReason !== 1 && row.closeReasonText !== "NormalClosure") throw Error("NOT_NORMAL_CLOSURE");
    return true;
  }

  for (let attempt = 0; attempt < 12 && now() < expiresAt; attempt++) {
    let row;
    try {
      const data = await call(detail, "GET", timeout(1500));
      row = Object.hasOwn(data, "result") ? data.result : data;
      if (!terminal(row, sid)) observe(row ? "detail_nonterminal" : "detail_empty");
    } catch (error) {
      if (!TRANSIENT.has(error.message)) throw error;
      observe(error.message === "HTTP_404" ? "detail_not_found" : "detail_transient");
    }
    if (check(row, "detail_terminal")) return true;
    // A deletion can become visible in session history before the detail endpoint.
    if (latest && now() < expiresAt) {
      let recent;
      try {
        recent = await latest(timeout(1500));
      } catch (error) {
        if (!TRANSIENT.has(error.message)) throw error;
        observe("history_transient");
      }
      if (check(recent?.find(row => row.sessionId === sid), "history_terminal")) return true;
    }
    if (now() < expiresAt) await pause(timeout(1200));
  }
  throw Error("CLOSE_UNCONFIRMED");
}

module.exports = {confirmClose};

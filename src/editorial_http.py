"""Bounded HTTPS retrieval for editorial evidence and visual assets."""
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


USER_AGENT = "ZenEditorial/1.0 (+https://github.com/kaokshi/kaokshi.github.io)"


class Blocked(RuntimeError):
    """A dependency prevented safe completion."""


class ProviderError(Blocked):
    def __init__(self, code, *, stop=False, retry_after=None, telemetry=None):
        super().__init__(code)
        self.stop = stop
        self.retry_after = retry_after
        self.telemetry = telemetry or {}


def check_url(url, domains):
    parts = urlsplit(url)
    host = parts.hostname or ""
    if (parts.scheme != "https" or parts.username or parts.password
            or parts.port not in (None, 443)
            or not any(host == d or host.endswith("." + d) for d in domains)):
        raise ProviderError("url_outside_allowed_sources", stop=True)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request(url, domains, *, data=None, headers=None, timeout=25, limit=12_000_000, method=None):
    check_url(url, domains)

    class RestrictedRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, response_headers, newurl):
            check_url(newurl, domains)
            if headers and any(h.lower() == "authorization" for h in headers):
                raise ProviderError("authenticated_redirect_refused", stop=True)
            return super().redirect_request(req, fp, code, msg, response_headers, newurl)

    req = Request(url, data=data, method=method, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with build_opener(RestrictedRedirect()).open(req, timeout=timeout) as response:
            body = response.read(limit + 1)
            if len(body) > limit:
                raise ProviderError("oversize_response")
            return body
    except HTTPError as exc:
        codes = {401: "authorization", 402: "payment_required", 403: "forbidden",
                 429: "quota_or_rate_limit"}
        raise ProviderError(f"http_{exc.code}_{codes.get(exc.code, 'request_failed')}",
                            stop=exc.code in codes) from None
    except (URLError, TimeoutError, OSError):
        raise ProviderError("connection_failed") from None

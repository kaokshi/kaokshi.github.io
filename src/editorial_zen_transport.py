"""Identified HTTPS transport for the ZEN Control executor, never a substitute for authentication."""
from urllib.request import HTTPRedirectHandler, build_opener

USER_AGENT = "ZEN-Control-Executor/1.0"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def bridge_opener():
    opener = build_opener(NoRedirect())
    opener.addheaders = [("User-Agent", USER_AGENT)]
    return opener

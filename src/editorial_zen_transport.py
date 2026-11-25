"""Identified HTTPS transport for the ZEN Control executor, never a substitute for authentication."""
from urllib.request import build_opener

from .editorial_http import NoRedirect

USER_AGENT = "ZEN-Control-Executor/1.0"


def bridge_opener():
    opener = build_opener(NoRedirect())
    opener.addheaders = [("User-Agent", USER_AGENT)]
    return opener

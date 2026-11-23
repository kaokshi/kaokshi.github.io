#!/usr/bin/env python3
"""Private Wikimedia Commons illustration discovery (never automatic approval).

Searches candidate image metadata only. The editor must inspect each image,
its description page, rights and subject relevance before using it.
"""
import argparse
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


API="https://commons.wikimedia.org/w/api.php"
KNOWN_LICENSES = {
    "CC0": "CC0-1.0",
    "CC0 1.0": "CC0-1.0",
    **{f"CC {code} {version}": f"CC-{code}-{version}"
       for code in ("BY", "BY-SA") for version in ("2.0", "2.5", "3.0", "4.0")},
    "PUBLIC DOMAIN": "PUBLIC_DOMAIN",
    "PD": "PUBLIC_DOMAIN",
}
EXTENSIONS={".jpg",".jpeg",".png",".webp"}


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.data=[]
    def handle_data(self, data):
        self.data.append(data)


def plain(value):
    if not isinstance(value,str):
        return ""
    parser=PlainText()
    parser.feed(value[:12000])
    return html.unescape(" ".join(" ".join(parser.data).split()))[:1200]


def value(metadata, key):
    data=metadata.get(key,{})
    return plain(data.get("value", "")) if isinstance(data,dict) else ""


def candidate(page):
    if not isinstance(page,dict) or page.get("ns")!=6:
        return None
    info=page.get("imageinfo",[])
    if not isinstance(info,list) or not info or not isinstance(info[0],dict):
        return None
    info=info[0]
    title=page.get("title","")
    if (not isinstance(title,str) or not title.startswith("File:")
            or Path(title).suffix.casefold() not in EXTENSIONS):
        return None
    metadata=info.get("extmetadata",{})
    if not isinstance(metadata,dict):
        return None
    license_label=value(metadata,"LicenseShortName")
    license_id=KNOWN_LICENSES.get(license_label.upper())
    pageurl=info.get("descriptionurl","")
    imageurl=info.get("thumburl") or info.get("url", "")
    if (not isinstance(pageurl,str) or not pageurl.startswith("https://commons.wikimedia.org/wiki/")
            or not isinstance(imageurl,str) or urlsplit(imageurl).scheme!="https"
            or urlsplit(imageurl).hostname not in {"upload.wikimedia.org","commons.wikimedia.org"}):
        return None
    return {"title":title,"source_page":pageurl,
            "asset_url":imageurl, "original_asset_url": info.get("url"),
            "license_claimed":license_id,
            "license_status": "recognized_claim_unverified" if license_id else "unknown_or_restricted",
            "license_label":license_label,
            "license_url":value(metadata,"LicenseUrl"),
            "artist":value(metadata,"Artist"),
            "credit":value(metadata,"Credit"),
            "attribution":value(metadata,"Attribution"),
            "attribution_required":value(metadata,"AttributionRequired"),
            "description":value(metadata,"ImageDescription"),
            "restrictions": value(metadata,"Restrictions"),
            "categories": value(metadata,"Categories"),
            "width":info.get("width"),"height":info.get("height"),
            "candidate_only":True,
            "semantic_match_verified":False,
            "license_verified":False,
            "image_inspected":False,
            "editor_approved":False,
            "rights_warning":"All images may be found as candidates, including unknown or restricted licenses. Discovery does not give permission to republish; verify rights separately."}


def search(query, limit=20, opener=urlopen):
    if not isinstance(query,str) or not (2 <= len(query.strip()) <= 150):
        raise ValueError("Provide a short descriptive search phrase")
    if not isinstance(limit,int) or not 1 <= limit <= 50:
        raise ValueError("limit must be 1–50")
    params={"action":"query","format":"json","generator":"search",
            "gsrsearch":query,"gsrnamespace":"6","gsrlimit":str(limit),
            "prop":"imageinfo","iiprop":"url|size|extmetadata","iiurlwidth":"1440",
            "iiextmetadatafilter":"LicenseShortName|LicenseUrl|Artist|Credit|Attribution|AttributionRequired|ImageDescription|Restrictions|Categories"}
    url=API+"?"+urlencode(params)
    request=Request(url,headers={"User-Agent":"ZenEditorialResearch/0.1 (rights metadata research only)"})
    with opener(request,timeout=25) as response:
        payload=json.load(response)
    pages=payload.get("query",{}).get("pages",{})
    if not isinstance(pages,dict):
        raise ValueError("Unexpected Wikimedia API response")
    results=[x for p in pages.values() if (x:=candidate(p))]
    return {"schema":"zen-commons-image-candidates-v1","query":query,
            "provider":"Wikimedia Commons API",
            "candidates":results,
            "count":len(results),
            "approval_status":"owner_review_required",
            "publication_authorized":False}

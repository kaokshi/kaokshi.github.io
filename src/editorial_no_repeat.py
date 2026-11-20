#!/usr/bin/env python3
"""Fail-closed duplicate-topic guard for all three Zen channels.

Compares exact/near titles and known editorial topic anchors from the approved
ten-article packet and the existing cinema corpus. No model or paid API needed.
This checks TOPIC duplication, not plagiarism or semantic similarity of full prose.
"""
from difflib import SequenceMatcher
import re
import unicodedata

STOP={"почему","который","которая","которые","когда","после","перед","через",
      "история","которого","которую","только","самый","самая","ночью","ночной",
      "фильм","фильма","обзор","пересказ","сюжет","загадка","год","года","как",
      "что","это","для","или","над","под","из","без","его","она","где",
      "кто","котором","году","полный","спойлерами","со","почему","когда"}


def normalize(value):
    return " ".join(re.findall(r"[a-zа-я0-9]+",
           unicodedata.normalize("NFKC",str(value)).lower().replace("ё","е")))


def words(value):
    return {w for w in normalize(value).split() if len(w)>=4 and w not in STOP}


# Explicit high-confidence work/topic anchors; covers paraphrases that a simple
# title equality or stem overlap does not catch. Genre scoping avoids unrelated uses.
CATALOG={
    "legends":(
      ("проходн", "журнал"),
      ("последн", "электричк"),
      ("лифт", "звонк"),
      ("окно", "чердак"),
      ("двор", "фотограф"),
    ),
    "science":(
      ("красн", "спрайт"),
      ("свет", "мор"),  # require bioluminescence to avoid any ocean topic
      ("тихоходк",),
      ("темн", "матери"),
      ("полярн", "сияни"),
    ),
    "cinema":(
      ("шоу", "труман"),
      ("прибытие",),
      ("интерстеллар",),
      ("остров", "проклят"),
      ("начало",),
      ("паразит",),
      ("престиж",),
      ("день", "сурк"),
      ("одержимост",),
      ("отель", "будапешт"),
    ),
}


def _catalog_match(channel, topic):
    terms=normalize(topic).split()
    for anchor in CATALOG.get(channel,()):
        if anchor==("свет","мор"):
            if ("биолюминесцен" not in normalize(topic) and
                not any(t.startswith("свет") for t in terms)):
                continue
        if all(any(t.startswith(prefix) for t in terms) for prefix in anchor):
            return True
    return False


def topic_duplicate_reason(channel, topic, prior_titles=()):
    """Return a stable diagnostic code, or None if topic is safe to propose."""
    key=normalize(topic)
    if not key:
        return "topic_empty"
    if _catalog_match(channel,topic):
        return "previously_covered_subject"
    proposed=words(topic)
    for old in prior_titles:
        old_key=normalize(old)
        if not old_key:
            continue
        if key==old_key:
            return "exact_previous_title"
        if min(len(key),len(old_key))>=16 and SequenceMatcher(None,key,old_key).ratio()>=0.82:
            return "near_previous_title"
        known=words(old)
        if len(proposed)>=2 and len(known)>=2:
            union=len(proposed|known)
            if union and len(proposed&known)/union>=0.67:
                return "near_previous_topic"
    return None

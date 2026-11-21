"""Conservative editorial checks for reader interest; NOT actual audience retention.

These cheap static proxies flag repetition, monotonous structure and weak openings.
They cannot score whether humans will enjoy or finish a story, and they never
replace source verification, independent editorial review or Zen analytics.
"""
import re
from collections import Counter

MIN_CHARS=3000
MAX_CHARS=6000
HEADINGS=re.compile(r"(?m)^\*\*[^*\n]{5,120}\*\*\s*$")
WORD=re.compile(r"[а-яёa-z]{3,}",re.I)
SPACE=re.compile(r"\s+")

def text_stats(prose, channel):
    compact=len(SPACE.sub("",re.sub(r"[#*_]","",prose)))
    headings=HEADINGS.findall(prose)
    words=[w.lower().replace("ё","е") for w in WORD.findall(prose)]
    count=len(words)
    # Multiple repetitions with same 5-token sequence are a useful signal,
    # but names and phrases are sometimes intentionally repeated.
    phrases=list(zip(*(words[i:] for i in range(5)))) if count>=5 else []
    freq=Counter(phrases)
    repeated_fivegrams=sum(n-1 for n in freq.values() if n>1)
    sections=re.split(r"(?m)^\*\*[^*\n]{5,120}\*\*\s*$",prose)
    body_sections=[s.strip() for s in sections[1:] if s.strip()]
    lengths=[len(WORD.findall(s)) for s in body_sections]
    # Hook as a proxy: opening must carry information instead of an empty
    # opening paragraph. This is not a semantic judgment of intrigue.
    first=re.split(r"\n\s*\n",prose.strip(),maxsplit=1)[0]
    first=re.sub(r"^.*?\n","",first,count=1) if "\n" in first else ""
    opening_words=len(WORD.findall(first))
    last=prose[-550:]
    cta=bool(re.search(r"подпис(?:ывайтесь|аться|ку|чиков|аться)",last,re.I))
    normalized_paragraphs=[" ".join(WORD.findall(p.lower())) for p in
                           re.split(r"\n\s*\n",prose)]
    normalized_paragraphs=[p for p in normalized_paragraphs if len(p)>=50]
    repeated_paragraphs=len(normalized_paragraphs)-len(set(normalized_paragraphs))
    max_section_share=(max(lengths)/sum(lengths) if lengths and sum(lengths) else 1.0)
    return {
        "chars_without_whitespace":compact,
        "length_ok":MIN_CHARS<=compact<=MAX_CHARS,
        "bold_section_headers":len(headings),
        "section_headers_ok":4<=len(headings)<=8,
        "subscription_cta_at_end":cta,
        "words":count,
        "lexical_variety_sample":round(len(set(words))/count,3) if count else 0,
        "repeated_fivegrams":repeated_fivegrams,
        "repeated_paragraphs":repeated_paragraphs,
        "largest_section_share":round(max_section_share,3),
        "opening_words_before_first_blank":opening_words,
        "human_interest_verified":False,
        "observed_reader_retention":None,
        "genre":channel,
    }

def quality_flags(stats):
    flags=[]
    if not stats["length_ok"]:
        flags.append("length_outside_3000_6000")
    if not stats["section_headers_ok"]:
        flags.append("section_count_outside_4_8")
    if not stats["subscription_cta_at_end"]:
        flags.append("missing_cta_at_end")
    if stats["repeated_paragraphs"]:
        flags.append("repeated_paragraph")
    if stats["words"]>=350 and stats["repeated_fivegrams"]>max(12,int(stats["words"]*.06)):
        flags.append("excessive_fivegram_repetition")
    if stats["largest_section_share"]>.55:
        flags.append("one_section_dominates")
    # No automated pass/fail for semantic hook quality: an editor must read it.
    return flags

def review_checklist(channel):
    shared=[
        "Заголовок обещает конкретный результат и не вводит в заблуждение",
        "Первый абзац даёт конкретную ситуацию, удивление или ясный вопрос, без пустого вступления",
        "Каждый раздел добавляет новую информацию, действие или поворот, а не повторяет предыдущий",
        "Темп меняется естественно; нет цепочки одинаковых коротких или канцелярских фраз",
        "Переходы между разделами поддерживают интерес без искусственных клиффхэнгеров",
        "Финал отвечает на центральный вопрос и завершает материал",
        "Текст не повторяет ранее опубликованные сюжеты, темы и речевые шаблоны",
    ]
    specific={
        "legends":[
            "Напряжение растёт через новые события, а не многократные обещания страшного",
            "Персонажи действуют причинно и имеют индивидуальные детали, без шаблонных развязок"],
        "science":[
            "В начале есть необычный, но проверяемый факт и понятный вопрос",
            "Любопытство удовлетворяется объяснениями; нет выдуманных исследований и сенсаций"],
        "cinema":[
            "Есть конкретные сцены и анализ фильма без вымышленных деталей",
            "Обзор раскрывает причины смотреть или не смотреть, а не пересказывает синопсис"],
    }
    return shared+specific[channel]

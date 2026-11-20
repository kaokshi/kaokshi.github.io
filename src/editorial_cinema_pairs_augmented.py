#!/usr/bin/env python3
"""Build *research-only* film-domain paired labels from dated human reviews.

For each human-origin historical film review, generate a genuinely new
machine-origin Russian review conditioned ONLY on the film title and sentiment.
Never claim authorship-proof of a scraped human review. Never copy third-party
reviews into repository, logs, persisted model artifacts, or public RSS.

Generated/dated text is a diagnostic proxy for film reviews, NOT long recaps.
"""
import hashlib
import random

SOURCE_DATASET = "MihaoModea/kinopoisk"
GENERATOR_MODEL = "ai-forever/rugpt3small_based_on_gpt2"
SEED = 20261120
MIN_CHARACTERS = 350


def cyrillic_fraction(s):
    letters = [c for c in s if c.isalpha()]
    return sum("а" <= c.lower() <= "я" or c.lower() == "ё" for c in letters) / len(letters) if letters else 0


def historical_reviews(min_count=20):
    from datasets import load_dataset
    ds = load_dataset(SOURCE_DATASET, split="train")
    result = []
    used_movies = set()
    for row in ds:
        movie = str(row.get("movie_name") or "").strip()
        text = row.get("content")
        date = str(row.get("date") or "")
        if not movie or movie in used_movies or date[:4] > "2012" or not (date[:4].isdigit()):
            continue
        if not isinstance(text, str) or len(text) < 900 or cyrillic_fraction(text) < .85:
            continue
        title = str(row.get("title") or "")
        sentiment = str(row.get("grade3") or "Neutral")
        key = hashlib.sha256(movie.encode("utf-8")).hexdigest()
        result.append({"group_id": key, "film_name": movie,
                       "review_human": text[:4500], "human_review_date": date,
                       "sentiment": sentiment, "human_source": SOURCE_DATASET,
                       "title": title})
        used_movies.add(movie)
        if len(result) >= min_count:
            break
    if len(result) < min_count:
        raise RuntimeError(f"Only {len(result)} usable historic film texts")
    return result


def generate_pairs(n=20):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, set_seed
    reviews = historical_reviews(min_count=n)
    set_seed(SEED)
    random.seed(SEED)
    tokenizer = AutoTokenizer.from_pretrained(GENERATOR_MODEL)
    model = AutoModelForCausalLM.from_pretrained(GENERATOR_MODEL)
    model.eval()
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    pairs = []
    with torch.inference_mode():
        for index, review in enumerate(reviews):
            # Crucial: no portion of the human review text in the generation
            # prompt. Otherwise the classifier might learn copying artifacts.
            prompt = (f"Отзыв о фильме «{review['film_name']}». "
                      f"Оценка: {review['sentiment']}.\n"
                      "Сюжет, режиссура и впечатления зрителя: ")
            encoded = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=180)
            output = model.generate(
                **encoded,
                do_sample=True,
                top_k=50,
                top_p=0.92,
                temperature=0.9,
                max_new_tokens=300,
                pad_token_id=tokenizer.eos_token_id,
            )
            generated = tokenizer.decode(output[0][encoded.input_ids.shape[1]:], skip_special_tokens=True).strip()
            if len(generated) < MIN_CHARACTERS or cyrillic_fraction(generated) < .75:
                raise RuntimeError("Generated film text too short or insufficiently Russian: sample "+str(index))
            human = review["review_human"]
            # Length-match without tricking training with systematically
            # shorter AI outputs. Any mismatch is explicitly reported.
            human = human[:len(generated)]
            pair = {
                "pair_id": review["group_id"],
                "movie_group_sha256": review["group_id"],
                "human": human,
                "ai": generated,
                "human_provenance": "pre-2013 published film review, publisher metadata, not independently audited",
                "machine_provenance": "generated here by documented model and seed",
                "model": GENERATOR_MODEL,
                "human_characters": len(human),
                "machine_characters": len(generated),
                "human_sha256": hashlib.sha256(human.encode()).hexdigest(),
                "machine_sha256": hashlib.sha256(generated.encode()).hexdigest(),
            }
            pairs.append(pair)
            print("CINEMA_PAIR_GENERATED",index+1,"lengths",len(human),len(generated),"group",review["group_id"][:12],flush=True)
    return pairs

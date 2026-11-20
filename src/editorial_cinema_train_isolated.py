#!/usr/bin/env python3
"""Run film-text generation and four-detector cinema research in separate processes.

This prevents a retained CPU PyTorch generation model from competing for memory
with ruBERT/ModernBERT in the same job. Everything is local to the temporary
GitHub runner; temporary source reviews are never uploaded or committed.
"""
import argparse
import json
import os
from pathlib import Path

def generate(path):
    from editorial_cinema_pairs_augmented import generate_pairs
    pairs=generate_pairs(n=20)
    if len(pairs)!=20:
        raise RuntimeError("Incomplete cinema generation")
    path.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as writer:
        json.dump(pairs,writer,ensure_ascii=False)
    print("CINEMA_GENERATION_COMPLETE",len(pairs),flush=True)

def train(path,out):
    import editorial_research_v2 as experiment
    import editorial_cinema_pairs_augmented as cinema_data
    if not path.is_file():
        raise RuntimeError("Private generated pairs not found")
    pairs=json.loads(path.read_text(encoding="utf-8"))
    if len(pairs)!=20:
        raise RuntimeError("Expected exactly 20 generated film pairs")
    for p in pairs:
        if not all(isinstance(p.get(k),str) and p[k] for k in ("pair_id","human","ai")):
            raise RuntimeError("Bad generated film pairing")
    cinema_data.generate_pairs=lambda n=20: pairs if n==20 else (_ for _ in ()).throw(ValueError("Unexpected pair count"))
    # Destroy the temporary corpus file before running detector subprocesses.
    path.unlink()
    try:
        experiment.run("cinema",out.resolve())
    except Exception:
        # Print only diagnostic metadata on failures; never publish source text.
        raise

def main():
    p=argparse.ArgumentParser()
    p.add_argument("mode",choices=("generate","train"))
    p.add_argument("--private-pairs",required=True)
    p.add_argument("--out",default="editorial-training-cinema-isolated")
    a=p.parse_args()
    path=Path(a.private_pairs).resolve()
    if a.mode=="generate":
        generate(path)
    else:
        train(path,Path(a.out))

if __name__=="__main__":
    main()

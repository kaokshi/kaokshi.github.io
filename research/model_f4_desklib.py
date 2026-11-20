#!/usr/bin/env python3
"""Read-only Desklib research pilot for 10 Zen draft versions.
English-trained, scores on Russian are exploratory. No publishing.
"""
import hashlib
import json
from pathlib import Path
import re
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from editorial_ai_check import extract_text
SLUGS=["legends-01-prohodnaya-17","legends-02-poslednyaya-elektrichka",
"legends-03-lift-dvoynoy-zvonok","legends-04-okno-na-cherdake",
"legends-05-dvor-na-fotografii","science-01-krasnye-spraity",
"science-02-svetyascheesya-more","science-03-tardigrady",
"science-04-temnaya-materiya","science-05-polyarnoe-siyanie"]
BASELINE_A=[2.92,18.56,14.66,16.24,52.21,20.09,28.12,5.06,8.73,6.77]
# Frozen text hashes from successful, separate Model A run 44765621268.
BASELINE_SHA=["bd809c19cdcafdc500dc507f9fe6b4cd72e60661a8b6f89a0749765e1766c7fb",
"6ba82a9cd684e662075714f98d0c4a45eaf7f803ab0b9e75d8378a8d7885dd86",
"d7a23d66c8cedbc736c8a97b35a659656ba49f04d397f22a2b1055f6aa798a8b",
"70b5461288f107b83047ac8928457a4e68fac311fd96bd342f9dffa51e413921",
"3d41c62826ddcf9a3da28776efea8e86087e5b389892558b14da3952a114f558",
"396c537794c6ff95371d84965638f0f8b523f9f94ab7b9068ad66afa9d863cdd",
"932350751d37d1abc3131d89d79660fd55b5c4c57afef1576e221dab31226515",
"e88ba6dd9ceac69e7f91b8ea28a626daf80408e75cd7b68c0a6ace07b1b8326b",
"3e2da43b263099c7bc91a5091c3233b10fc7214251f1158496466861297d4568",
"b61ea9772a12d345a8e901c0639c938d0f41f5b54915e32d317e14e397fff116"]
def inputs():
    for slug,score,expected in zip(SLUGS,BASELINE_A,BASELINE_SHA):
        a=json.loads((ROOT/"content/articles"/(slug+".json")).read_text("utf-8"))
        txt=extract_text(a["body_html"])
        sha=hashlib.sha256(txt.encode("utf-8")).hexdigest()
        if sha!=expected: raise ValueError("Baseline hash mismatch: "+slug)
        yield slug,txt,sha,score

def predict_all():
    import torch
    import torch.nn as nn
    from transformers import AutoConfig,AutoModel,AutoTokenizer,PreTrainedModel
    class DesklibAIDetectionModel(PreTrainedModel):
        config_class=AutoConfig
        def __init__(self,config):
            super().__init__(config)
            self.model=AutoModel.from_config(config)
            self.classifier=nn.Linear(config.hidden_size,1)
            self.post_init()
        def forward(self,input_ids,attention_mask=None,**kwargs):
            values=self.model(input_ids=input_ids,attention_mask=attention_mask).last_hidden_state
            weights=attention_mask.unsqueeze(-1)
            pooled=(values*weights).sum(1)/weights.sum(1).clamp(min=1)
            return {"logits":self.classifier(pooled)}
    mid="desklib/ai-text-detector-v1.01"
    tokenizer=AutoTokenizer.from_pretrained(mid)
    model=DesklibAIDetectionModel.from_pretrained(mid)
    model.eval()
    print("DESKLIB_LOADED",flush=True)
    for slug,txt,sha,a in inputs():
        ids=tokenizer.encode(txt,add_special_tokens=False)
        chunk_size=510
        chunks=[ids[i:i+chunk_size] for i in range(0,len(ids),chunk_size)]
        probs=[]
        with torch.no_grad():
            for ch in chunks:
                packed=tokenizer.prepare_for_model(ch,return_tensors="pt",
                                                    add_special_tokens=True,
                                                    truncation=True,max_length=512)
                v=model(input_ids=packed["input_ids"],attention_mask=packed["attention_mask"])["logits"]
                probs.append(float(torch.sigmoid(v.squeeze()).item()))
        row={"slug":slug,"sha256":sha,"model_a_index":a,
             "desklib_mean_chunk_score_0_100":round(100*sum(probs)/len(probs),3),
             "desklib_chunk_scores_0_100":[round(100*p,3) for p in probs],
             "tokens":len(ids),"chunks":len(chunks),
             "note":"English-trained classifier; chunk mean is NOT calibrated document probability"}
        print("RESULT "+json.dumps(row,ensure_ascii=False),flush=True)

if __name__=="__main__":
    if "--baseline-only" in sys.argv:
        for slug,txt,sha,a in inputs():
            print("BASELINE "+json.dumps({"slug":slug,"sha256":sha,"model_a_index":a,
                       "characters_without_whitespace":len(re.sub(r"\\s","",txt))},ensure_ascii=False))
    else:
        predict_all()

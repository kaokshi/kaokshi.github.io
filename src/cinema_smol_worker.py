import json,sys,os,time,resource
from pathlib import Path
import torch
from PIL import Image
from transformers import AutoProcessor,AutoModelForImageTextToText

def main():
    model_id=os.environ.get('SMOL_MODEL_ID','HuggingFaceTB/SmolVLM2-2.2B-Instruct')
    processor=AutoProcessor.from_pretrained(model_id)
    model=AutoModelForImageTextToText.from_pretrained(model_id,torch_dtype=torch.bfloat16,_attn_implementation='eager').eval()
    paths=json.loads(Path(sys.argv[1]).read_text())
    captions=[]
    for n,path in enumerate(paths):
        start=time.monotonic()
        with Image.open(path) as img:
            image=img.convert('RGB')
        messages=[{'role':'user','content':[{'type':'image'},{'type':'text','text':'Describe only visible objects, actions, setting and characters in this movie frame in one concise sentence. Do not guess names or plot.'}]}]
        prompt=processor.apply_chat_template(messages,add_generation_prompt=True)
        inputs=processor(text=prompt,images=[image],return_tensors='pt')
        # Match floating-point vision inputs to the actual convolution weights.
        # Token IDs and boolean/integer masks must retain their original dtype.
        vision_dtype=model.model.vision_model.embeddings.patch_embedding.weight.dtype
        inputs['pixel_values']=inputs['pixel_values'].to(dtype=vision_dtype)
        assert inputs['pixel_values'].dtype==vision_dtype
        with torch.inference_mode():
            ids=model.generate(**inputs,max_new_tokens=int(os.environ.get('SMOL_MAX_TOKENS','40')),do_sample=False)
        caption=processor.decode(ids[0,inputs['input_ids'].shape[1]:],skip_special_tokens=True).strip()
        assert caption
        captions.append(caption)
        print('SMOL_FRAME',n+1,'/',len(paths),'seconds',round(time.monotonic()-start,2),flush=True)
    print('SMOL_PEAK_RSS_KIB',resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,flush=True)
    Path(sys.argv[2]).write_text(json.dumps(captions,ensure_ascii=False))
if __name__=='__main__':main()

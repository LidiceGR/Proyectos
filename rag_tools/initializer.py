"""Run-local AST-grounded lexical retrieval index (BM25 baseline).

This is retrieval-augmented grounding, but NOT a semantic embedding index.
No network, model downloads or arbitrary file reads are required.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.artifact_io import write_json


def _tokens(text: str) -> list[str]:
    return [s.casefold() for s in re.findall(r'[A-Za-z_][A-Za-z_0-9]*|\d+', text)]


def rag_is_implemented() -> bool:
    return True


def initialize_rag_index(run_directory: str | Path) -> dict[str, Any]:
    """Index original source-backed SQL snippets and AST-aligned statement lines."""
    root=Path(run_directory).resolve()
    inventory=json.loads((root/'metadata'/'inventory.json').read_text(encoding='utf-8-sig'))
    chunks=[]; seen=set()
    for group in ('procedures','triggers','tables'):
        for obj in inventory.get(group,[]):
            ref=obj.get('source') or {}
            source=ref.get('file')
            if not isinstance(source,str) or not source.startswith('sql_sources/'):
                continue
            path=(root/source).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                continue
            lines=path.read_text(encoding='utf-8-sig').splitlines()
            positions=[(op.get('source') or {}).get('line') for op in obj.get('operations', [])]
            positions=[n for n in positions if isinstance(n,int) and 1<=n<=len(lines)]
            if not positions:
                positions=[int(ref.get('line') or 1)]
            for line in positions:
                begin=max(1,line-3);end=min(len(lines),line+7)
                key=(source,begin,end)
                if key in seen:continue
                seen.add(key)
                body='\n'.join(lines[begin-1:end])
                chunks.append({'chunk_id':f'CHUNK-{len(chunks)+1:05d}',
                               'source_file':source,'object_name':obj.get('name'),
                               'start_line':begin,'end_line':end,'text':body,
                               'terms':dict(Counter(_tokens(body+' '+str(obj.get('name','')))))})
    dest=root/'rag'/'index.json';dest.parent.mkdir(parents=True,exist_ok=True)
    result={'schema_version':'1.0','backend':'lexical_bm25','generated_utc':datetime.now(timezone.utc).isoformat(),
            'note':'Lexical retrieval baseline, NOT sentence-transformer embeddings; verify original SQL before citing.',
            'chunk_count':len(chunks),'chunks':chunks}
    write_json(dest,result)
    return {'index_file':str(dest),'chunks':len(chunks)}


def retrieve_evidence(run_directory: Path, query: str, top_k: int=5) -> dict[str,Any]:
    """Rank AST-aligned local source passages by BM25; include line citations."""
    if not isinstance(query,str) or not query.strip() or len(query)>500:
        return {'tool':'retrieve_evidence','status':'rejected','reason':'Query must be between 1 and 500 characters'}
    if type(top_k) is not int or not 1<=top_k<=10:
        return {'tool':'retrieve_evidence','status':'rejected','reason':'top_k out of bounds'}
    root=Path(run_directory).resolve();path=root/'rag'/'index.json'
    if not path.is_file():
        return {'tool':'retrieve_evidence','status':'not_initialized','matches':[]}
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    chunks=data.get('chunks') or []
    if not chunks:
        return {'tool':'retrieve_evidence','status':'not_found','matches':[]}
    import math
    terms=set(_tokens(query));avg=max(1,sum(sum(c['terms'].values()) for c in chunks)/len(chunks))
    scored=[]
    for c in chunks:
        counts=c['terms'];dl=sum(counts.values());score=0.0
        for term in terms:
            tf=counts.get(term,0)
            if tf:
                df=sum(1 for chunk in chunks if term in chunk['terms'])
                idf=math.log(1+(len(chunks)-df+0.5)/(df+0.5))
                score+=idf*tf*2.2/(tf+1.2*(0.25+0.75*dl/avg))
        if score>0:
            scored.append((score,c))
    scored.sort(key=lambda v:(-v[0],v[1]['source_file'],v[1]['start_line']))
    matches=[{k:v for k,v in c.items() if k!='terms'} | {'score':round(score,4)}
             for score,c in scored[:top_k]]
    return {'tool':'retrieve_evidence','status':'found' if matches else 'not_found',
            'backend':'lexical_bm25','query':query,'matches':matches,
            'note':'Lexical matches are hypotheses, not validated source-to-rule evidence.'}

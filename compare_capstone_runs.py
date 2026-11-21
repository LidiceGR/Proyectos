"""Compare one business-rule candidate across Capstone experiment runs.

Usage (run from project root):
  python compare_capstone_runs.py --workspace workspace --object dbo.RegisterOperation

Only reads existing artifacts. Never runs LLMs or changes workspace data.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

DEFAULT_RUNS = (
    'comparison_01_baseline', 'comparison_02_tools_only',
    'comparison_03_verified_no_rag', 'comparison_04_verified_with_rag',
)
LABELS = (
    '1. Baseline: no tools / no verifier / no RAG',
    '2. ReAct: tools / no verifier / no RAG',
    '3. Multi-agent: tools / verifier / no RAG',
    '4. Multi-agent: tools / verifier / BM25 available',
)


def read(path: Path) -> dict:
    try:
        data=json.loads(path.read_text(encoding='utf-8-sig'))
        return data if isinstance(data,dict) else {}
    except (OSError,ValueError):
        return {}


def task_for_object(run:Path, object_name:str)->str|None:
    tasks=read(run/'research'/'research_tasks.json').get('tasks',[])
    for task in tasks:
        if isinstance(task,dict) and str((task.get('target') or {}).get('object_name','')).casefold()==object_name.casefold():
            return str(task.get('task_id'))
    return None


def locate(run:Path, task_id:str|None)->dict:
    result={'run':run.name,'exists':run.is_dir(),'rule':'','stage':'not run',
            'status':'not run','tool_calls':0,'model_actions':0,'policy_actions':0,
            'rag_index':(run/'rag'/'index.json').is_file(),'rag_action_observed':False,
            'catalog_action_observed':False,'source':''}
    if not result['exists'] or not task_id: return result
    research=run/'research'
    candidates=[]
    for file in sorted((research/'agent_runs').glob(task_id+'_*.json')):
        data=read(file)
        if isinstance(data.get('decision'),dict): candidates.append((data,file))
    if candidates:
        data,file=candidates[-1]
        result.update(rule=str((data.get('decision') or {}).get('candidate_rule') or ''),
                      stage='initial research',status=str(data.get('status') or 'unknown'),
                      source=str(file.relative_to(run)),
                      tool_calls=data.get('tool_actions_executed',0),
                      model_actions=data.get('model_actions_executed',0),
                      policy_actions=data.get('policy_actions_executed',0))
        observations=data.get('observations',[])
        if isinstance(observations,list):
            names=[str((o.get('action') or {}).get('name')) for o in observations if isinstance(o,dict)]
            result['rag_action_observed']='retrieve_evidence' in names
            result['catalog_action_observed']='lookup_catalog_value' in names
    verification=read(research/'verifications'/f'{task_id}.json')
    if verification:
        decision=verification.get('candidate') or {}
        if decision.get('candidate_rule'):result['rule']=str(decision['candidate_rule'])
        result.update(stage='post verification',status=str(verification.get('status') or 'unknown'),
                      source=f'research/verifications/{task_id}.json')
        rounds=verification.get('rounds') or []
        if isinstance(rounds,list):
            names=[str((r.get('action') or {}).get('name')) for r in rounds if isinstance(r,dict) and r.get('phase')=='act_observe']
            result['rag_action_observed']=result['rag_action_observed'] or ('retrieve_evidence' in names)
            result['catalog_action_observed']=result['catalog_action_observed'] or ('lookup_catalog_value' in names)
    return result


def render(results:list[dict], target:str)->str:
    cards=[]
    for title,r in zip(LABELS,results):
        esc=lambda x:html.escape(str(x),quote=True)
        facts=('Index BM25: '+('yes' if r['rag_index'] else 'no')+
               ' | retrieve_evidence executed: '+('yes' if r['rag_action_observed'] else 'no')+
               ' | catalog lookup executed: '+('yes' if r['catalog_action_observed'] else 'no'))
        rule=r['rule'] or 'No candidate rule saved (run missing or no model result).'
        cards.append('<section><h2>'+esc(title)+'</h2><p><b>Status:</b> '+esc(r['status'])+
            ' · <b>Candidate from:</b> '+esc(r['stage'])+'</p><p class="facts">'+esc(facts)+
            '</p><blockquote>'+esc(rule)+'</blockquote><small>'+esc(r['run'])+' — '+esc(r['source'])+'</small></section>')
    return """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Capstone experiment comparison</title><style>body{font:15px system-ui,sans-serif;margin:30px auto;padding:0 18px;max-width:1400px;background:#f5f7fb;color:#182335}header{margin-bottom:25px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}section{background:white;border:1px solid #dbe2ea;border-radius:15px;padding:22px;min-width:0}h2{font-size:17px;margin-top:0}blockquote{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.65;margin:14px 0;border-left:4px solid #8ba5c3;padding:10px 14px;background:#f8fafc}.facts{color:#475569;font-size:13px}small{color:#64748b;overflow-wrap:anywhere}footer{margin-top:28px;color:#64748b}@media(max-width:800px){.grid{grid-template-columns:1fr}}</style></head><body><header><h1>Capstone — Same workflow, four configurations</h1><p>Object: <b>"""+html.escape(target)+"""</b></p><p>This is a comparison of saved outputs, not a correctness score. A BM25 index does not establish that RAG was used; inspect actual tool traces.</p></header><div class="grid">"""+''.join(cards)+"""</div><footer>Source-backed outputs remain provisional until verified and reviewed by a domain expert. Separate controller policy actions from LLM-chosen actions.</footer></body></html>"""


def main()->int:
    p=argparse.ArgumentParser()
    p.add_argument('--workspace',default='workspace')
    p.add_argument('--object',default='dbo.RegisterOperation')
    p.add_argument('--run-ids',nargs=4,default=DEFAULT_RUNS)
    p.add_argument('--output',default='capstone_comparison_report.html')
    args=p.parse_args()
    workspace=Path(args.workspace)
    results=[]
    for run_id in args.run_ids:
        run=workspace/run_id
        task_id=task_for_object(run,args.object)
        results.append(locate(run,task_id))
    out=Path(args.output)
    out.write_text(render(results,args.object),encoding='utf-8')
    print('Comparison saved:',out.resolve())
    for r in results:
        print(f"{r['run']}: {r['status']}, catalog_tool={r['catalog_action_observed']}, "
              f"rag_used={r['rag_action_observed']}, rule={r['rule'][:90]!r}")
    return 0

if __name__=='__main__':
    raise SystemExit(main())

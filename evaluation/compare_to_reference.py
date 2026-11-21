"""Compare a multi-agent run to a HUMAN-VETTED reference set.

Do not treat these surface metrics as semantic truth or substitute a model judge
for human adjudication. Only references with reviewed_by and reviewed=true count.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from typing import Any


def evaluate(run_directory: Path, reference_file: Path) -> dict[str,Any]:
    ref=json.loads(reference_file.read_text(encoding='utf-8-sig'))
    cases=[x for x in ref.get('cases',[]) if x.get('reviewed') is True and x.get('reviewed_by')]
    if not cases:
        raise ValueError('No expert-reviewed cases supplied; accuracy cannot be calculated')
    research=run_directory/'research'
    decisions={}
    for file in (research/'verifications').glob('TASK-*.json'):
        data=json.loads(file.read_text(encoding='utf-8-sig'))
        decisions[data['task_id']]=data
    matching=0;classification_correct=0;escalations_needed=0;escalations_correct=0
    condition_total=0;condition_hits=0;effect_total=0;effect_hits=0
    for example in cases:
        result=decisions.get(example['task_id'])
        if result is None:continue
        matching+=1
        got=result.get('candidate') or {}
        if got.get('classification')==example.get('classification'):
            classification_correct+=1
        condition_set={' '.join(str(x).casefold().split()) for x in got.get('conditions') or []}
        expected=example.get('expected_conditions') or []
        for clause in expected:
            condition_total+=1
            if ' '.join(clause.casefold().split()) in condition_set:condition_hits+=1
        effect=(str(got.get('effect') or '')+' '+str(got.get('observed_behavior') or '')).casefold()
        for term in example.get('required_effect_terms',[]):
            effect_total+=1
            if term.casefold() in effect:effect_hits+=1
        if example.get('escalation_required') is True:
            escalations_needed+=1
            if result['status']!='technically_evidenced':escalations_correct+=1
    return {'schema_version':'1.0','reference_cases_reviewed':len(cases),
            'matched_cases':matching,
            'classification_accuracy':round(classification_correct/matching,4) if matching else None,
            'condition_exact_recall':round(condition_hits/condition_total,4) if condition_total else None,
            'required_effect_term_recall':round(effect_hits/effect_total,4) if effect_total else None,
            'escalation_sensitivity':round(escalations_correct/escalations_needed,4) if escalations_needed else None,
            'not_automatically_measurable':['semantic correctness','full claim groundedness','false positives from unknown rules'],
            'warning':'Proxies based on expert labels and surface matching; inspect evidence to judge semantic correctness.'}


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--run-directory',type=Path,required=True)
    cli.add_argument('--references',type=Path,required=True)
    cli.add_argument('--output',type=Path)
    args=cli.parse_args()
    metrics=evaluate(args.run_directory,args.references)
    text=json.dumps(metrics,indent=2,ensure_ascii=False)
    if args.output:args.output.write_text(text+'\n',encoding='utf-8')
    print(text)

if __name__=='__main__':main()

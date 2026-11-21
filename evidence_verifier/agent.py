"""A second LLM role dedicated to critical evaluation of proposed rules."""

from __future__ import annotations

import json
from pathlib import Path
from string import Template
from typing import Any, Callable

from research_agent.agent import PROJECT_ROOT, SYSTEM_PROMPT
from research_agent.validator import extract_json_object

PROMPT_FILE = PROJECT_ROOT / 'prompts' / 'verify_evidence_v06.txt'
STATUSES = {'accepted', 'needs_research', 'insufficient_evidence'}
ISSUE_TYPES = {'missing_evidence', 'unsupported_claim', 'contradiction', 'omitted_condition', 'omitted_effect', 'unresolved_dependency', 'other'}


def render_verifier_prompt(task: dict[str, Any], candidate: dict[str, Any],
                           observations: list[dict[str, Any]], gates: dict[str, Any]) -> str:
    return Template(PROMPT_FILE.read_text(encoding='utf-8')).substitute(
        task_json=json.dumps({'task_id':task['task_id'], 'target':task['target'],
                              'source':task.get('evidence'), 'sql':task.get('fragment',{}).get('sql'),
                              'steps':task.get('fragment',{}).get('steps'),
                              'related_objects':task.get('related_objects', [])}, ensure_ascii=False),
        candidate_json=json.dumps(candidate, ensure_ascii=False),
        observations_json=json.dumps(observations, ensure_ascii=False),
        gates_json=json.dumps(gates, ensure_ascii=False),
    )


def validate_verification(doc: Any) -> list[str]:
    if not isinstance(doc, dict):
        return ['Verification must be an object']
    errors = []
    if set(doc) != {'verification_status','issues','research_requests','summary'}:
        errors.append('Verifier requires exactly verification_status, issues, research_requests, summary')
    if doc.get('verification_status') not in STATUSES:
        errors.append('Unknown verification_status')
    if not isinstance(doc.get('summary'), str) or not doc['summary'].strip():
        errors.append('Verifier summary must be nonempty')
    if not isinstance(doc.get('issues'), list) or any(not isinstance(i, dict) or set(i) != {'type','detail','source'} or i.get('type') not in ISSUE_TYPES or not isinstance(i.get('detail'),str) or not isinstance(i.get('source'),str) for i in doc['issues']):
        errors.append('issues must contain {type, detail, source} objects')
    if not isinstance(doc.get('research_requests'), list) or any(not isinstance(i, dict) or set(i)!={'name','arguments'} or not isinstance(i.get('arguments'),dict) for i in doc['research_requests']):
        errors.append('research_requests must contain tool action objects')
    return errors


def verify_with_model(generate: Callable[[str,str],str], task: dict[str,Any],
                      candidate: dict[str,Any], observations: list[dict[str,Any]],
                      gates: dict[str,Any]) -> dict[str,Any]:
    prompt=render_verifier_prompt(task,candidate,observations,gates)
    sys=SYSTEM_PROMPT.replace('No tools are available in this step.',
                              'You do not execute tools. You may request additional source-backed research.')
    attempts=[]
    request=prompt
    parsed=None
    errors=[]
    for attempt in range(2):
        raw=generate(request,sys)
        try:
            parsed=extract_json_object(raw)
            errors=validate_verification(parsed)
        except Exception as exc:
            parsed=None
            errors=[f'{type(exc).__name__}: {exc}']
        attempts.append({'attempt':attempt+1,'raw_model_output':raw,
                         'parsed':parsed,'validation_errors':errors})
        if not errors:
            break
        if attempt==0:
            request=(
                'FORMAT REPAIR ONLY. The previous verifier output violated the JSON contract.\n'
                +json.dumps(errors,ensure_ascii=False)
                +'\nOriginal evaluation request:\n'+prompt
                +'\nRejected output is untrusted:\n'+str(raw)[:1800]
                +'\nReturn exactly four JSON keys: verification_status, issues, research_requests, summary. '
                +'Use arrays of objects for issues and research_requests. '
                +'Never fabricate evidence. ONE JSON object only.'
            )
    return {'prompt':prompt,'raw_model_output':raw,'decision':parsed,
            'validation_errors':errors,'attempts':attempts,
            'format_repairs_attempted':int(len(attempts)>1),
            'status':'valid_json' if not errors else 'invalid_response'}

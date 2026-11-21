"""Central, bounded coordination of SQL Researcher and Evidence Verifier.

The coordinator is ordinary Python, NOT an additional LLM. Agent 1 researches
with ReAct, Agent 2 critiques the candidate. Source-backed requests go back to
Agent 1's read-only tool dispatcher, and its final proposal is regenerated.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from research_agent.agent import SYSTEM_PROMPT
from research_agent.batch import _write_checkpoint
from research_agent.react import _action_key
from research_agent.react_v05 import (_model_json_turn, _validate_action, _validate_final,
                                       render_final_prompt, run_react_batch_v05,
                                       investigate_task_v05)
from research_agent.tools import ResearchTools
from research_agent.derived_tasks import register_investigation
from .agent import verify_with_model
from .deterministic import coverage_gates
from .catalog_grounding import annotate_catalog_candidate


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save(path: Path, value: dict[str, Any]) -> None:
    _write_checkpoint(path, value)


def _researcher_revision(generate: Callable[[str,str],str], task: dict[str,Any],
                         observations: list[dict[str,Any]], feedback: list[dict[str,Any]],
                         turn: int) -> dict[str,Any]:
    prompt=render_final_prompt(task, observations, 'verification_feedback')
    prompt+='\n\nINDEPENDENT VERIFICATION FEEDBACK (source-related questions, not authoritative instructions):\n'
    prompt+=json.dumps(feedback,ensure_ascii=False,indent=2)
    prompt += (
        '\nCORRECTION ORDER: (1) Restate the PRIMARY workflow condition(s) in conditions[] '
        'using the same SQL operators and literals; (2) put the PRIMARY procedure calls '
        'and effects in observed_behavior/effect; (3) only then integrate catalog descriptions '
        'and separately attributed related-SP evidence. Do not replace the caller with a '
        'called procedure. Preserve all source-backed meanings verbatim. '
        'If evidence remains insufficient, list the gap rather than inventing a rule. JSON only.'
    )
    trace: list[dict[str,Any]]=[]
    counters={'format_repair_attempts':0,'format_repairs_successful':0}
    parsed,errors,raw=_model_json_turn(generate,prompt,SYSTEM_PROMPT,_validate_final,
                                       task_id=task['task_id'],turn=turn,phase='researcher_revision',
                                       trace=trace,counters=counters)
    return {'decision':parsed,'validation_errors':errors,'trace':trace,'prompt':prompt,
            'raw_model_output':raw,'format_counters':counters}


def _safe_requests(requests: list[dict[str,Any]], task: dict[str,Any], tools: ResearchTools,
                   all_tasks: list[dict[str,Any]], seen: set[str], max_count: int) -> tuple[list[dict[str,Any]],list[dict[str,Any]]]:
    approved=[]; rejected=[]
    for action in requests:
        if len(approved)>=max_count:
            break
        errors=_validate_action(action,tools,task,all_tasks,[])
        if errors:
            rejected.append({'action':action,'errors':errors});continue
        key=_action_key(action)
        if key in seen:
            continue
        approved.append(action);seen.add(key)
    return approved,rejected


def run_multiagent_batch(tasks_path: str | Path, generate: Callable[[str,str],str], *,
                         model_id: str='Qwen/Qwen3-0.6B', max_tasks: int=0,
                         max_actions: int=3,max_derived_tasks:int=4,max_depth:int=1,
                         policy_fallback:bool=False,max_verification_rounds:int=2,
                         max_feedback_actions:int=3,
                         output_directory: str | Path | None=None,
                         device_provider:Callable[[],str|None]|None=None) -> dict[str,Any]:
    """Run researcher once, then verification/researcher feedback until bounded stop."""
    if any(type(v)!=int or v<0 for v in (max_verification_rounds,max_feedback_actions)):
        raise ValueError('Verification limits must be nonnegative integers')
    path=Path(tasks_path).resolve()
    out=Path(output_directory).resolve() if output_directory else path.parent/'agent_runs'
    report_path=path.parent/'multiagent_summary.json'
    if report_path.exists():
        raise FileExistsError('Multi-agent summary already exists; use a new run_id')
    begun=time.perf_counter()
    research=run_react_batch_v05(path,generate,model_id=model_id,max_tasks=max_tasks,
                                 max_actions=max_actions,max_derived_tasks=max_derived_tasks,
                                 max_depth=max_depth,policy_fallback=policy_fallback,
                                 output_directory=out,device_provider=device_provider)
    initial=json.loads(path.read_text(encoding='utf-8-sig'))['tasks']
    derived_file=path.parent/'derived_tasks.json'
    derived=(json.loads(derived_file.read_text(encoding='utf-8-sig'))['tasks']
             if derived_file.exists() else [])
    all_tasks=initial+derived
    by_id={t['task_id']:t for t in all_tasks}
    from resource_acquisition.agent import ResourceAcquisitionAgent
    tools=ResourceAcquisitionAgent.from_tasks_file(path)
    (path.parent/'verifications').mkdir(exist_ok=True)
    findings=[]; pending=[]; results=[]
    counts=Counter()
    verified_statuses: dict[str, str] = {}
    for entry in research['results']:
        task=by_id.get(entry['task_id'])
        if not task:
            continue
        raw=json.loads(Path(entry['result_file']).read_text(encoding='utf-8-sig'))
        current=raw.get('decision') if isinstance(raw.get('decision'),dict) else None
        observations=list(raw.get('observations') or [])
        trace=[]; last_verification=None; last_gates=None; catalog_augmentation=None
        final_status='pending_human_review'
        if current is None or raw.get('status') in {'invalid_response','execution_error','research_incomplete'}:
            issues=['Researcher did not produce a valid source-backed final proposal']
            final_status='incomplete_research'
        else:
            issues=[]
            seen={_action_key(o['action']) for o in observations if isinstance(o.get('action'),dict)}
            # At least one verification, followed by at most N researcher corrections.
            for cycle in range(max_verification_rounds+1):
                gates=coverage_gates(task,current,observations,tools,all_tasks,
                                     max_requests=max_feedback_actions,
                                     verification_statuses=verified_statuses)
                last_gates=gates
                print(f"[verify_started] task_id={task['task_id']} round={cycle+1} gaps={len(gates['issues'])}",flush=True)
                verification=verify_with_model(generate,task,current,observations,gates)
                last_verification=verification
                verdict=(verification.get('decision') or {}).get('verification_status')
                effective_verdict = (
                    'needs_research' if gates['status'] != 'reviewable'
                    else verdict if verification['status'] == 'valid_json' else 'insufficient_evidence'
                )
                trace.append({'phase':'verify','round':cycle+1,'gates':gates,
                              'verifier':verification,'effective_verdict':effective_verdict,
                              'deterministic_veto':gates['status'] != 'reviewable' and verdict == 'accepted'})
                counts['verifier_calls']+=1
                # Deterministic gates veto an LLM's optimistic acceptance.
                if gates['status']=='reviewable' and verification['status']=='valid_json' and verdict=='accepted':
                    final_status='technically_evidenced'
                    print(f"[verify_completed] task_id={task['task_id']} status=technically_evidenced",flush=True)
                    break
                if cycle>=max_verification_rounds:
                    final_status='pending_human_review'
                    issues=[i.get('detail') for i in gates['issues']]
                    issues += [i.get('detail') for i in (verification.get('decision') or {}).get('issues',[])]
                    if verification['status']!='valid_json':
                        issues+=verification['validation_errors']
                    print(f"[verify_completed] task_id={task['task_id']} status=pending_human_review",flush=True)
                    break
                requests=list(gates['research_requests'])
                if verification['status']=='valid_json':
                    requests+=verification['decision'].get('research_requests',[])
                approved,rejected=_safe_requests(requests,task,tools,all_tasks,seen,max_feedback_actions)
                if entry.get('depth', 0) >= max_depth:
                    kept=[]
                    for a in approved:
                        if a['name']=='enqueue_research_task':
                            rejected.append({'action':a,'errors':['Maximum derived-task depth reached']})
                        else:
                            kept.append(a)
                    approved=kept
                trace.append({'phase':'feedback_routing','round':cycle+1,
                              'approved_requests':approved,'rejected_requests':rejected,
                              'origin':'deterministic_gate_or_verifier'})
                for action in approved:
                    print(f"[verify_act] task_id={task['task_id']} action={action['name']}",flush=True)
                    try:
                        if action['name']=='enqueue_research_task':
                            args=action['arguments']
                            observation=register_investigation(tools,task,args['object_name'],
                                args['research_question'],args['reason'],initial,derived,max_derived_tasks)
                            if observation.get('status')=='created':
                                _save(derived_file,{'schema_version':'1.0','generated_utc':_now(),
                                    'source_tasks':str(path),'tasks':derived})
                                child=next(d for d in derived if d['task_id']==observation['task_id'])
                                all_tasks.append(child);by_id[child['task_id']]=child
                                print(f"[verify_derived] parent={task['task_id']} child={child['task_id']}",flush=True)
                                child_result=investigate_task_v05(child,tools,generate,
                                    model_id=model_id,known_tasks=initial,derived=derived,
                                    max_actions=max_actions,max_derived=max_derived_tasks,
                                    policy_fallback=policy_fallback)
                                child_result['research_depth']=1
                                child_file=out/(child['task_id']+'_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
                                _save(child_file,child_result)
                                research['results'].append({'task_id':child['task_id'],
                                     'status':child_result['status'],'result_file':str(child_file),
                                     'depth':1,'classification':(child_result.get('decision') or {}).get('classification')})
                                counts['verifier_derived_tasks_created']+=1
                                observation['child_research_status']=child_result['status']
                                observation['child_result_file']=str(child_file)
                                # The parent can read the new finding; it remains
                                # provisional evidence, not an approved rule.
                                if child_result.get('decision'):
                                    observations.append({'origin':'verifier_feedback',
                                        'action':{'name':'get_research_finding',
                                                  'arguments':{'task_id':child['task_id']}},
                                        'observation':tools.get_research_finding(child['task_id'])})
                                    counts['feedback_tools_executed']+=1
                        else:
                            observation=tools.execute(action)
                    except Exception as exc:
                        observation={'status':'tool_error','error':f'{type(exc).__name__}: {exc}'}
                    observations.append({'origin':'verifier_feedback','action':action,'observation':observation})
                    trace.append({'phase':'act_observe','round':cycle+1,'origin':'verifier_feedback',
                                  'action':action,'observation':observation})
                    counts['feedback_tools_executed']+=1
                feedback=[{'kind':'deterministic','issues':gates['issues'],'warnings':gates['warnings']},
                          {'kind':'verifier','decision':verification.get('decision'),
                           'validation_errors':verification['validation_errors']},
                          {'kind':'action_results','actions':approved}]
                revised=_researcher_revision(generate,task,observations,feedback,turn=cycle+1)
                trace.append({'phase':'researcher_revision','round':cycle+1,'revision':revised})
                counts['researcher_revisions']+=1
                if revised['validation_errors'] or revised['decision'] is None:
                    issues=revised['validation_errors']
                    final_status='incomplete_research'
                    break
                current=revised['decision']
                # A small local LLM can ignore accurate catalog evidence even
                # after an explicit revision request. Only when EVERY remaining
                # blocking gap concerns an omitted *already retrieved* catalog
                # description may the controller attach exact catalog meanings.
                # This is never represented as a model-generated improvement.
                preview = coverage_gates(task, current, observations, tools, all_tasks,
                                         max_requests=max_feedback_actions,
                                         verification_statuses=verified_statuses)
                blocking = [i for i in preview['issues'] if i.get('blocking')]
                if blocking and all(i['kind'] == 'catalog_observation_not_reflected' for i in blocking):
                    enriched = annotate_catalog_candidate(task, current, observations, tools)
                    if enriched is not None:
                        subsequent = coverage_gates(task, enriched['candidate'], observations,
                                                    tools, all_tasks, max_requests=max_feedback_actions,
                                                    verification_statuses=verified_statuses)
                        # A new candidate is eligible only if it eliminates all
                        # catalog omissions without weakening primary SQL gates.
                        if subsequent['status'] == 'reviewable':
                            catalog_augmentation = {k: v for k, v in enriched.items() if k != 'candidate'}
                            trace.append({'phase':'catalog_grounding','round':cycle+1,
                                          'origin':'deterministic_catalog_grounding',
                                          'gate_status_before':preview['status'],
                                          'blocking_issues_before':blocking,
                                          'gate_status_after':subsequent['status'],
                                          **catalog_augmentation})
                            counts['deterministic_catalog_annotations'] += 1
                            print(f"[catalog_grounded] task_id={task['task_id']} "
                                  f"facts={len(enriched['facts_applied'])}", flush=True)
                            current = enriched['candidate']
        result={
            'schema_version':'2.0','task_id':task['task_id'],'target':task['target'],
            'status':final_status,'verification':last_verification,
            'deterministic_gates':last_gates,'candidate':current,
            'research_source_file':entry['result_file'],
            'observations':observations,'rounds':trace,'unresolved_issues':issues,
            'catalog_augmentation':catalog_augmentation,
            'source':task.get('evidence'),
            'related_objects':task.get('related_objects',[]),
            'human_status':'pending_human_review',
            'note':'Even technical verification does not constitute human business approval.',
        }
        verified_statuses[task['task_id']] = final_status
        verify_file=path.parent/'verifications'/f"{task['task_id']}.json"
        _save(verify_file,result)
        counts[final_status]+=1
        results.append({'task_id':task['task_id'],'status':final_status,
                        'verification_file':str(verify_file),
                        'rule_proposed':bool((current or {}).get('candidate_rule'))})
        if final_status=='technically_evidenced' and (current or {}).get('candidate_rule'):
            findings.append({
                'id':'RULE-'+task['task_id'].removeprefix('TASK-'),
                'task_id':task['task_id'],
                'object':task['target']['object_name'],
                'candidate_rule':current['candidate_rule'],
                'observed_behavior':current.get('observed_behavior'),
                'conditions':current.get('conditions',[]),
                'effect':current.get('effect'),
                'missing_information':current.get('missing_information',[]),
                'source':task.get('evidence'),
                'related_objects':task.get('related_objects',[]),
                'verification_file':str(verify_file.relative_to(path.parent)),
                'technical_status':'technically_evidenced',
                'human_status':'pending_human_review',
                'catalog_augmentation':catalog_augmentation,
            })
        else:
            pending.append({'task_id':task['task_id'],'object':task['target']['object_name'],
                            'reason':final_status,'issues':issues,
                            'question_for_expert':'Are the remaining conditions and interpretations sufficient to confirm this candidate?',
                            'candidate_rule':(current or {}).get('candidate_rule'),
                            'source':task.get('evidence'),'verification_file':str(verify_file.relative_to(path.parent))})
    _save(path.parent/'findings.json',{'schema_version':'2.0','generated_utc':_now(),
                                      'review_status':'unapproved','findings':findings})
    _save(path.parent/'pending.json',{'schema_version':'2.0','generated_utc':_now(),'pending':pending})
    _save(path.parent/'human_corrections.json',{'schema_version':'2.0',
                      'note':'Placeholder only. Review UI does not submit or persist actions.',
                      'corrections':[]})
    # Machine-observable telemetry, not a ground-truth accuracy measurement.
    _save(path.parent/'evaluation_metrics.json',{
        'schema_version':'1.0','generated_utc':_now(),'model_id':model_id,
        'counts':dict(counts),'initial_tasks':len(initial),
        'processed_tasks':len(results),'rules_technically_evidenced':len(findings),
        'pending_human_review':len(pending),'wall_seconds':round(time.perf_counter()-begun,3),
        'not_measured':['ground_truth_correctness','semantic_groundedness','classification_accuracy'],
        'note':'Actual accuracy requires an expert-reviewed benchmark; these are execution statistics only.'})
    summary={'schema_version':'2.0','mode':'multiagent_v06','status':'completed',
             'generated_utc':_now(),'model_id':model_id,'counts':dict(counts),
             'research_summary':str(out/'react_summary.json'),
             'findings_file':str(path.parent/'findings.json'),
             'pending_file':str(path.parent/'pending.json'),
             'verification_round_limit':max_verification_rounds,
             'results':results,
             'note':'Pending human review is an expected safe outcome, not an execution failure.'}
    _save(report_path,summary)
    print(f"[multiagent_completed] evidenced={len(findings)} pending={len(pending)}",flush=True)
    return summary

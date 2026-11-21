"""Source-backed, deterministic evidence completeness gates.

These gates never prove that a candidate is semantically correct. They identify
investigable gaps and expose exact source provenance for the verifier model.
"""

from __future__ import annotations

import re
from typing import Any

from research_agent.evidence_checker import check_evidence
from research_agent.react import _action_key, _source_policy_actions
from research_agent.tools import ResearchTools, _identifier
from .primary_anchors import primary_anchor_issues


def _observed_keys(observations: list[dict[str, Any]]) -> set[str]:
    return {_action_key(o['action']) for o in observations if isinstance(o, dict) and isinstance(o.get('action'), dict)
            and (o.get('observation') or {}).get('status') in {'found', 'not_found', 'ambiguous', 'not_ready'}}


def _task_for_object(name: str, tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    matches = [task for task in tasks if _identifier(str((task.get('target') or {}).get('object_name', ''))) == _identifier(name)]
    return matches[0] if len(matches) == 1 else None


def coverage_gates(task: dict[str, Any], decision: dict[str, Any],
                   observations: list[dict[str, Any]], tools: ResearchTools,
                   tasks: list[dict[str, Any]], *, max_requests: int = 3,
                   verification_statuses: dict[str, str] | None = None) -> dict[str, Any]:
    """Check catalog coverage, static calls, field effects and source conditions."""
    base = check_evidence(task, decision)
    issues: list[dict[str, Any]] = [{'kind': 'primary_coverage', 'detail': msg, 'blocking': True} for msg in base['issues']]
    issues.extend(primary_anchor_issues(task, decision))
    warnings: list[str] = list(base['warnings'])
    requests: list[dict[str, Any]] = []
    seen = _observed_keys(observations)

    # Catalog evidence gaps are deterministic: only offer lookups with an
    # actual catalog key column and a literal present in the task's AST.
    for candidate in _source_policy_actions(task, tools):
        if candidate['name'] != 'lookup_catalog_value' or _action_key(candidate) in seen:
            continue
        issues.append({'kind': 'unresolved_catalog_value', 'detail': candidate['arguments'], 'blocking': True})
        requests.append(candidate)

    # A successful catalog lookup must inform the candidate's natural language.
    # Otherwise the act/observe cycle has retrieved evidence without using it.
    # The user-facing rule itself must reflect catalog meanings, not merely
    # the internal observed_behavior field.
    candidate_text = str(decision.get('candidate_rule') or '').casefold()
    source_tables = {_identifier(str(table))
                     for step in (task.get('fragment') or {}).get('steps', [])
                     for table in ([step.get('target_table')] + list(step.get('read_tables') or []))
                     if table}
    if decision.get('classification') == 'candidate_business_rule':
        for entry in observations:
            action, response = entry.get('action') or {}, entry.get('observation') or {}
            if action.get('name') != 'lookup_catalog_value' or response.get('status') != 'found':
                continue
            for match in response.get('matches', []):
                catalog_scope = {_identifier(str(t)) for t in
                                 (next((c.get('applies_to') for c in tools.catalogs
                                        if c.get('name') == match.get('catalog')), None) or [])}
                if catalog_scope and source_tables and catalog_scope.isdisjoint(source_tables):
                    issues.append({'kind': 'catalog_scope_mismatch',
                                   'detail': {'catalog': match.get('catalog'),
                                              'code': match.get('code'),
                                              'source_tables': sorted(source_tables),
                                              'allowed_tables': sorted(catalog_scope)},
                                   'blocking': True})
                    continue
                description = match.get('description')
                if isinstance(description, str) and description.strip() and description.casefold() not in candidate_text:
                    issues.append({'kind': 'catalog_observation_not_reflected',
                                   'detail': {'catalog': match.get('catalog'),
                                              'code': match.get('code'),
                                              'description': description},
                                   'blocking': True})

    # Static cross-procedure calls should have source-backed context, not just
    # a name or an inferred effect from the caller's SQL.
    for ref in task.get('related_objects', []):
        if ref.get('relation') != 'CALLS' or ref.get('type') != 'procedure':
            continue
        target = str(ref.get('name', ''))
        linked = _task_for_object(target, tasks)
        if not linked:
            issues.append({'kind': 'unresolved_called_procedure', 'detail': target, 'blocking': True})
            continue
        action = {'name': 'get_research_finding', 'arguments': {'task_id': linked['task_id']}}
        if _action_key(action) not in seen:
            issues.append({'kind': 'unreviewed_called_procedure', 'detail': {'object': target, 'task_id': linked['task_id']}, 'blocking': True})
            requests.append(action)
        elif verification_statuses is not None and verification_statuses.get(linked['task_id']) != 'technically_evidenced':
            issues.append({'kind': 'unverified_called_procedure',
                           'detail': {'object': target, 'task_id': linked['task_id'],
                                      'technical_status': verification_statuses.get(linked['task_id'], 'not_yet_verified')},
                           'blocking': True})

    # The model may describe an UPDATE yet drop one of its assigned fields.
    # These are potential omissions, not proof of a semantic error.
    body = ' '.join(str(decision.get(key) or '') for key in ('observed_behavior', 'effect', 'candidate_rule')).casefold()
    for step in (task.get('fragment', {}).get('steps') or []):
        eff = step.get('effects') or {}
        for assignment in eff.get('assignments', []):
            column = assignment.get('column')
            if isinstance(column, str) and column and column.casefold() not in body:
                warnings.append(f"Possibly omitted modified column '{column}' at line {step.get('source_line')}")
                # A non-mechanical check; let the verifier decide if paraphrased.
        if step.get('operation') == 'CALL' and step.get('called_object'):
            name = str(step['called_object'])
            if _identifier(name).rsplit('.', 1)[-1] not in _identifier(body):
                warnings.append(f"Called procedure {name} is not explicitly mentioned in the finding")

    # Check the stored-procedure result set is accounted for independently from
    # the integer RETURN code; this is a source-backed check on the full SQL.
    sql = str((task.get('fragment') or {}).get('sql', ''))
    for match in re.finditer(r'(?im)^\s*SELECT\s+(@\w+)\s+(\w+)\s*;?\s*$', sql):
        if match.group(2).casefold() not in body and match.group(1).casefold() not in body:
            issues.append({'kind': 'omitted_result_set', 'detail': match.group(0).strip(), 'blocking': True})

    unique: dict[str, dict[str, Any]] = {}
    for action in requests:
        unique.setdefault(_action_key(action), action)
    return {'status': 'needs_research' if any(i['blocking'] for i in issues) else 'reviewable',
            'issues': issues, 'warnings': warnings, 'research_requests': list(unique.values())[:max_requests],
            'source_coverage': base}

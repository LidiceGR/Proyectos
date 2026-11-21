"""Conservative, traceable catalog annotation for a source-backed candidate.

This is a deterministic *controller* transformation, never an LLM answer.
It does not resolve dependencies, modify SQL predicates or approve candidates.
Only a unique, authentic read-only tool result with explicit table scope and a
matching catalog row can contribute a description. Missing semantic analysis
is not silently filled with an invented claim.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from research_agent.tools import ResearchTools, _identifier, _short_name, _value_equal


def _source_tables(task: dict[str, Any]) -> set[str]:
    names = set()
    for step in (task.get('fragment') or {}).get('steps') or []:
        for raw in ([step.get('target_table')] + list(step.get('read_tables') or [])):
            if isinstance(raw, str) and raw.strip():
                names.add(_identifier(raw))
    return names


def _verified_facts(task: dict[str, Any], observations: list[dict[str, Any]],
                    tools: ResearchTools) -> list[dict[str, Any]]:
    source_tables = _source_tables(task)
    facts: list[dict[str, Any]] = []
    dedup = set()
    for item in observations:
        action, response = (item.get('action') or {}), (item.get('observation') or {})
        if action.get('name') != 'lookup_catalog_value' or response.get('status') != 'found':
            continue
        matches = response.get('matches') or []
        if len(matches) != 1:  # ambiguous/untrusted response cannot enrich a rule
            continue
        match = matches[0]
        if not isinstance(match, dict) or not isinstance(action.get('arguments'), dict):
            continue
        request = action['arguments']
        if (not _value_equal(match.get('code'), request.get('value'))
                or _short_name(str(match.get('key_column') or '')) !=
                   _short_name(str(request.get('column') or ''))):
            continue
        matching = [c for c in tools.catalogs
                    if c.get('name') == match.get('catalog')
                    and c.get('table') == match.get('table')
                    and _short_name(str(c.get('key_column') or '')) ==
                        _short_name(str(match.get('key_column') or ''))]
        if len(matching) != 1:
            continue
        catalog = matching[0]
        allowed = {_identifier(str(x)) for x in catalog.get('applies_to') or []}
        # Do not infer a table mapping from a shared column such as Status.
        if not allowed or not source_tables or allowed.isdisjoint(source_tables):
            continue
        source_rows = [row for row in catalog.get('values') or []
                       if isinstance(row, dict) and _value_equal(row.get('code'), match.get('code'))
                       and row.get('description') == match.get('description')]
        if len(source_rows) != 1:
            continue
        description = match.get('description')
        if not isinstance(description, str) or not description.strip():
            continue
        proof = match.get('evidence') or {}
        if proof.get('source_file') != catalog.get('source_file') or not _value_equal(proof.get('record_code'), match.get('code')):
            continue
        key = (catalog['name'], str(match['code']))
        if key in dedup:
            continue
        dedup.add(key)
        facts.append({
            'column': catalog['key_column'], 'code': match['code'],
            'description': description.strip(), 'catalog': catalog['name'],
            'source_file': catalog['source_file'], 'source_tables': sorted(source_tables),
            'origin': 'lookup_catalog_value',
        })
    return facts


def _render_inline(rule: str, fact: dict[str, Any]) -> tuple[str, bool]:
    """Annotate a known code *without changing* the code or SQL condition."""
    code, col, description = str(fact['code']), str(fact['column']), fact['description']
    # A quoted string literal is a safely recognizable value in the sentence.
    # Word boundaries prevent PENDING from matching NOT_PENDING.
    if not isinstance(fact['code'], (int, float)):
        literal = re.escape(code)
        regex = re.compile(r"(?<![\w])(?P<literal>'" + literal + r"'|\"" + literal + r"\")(?![\w])", re.I)
        if regex.search(rule):
            return regex.sub(lambda m: f"{m.group('literal')} ({description})", rule), True
        # Unquoted codes can also be explicit, but only adjacent to the catalog column.
        regex = re.compile(r"(?P<prefix>\b" + re.escape(col) + r"\s*(?:=|is|equals|to)\s*)(?P<literal>" + literal + r")(?![\w])", re.I)
        if regex.search(rule):
            return regex.sub(lambda m: f"{m.group('prefix')}{m.group('literal')} ({description})", rule), True
    else:
        regex = re.compile(r"(?P<prefix>\b@?" + re.escape(col) +
                           r"\s*(?:=|is|equals|of|to)\s*)(?P<literal>" +
                           re.escape(code) + r")(?![\w.])", re.I)
        if regex.search(rule):
            return regex.sub(lambda m: f"{m.group('prefix')}{m.group('literal')} ({description})", rule), True
    return rule, False


def annotate_catalog_candidate(task: dict[str, Any], decision: dict[str, Any],
                               observations: list[dict[str, Any]],
                               tools: ResearchTools) -> dict[str, Any] | None:
    """Return a traceable proposed revision or None; NEVER approves a finding."""
    if decision.get('classification') != 'candidate_business_rule':
        return None
    original = decision.get('candidate_rule')
    if not isinstance(original, str) or not original.strip():
        return None
    facts = _verified_facts(task, observations, tools)
    if not facts:
        return None
    # Refuse controller-assisted completion when the LLM has introduced a
    # different known value description from the same catalog without a tool
    # observation supporting that other code. A conflict needs investigation,
    # not a cosmetic annotation which might conceal the error.
    supported = {(f['catalog'], str(f['code']).casefold()) for f in facts}
    for catalog in tools.catalogs:
        if not any(f['catalog'] == catalog.get('name') for f in facts):
            continue
        for row in catalog.get('values') or []:
            if not isinstance(row, dict) or not isinstance(row.get('description'), str):
                continue
            key = (catalog.get('name'), str(row.get('code')).casefold())
            if (key not in supported and row['description'].strip()
                    and row['description'].casefold() in original.casefold()):
                return None
    revised = original
    applied = []
    fallback = []
    for fact in facts:
        if fact['description'].casefold() in revised.casefold():
            continue
        trial, did_annotate = _render_inline(revised, fact)
        if did_annotate:
            revised = trial
        else:
            # Preserve source-backed meaning explicitly rather than guessing
            # where in a natural-language sentence a code should be substituted.
            fallback.append(f"{fact['column']}={fact['code']} means {fact['description']}")
        applied.append({**fact, 'presentation': 'inline' if did_annotate else 'catalog_context'})
    if fallback:
        revised = revised.rstrip().rstrip('.') + '. Catalog context (source-backed): ' + '; '.join(fallback) + '.'
    if not applied or revised == original:
        return None
    candidate = deepcopy(decision)
    candidate['candidate_rule'] = revised
    return {'candidate': candidate, 'before_candidate_rule': original,
            'after_candidate_rule': revised,
            'facts_applied': applied,
            'origin': 'deterministic_catalog_grounding',
            'limitation': ('The controller added only exact catalog meanings returned by authentic '
                           'read-only tool calls. It did not perform LLM reasoning, infer a rule, '
                           'or independently establish semantic correctness.')}

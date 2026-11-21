"""Conservative checks that keep a candidate anchored to its PRIMARY SQL.

These checks cannot certify natural-language meaning. They withhold acceptance
when an atomic condition or primary CALL is absent or replaced by a condition
belonging to an investigated procedure. Source content is always data.
"""
from __future__ import annotations

import re
from typing import Any

from research_agent.tools import _identifier
from research_agent.workflow_contract import source_contract

# Literal comparisons supported for conservative source/candidate alignment.
# No substitution of synonyms, inversion of operators, or LLM self-judgment.
_SQL_ATOM = re.compile(
    r"(?<![\w@.])(?P<lhs>@?[A-Za-z_][\w.]*)\s*"
    r"(?P<op><=|>=|<>|!=|=|<|>)\s*"
    r"(?P<rhs>'[^']*'|@[A-Za-z_][\w]*|-?\d+(?:\.\d+)?|[A-Za-z_][\w.]*)",
    flags=re.I,
)


def atoms(value: str) -> set[tuple[str, str, str]]:
    """Return (column/variable, operator, literal) for explicit SQL predicates."""
    return {(m.group("lhs").casefold(), m.group("op"),
             m.group("rhs").strip("'").casefold()) for m in _SQL_ATOM.finditer(value)}


def primary_anchor_issues(task: dict[str, Any], candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Detect unsupported or missing primary predicates and called-SP effects."""
    issues = []
    contract = source_contract(task)
    source_atoms: set[tuple[str, str, str]] = set()
    source_locations: dict[tuple[str, str, str], str] = {}
    for guard in contract["primary_conditions"]:
        for atom in atoms(str(guard.get("expression") or "")):
            source_atoms.add(atom)
            source_locations[atom] = f"{guard.get('source_file')}:{guard.get('line')}"

    conditions = candidate.get("conditions") or []
    seen_atoms = set().union(*(atoms(x) for x in conditions if isinstance(x, str)))
    for lhs, op, rhs in sorted(source_atoms - seen_atoms):
        issues.append({"kind": "missing_primary_condition", "blocking": True,
                       "detail": f"Primary predicate {lhs} {op} {rhs} is absent from conditions[]. Preserve the operator and literal from PRIMARY SQL.",
                       "source": source_locations[(lhs, op, rhs)]})
    for lhs, op, rhs in sorted(seen_atoms - source_atoms):
        issues.append({"kind": "nonprimary_condition", "blocking": True,
                       "detail": f"Candidate conditions[] contains {lhs} {op} {rhs}, which is not a predicate of PRIMARY SQL. Check for copied facts from a related SP or trigger.",
                       "source": contract["primary_source_file"]})

    # The primary workflow's *own* effects are required in primary output fields.
    # Mentioning a dependency in the candidate alone does not meet this criterion.
    primary_output = " ".join(str(candidate.get(f) or "") for f in ("observed_behavior", "effect")).casefold()
    rule = str(candidate.get("candidate_rule") or "")
    rule_folded = rule.casefold()
    for step in contract["primary_operations"]:
        source_ref = f"{contract['primary_source_file']}:{step['source_line']}"
        if step["operation"] in {"INSERT", "UPDATE", "DELETE", "MERGE"}:
            operation = str(step["operation"] or "").casefold()
            target = str(step.get("target_table") or "")
            # This is an explicit source anchor, not free-form semantic judgment.
            if not target or target.casefold().rsplit(".", 1)[-1] not in primary_output or operation[:5] not in primary_output:
                issues.append({"kind": "missing_primary_dml_effect", "blocking": True,
                               "detail": f"PRIMARY {step['operation']} on {target} is not identifiable in observed_behavior/effect.",
                               "source": source_ref})
        if step["operation"] == "CALL" and step["called_object"]:
            called = str(step["called_object"])
            short = _identifier(called).rsplit(".", 1)[-1]
            if short not in _identifier(primary_output):
                issues.append({"kind": "missing_primary_call", "blocking": True,
                               "detail": f"PRIMARY workflow calls {called}, but observed_behavior/effect does not describe that call. A related finding is not the caller's effect.",
                               "source": source_ref})
            # The business rule itself must still describe the conditional
            # primary call, rather than solely a called SP's internal result.
            relevant_guards = [c for c in contract["primary_conditions"]
                               if c.get("line") and c["line"] <= (step.get("source_line") or 0)]
            if relevant_guards and candidate.get("classification") == "candidate_business_rule":
                name = _identifier(called).rsplit(".", 1)[-1]
                # Source-name mention is deliberately conservative; unrecognized
                # natural-language paraphrases require verifier/human review.
                if not re.search(r"(?<![\w])" + re.escape(name) + r"(?![\w])", _identifier(rule_folded)):
                    issues.append({"kind": "primary_rule_not_about_guarded_call", "blocking": True,
                                   "detail": f"A source-guarded call to {called} must be accounted for in candidate_rule; currently the rule may describe only the called SP.",
                                   "source": source_ref})
        # Also capture observable variable assignments, including initial values
        # before a conditional branch; do not accept a copied sibling result.
        for assignment in (step.get("effects") or {}).get("variable_assignments", []):
            name = str(assignment.get("variable") or "")
            if name and name.casefold() not in primary_output:
                issues.append({"kind": "missing_primary_assignment", "blocking": True,
                               "detail": f"PRIMARY workflow assignment to {name} was not described in observed_behavior/effect.",
                               "source": f"{contract['primary_source_file']}:{step['source_line']}"})
    return issues

"""Resource Acquisition role: guarded access to extracted SQL and metadata.

The Capstone Week 5 'Resource Agent' is implemented as a deterministic tool
broker in this baseline, not as a third autonomous language model. This keeps
read-only access and admissible source paths explicit and auditable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from research_agent.tools import ResearchTools

ALLOWED=frozenset({'lookup_catalog_value','inspect_related_object',
                   'inspect_source_fragment','get_research_finding','retrieve_evidence'})

@dataclass
class ResourceAcquisitionAgent(ResearchTools):
    audit_events: list[dict[str,Any]]=field(default_factory=list)

    def execute(self, action:dict[str,Any])->dict[str,Any]:
        name=action.get('name')
        if name not in ALLOWED:
            response={'tool':str(name),'status':'rejected','reason':'Tool not on resource acquisition allowlist'}
        else:
            response=super().execute(action)
        self.audit_events.append({'action':str(name),'status':response.get('status'),
                                  'source':'run_local_metadata_or_collected_sql'})
        return response

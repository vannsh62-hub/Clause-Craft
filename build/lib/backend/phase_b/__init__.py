"""Phase B — the drafting engine.

Receives a `ContractKnowledgeObject` and nothing else. It does not read source documents,
clause libraries, playbooks or reference material — the CKO is the sum of what Phase A
learned from all of those, and reaching past it would make the phase boundary a fiction.

Enforced three ways, deliberately overlapping:

- `run_drafting_engine(cko, ctx)` takes no other knowledge parameter (§ engine.py).
- `tests/test_phase_isolation.py` forbids Phase B importing Phase A except the CKO schema.
- `backend/invariants/phase_gate.py` checks the CKO is complete enough to draft from.
"""

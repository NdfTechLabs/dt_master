# Migration and deployment records

This directory separates the current NDF Frappe Docker integration milestone
from historical migration and deployment experiments.

## M4 source of truth

- [M4 — NDF Frappe Docker integration](M4_NDF_FRAPPE_DOCKER_INTEGRATION.md):
  **canonical M4.0–M4.7 milestone tracker**, integration architecture, current
  milestone position and remaining work.
- [M4.3 — Docker Orchestration Engine workplan](M4.3_DOCKER_ORCHESTRATION_ENGINE_WORKPLAN.md): current M4.3 package sequence, implementation status and acceptance boundaries.
- [M4.3.3 — Durable Operation Foundation acceptance and closure](M4.3.3_ACCEPTANCE_AND_CLOSURE.md): accepted operation coordinator, leases, worker recovery, reconciliation, Task Manager and browser/test evidence.
- [M4.3.4.1 — Runtime Adapter Contract and Implementation Plan](M4.3.4_RUNTIME_ADAPTER_CONTRACT_AND_IMPLEMENTATION_PLAN.md): versioned Host/Docker adapter boundary, source inventory, sequencing gates and M4.3.4 execution breakdown.
- [M4.3 — Host-native Runtime Capability Audit](M4.3_HOST_NATIVE_RUNTIME_CAPABILITY_AUDIT.md): completed H1 source inventory, script dependency map, API/UI mapping, reuse/adapt/rewrite decisions and H1–H8 parity plan.
- [M4.3 — Host-native Operation Contracts](M4.3_HOST_NATIVE_OPERATION_CONTRACTS.md): H2 proposed operation catalogue, typed request/result envelope, target/ownership gates, authorization, lock scopes and legacy API/UI compatibility.
- [M4.3.2.2 — Infrastructure Node Docker readiness schema](M4.3.2_NODE_DOCKER_READINESS_SCHEMA.md): accepted Node field ownership, inspection/readiness semantics and the versioned JSON snapshot contract.
- [M4.1 — Bench Environment schema and implementation](M4_BENCH_ENVIRONMENT_SCHEMA_AND_IMPLEMENTATION.md):
  canonical M4.1 target ownership model, schema, accepted architecture and authorization decisions, migration dispositions, implementation contracts and the separate implementation-authorization gate.
- [M4 — DT Master file-level audit](M4_DT_MASTER_FILE_LEVEL_AUDIT.md):
  existing implementation, API dependencies, execution, concurrency,
  authorization and proposed dispositions.
- [M4.1 — API caller and security audit addendum](M4_API_CALLER_AND_SECURITY_AUDIT_ADDENDUM.md):
  cross-repository DT Tenant-to-Master caller graph and authorization findings.
- [M4.1 — UI, workspace and role audit](M4_UI_WORKSPACE_ROLE_AUDIT.md):
  UI/workspace/role review and proposed dispositions.
- [M4 — consolidated file disposition matrix](M4_FILE_DISPOSITION_MATRIX.md):
  proposed retain/move/replace/adapt/remove dispositions for components
  inspected to date. It is not a claim that every path in both repositories
  has been individually audited.
- [M4.1 — security verification audit](M4_SECURITY_VERIFICATION_AUDIT.md):
  source-level realtime, sensitive-field, logging, backup and child-DocType
  findings, with runtime verification deferred to the relevant
  implementation/testing stage.

The M4 integration tracker is the sole source for milestone names, sequence
and status. The M4.1 Bench Environment specification is the canonical source
for target architecture and resolved design decisions. Audit records provide
evidence and proposed dispositions; they do not independently redefine the
milestone structure.

## Historical work

- [Same-VM Dalali Docker migration](same-vm-docker/README.md): a separate,
  earlier Dalali migration/rehearsal workstream. Its results and artifacts are
  retained as historical evidence, not as the current M4 runtime design or
  deployment instructions.

The current M4 runtime source of truth is the NDF Frappe Docker repository.
DT Master consumes its published product compositions and deployment contract;
it does not create a competing image-building or Compose implementation.

No implementation authorization is implied by any design or audit record.

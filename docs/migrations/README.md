# Migration and deployment records

This directory separates the current NDF Frappe Docker integration milestone
from historical migration and deployment experiments.

## M4 source of truth

- [M4 — NDF Frappe Docker integration](M4_NDF_FRAPPE_DOCKER_INTEGRATION.md):
  **canonical M4.0–M4.7 milestone tracker**, integration architecture, current
  milestone position and remaining work.
- [M4.1 — Bench Environment schema and implementation](M4_BENCH_ENVIRONMENT_SCHEMA_AND_IMPLEMENTATION.md):
  canonical M4.1 target ownership model, schema, resolved architecture
  decisions, migration dispositions, implementation contracts and approval
  boundary.
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

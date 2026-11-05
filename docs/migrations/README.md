# Migration and deployment records

This directory separates historical migration work from the current platform
integration milestone.

## Current work

- [M4 — NDF Frappe Docker integration](M4_NDF_FRAPPE_DOCKER_INTEGRATION.md):
  current architecture, server baseline, milestone status, decisions and
  remaining discovery/implementation gates.

## Historical work

- [Same-VM Dalali Docker migration](same-vm-docker/README.md): a separate,
  earlier Dalali migration/rehearsal workstream. Its results and artifacts are
  retained as historical evidence, not as the current M4 runtime design or
  deployment instructions.

The current M4 runtime source of truth is the NDF Frappe Docker repository.
DT Master consumes its published product compositions and deployment contract;
it does not create a competing image-building or Compose implementation.

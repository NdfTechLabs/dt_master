# Pinned base image downloaded and inspected — exact native runtimes selected

The operator subsequently chose **Match existing**. Offline native-runtime probe
v2 passed; the custom-build/download scope is pending approval in
EXACT_RUNTIME_BUILD_PLAN.md. The findings below describe the unmodified base.

The operator approved the approximately 981 MB download **directly to the VM**.
Pull and isolated inspection completed. No site data, backup decryption, custom
dependency installation or traffic switch occurred.

Pinned reference:

```text
frappe/erpnext@sha256:eaf1da1478c2b99339a0a8bc37af723a67ecf9cd4f84b313a0c3765743486fcc
```

## Inspection results

| Item | Native production | Downloaded image |
|---|---|---|
| Frappe | 16.53.0 | 16.53.0 |
| ERPNext | 16.52.1 | 16.52.1 |
| Python | 3.14.3 | **3.14.9** |
| Node | 24.14.0 | **24.22.1** |
| OS | Ubuntu 24.04 | Debian 13 (trixie) |
| Source commits | Pinned in source-manifest.json | Git revisions unavailable |

Application version strings match; that is not proof of exact deployed source
parity. The production source snapshot/dependency closure still needs a reviewed
custom image. The base alone also does not contain Dalali's complete custom app set.

Runtime patch versions differ from the original no-upgrade plan. Do not silently
waive that plan or use the candidate with customer data. Choose one route:

1. Prepare a custom layer/build that preserves Python 3.14.3 and Node 24.14.0,
   with compatibility/dependency checks and a new download/build estimate.
2. Explicitly approve evaluating the image's newer patch runtimes in a private
   clone, with functional validation before any production adoption. Live native
   runtimes stay unchanged; this is not automatic cutover approval.

Neither route is executed merely by completion of the pull.

## Isolation and production checks

- Pulled through the intended rootless account/daemon, under the existing aggregate
  2-CPU/3-GiB limits. Registry transfer did not pass through the workstation.
- Inspected with a temporary network-none, read-only, cap-drop/no-new-privileges
  container. No host/customer mounts; only image-created temporary volumes.
- Read versions using static source parsing and simple version commands; no app
  initialization or DB connection. Pip version-check disabled.
- Protected native config fingerprints matched before/after inspection.
- Independent checks found original native Bench PIDs RUNNING, native web/mail
  services active, roughly 119 GiB disk free after pull. No site was migrated.
- Progress stayed in `/var/lib/ndf-rootless-migration/base-image-pull.log` on the VM;
  inspection/package inventory in `base-image.json`. Only small summaries returned.

Registry metadata lists 981,159,820 compressed layer bytes. Docker 29's image
`Size` field is not treated as a reliable unpacked filesystem measurement. The
first inspection receipt used a misleading `uncompressed_image_bytes` label for
that field; the helper now labels it `docker_reported_image_size_bytes`. This
does not affect the digest, version findings or production checks.

A pip cache warning during read-only inspection was expected and did not cause
package downloads or mutation. Archive decryption/backup downloads remain prohibited.

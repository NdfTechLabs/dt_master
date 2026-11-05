# Exact-runtime evaluation image — no-data validation passed

Subsequent isolated technical rehearsal passed (REHEARSAL_RESULT.md), including
native 0.12.6 PDF/font copies mounted readonly. Those runtime artifacts are not
baked into this image; do not delete them while active roles depend on them.

Validated private local image:

```text
tag: ndf-local/exact-runtime:dalali-v2
image ID: sha256:e8b4c638deb0c739fa7a2f932c5a79f6bd5a5be291393dd3f1d114840bf7e8eb
parent: sha256:c498d968d41e675c3ab27a6aabaef679720e118e2be919923590b4b7e5b37147
```

This is **not a completed migration or production functional acceptance**. No
customer data, site configuration or live host directories were mounted.

## Passed checks

| Check | Verified result |
|---|---|
| Python / shared libpython | 3.14.3 |
| Node | 24.14.0 |
| Yarn | 1.22.22 |
| OpenSSL used by native runtime | 3.0.13 |
| MariaDB clients | 10.11.14 |
| Bench CLI | 5.29.1 |
| Frappe / ERPNext | 16.53.0 / 16.52.1 |
| Pilot app imports | All 12, from /home/frappe/bench/apps/<app>/ |
| Application package inventory | All 231 match native versions |
| Other imports | PyMySQL, MySQLdb, Pillow, cryptography, Redis, RQ, Gunicorn, NumPy, pandas, pyarrow |
| Isolation | Read-only, network none, cap-drop/no-new-privileges, bounded CPU/RAM, no host/data mounts |
| Protected native state | Health/PIDs, configuration fingerprints and forwarding match |

## Build history and corrective layer

The original large build was canceled by the 35-GiB staging disk guard during
final export/unpack. It retained the exact local image shown as parent above;
the original command is still recorded **canceled**, not retrospectively success.
The retained parent was verified against its export log/base layers/startup config
and started successfully, but strict validation found Yarn absent.

Operator approved an additive native-Yarn fix capped at 100 MB, no downloads or
cleanup. All 11 source files of /usr/lib/node_modules/yarn were pinned by SHA-256,
privately copied and scanned, without editing the native package. New image adds
only Yarn and its wrapper PATH. Original image/tag, snapshots and contexts remain.

An initial digest-qualified local FROM caused a Docker registry metadata lookup
and access-denied failure before layers were built. No image/dependency layers
were downloaded or source pushed. The failed recipe/log remains preserved. The
successful recipe uses the existing local parent tag with --pull=false, verifies
its pinned image ID before/after, and verifies the child layer prefix.

Corrective image build and strict no-data validation succeeded. Added disk use:
**29,708,288 bytes**, within the 100-MB subcap and original 35-GiB staging budget.
No budget increase or automatic cleanup. Aggregate 2 CPU / 3 GiB unchanged.

## Private VM evidence

Under /var/lib/ndf-rootless-migration/exact-runtime-v1:

```text
state.json
validation-yarn-v2.json
yarn-layer-v1/result.json
yarn-layer-v1/build.log
yarn-layer-v1/context-manifest.json
yarn-layer-v1/context-manifest-local-parent.json
```

Small new context: /opt/ndf-rootless/yarn-layer-v1. Raw package copy and all older
partial contexts remain retained. These private files are not Git artifacts.

## Remaining gates

- Assets integration and required font/PDF parity, image startup/role definitions.
- Reviewed isolated database/Redis/frontend versions and any separate download
  budget: the image having MariaDB **clients** is not an isolated database server.
- Exact private server-side data-copy/restore scope, fresh consistency/writer
  fencing strategy, new storage destinations and disk/resource budget. Never mount
  the live Bench or host database. Do not decrypt the downloaded archive.
- Master automation/ownership mapping, clone egress/side-effect blocking and
  functional/recovery acceptance. Dalali remains in native maintenance.
- Explicit pilot cutover/write-open decisions; no other production services move.

Cleanup only after migration/recovery/rollback acceptance, with an exact reviewed
list. Preserve active images, volumes, source/WIP recovery copies and required
encrypted backups. No deletion is authorized by completion of image validation.

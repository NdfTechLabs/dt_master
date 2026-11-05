# UI browser sandbox policy — separate approval pending

**Subsequent approval:** operator selected **Approve browser-only profile**.
Implementation/probes must stay within the exact scope below; no prerequisite or
Chrome sandbox success is assumed from approval. No host/existing policy changes.

**Executed result:** positive user/PID/net and negative forbidden-namespace probes
passed, followed by pinned-tool verification and normal Chrome synthetic rendering
without --no-sandbox. No customer browser data loaded yet; request/action-guard
proof is required before credentials. Existing host/app policies remain unchanged.

The operator approved the 150-MB VM UI tool budget and guarded tests, with an
explicit requirement to stop before any security-policy relaxation. No browser
binary/support package has been downloaded and no UI/customer browser test run.

## Observed prerequisite

A new no-data rootless test container, UID/GID 1000, readonly root, cap-drop ALL,
no-new-privileges, network none, 64-MB RAM and 0.25 CPU attempted only
`unshare(CLONE_NEWUSER)`: result -1 / errno EPERM. Existing AppArmor context is
`ndf-rootlesskit-29_6_1 (unconfined)`, seccomp enabled with one filter.

Read-only dockerd Go build-info inspection identifies the actual default seccomp
module as **github.com/moby/profiles/seccomp v0.2.3**. Its public default JSON was
reviewed, not applied: unshare/setns/chroot and namespace clone calls are allowed
only with capabilities that this deliberately cap-dropped browser will not have.
The current policy therefore blocks the normal inner sandbox prerequisite; this
does not justify using --no-sandbox or enabling a host-wide namespace exception.

Source for proposed baseline:
`https://raw.githubusercontent.com/moby/profiles/seccomp/v0.2.3/seccomp/default.json`.
Pin exact bytes/SHA-256 before generating any profile and retain baseline/diff.
No default policy files or existing containers are changed by this document.

## Proposed browser-only alternative

- Keep defaultAction ERRNO and every existing default syscall rule. Use a new
  private profile only on a new labelled UI browser/probe container.
- Permit only needed legacy clone/unshare/setns namespace operations for inner
  **user/PID/network namespaces**, with argument masks/types. Do not grant mount,
  UTS/IPC/cgroup namespace operations, SYS_ADMIN or other container capabilities.
- Keep clone3's default ENOSYS fallback rather than unconditionally allow a
  pointer-based syscall whose flags cannot be constrained by seccomp arguments.
- If needed for the browser's inner filesystem sandbox, allow chroot syscall
  without adding SYS_CHROOT to the outer container. Kernel checks still apply;
  this is not a host/root filesystem mount or capability grant.
- No seccomp-unconfined, privileged mode, --no-sandbox, host networking, new host
  AppArmor profile/sysctl or daemon restart. No application/DB/Redis policy change.
- This **does widen syscall access inside this browser container**, increasing
  its reachable kernel interface. It does not grant host capabilities. Retain the
  existing rootless/readonly/no-new-privileges/no-egress/resource boundaries and
  private clone-only proxy/action allowlist as independent layers.

## Staged validation before data

1. Review/pin baseline and exact added rules; unit-test no removed rules/broader
   defaults, masks/types and unsupported architecture behavior (host amd64 only).
2. Run no-data namespace probes with the private browser profile. Confirm expected
   inner sandbox operations work; forbidden mount/other namespace operations stay
   denied, outer capabilities stay zero, no default route/external access.
3. Only after prerequisite proof, perform the already-approved capped tool download
   and launch normal Chrome sandbox on synthetic content. No clone credentials yet.
4. If another syscall, policy change or sandbox bypass is required, stop again;
   never broaden the profile silently or treat successful unshare as proof that
   the full Chrome sandbox is active.
5. Run clone-origin proxy/unsafe-action blocking probes before real UI navigation.
   Customer pages and any credentials/profile remain VM-side; no PII artifacts.

An alternative is to keep current security policy and stop browser automation for
now. Technical rehearsal remains passed; business/UI acceptance and final sync
remain gated. Production traffic, source maintenance and cleanup are untouched.

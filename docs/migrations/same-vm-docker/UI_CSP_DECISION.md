# Clone-only CSP compatibility decision

**Operator approved:** selected **Approve clone-only CSP**, permitting only the
exact private script-src addition and clone Nginx validation/reload below. No
production policy decision or wider security change is included.

**Executed:** original private config retained, exact token change validated and
clone Nginx gracefully reloaded; exact served policy verified. The initial
asynchronous reload/header race restored the original config, then a bounded
old/new-header transition check succeeded. Desk template rendering is now working.
UI_ACCEPTANCE_RESULT.md records remaining blockers; acceptance has not passed.

## Evidence

Local validation: 160 tests passed, both UI JavaScript files passed Node syntax
checks, and `git diff --check` passed. Fresh no-data guard passed with the current
runner/proxy hashes before credentials were loaded.

The subsequent VM-only browser test authenticated Administrator and booted Desk,
but timed out rendering the Item list. Sanitized browser state reports
`page_csp_eval_blocked: true`; the list route exists but list DOM does not.
Exceptions include `frappe.render` and `frappe.make_page` in the frozen Desk bundle.
The older CDP-only `csp_eval_blocked: false` diagnostic missed the caught failure;
it does not override the document's security-policy-violation evidence.

Frozen Frappe `public/js/frappe/microtemplate.js` compiles templates using
`new Function("obj", fn_str)` at line 88, catches compilation errors, and returns
the compiled entry. Rendering then calls that entry. The private rehearsal CSP
currently excludes `unsafe-eval`, consistent with the observed rendering failure.
This establishes a compatibility blocker, not proof all UI errors share one cause.

## Exact approved/applied change

Only in the rehearsal frontend's private `nginx-tmpfs.conf`:

```diff
-script-src 'self' 'unsafe-inline';
+script-src 'self' 'unsafe-inline' 'unsafe-eval';
```

Retain every other CSP directive, browser sandbox/seccomp, proxy origin/action
restrictions, internal network, resource limits and VM-only privacy protections.
No CDP CSP bypass, app-code/runtime change, additional download or host policy edit.

After approval: verify the labelled clone frontend and exact mounted config,
preserve original bytes/hash privately, apply only this token change to that
private config, validate with the clone container's `nginx -t`, and gracefully
reload only its Nginx. Retain the mount inode if required by the read-only bind.
No container/deployment/volume removal or recreation. If validation fails, restore
the preserved config before reload and stop. Verify the served CSP and protected
native-service baselines, then rerun guarded UI tests. Stop if a wider change is
needed. This is no authorization to touch host Nginx/TLS or any native site.

## Risk and remaining gates

`unsafe-eval` permits dynamically compiled JavaScript in clone pages, weakening
CSP protection against script injection. It does not permit external origins or
unsafe business actions through the independent proxy. This approval, if granted,
is for private rehearsal compatibility only, not a production CSP decision.

Page-view tracking and dynamic recommendations remain denied. Recommendations
reach shopping-cart/party code and need further implicit-write review. Remaining
read/form/storefront/role/attachment/PDF/Note checks have not passed. Automated UI
evidence is not operator business acceptance.

No final synchronization, new source pause, source schema change, public route
switch, write-open, backup decryption or cleanup occurred. Native Dalali remains
in maintenance. Final-sync writer ownership/fencing remains unresolved.

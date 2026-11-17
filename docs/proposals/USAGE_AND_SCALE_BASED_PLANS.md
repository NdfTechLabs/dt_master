# Usage- and scale-based plans

**Status: Proposal for discussion.** Nothing here is implemented or approved.

## Purpose

We want a wider range of packages so that customers of different sizes can buy a plan that fits: plans restricted to a number of users or an amount of resources, usage-based pricing, and other commercial models. This document records what the platform already has, which models fit it, a suggested build order, and the decisions stakeholders need to make before any design work starts.

## What exists today

Most of the data needed for limit-based plans is already in `dt_master`. What is missing is enforcement, metering and billing.

- **Limits are modelled but not enforced.** `Machine Constraint` is linked to a `Capability Profile`, which is linked to a `Subscription Plan`. It already has `max_users`, `storage_gb`, `database_storage_gb`, `backup_storage_gb`, `max_api_requests_per_minute`, `max_background_jobs` and `max_file_upload_mb`, plus CPU, RAM and worker settings. The master sends these to the tenant as `machine_properties` in `get_tenant_capabilities`, and the Tenant Subscription page in `dt_tenant` displays them. Nothing in either app checks them.
- **Limits are defined in two places.** `Infrastructure Profile` also carries `max_users`, `max_storage_gb`, `max_sites` and `max_databases` per plan. One of the two needs to become the source of truth.
- **No usage is measured.** The tenant never reports user counts, storage or request volume back to the master.
- **Billing is flat-rate.** ERPNext's `Subscription` supports a quantity per plan row, which gives per-seat billing natively, but it has no metered billing.
- **Extensions already have pricing fields.** `Extension` has `pricing_model` (Free / Paid), `price_type` (One-time / Subscription / Usage-based), `price_amount`, `billing_interval` and `trial_days`, but marketplace install never checks or charges against them.
- **Plan changes are not implemented.** `dt_master.api.subscription.change_subscription_plan` validates the plan name but does not change the Subscription's plan rows. Any model below that involves upgrades, downgrades or seat changes depends on specifying this first.

## Business models that fit

| Model | How it works | What it needs |
| --- | --- | --- |
| Tiered plans with hard limits | Starter / Growth / Enterprise, each with user, storage and module caps | Enforcement on the tenant only; billing unchanged |
| Per-seat | Price per active user, with a minimum | Seat count on the Subscription row quantity, plus enforcement |
| Tier plus overage | Flat fee includes a quota; extra users or GB are billed per unit | Metering, plus an overage line on the invoice |
| Paid add-ons | Extra modules, marketplace apps, storage packs, extra sites | Entitlement check at install, plus extra plan rows on the Subscription |
| Pure usage-based | Bill on documents, API calls or transactions | Reliable metering and invoice generation from usage; the most work |
| Dedicated / enterprise | Own bench or node, custom limits, SLA | Mostly there: `deployment_type` and `sla_level` exist; needs per-customer overrides |
| Prepaid credits | Customer buys credits, usage draws them down | A ledger on the master; suits M-Pesa customers who prefer prepaying |
| Freemium / trial | Free tier with tight caps, or a time-limited trial | Trials exist (`Trialing` status); a free tier is a plan with low limits |

These are not mutually exclusive. A likely end state is tiered plans as the base, per-seat or overage on top for growth, and paid add-ons for modules and marketplace apps.

## Recommended order

Each step is useful on its own and produces what the next one depends on.

1. **Hard limits on tiered plans.** The cheapest step, and it makes Starter / Growth / Enterprise packaging possible. The tenant enforces from the capability snapshot it already receives:
   - user count when a user is created or enabled;
   - storage when a file is uploaded;
   - `max_file_upload_mb` through system settings;
   - worker counts and CPU/RAM at provisioning time, on the node.
2. **Usage reporting.** The six-hourly capability sync also posts a small usage snapshot (active users, file and database size, counts of key documents) to a new usage record on the master. This gives usage dashboards, "you are at 80% of your plan" prompts, and the data needed to price the later models.
3. **Per-seat and add-ons.** Seats map onto the existing Subscription row quantity. Add-ons become extra plan rows, and marketplace install checks entitlement against the `Extension` pricing fields.
4. **Overage and metered billing.** A scheduled job on the master turns usage above quota into invoice lines. This comes last, once metering has been trustworthy for a few billing cycles.

## Decisions that shape the design

These are the questions for stakeholders. The answers determine the schema and how much of the order above is needed.

1. **Which models do we offer first?** Tiered limits alone need only step 1. Per-seat needs steps 1 and 3. Overage and usage-based need all four.
2. **Hard block or soft limit?** Blocking the next user at the cap is simple but frustrates growing customers. Allowing it and billing overage needs metering and invoicing. A common middle ground is to warn at 80%, allow a small grace margin, then block.
3. **What counts as a user?** Enabled system users is the usual answer; website and portal users normally do not count. This must be fixed before any limit or seat price is published.
4. **Which resources are limited or billed?** Users and storage are the obvious ones. Decide whether API requests, background jobs, sites, documents or transactions are in scope, since each one adds metering work.
5. **Where do per-customer exceptions live?** Limits are currently per plan. Enterprise deals will need overrides on the Tenant Site or the Subscription.
6. **Which record owns the limits?** `Machine Constraint` and `Infrastructure Profile` overlap. Pick one as the source of truth and retire or narrow the other.
7. **Who administers the tenant site?** Tenant-side enforcement is only advisory if the customer's administrators hold System Manager on their site, because they can edit the snapshot or bypass the checks. Once limits carry money, either customers do not get System Manager, or the limits that matter are also enforced on the master and the node.
8. **How do mid-cycle changes work?** Upgrades, downgrades and seat changes need a proration rule and an effective date. This also unblocks the unimplemented plan-change API.
9. **How are paid add-ons charged?** One-time, recurring on the main subscription, or usage-based, and what happens to an installed add-on when payment lapses.
10. **What happens at the limit for existing customers?** Customers already above a new plan's caps need a grandfathering or migration rule before enforcement is switched on.

## Relationship to M4

The M4 work in `docs/migrations/` moves runtime ownership to Bench Environments. Resource limits that are enforced at the node (CPU, RAM, workers, storage) should be designed against that model, not the current host-bench one. Limits enforced inside the site (users, modules, file size) are independent of M4.

## Next step

Collect answers to the decisions above. From those, write a concrete schema and implementation plan for the chosen models.

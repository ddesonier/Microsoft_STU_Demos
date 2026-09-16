# hardening.json — Entra ID Security Hardening Baseline

## Overview

`hardening.json` is a declarative template applied by `Apply-GraphTemplate.ps1`
(documented in **README-Engine.md**). It provisions a per-tenant Microsoft Entra ID
security-hardening baseline that reduces identity-based attack surface: a break-glass group,
restricted default user permissions, user and group/Team owner consent lockdown, a PIM
authentication context, and a set of Conditional Access policies. Unlike `collaboration.json`,
it is single-tenant and takes no partner input.

The template is idempotent: it may be applied repeatedly, and each run reconciles the
tenant to the declared desired state. All Conditional Access policies are created in
**report-only** so their impact can be reviewed before enforcement.

## Invocation

```powershell
# Read-only preview (requests read-only scopes only):
.\Apply-GraphTemplate.ps1 -TemplatePath .\hardening.json -WhatIf

# Apply the baseline:
.\Apply-GraphTemplate.ps1 -TemplatePath .\hardening.json
```

No `-Domain` or `-TenantId` is required — the template applies to the signed-in tenant.
The `-Environment` parameter (Global / USGov / USGovDoD / Custom) selects the cloud, and
`{{graph-endpoint}}` is injected accordingly.

## Design principles

The template implements the following model:

- **Report-only first.** All Conditional Access policies (TCG01–TCG11) are created in
  `enabledForReportingButNotEnforced` and exclude the Emergency Access break-glass group.
  Review impact in the sign-in logs, then enable each policy individually.
- **Built-in MFA strength now; phishing-resistant MFA is the end goal.** Every MFA
  requirement uses the built-in *Multifactor authentication* authentication strength
  (`00000000-0000-0000-0000-000000000002`), not the legacy `mfa` grant control. A future
  iteration moves the registration, admin, and PIM policies to phishing-resistant strength
  (`…004`) for all users and applications.
- **Create-if-missing safety.** The group and Team owner consent lockdown is created only
  when a `Group.Unified` group-settings object does not already exist, so other
  group-governance settings on an existing object are never overwritten.
- **The script manages only what it declares.** Existing objects are detected and left in
  place; group lookups dedupe on `displayName` so re-runs never duplicate policies or groups.

## Required Microsoft Graph permissions

These are the **delegated** scopes (`graphScopes.delegated`), used for interactive sign-in:

```
Group.ReadWrite.All                 AuthenticationContext.ReadWrite.All
Policy.ReadWrite.Authorization      Application.Read.All
Policy.ReadWrite.ConditionalAccess  GroupSettings.ReadWrite.All
Policy.Read.All
```

For app-only execution via `-AccessToken` (service principal or managed identity), the template's
`graphScopes.application` set is used instead. It is identical **except** that
`GroupSettings.ReadWrite.All` has no application-permission equivalent, so the app role
**`Directory.ReadWrite.All`** is required to create the `Group.Unified` settings object app-only.
`Scripts/Initialize-TcgDeployerApp.ps1` grants that application set automatically.

Microsoft Entra ID **P2** is required for the risk-based policies (TCG03, TCG04) and for
Privileged Identity Management. Delegated execution requires the Conditional Access
Administrator, Authentication Policy Administrator, and Groups Administrator roles, or
Global Administrator. In `-WhatIf`, the engine requests read-only equivalents of the write
scopes so no write consent is needed to diff state.

## Applied configuration

Resources are applied in template order. The tables below describe each resource and the
action the script performs.

### Groups

Security groups are created if missing (dedupe on `displayName`) and captured for reference
by later resources. The token `#EXT#` in a user principal name identifies an external user.

| # | Group display name | Type | Purpose |
|---|---|---|---|
| 1 | Emergency Access | Assigned | Break-glass accounts. **Created empty — populate with 2+ cloud-only accounts.** Excluded from every Conditional Access policy. Captured as `emergencyAccess`. |
| 2 | External Users All | Dynamic | UPN contains `#EXT#`. All external users. Captured as `externalUsersAll`. Shared, identically defined with `collaboration.json` so each template can run on its own. |

### Tenant policy and consent

| # | Resource | Action |
|---|---|---|
| 3 | Authorization policy — default user permissions | *Configure.* Idempotent PATCH setting `allowedToCreateApps=false`, `allowedToCreateSecurityGroups=false`, `allowedToCreateTenants=false`, `permissionGrantPoliciesAssigned=[]` (user consent to apps disabled), and `guestUserRoleId` to the **limited** guest tier (`10dae51f-b6af-4016-8d66-8c2a99b929b3`) — guests get restricted directory access while remaining distinct from members. High-side tenants can tighten this to the most-restrictive role (`2af84b1e-32c8-42b7-82bc-daa82404023b`, guests see only their own objects). |
| 4 | Group settings — disable group and Team owner consent | *Create-if-missing.* Creates the `Group.Unified` group-settings object with `EnableGroupSpecificConsent=false`, closing the group/Team owner consent path. If a `Group.Unified` object already exists it is **not** modified. |

### Authentication context

| # | Resource | Action |
|---|---|---|
| 5 | Authentication context — PIM Admin Context | *Configure.* Creates/publishes authentication context `c90` ("PIM Admin Context") used by the PIM-tier policies. Assumed unused in the target tenant. |

> **Authentication methods are intentionally out of scope.** Enabling passwordless methods
> (FIDO2/passkey, certificate-based auth, Temporary Access Pass) is **not** automated by this
> template. Unlike Conditional Access, the authentication methods policy has **no report-only
> mode** — it takes effect tenant-wide the instant it is written, and scoping a method to a pilot
> group can immediately lock out anyone (including the operator) who relies on that method but is
> not in the group. Configure authentication methods manually in the portal, adding your own
> account and break-glass accounts to the pilot group **before** enabling and registering, then
> expand the pilot deliberately.

### Conditional Access policies

All policies are created (dedupe on `displayName`) in **report-only**
(`enabledForReportingButNotEnforced`) and **exclude the Emergency Access group**. "Auth
strength: MFA" is the built-in *Multifactor authentication* strength (`…002`).

| # | Policy | Target | Condition | Grant control |
|---|---|---|---|---|
| 6 | TCG01 — Require MFA for all users | All users, all apps | — | Auth strength: MFA |
| 7 | TCG02 — Require Hybrid Entra Join | All users, all apps | — | Require Hybrid Entra joined device |
| 8 | TCG03 — Protect risky sign-ins | All users, all apps | Sign-in risk high/medium | Auth strength: MFA + sign-in frequency every time |
| 9 | TCG04 — Protect risky users | All users, all apps | User risk high | (AND) Risk remediation + auth strength: MFA + sign-in frequency every time |
| 10 | TCG05 — Block legacy authentication | All users, all apps | Client apps: Exchange ActiveSync, other | Block |
| 11 | TCG06 — Block device code flow | All users, all apps | Auth flow: device code flow | Block |
| 12 | TCG07 — Protect security info registration | All users | User action: register security info | Auth strength: MFA |
| 13 | TCG08 — Protect device registration | All users | User action: register or join devices | Auth strength: MFA + sign-in frequency every time |
| 14 | TCG09 — Protect Admin Roles | Admin directory roles | — | Auth strength: MFA |
| 15 | TCG10 — PIM Admin — Block high user risk | Auth context `c90` | User risk high | Block |
| 16 | TCG11 — PIM Admin — Require Auth Strength | Auth context `c90` | — | Auth strength: MFA + sign-in frequency every time |

## Operational considerations

### Conditional Access policies ship in report-only

Every policy is created in `enabledForReportingButNotEnforced`. Report-only policies record
impact but do not block or grant. After reviewing sign-in-log impact, enable TCG01–TCG11
individually. Because report-only does not enforce, `block` policies (TCG05, TCG06, TCG10)
show would-be impact but do not block until enabled.

### Emergency Access is created empty

The break-glass group is created but has no members. Populate it with two or more
cloud-only accounts (assigned Global Administrator, long random credentials stored offline)
**before** enabling any policy — every policy excludes this group so break-glass accounts
retain access during a lockout.

### Authentication methods are configured manually

The template does **not** enable or scope any authentication method (FIDO2/passkey,
certificate-based auth, Temporary Access Pass) and does not create per-method pilot groups. The
authentication methods policy has no report-only mode and takes effect tenant-wide immediately,
so scoping a method to a pilot group can lock out anyone (including the operator) who relies on
that method but is not yet in the group. Configure methods in the portal: add your own account
and the break-glass accounts to the pilot group **before** enabling and registering, then expand
deliberately. (Windows Hello for Business is likewise out of scope — it is device-registration /
Intune-managed, not part of the authentication methods policy.)

### Group and Team owner consent is create-if-missing

Resource 4 creates the `Group.Unified` settings object only when one does not already exist.
On a tenant that already has a `Group.Unified` object, verify `EnableGroupSpecificConsent`
is `false` in the portal — the template will not overwrite an existing object, because
group-settings updates replace the entire values collection and would reset other
group-governance settings.

### Authentication context c90

The PIM Admin authentication context id `c90` is assumed unused in the target tenant. If it
is already taken, change it in three places: the authentication-context resource, and the
`includeAuthenticationContextClassReferences` condition of TCG10 and TCG11.

### Not scripted (manual follow-on)

- **PIM role configuration** — run PIM Discovery and Insights, convert standing assignments
  to eligible, and require the PIM Admin context (c90) on activation, through the portal.
- **Admin consent workflow** — reviewers are tenant-specific; configure manually if wanted.
- **LinkedIn account connections** and **admin-portal restriction** have no supported
  Microsoft Graph v1.0 API and are portal-only.

## Idempotency and re-execution

The template may be applied repeatedly. Each run reports its actions:

- **Created** or **Found** — a new object was created, or an existing object was detected
  and left unchanged.
- **Applied** — a configure operation re-asserted the desired state, independent of whether
  a change occurred.
- **Skipped** — no action was required, or a create-if-missing resource already existed.
- **Warning** — a guard or precondition was triggered.

A read-only preview is available with `-WhatIf`; `-Verbose` additionally reports each
Microsoft Graph request.

## Go-live sequence

1. Review with `-WhatIf`.
2. Apply the template — all Conditional Access policies land in report-only.
3. Populate the Emergency Access break-glass group with two or more cloud-only accounts.
4. Configure authentication methods manually in the portal (add yourself + break-glass to any
   pilot group before enabling/registering — see "Authentication methods are configured manually").
5. Review report-only impact in the sign-in logs.
6. Enable TCG01–TCG11 individually.
7. Run PIM Discovery and Insights and convert standing access to eligible (manual).

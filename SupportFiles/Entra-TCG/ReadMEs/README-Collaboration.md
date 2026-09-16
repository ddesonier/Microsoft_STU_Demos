# collaboration.json — Cross-Tenant Community Collaboration

## Overview

`collaboration.json` is a declarative template applied by `Apply-GraphTemplate.ps1`
(documented in **README-Engine.md**). It establishes a multi-tenant collaboration fabric
across community Microsoft Entra tenants without relying on Microsoft 365 Multi-Tenant
Organization (MTO). The template configures administrator-gated B2B collaboration,
per-partner cross-tenant trust, dynamic and static group structures, an access-package
entitlement gate, and a staged outbound cross-tenant synchronization configuration.

The template is idempotent: it may be applied repeatedly, and each run reconciles the
tenant to the declared desired state.

## Invocation

```powershell
# Read-only preview, resolving a partner tenant from its domain:
.\Apply-GraphTemplate.ps1 -TemplatePath .\collaboration.json -Domain fabrikam.com -WhatIf

# Apply for one or more partner tenants:
.\Apply-GraphTemplate.ps1 -TemplatePath .\collaboration.json -Domain fabrikam.com,contoso.com
```

The `-Domain` and `-TenantId` parameters define the **partner tenants**. Resources marked
*forEach* are applied once per partner.

## Design principles

The template implements the following model:

- **Requestors are internal users.** Internal users request the access package in their
  home tenant. Membership in the gate group scopes them into the outbound cross-tenant
  synchronization job, which provisions them into partner tenants as members rather than
  guests. Access packages therefore govern which internal users synchronize outward, not
  which external users request inward.
- **Inbound collaboration is denied by default; partners form an allow-list.** Only the
  partner tenants explicitly configured by the template may collaborate inbound. Outbound
  collaboration remains open.
- **Trust and automatic consent are configured per partner.** Multifactor,
  compliant-device, and hybrid-join claims are honored per partner, and the guest
  redemption prompt is suppressed per partner rather than globally.
- **The script manages only the objects it creates.** Pre-existing synchronization
  applications are detected and left unmodified; the script neither duplicates nor alters
  configuration it does not own.

## Required Microsoft Graph permissions

These are the **delegated** scopes (`graphScopes.delegated`), used for interactive sign-in:

```
Policy.ReadWrite.Authorization      Group.ReadWrite.All
Policy.Read.All                     EntitlementManagement.ReadWrite.All
Policy.ReadWrite.CrossTenantAccess  Application.ReadWrite.All
Synchronization.ReadWrite.All       AuditLog.Read.All
```

For app-only execution via `-AccessToken` (service principal or managed identity), the template's
`graphScopes.application` set is used. For this template the application app roles are identical
in name to the delegated scopes above. `Scripts/Initialize-TcgDeployerApp.ps1` grants them automatically.

Delegated execution additionally requires appropriate Microsoft Entra directory roles
(Global Administrator, or the combination of Identity Governance Administrator, Hybrid
Identity Administrator, Cloud Application Administrator, and Security Administrator).
Microsoft Entra ID P1 or P2 licensing is required, as cross-tenant synchronization is a
licensed capability.

## Applied configuration

Resources are applied in template order. The tables below describe each resource and the
action the script performs.

### Tenant policy

| # | Resource | Action |
|---|---|---|
| 1 | Authorization policy (invites) | *Configure.* Restricts guest invitation to administrators and guest inviters. Applied as an idempotent PATCH. |
| 2 | B2B invitation domain restrictions (precondition) | *Assert.* A read-only evaluation. If the tenant restricts B2B invitations to a domain allow-list that would exclude community tenants, the script emits a warning and continues. The policy is never modified. |

### Cross-tenant access

| # | Resource | Action |
|---|---|---|
| 3 | Cross-tenant access — partner *(forEach)* | *Create and configure.* Creates the partner configuration keyed by tenant ID if absent, then configures inbound trust, automatic user consent, and inbound and outbound B2B collaboration as allowed. |
| 4 | Cross-tenant access — default | *Configure.* Sets the tenant default for inbound B2B collaboration to blocked, so that only explicitly configured partners may collaborate inbound. |

### Groups

Dynamic groups are governed by membership rules; static groups are populated by the access
package and synchronization configuration. The token `#EXT#` in a user principal name
identifies an external user (synchronized or invited).

| # | Group display name | Type | Membership definition and purpose |
|---|---|---|---|
| 5 | External Community Members | Dynamic | `userType = Member` and UPN contains `#EXT#`. Users synchronized into the tenant via cross-tenant synchronization. |
| 6 | External Users All | Dynamic | UPN contains `#EXT#`. All external users (synchronized or invited). Shared, identically defined with `hardening.json` so each template can run on its own. |
| 7 | External Community Guests | Dynamic | `userType = Guest` and UPN contains `#EXT#`. B2B-invited guests. |
| 8 | Organization Internal Only | Dynamic | `userType = Member` and UPN does not contain `#EXT#`. Internal tenant members. |
| 9 | NOFORN - External Community | Dynamic | External users (`#EXT#`) whose `country` equals United States. |
| 10 | NOFORN - Internal | Dynamic | Internal members (not `#EXT#`, `userType = Member`) whose `country` equals United States. Captured as `noforInternal`; used as the requestor scope of the assignment policy (resource 16). |
| 11 | NOFORN - All Community | Dynamic | All users whose `country` equals United States. |
| 12 | Community Collab Sync - All | Static | The outbound synchronization gate. Captured as `group`. Members are provisioned outward to partner tenants. Serves as both the access-package target and the scope of the cross-tenant synchronization jobs. |

### Entitlement management

| # | Resource | Action |
|---|---|---|
| 13 | Catalog — Community Collaboration | *Create.* The access package catalog. Captured as `catalog`. |
| 14 | Catalog resource — add All group | *Create.* Adds group 12 to the catalog as a resource. Applied with retry-and-backoff to accommodate directory replication latency. |
| 15 | Access package — Community Collaboration - All | *Create with role scope.* Creates the access package, then attaches the group's Member role scope after polling for the catalog resource to become available. Captured as `accessPackage`. |
| 16 | Policy — Community Collaboration - All (no approval) | *Create.* An assignment policy scoped to the **NOFORN - Internal** group's members (`specificDirectoryUsers`), permitting self-service request without approval. Scoping the requestors to the US-country internal members group replaces the former separation-of-duties exclusion. |

### Cross-tenant synchronization (staged)

These resources are applied once per partner. They configure, but do not start, outbound
provisioning.

| # | Resource | Action |
|---|---|---|
| 17 | Sync inbound-allow | *Configure (PUT).* Enables inbound user synchronization for the partner. If already enabled, the resulting conflict is reported as skipped. |
| 18 | Sync app | *Create with guard.* Instantiates the cross-tenant synchronization gallery application named `CTS - <Partner>`. If a synchronization application with a different name already exists, the guard emits a warning and skips creation. Captures the service principal identifier and the `msiam_access` application role identifier. |
| 19 | Sync job | *Create.* Creates the `Azure2Azure` provisioning job on the service principal. Requires the captured service principal identifier. |
| 20 | Sync secrets | *Configure (PUT).* Saves the credentials `CompanyId = <partner tenant ID>` and `AuthenticationType = SyncPolicy`, which bind the application to its target tenant. Requires the captured service principal identifier. |
| 21 | Sync scope | *Create.* Assigns group 12 to the service principal's `msiam_access` role so that its members are provisioned outward. Existence is evaluated client-side because the `appRoleAssignedTo` endpoint does not support `$filter`. Requires the captured service principal identifier. |

## Operational considerations

### Synchronization is configured but not started

The template provisions the outbound synchronization application, job, secrets, and scope,
but does not start provisioning. Starting synchronization has a cross-tenant prerequisite:
the partner tenant must have enabled inbound synchronization for this tenant by applying
its own configuration. Provisioning should be started as a separate operation once both
tenants have completed their configuration.

### Synchronization application identity

A cross-tenant synchronization application's target tenant is stored only in the
write-only `CompanyId` secret and cannot be read back through Microsoft Graph.
Consequently, the `CTS - <Partner>` display-name convention is the sole durable, readable
handle that associates an application with a partner, and it must be preserved. An existing
application with a non-conforming name is intentionally not adopted; to bring such an
application under management, rename it to `CTS - <Partner>` and re-apply the template.

### Ordering of the default block and partner allow entries

On a newly provisioned tenant, applying the default inbound block presents no risk. On a
tenant with existing inbound collaboration, partner allow entries should exist before the
default is set to blocked, so that no active partner relationship is interrupted. The
template orders partner configuration (resource 3) before the default (resource 4)
accordingly.

### Resource ordering and captured values

Resources that produce captured values (`noforInternal`, `group`, `catalog`, and
`accessPackage`) are declared before the resources that consume them, so that token
references resolve at apply time. New resources must be placed in dependency order.

## Idempotency and re-execution

The template may be applied repeatedly. Each run reports its actions:

- **Created** or **Found** — a new object was created, or an existing object was detected
  and left unchanged.
- **Applied** — a configure or role-scope operation re-asserted the desired state,
  independent of whether a change occurred.
- **Skipped** — no action was required, a prerequisite was unavailable, or a conflict
  indicated the desired state already existed.
- **Warning** — a precondition or guard was triggered, such as a restrictive B2B domain
  policy or an existing unmanaged synchronization application.

A read-only preview is available with `-WhatIf`; `-Verbose` additionally reports each
Microsoft Graph request.

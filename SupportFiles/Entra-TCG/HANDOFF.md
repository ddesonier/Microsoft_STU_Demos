# Entra-TCG — Session Handoff

> Purpose: get a new chat/agent up to speed on this project quickly. Read this first,
> then skim `ReadMEs\README-Engine.md`, `README-Collaboration.md`, and `README-Hardening.md`.
> Last reviewed: 2026-09-11.

## 1. Where things live

- **Git root:** `C:\Scout\TCGs\IC-CIO-ZT`
  - Remote `origin`: `https://ODNI-Services@dev.azure.com/ODNI-Services/IC-CIO-ZT/_git/IC-CIO-ZT`
  - Branch: `main`
  - Top-level `README.md` is still the default Azure DevOps stub (all TODOs) — not project docs.
- **Project folder:** `C:\Scout\TCGs\IC-CIO-ZT\Entra-TCG`
  - `Scripts\` — `Apply-GraphTemplate.ps1` (engine), `Initialize-TcgDeployerApp.ps1` (certificate
    app-registration setup + token helper for app-only `-AccessToken`, Cloud Shell),
    `Configure-TeamsFederation.ps1`, `Start-CommunitySync.ps1`
  - `Templates\` — `collaboration.json`, `hardening.json`
  - `ReadMEs\` — `README-Engine.md`, `README-Collaboration.md`, `README-Hardening.md`, `CloudShellDeployment.md`

## 2. Mental model (the whole project in one paragraph)

A single **generic engine** (`Apply-GraphTemplate.ps1`) reads a declarative JSON template
(`{ graphScopes, resources[] }`) and applies each resource **idempotently** to Microsoft
Entra through `Invoke-MgGraphRequest`. The engine has **no domain knowledge** — every URI,
body, and decision lives in the template. Two templates drive it: **collaboration.json**
(cross-tenant community collaboration, multi-tenant via `forEach` partners) and
**hardening.json** (single-tenant security baseline). Two things that can't be expressed as
Graph calls have their own scripts: **Teams federation** (Teams `Cs*` cmdlets) and
**starting cross-tenant sync** (needs a cross-tenant precondition met first).

## 3. The engine — capability reference

Each resource: `name`, `type` (readability/behavior hint), `mode`
(`create` | `configure` | `assert`), plus capability blocks:

| Block | Meaning |
|---|---|
| `exists` | Lookup (single or ordered array; first match wins) to decide if object is present. Supports `match`, `capture`, `notFoundStatuses`. |
| `create` | POST/PUT when `exists` found nothing. Extras: `guard` (pre-create GET that warns+skips), `retryCount`/`retryDelaySeconds` (replication lag), `capture`. |
| `configure` | PATCH/PUT applied every run (drift correction). Extra: `skipOnConflict` (409/"conflict" ⇒ `[Skipped]`). |
| `assert` | Read-only precondition; warns on fail, never mutates. Supports `decode: json`. |
| `roleScope` | `lookup` (poll until present) + POST — entitlement-management role wiring. |
| `forEach` | Fan resource over a collection (`{ source: "partners", var: "partner" }`). |
| `dependsOn` | Human note only — engine runs in array order. |
| `requires` | Skip resource if a prerequisite capture (e.g. `spId`) is missing. |
| `captureAs` | Store found/created object under a name, promoted **globally** for `{{name.id}}`. |

**`match` mini-JMESPath:** `value`, `value[0]`, `[0]`, `value[?prop]`,
`value[?prop=='literal']`, `a|[0]`, dotted paths. Tokens inside `match` resolve first.

**Tokens (`{{...}}`):** `{{graph-endpoint}}` (env base URL), `{{partner.tenantId}}` /
`{{partner.displayName}}` (in `forEach`), `{{<captureAs>.id}}`, and per-partner captures
`{{spId}}` / `{{appRoleId}}` / `{{jobId}}`. Unresolved tokens fail loudly; under `-WhatIf`
they render `<unresolved:...>` and the dependent lookup is skipped.

**Capture scopes:** global singletons (`group`, `catalog`, `accessPackage`, `noforInternal`,
plus any `captureAs`) and per-partner (`spId`, `appRoleId`, `jobId`, keyed by tenant ID).

**Action kinds / colors:** `Created` (green, new), `Found` (cyan, existed untouched),
`Applied` (green, state enforced), `Asserted` (blue), `Skipped` (gray), `WhatIf` (magenta),
`Warning` (yellow), `Error` (red). `Created` vs `Found` is the true new-vs-existing signal.

**Errors:** default is continue-and-report (exit code 1 if any errors); `-StopOnError` halts.

## 4. Templates

### collaboration.json (21 resources) — multi-tenant, partner-driven
Partners come from `-Domain` (resolved to tenant IDs via OIDC metadata) and/or `-TenantId`.
Model: **internal users** request an access package in their home tenant → membership in the
gate group **Community Collab Sync - All** scopes them into outbound cross-tenant sync
(provisioned as *members*, not guests). Inbound denied by default; partners are an allow-list.
Flow, in array order:
1. Authorization policy (invites) — configure.
2. B2B invitation domain restrictions — assert (warn only).
3. Cross-tenant access **per partner** (`forEach`) — create + configure trust/consent/B2B.
4. Cross-tenant access default — block inbound.
5–12. Groups (dynamic: External Community Members/Guests, External Users All, Internal Members
   Only, NOFORN - External Community / Internal / All Community; static:
   **Community Collab Sync - All** `captureAs group`).
   Note: "External Users All" dynamic group is also created here (shared with hardening).
13–16. Entitlement mgmt: catalog `captureAs catalog`, add group as catalog resource
   (retry/backoff), access package `captureAs accessPackage` + role scope, assignment policy
   (self-service, no approval; requestors scoped to **NOFORN - Internal** `captureAs
   noforInternal` via `specificDirectoryUsers` — replaces the old Forn - All SoD exclusion).
17–21. **Per partner** (`forEach`): sync inbound-allow (PUT), sync app
   `CTS - <Partner>` (create with **guard**, captures `spId`/`appRoleId`), sync job
   (`Azure2Azure`), sync secrets (`CompanyId`=partner tenant, `AuthenticationType`=SyncPolicy),
   sync scope (assign gate group to `msiam_access`). **Sync is configured but NOT started.**

Key operational notes:
- `CTS - <Partner>` display name is the **only** durable readable handle to a sync app's
  target tenant (`CompanyId` secret is write-only). An app with a non-conforming name is
  intentionally not adopted — rename to `CTS - <Partner>` and re-apply to manage it.
- Partner allow entries (res 3) are ordered before the default block (res 4) so existing
  partner relationships aren't interrupted.

### hardening.json (16 resources) — single tenant, no partner input
Applies to the signed-in tenant. `-Environment` still selects the cloud. All CA policies land
in **report-only** (`enabledForReportingButNotEnforced`) and exclude the break-glass group.
- Groups 1–2: Emergency Access `captureAs emergencyAccess` (**created empty — populate with
  2+ cloud-only accounts before enabling any policy**), External Users All (dynamic, shared
  with collaboration).
- 3–4: default user-permission lockdown (no app/SG/tenant creation, user consent off);
  group/Team owner consent disabled via `Group.Unified` settings **create-if-missing**
  (won't overwrite an existing settings object).
- 5: PIM auth context **`c90`**.
- 6–16: CA policies TCG01–TCG11. MFA uses built-in **auth strength `…0002`** via the nested
  `authenticationStrength: { id }` form (not `@odata.bind`, not legacy `mfa` grant). Future
  goal: move registration/admin/PIM to phishing-resistant `…0004`.
- **Authentication methods are NOT scripted** (removed 2026-09-11): FIDO2/CBA/TAP and their pilot
  groups (Passkey/CBA/Windows Hello Users) were dropped because the auth-methods policy enforces
  instantly (no report-only) and a pilot-group scope can lock out the operator. Configure methods
  manually in the portal.
- Not scripted (manual): authentication methods, PIM role config, admin-consent workflow,
  LinkedIn/admin-portal restrictions (no Graph v1.0 API).
- If auth context `c90` is taken, change it in 3 places (the context resource + TCG10 + TCG11).

## 5. Companion scripts (can't run through the engine)

- **Configure-TeamsFederation.ps1** — Teams federation + meeting/lobby lives in the
  `MicrosoftTeams` module (`Cs*` cmdlets), not Graph. Replaces allowed-domains with exactly
  the community domains (closed allow-list); `-SetLobbyBypassForFederated` opt-in.
  Run on every community tenant.
- **Start-CommunitySync.ps1** — the final "start job" step has a **cross-tenant precondition**:
  `validateCredentials` only succeeds after the *partner* tenant has enabled inbound sync for
  you (i.e. they've run the collaboration template too). Safe to re-run as tenants come online;
  reports `NotReady` (not an error) until the partner is up.

## 6. Prerequisites

- **PowerShell 7+**, module **Microsoft.Graph.Authentication**
  (`Install-Module Microsoft.Graph -Scope CurrentUser`). Teams script needs **MicrosoftTeams**.
- **Licensing:** Entra ID P1/P2 (cross-tenant sync is licensed; hardening risk policies +
  PIM need **P2**).
- **Graph scopes** are declared per template (`graphScopes`). Under `-WhatIf` the engine
  swaps in read-only equivalents.
- **Directory roles** for delegated execution: Global Admin, or the documented least-privilege
  role combos (see each README).

## 7. End-to-end run order (per tenant)

```powershell
cd C:\Scout\TCGs\IC-CIO-ZT\Entra-TCG\Scripts

# 1. Preview then apply hardening (single tenant)
.\Apply-GraphTemplate.ps1 -TemplatePath ..\Templates\hardening.json -WhatIf
.\Apply-GraphTemplate.ps1 -TemplatePath ..\Templates\hardening.json

# 2. Preview then apply collaboration (per partner)
.\Apply-GraphTemplate.ps1 -TemplatePath ..\Templates\collaboration.json -Domain fabrikam.com -WhatIf
.\Apply-GraphTemplate.ps1 -TemplatePath ..\Templates\collaboration.json -Domain fabrikam.com,contoso.com

# 3. Teams federation (every community tenant)
.\Configure-TeamsFederation.ps1 -Domain fabrikam.com,contoso.com

# 4. AFTER every partner has applied collaboration, start sync (re-run as they onboard)
.\Start-CommunitySync.ps1 -Domain fabrikam.com,contoso.com
```
Post-apply manual steps: populate Emergency Access (2+ cloud-only accounts), configure
authentication methods manually in the portal (add yourself + break-glass to any pilot group
first), review report-only impact, enable TCG01–TCG11 individually, run PIM Discovery and convert
standing access to eligible.

## 8. Fixes applied (2026-09-10) and remaining notes

Resolved this session (verified: JSON valid, PS parses, README cross-refs consistent):
1. **`-TemplatePath` is now mandatory** (no default). The engine validates the file exists
   and is valid JSON with a `resources` array before connecting to Graph. All README/help
   examples updated to pass `-TemplatePath` explicitly.
2. **Removed stale `tenant-config-fixed.json`** reference from the engine help.
3. **README-Collaboration renumbered to 23 resources** — the shared "External Users All"
   dynamic group is now listed (row 6), and downstream cross-references (SoD = resource 17,
   gate group = group 13) were corrected. Group rows now use actual template display names
   (e.g. "NoForn - External Community").
4. **`mailNickname` de-duplicated** — "External Community Guests" is now `ExtCommunityGuests`
   (was colliding with "External Community Members" on `ExtCommunityMembers`). Note the
   "External Users All" group intentionally shares `ExternalUsersAll` across both templates
   because it is the *same* group definition (lets each template run standalone).
5. **`Start-CommunitySync.ps1` env set aligned with the engine** — `China` removed, `Custom`
   added (with `-CustomGraphEndpoint` / `-CustomLoginHost` / `-CustomMgEnvironment`).
6. **Companion-script comments renamed** — all `Apply-TenantConfig.ps1` references now say
   `Apply-GraphTemplate.ps1` (collaboration.json), noting they run after that template.

New capability (2026-09-10, revised 2026-09-11):
- **`-AccessToken [securestring]` on `Apply-GraphTemplate.ps1`.** Authenticates Microsoft Graph
  with a **pre-acquired access token** instead of Graph PowerShell's interactive/device-code
  sign-in (which is broken in the user's non-public/sovereign cloud). Design chosen deliberately
  over an in-script `az login` (option A in that discussion): the engine owns no auth — you
  acquire the token any way you like (recommended: a **dedicated app registration** with the
  template's Graph **application** permissions, admin-consented, signing in **app-only** via
  `az login --service-principal` — no device code; certificate or managed identity also work).
  The engine decodes the token JWT (`Get-JwtClaims`) to read app **roles** or delegated **scp**,
  connects via `Connect-MgGraph -AccessToken` (SecureString on SDK v2, plaintext on v1), and
  validates with `Test-ScopeSatisfied` (ReadWrite⇒Read + broad Directory.* coverage). It
  **hard-fails only** when the token carries zero Graph roles/scopes (wrong audience/expired/no
  consent). Templates now declare **both** scope sets (`graphScopes.delegated` +
  `graphScopes.application`), so app-only tokens validate against the explicit application app
  roles and delegated tokens against the delegated scopes; a real miss warns. (Where they
  differ: `GroupSettings.ReadWrite.All` delegated ⇒ `Directory.ReadWrite.All` application.) No `az`, no secret handling inside the engine; not Cloud-Shell-specific.
  Removed the earlier `-UseCloudShell`/`-ClientId`/`-ClientSecret`/`-AuthTenantId` approach.
  Token-acquisition recipes (app-only secret/cert, managed identity, delegated CLI caveat) +
  the storage-account/Azure-Files identity-based Cloud Shell walkthrough in
  `ReadMEs\CloudShellDeployment.md`. Helpers: `Get-JwtClaims`, `Test-ScopeSatisfied`.

Fixes applied (2026-09-11):
- **Deleted junk file `tatus`** (a mis-redirected `git status` accidentally committed in
  d4d6fc6). Staged for removal.
- **collaboration.json reduced to 21 resources** — reflects the manual removal of the
  **Forn - All** dynamic group. Removed the now-orphaned **SoD "Access package incompatible
  group - Forn - All"** resource, which referenced the never-captured `{{fornAll.id}}` token
  and would have failed at apply time (unresolved token). No resource now references `fornAll`.
- **Docs reconciled to the actual template** — README-Collaboration group table (8 groups,
  rows 5–12, using real display names incl. `Community Collab Sync - All` as the gate group),
  entitlement table (13–16), and sync table (17–21) renumbered; the SoD row and the
  "Separation of duties is unidirectional" section removed; `fornAll` dropped from the
  captured-values note. This HANDOFF's section 4 updated to match.
- **Requestor scope replaces SoD (design change).** The assignment policy (resource 16) now
  scopes requestors to the **NOFORN - Internal** group via `allowedTargetScope:
  specificDirectoryUsers` + `specificAllowedTargets` (`groupMembers` → `{{noforInternal.id}}`),
  instead of `allMemberUsers`. Same net effect as the old Forn - All SoD exclusion
  (only US-country internal members can request), with fewer objects. Self-service request and
  no-approval are unchanged; the granted role is still **Member** of the gate group.
  Added `captureAs: noforInternal` to the NOFORN - Internal group (resource 10).
- **Fixed idempotency bug on the NOFORN - Internal group** — its `exists` filter looked up
  `'NOFORN Internal'` while it creates `'NOFORN - Internal'` (hyphen), so re-runs would never
  match and could duplicate. Filter corrected to the real display name.
- **Standardized group `mailNickname`s to PascalCase** (`NoFornInternal`, `NoFornAll`); shared
  `ExternalUsersAll` left as-is to stay identical to hardening.json.

New capability (2026-09-11):
- **`graphScopes` split into `delegated` + `application` sets.** Both templates now declare
  `graphScopes: { delegated:[...], application:[...] }`. The engine requests the **delegated**
  set for interactive sign-in (and its read-only map under `-WhatIf`), and validates an
  **app-only `-AccessToken`** against the **application** app-role set (a delegated token still
  validates against `delegated`). This keeps app-only-only permissions out of the delegated
  consent request — notably `Directory.ReadWrite.All` (application) vs `GroupSettings.ReadWrite.All`
  (delegated) for hardening. A legacy flat `graphScopes` array is still accepted as delegated.
- **New `Scripts\Initialize-TcgDeployerApp.ps1`** — minimal-footprint app-only credential path that runs
  entirely in **Cloud Shell** (no extra Azure resources, no client secret, no compute). Creates/
  reuses an app registration + SP, ensures a **self-signed certificate** credential, reads the
  template's `graphScopes.application` (single source of truth), grants those Graph app roles to
  the app's SP (app-only admin consent) with a verify-retry loop, and with **`-AcquireToken`**
  cert-signs-in and returns a Graph token as a **SecureString** for `Apply-GraphTemplate.ps1
  -AccessToken`. Takes a **`-TemplatePath`** (like the engine) so it works with any template and
  with files uploaded flat to Cloud Shell home. Chosen over a managed identity because a user-assigned MI needs a compute host
  (VM/VMSS/ACI/Function) that Cloud Shell can't carry — an extra resource this design avoids.
  Recipe in `CloudShellDeployment.md`; README permission sections note the app-role set.

Fixes to hardening.json (2026-09-11, from live apply testing):
- **Conditional Access auth-strength grant control: switched from the `@odata.bind` navigation
  form to the nested object.** TCG01, TCG03, TCG07, TCG08, TCG09, TCG11 (auth-strength-only
  grant) returned **400 BadRequest 1007 ("object … does not match the schema")** on create. The
  template used `"authenticationStrength@odata.bind": ".../authenticationStrengthPolicies/<id>"`,
  which Graph did not honor here — leaving those policies with no effective grant control. Replaced
  all **7** occurrences (including TCG04) with the nested form:
  `"authenticationStrength": { "id": "<id>" }`. TCG04 kept working throughout because it also
  carries `builtInControls: ["riskRemediation"]`; that contrast identified the issue. (An earlier
  pass had also removed the empty `builtInControls: []` from the six, which is still correct and
  retained.) Verified: JSON valid (22 resources), 7 nested auth-strength blocks, 0 `@odata.bind`
  or `authenticationStrengthPolicies` references remain.
- **Removed authentication-methods setup entirely.** Dropped the FIDO2, CBA (x509), and TAP
  method-configuration resources **and** their now-orphaned pilot groups (Passkey Users, Windows
  Hello Users, CBA Users). Rationale: the auth-methods policy has **no report-only mode** and
  enforces tenant-wide the instant it is written, so scoping a method to a pilot group can
  immediately lock out the operator (this happened during testing). Kept Emergency Access and the
  shared External Users All group. Also removed the now-unused `Policy.ReadWrite.AuthenticationMethod`
  from both `graphScopes` sets (least privilege). hardening.json is now **16 resources**; README
  and HANDOFF renumbered (groups 1–2, consent 3–4, PIM context 5, TCG01–11 = 6–16). Auth methods
  are now a documented **manual** portal step.
- **Renamed the deployer script and its app.** `New-DeployerApp.ps1` →
  `Initialize-TcgDeployerApp.ps1` (approved verb `Initialize-`; the script is idempotent —
  it ensures/configures an existing app rather than only creating one). Default app/SP display
  name `Entra-TCG Deployer` → **`TCG Deployer`** (a generic deployer identity, not Entra-specific
  and reusable for any API/config). Cert files now `tcg-deployer.{key,crt,pem}`. All doc/script
  references updated, including the manual app-registration recipe in CloudShellDeployment.md.

New capability (2026-09-11):
- **`Initialize-TcgDeployerApp.ps1` custom-cloud support.** Added `-Environment Custom` (mirroring the
  engine) with `-CustomAzCloud` (the Azure CLI cloud name for `az cloud set`) and
  `-CustomGraphEndpoint` (that cloud's Graph endpoint / token audience). Built-in Global / USGov /
  USGovDoD unchanged; Custom validates both params are supplied. Removed the now-unused `Portal`
  field from the deployer's CloudMap (the manual-consent block that used it was already gone).

Companion docs updated (2026-09-11):
- Reconciled `C:\Clawpilot\WIP\TCGs\working\TCG-Entra-Collaboration.docx` and
  `TCG-Entra-Hardening.docx` with the current README/template state. Collaboration now references
  `Apply-GraphTemplate.ps1`, 21 resources, `NOFORN - Internal` requestor scoping, and the shared
  `Initialize-TcgDeployerApp.ps1` / `-AccessToken` Cloud Shell deployment path. Hardening now
  reflects 16 resources, no scripted authentication-method resources or pilot groups, split
  delegated/application permission sets, and manual auth-method/PIM follow-on steps.

Top-level docs updated (2026-09-11):
- Replaced the Azure DevOps placeholder at `C:\Clawpilot\New folder\IC-CIO-ZT\README.md` with
  a project landing page covering repository layout, collaboration/hardening summaries,
  deployment flow, Cloud Shell / `Initialize-TcgDeployerApp.ps1` guidance, operational safety
  notes, and links to the technical README files.

Still open / by design:
- `git` CLI was not on PATH in the review environment — repo state read from `.git\config`.
  Nothing was committed; changes are working-tree only, pending user review.

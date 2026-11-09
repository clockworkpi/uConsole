# External coding agents

Workbench does not embed a coding assistant. Its local MCP adapter exposes
registered workspaces through the same headless runtime and image commands.
The current transport is newline-delimited JSON-RPC over stdio, implementing
MCP version `2025-11-25` initialization, tools and read-only resources; see the
[MCP transport specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).

## Register a server

Use your coding CLI's MCP registration mechanism with this command and argument
array. There is no GUI dependency and no listening network service:

```json
{
  "command": "uconsole-mcp",
  "args": [
    "--workspace", "dev=/absolute/path/to/workspace",
    "--files-root", "/absolute/path/to/exchange",
    "--allow", "boot",
    "--allow", "force-stop",
    "--allow", "guest-exec",
    "--allow", "transfer",
    "--allow", "image-write"
  ]
}
```

From a checkout, use `python3` as the command and prepend
`/absolute/path/to/uConsole/tools/uconsole_mcp.py` to the argument array.
Client-specific configuration wrappers differ; the object above describes the
server command, not a universal client configuration file.

Omit grants you do not want. Without `--allow`, the server permits inspection
only. Workspace paths are registered at startup and cannot be supplied as tool
arguments. Repeat `--workspace ID=PATH` for additional images. File access is
restricted to the resolved `--files-root`, including symlink resolution; this
is an application boundary, not an OS sandbox against other local processes.

| Grant | Effect |
| --- | --- |
| `boot` | Boot/stop an owned VM |
| `force-stop` | Permit forced power removal; required for boot so client disconnect can clean up owned guests |
| `guest-exec` | Root shell commands inside a maintenance guest, not the host |
| `transfer` | Guest upload/download and screenshot writes under the configured host files root |
| `image-write` | Prepare, checkpoint, restore, recover, refresh boot files and export |
| `host-task` | Run fixed tasks from a separately reviewed, digest-pinned owner policy |
| `device-control` | Change modeled power/modem state, inject keyboard input, or connect/disconnect audio and modem USB surrogates in an owned VM |
| `target-recovery` | Run fixed prepared recovery jobs from a separately reviewed policy; never implied by `target-write` |

### Prepared recovery jobs (advanced)

The owner may pass `--recovery-policy /absolute/policy.json` and
`--recovery-policy-sha256 SHA256` to either Workbench or the standalone MCP
server. Standalone execution additionally requires `--allow target-recovery`;
Workbench attachment requires `--agent-allow target-recovery`. Attachment mode
(`--connect`) cannot supply policies or increase grants. Policies are never
auto-discovered from a guest or workspace.

In Workbench, **Recovery jobs** opens the same prepared-job interface. Use
**Review policy…** to display its complete local target identities, paths,
operation effects and pins without contacting hardware. **Approve reviewed
policy…** enables the exact reviewed bytes; it does not start a job or increase
existing clients' grants. Select a job, review it, then explicitly confirm
**Run selected job…**. Keep Workbench open until the result is known. If status
retrieval fails, use **Recheck job status**, not another submission. The result
panel retains failures and makes no implicit rollback or boot-release claim.
Read-only SSH probes disconnect child stdin from the owner's protocol stream;
operations that intentionally send a request over stdin retain their explicit
pipe. This prevents an SSH observation child from consuming MCP input. Accepted
jobs still require their own terminal evidence after any transport failure.

**Discover build target…** reads a normal SSH target's boot identity and kernel
without changing the target. Review that identity, then choose **Build private
recovery image…** to select private credentials (`wpa.conf`, `authorized_keys`,
`ssh_host_ed25519_key`) and a host output parent. After separate confirmation,
Workbench sends fixed builder sources and those credentials over SSH, uses
noninteractive sudo on the native ARM64 target, and builds in exclusive private
scratch. Matching kernel modules and native build dependencies must already be
installed; this action does not install packages. Discovery and building run as
foreground controller jobs without blocking the GUI. Keep Workbench open and
recheck an accepted job rather than submitting again after a status error.

The host retains a private image, its verified transfer checksum, and build
evidence. The target retains its private scratch and logs. Build failure retains
both locations, consumes the displayed discovery, and performs no automatic
retry. A successful build is **not published or boot-qualified**: neither action
writes boot files, changes selectors, reboots, approves recovery policies, grants
agents authority, or replaces whole-card backup and fallback qualification.
These owner controls are deliberately absent from MCP. In the 2026-09-26
physical qualification at `b0458fb`, public-controller discovery, native build,
verified private-image transfer, private mount setup/reboot, publication and
sealed staging preparation passed. All four staging phases, one-shot recovery
boot and fresh enrollment subsequently passed. The current-card backup/restore
round trip also passed: all 7,481 chunks, the final root hash, independent
reconciliation, recovery-hold release, normal boot, staged-file/image cleanup,
and restoration of the original fstab and effective boot permissions were
verified. All backups remain retained; existing filesystem errors were preserved,
not repaired. This fresh-backup restore needed zero changed-byte writes; it is
public-controller workflow evidence, not a new enhanced-image deployment claim.
Enhanced-image native boot, full IDE reimport/retest, and changed-byte rollback
have separate retained evidence in the device plan. Final release-revision
publication checks remain required.

**Prepare private boot mount…** backs up the target's `/etc/fstab`, observes its
normal boot and current public FAT mount, and drafts only the boot mount's
root-only permission options. It checks the candidate with `findmnt` using
temporary target `/run` scratch, then rechecks the unchanged fstab, boot and mount.
**Apply private mount policy…** displays the original and proposed fstab text
before separate confirmation. It applies only that pinned file transaction,
retains the original metadata/content for restoration, and checks the resulting
file. An `apply-attempt/` journal prevents replay after any attempted application.
Neither action remounts, reboots, publishes an image or grants agent permission.
`applied-awaiting-private-mount` does **not** mean effective privacy: a later
fresh mount and non-root read-denial check are still required. Publication keeps
refusing a public mount. Never restore public permissions while private images
remain on the boot filesystem. Cleanup is separate from policy acknowledgement.

**Reboot for private mount…** requires the exact acknowledged fstab transaction
and separate confirmation that target applications will be interrupted. It sends
one normal reboot request only after rechecking the old boot and applied file,
then waits up to ten minutes for a different normal boot with the same native
root, unchanged fstab, private FAT masks/ownership and non-root read denial.
Transport timeout alone is neither failure nor proof of reboot; the subsequent
observations determine verification. The retained `reboot-attempt/` prevents a
second request. **Verify private mount…** performs those checks without rebooting
after a manual reboot or an uncertain request. Each read-only recheck retains a
new observation directory; it never resets the original attempt. A result of
`verified-private-normal-boot` qualifies the mount policy, not recovery fallback
or image deployment. Both controls remain owner-only and absent from MCP.

**Prepare publication…** accepts a completed private build journal and the
SHA-256 of its `acceptance.json`. After owner review it verifies the retained
image and build provenance, reads the target's normal boot and fstab, then
inspects the persistent private mount policy and proposed destination. It writes
an exact publication draft under `publication/` and an owner review with its plan
pin. This step refuses a public boot mount, existing destination, changed build
or reboot. It never publishes, changes mount permissions, alters fstab or approves
an operation. Private mount provisioning remains a separate owner step; this is
not a completed bootstrap wizard.

**Publish reviewed image…** selects that completed preparation and displays its
exact target, source, destination, digest and expected normal boot. Confirmation
authorizes one credential-bearing image publication, not a selector change or
reboot. The controller rechecks the pinned inputs, records durable intent, checks
private policy and absence, and verifies publication acknowledgement and final
private bytes. A `publish-attempt/` journal prevents resubmission even after a
failed preflight. If SSH or final verification fails, retain the journal and
inspect/reconcile the original publication operation; do not delete evidence to
retry. `published-not-boot-qualified` confirms publication only. Use its
`publication/` journal and plan pin for the separate staging preparation; physical
fallback qualification and full-card backup remain required. This owner action
is not exposed through MCP and does not expand agent grants.

**Prepare boot staging…** is an owner-only normal-SSH preparation step, not an
agent permission or a staging action. Select an acknowledged private recovery
image publication journal, its plan pin, a reviewed matched-firmware bundle and
its canonical journal digest, then a new private output directory. Workbench
shows the target and source pins before asking to read the target. Preparation
backs up all nine boot preimages, authors four ordered drafts and a hold review,
then rechecks the normal boot identity, unchanged preimages and private published
image. Failures retain incomplete artifacts and grant no authority. This requires
an already provisioned private recovery image; it does not build/publish that
image, qualify fallback, stage files, reboot, approve a policy, or replace the
whole-card backup required before root writes.

**Enroll recovery session…** records a fresh RAM boot after the separately
authorized boot transition. Select the sealed staging preparation and its
acceptance pin, pinned recovery host/key/known-hosts files, expected kernel and
hardware serial, and expected firmware selection (one-shot tryboot or persistent
recovery). Workbench reads the new RAM boot UUID itself, then pins that exact
UUID for the remaining checks; it never follows a second boot automatically.
The owner is taken from
sealed staging, never guessed or generated again. After confirmation, Workbench
performs read-only pinned SSH identity/selection checks and writes a private
`session/binding.json` plus its digest. It does not acquire a lease, reboot,
write the card, approve policy or increase agent grants. Use the resulting
session path and pin in a separately reviewed recovery-job policy.

Enrollment is not lease recovery: do not use it when an existing host session
already owns that boot. A per-staging, per-boot claim prevents duplicate wizard
enrollment once the boot is known, including after a subsequent observation failure.
Discovery failure before a verified boot still retains its incomplete journal.
Retain the original job,
claim and evidence; never delete them to reset a sequence or automatically retry.
Only `enrolled-not-leased` denotes a completed local binding, not a live lease
or qualified fallback. This owner action is deliberately absent from MCP.

After all four phases have been applied through **Target changes → Review
recovery staging…**, **Boot recovery and enroll…** bridges the acknowledged
staging to a fresh session. Select the same sealed staging and pinned recovery
credentials, kernel and serial. Separate confirmation authorizes one tryboot
request and interrupts target applications. The host requires every phase's
acknowledgement and no pending/restoration attempt; the target rechecks the
normal boot, private image, and all nine staged file snapshots under its lock
before rebooting. An exclusive staging claim prevents another request even
after uncertainty. Observation retries do not resend the reboot, and enrollment
pins the first verified new RAM UUID rather than following a second boot.

This action does not acquire a lease, prove fallback or authorize root writes.
Keep physical power-cycle access available for an unqualified recovery image.
After `recovery-boot-enrolled-not-leased`, prepare, approve and run the backup
promptly; an unleased recovery boot can expire back to normal. Workbench enables
the existing backup-draft action using the nested enrollment evidence. Failed
boot/enrollment evidence remains intact; do not reset the claim or resubmit the
reboot. The bridge is owner-only and absent from MCP.

After successful enrollment in this panel, **Prepare backup job…** asks where
to retain the future full-card archive and its evidence. It checks the sealed
enrollment and credentials, locks the local session without renewing its lease,
and reads the offline SD identity and geometry twice on that exact RAM boot.
The result is a private policy containing only `backup-card`. Workbench opens
it for review; **Approve reviewed policy…** and running the selected job remain
separate owner actions. Check the observed card identity and archive destination
before approval. Drafting neither creates a backup nor grants root-write access.
It refuses a busy session, uncertain renewal, changed boot/card/credentials, or
output overlapping the enrolled session. Retain failure evidence; do not reset
the lease journal. Policy authoring is owner-only and absent from MCP. This
step does not provide a complete deploy/restore wizard.

After enrollment, **Prepare current-card hash…** uses the same guarded SD
identity inventory to draft only a `hash-card` job. Review its target, exact
boot, card identity and evidence destination, then separately approve and run
it. Preparation reads identity/geometry twice but neither renews a lease nor
hashes the card. Execution later captures independent current-card/range hashes
under the existing recovery session. These observations are inputs to hold and
deployment planning, not a backup or permission to write/release the card. A
changed card/boot, a busy session or an unresolved renewal prevents drafting;
the authoring action cannot generate write or release operations and is absent
from MCP. Existing authorized recovery clients can run the owner-approved ID.

**Prepare recovery hold…** compiles an install-hold plan and unapproved job
policy entirely offline. Select the sealed staging directory from enrollment,
the completed current-card hash receipt and pin, and an explicitly chosen
original-backup or verified-export source manifest and pin. The root guard comes
from that independent source, never from blindly accepting the observed card.
Workbench checks staging/session identity and the exact boot, revalidates the
selected records after queueing, and refuses a busy or uncertain session.
It also requires the original rollback archive to remain present, private and
identical to the selected source's recorded fingerprint; a manifest alone is
not sufficient to prepare this draft. Keep the archive through deployment and
restoration; the later root-transfer worker rechecks its source independently.
Review the resulting `install-hold` policy, approve it separately, then run it
only after reviewing the persistent-recovery selector warning. Preparation does
not contact the device, renew its lease, install the hold, reboot, grant root
writes, or prepare a normal-boot release. Execution uses the existing guarded
selector worker and must independently confirm current file and root preimages.
Retain uncertain attempts and reconcile them; never redispatch an attempted
hold. This owner-only authoring route is absent from MCP; existing authorized
recovery clients may execute only the separately approved job ID.

After an **acknowledged** install-hold, **Reboot into held recovery…** uses the
existing enrollment, sealed staging and owner-pinned hold plan. It locks the
existing durable session, refuses uncertain renewals, renews that session, and
records a one-use reboot claim. On the target, the fixed worker checks the exact
RAM boot and card layout, holds writer-exclusion locks, checks every held boot
file and recovery image with a read-only boot mount, unmounts it, and checks the
live watchdog budget again before one persistent-recovery reboot. It does not
write the SD card or release the hold. An SSH timeout never permits a retry.
The first newly verified RAM boot is pinned and enrolled with `tryboot=0`;
the old lease sequence is never copied or reset. The **new session is not yet
leased**: promptly approve its next job, and independently reconcile the hold
before deployment. This owner-only control is not available through MCP and
does not grant root-write or normal-release authority. Retain all failed
attempts; read-only enrollment of an observed fresh boot is separate from
rebooting it again. Host/worker-boundary tests are not physical qualification
of this new control.

**Prepare reconciliation…** authors an observation-only policy from the current
enrollment and an explicitly selected `hold` or `root` attempt journal and plan
pin. It validates the original dispatch (including pinned source health for
root attempts), rejects an active journal/session lock or uncertain lease, and
preserves the original acceptance even when it is uncertain. An explicitly
enrolled new boot is allowed; the old lease and completion are never adopted.
Preparation is entirely offline and does not approve or execute anything.
Review the resulting `reconcile-hold` or `reconcile-root` policy separately,
then approve and run it. Execution uses the existing fenced inspection and
independent full-card hash workers. Hold inspection may unmount the exact stale
boot mount, flushing prior pending boot writes; it does not rewrite selectors.
Neither operation retries root writes, repairs a filesystem, reboots, or grants
normal-boot release. This preparation control is owner-only, not an MCP method;
authorized agents may execute only the separately owner-approved job ID.

**Prepare enhanced-root deployment…** and **Prepare original-root restoration…**
compile a root-only transfer plan and unapproved policy from the currently
enrolled persistent recovery boot. Select the original install-hold journal,
its completed `reconciled-after` observation from this boot, the verified
derivative or original-source manifest, and the source filesystem-health
directory; supply the owner-recorded pin for each. The original backup must
still exist with its recorded private-file identity. Deployment also rechecks
the enhanced image's identity and requires clean pinned derivative-root health;
it cannot waive filesystem errors. Original restoration displays a separate,
default-No confirmation before retaining an explicit decision to restore known
original filesystem errors without repair. After either draft, policy approval
and execution remain separate. The existing workers independently reverify
sources, target identity, persistent hold, live lease and protected ranges,
retain every uncertain attempt, and never retry it automatically. These
preparation controls do not contact the target, renew leases, write root bytes,
grant agent authority, reboot, or release normal boot. Native compatibility and
post-write reconciliation/release remain separate gates. Host fixtures for the
new controls are not physical qualification of this route.

**Prepare normal-boot release…** requires the root operation's plan pin and a
completed independent root-reconciliation journal, plus the original
`hold-review.json` and its pin (normally in sealed staging) and the retained
original backup. Workbench reclassifies the saved fence, file inspection and
full-card hashes, rather than trusting a success label. Only the approved source
root with intact protected ranges and held files qualifies; an incomplete
same-boot writer that requires a new recovery boot does not. Independent recovery
in a freshly enrolled boot can qualify without falsely changing the original
attempt to completed. Source health stays bound to the original operation;
known original filesystem errors require a separate default-No decision.
The result is an unapproved `release-hold` policy. Preparation is offline and
does not renew a lease, change boot selection, reboot, write root bytes or grant
agent authority. Review/approval and selector execution remain separate. Normal
return and cleanup are subsequent actions; keep the private boot mount until
all credential-bearing boot artifacts have been removed through their journals.

After an acknowledged selector release, **Reboot to normal system…** binds the
enrolled RAM session, release plan and original sealed staging. The target
worker takes writer-exclusion locks, verifies all released boot files and the
private recovery image with a read-only mount, unmounts it and rechecks the live
lease before one fixed reboot. A durable claim prevents a second submission,
even after an SSH timeout. **Verify normal return…** performs only observations:
it neither renews the old lease nor repeats a reboot, and can use the retained
enrollment after a panel restart. A verified return requires a new normal boot
UUID, the original native root selection and machine identity, the expected
kernel and hardware serial, and stable boot bookends. These controls are
owner-only, not MCP methods. Successful return disables stale RAM-session
controls; it does not claim application behavior, filesystem health or cleanup.
Keep failed evidence and the private boot mount; cleanup follows verified return.

**Clean up recovery artifacts…** is owner-only and requires the sealed staging
pin and verified new normal-boot UUID. It restores the four staging phases in
reverse order using their original journals, skipping only confirmed restores.
Uncertain attempts require explicit reconciliation, never automatic retry.
After all nine boot-file preimages match, the original publication journal
removes only its owned private recovery image. The target rechecks those
preimages and the exact boot under the image operation's exclusion lock.
The receipt verifies image absence and a stable normal boot, but does not grant
agent authority, reboot, write root bytes or restore public FAT permissions.
The private mount remains in place: restoring its original policy requires a
separate credential-artifact inventory and subsequent mount verification.

**Restore original boot-mount policy…** uses the pinned original fstab transaction
(the `transaction` directory, including for older retained preparations), sealed
staging and the cleanup boot UUID. It requires acknowledged original application,
completed reverse staging and journaled image removal. Under the target lock it
checks the normal boot, all nine preimages and effective private mount, inventories
the boot tree, restores only the original fstab, then repeats the guards. The
bounded no-follow inventory refuses Forge/recovery names, special files, links,
nested mounts and byte-identical aliases of the known credential image. It neither
deletes unexpected files nor claims to scan unrelated user files for all secrets.
A durable attempt prevents repeat submission even after transport uncertainty.
This owner-only action does not remount or reboot: current permissions stay
private, and original effective permissions are not qualified until a subsequent
fresh mount is independently verified.

**Reboot for original permissions…** completes that final transition only after
an acknowledged original-fstab restoration. The original privacy transaction and
plan pin reconstruct the retained cleanup evidence, including after a panel
restart. The target checks the original fstab, all restored boot preimages,
effective private mount and unchanged artifact inventory under its exclusion
lock before one reboot. A durable one-use claim prevents resubmission.
**Verify original permissions…** is read-only and can finish observation after
an uncertain reboot without repeating it. It requires a different normal boot
with the original root, effective original FAT masks and non-root readability,
restored file contents and unchanged inventory. The expected FAT mode change is
accounted for explicitly. These controls are owner-only and grant no MCP access;
successful mount verification does not imply filesystem health or native
application qualification.

After reopening Workbench, **Open retained enrollment…** loads the original
enrollment receipt pin, key and pinned host-key file for subsequent plan
preparation. It validates the complete local lease journal under its nonblocking
owner lock; a session held by another process is refused. This operation is
offline and does not renew or adopt a target lease, reset sequence numbers,
retry a pending request, approve policy or grant agent permissions. Pending
renewals remain pending and require the separately reviewed `retry-lease` path.
The UI does not claim the target boot or lease is still live; subsequent jobs
must validate them against the target before acting.

For a completed retained backup, **Prepare retained backup source…** is an
owner-only, host-only action. Select the private backup directory and its
owner-recorded canonical `acceptance.json` SHA-256, then review the card size,
card digest and plan pin before confirmation. Workbench reads the complete
compressed archive twice: first to derive independent range hashes, then to
verify every root chunk against those bytes. It preserves the original archive
and creates only small private evidence records, not another full-card copy.
The resulting `source/manifest.json` and its displayed pin can feed separately
reviewed restore preparation. Changed receipts, archive identity, checksum or
permissions fail closed with evidence retained; an existing output is never
overwritten. This action neither checks/repairs filesystem health nor contacts
the target, renews a recovery lease, or grants restore/release authority. Do not
rely on offline preparation to keep an unleased recovery boot alive. Filesystem
inspection and guided deploy/restore policy preparation are separate steps.

**Check retained backup filesystems…** takes the same pinned backup and a new
output directory. Confirmation displays the storage requirement: twice the
card's byte length plus a reserve. Workbench refuses insufficient space or
missing checkers before creating output. It materializes a byte-verified private
`image/image.img`, copies both partitions, and runs read-only FAT/ext4 checkers
on their file descriptors without mounting or repairing anything. The checked
image can be imported into a separate forge workspace; the retained archive
remains the rollback source. `health/acceptance.json` and the displayed health
pin feed separate restore review. A completed job with nonzero checker results
explicitly reports **NOT qualified**; it does not silently repair or approve
restoration of a damaged original. Copies and checker diagnostics remain for
review, including failures. This host-only action does not renew recovery
leases. Guided deploy/restore policy authoring remains unfinished.

After editing, stopping and exporting a guest, **Verify exported image lineage…**
selects the original backup, its prepared root-source directory and manifest
pin, and the private exported image. Review those inputs before confirmation.
Workbench checks the entire original archive and export and accepts only root
partition changes: all bytes outside the root, including the partition table
and boot files, must be identical. It retains the changed-chunk map and pinned
rollback lineage under `derivative/`, without copying, mounting or changing
either image. The original backup remains the restore source, not the enhanced
image. Changed file identity, a mismatched original manifest, incomplete source
evidence or protected-byte changes fail with evidence retained. This owner-only
action has no MCP deployment grant, target contact or lease renewal. A verified
derivative is not proof of filesystem health or native boot; those and explicit
deployment approval remain separate gates.

**Check exported root filesystem…** consumes the verified `derivative/` directory
and its owner-recorded manifest pin. It rechecks the full export and rollback
lineage, retains a verified private root copy, and runs `e2fsck -f -n` through a
read-only descriptor. Review the displayed storage requirement before starting.
The displayed health pin refers to the resulting `acceptance.json`; source-stream
and checker records stay alongside it for later deployment review. A nonzero
checker result is explicitly **NOT qualified**, even though the check job
completed. The boot filesystem is not checked, and nothing is mounted, repaired,
deployed or rebooted. No lease is renewed or deployment authority granted.

The same owner preparation is available from a checkout:

```sh
python3 tools/forge_recovery_stage_prepare.py \
  --publication /private/published-image-journal \
  --publication-sha256 APPROVED_PLAN_SHA256 \
  --firmware-bundle /private/firmware-bundle.json \
  --firmware-sha256 APPROVED_CANONICAL_BUNDLE_SHA256 \
  --output /private/new-staging-draft
```

Input journals must be private and owner-owned; new outputs are created private.
The JSON pins use the canonical journal encoding (sorted keys, two-space indentation, final
newline). A prepared draft is historical evidence, not authorization to execute
its phases or proof that the target remains unchanged.

New drafts seal the request, boot/publication bookends, preimages, compiled
review and hold review in `acceptance.json`. With that file's owner-recorded
canonical SHA-256, independently validate the complete draft offline:

```sh
python3 tools/forge_recovery_stage_review.py \
  --directory /private/new-staging-draft \
  --acceptance-sha256 APPROVED_ACCEPTANCE_SHA256
```

The verifier recompiles all four transitions and checks every guarded preimage,
output, phase order and journal location. It rejects unsealed older drafts;
prepare them again rather than upgrading their evidence. Verification neither
contacts the target nor grants staging authority, and its output omits private
payloads and the lease owner. Verification alone does not authorize execution.

Workbench's **Physical SSH target** panel provides **Review recovery staging…**.
Select the sealed preparation, its acceptance pin and the exact normal boot UUID
you have independently verified. Review the four phases and separately choose
**Approve reviewed plan…**. This registers four `recovery-stage` transactions;
it neither stages files nor grants existing read-only clients write access.
Existing authorized `target-write` clients can invoke the newly approved IDs.
Use Apply/Restore on each selected phase, with separate confirmations. Restore
after a reboot requires new owner approval naming that normal boot UUID.

MCP uses `target_transition` for the selected staging transaction and
`target_staging_reconcile` for its attempted direction. Both accept only
`workspace`, `transaction` and `direction` (`apply` or `restore`), require
`target-write`, serialize physical-target ownership and cannot be cancelled while
running. Listings and job context include the preparation pin, phase and approved
boot, not private journal contents. Reconciliation may observe a new native boot
but does not grant write authority on that boot. Workbench exposes separate
**Reconcile staging apply…** and **Reconcile staging restore…** buttons.

The same explicit, single-phase staging is available from a checkout. Each write
requires the sealed preparation pin and the exact normal boot UUID being approved:

```sh
python3 tools/forge_recovery_stage_dispatch.py apply \
  --directory /private/new-staging-draft \
  --acceptance-sha256 APPROVED_ACCEPTANCE_SHA256 \
  --phase firmware-start --approve-boot-id APPROVED_NORMAL_BOOT_UUID
```

Apply phases in the reviewed order: `firmware-start`, `firmware-fixup`, `command`,
`selector`. Restore with `restore` in reverse order, approving the current normal
boot UUID. Every worker checks the bound boot, private physical boot mount and
all nine guarded paths under the target lock. Apply additionally requires the
acknowledged, unchanged private recovery image. Each phase changes only its
selected alternate boot file, with per-boot attempt records under `/run`; native
firmware, normal `config.txt`, root images and reboot state are not changed.

A failed command is **not permission to repeat it**. Use `reconcile-apply` or
`reconcile-restore` with the same directory, pin and phase (no `--approve-boot-id`).
Reconciliation fences a delayed worker and inspects files without retrying a
write. An incomplete target attempt blocks further staging until a separately
reviewed normal reboot and another explicit reconciliation. Old-boot workers
then refuse to execute. Conflicts remain blocked, and any scratch files are
reported and preserved, not silently deleted. No command automatically reboots,
enters RAM recovery, releases a hold, qualifies fallback, or deploys a root image.

`recovery_jobs` lists approved IDs, operations and pins, without credential or
backup paths. `recovery_job` accepts only `workspace` and `job`; poll the returned
job ID. Read-only clients do not inherit the owner's recovery permission.
Jobs serialize with other operations on the same workspace or pinned physical
machine. Running recovery jobs cannot be cancelled. Disconnect does not cancel
accepted work or authorize a reboot.

This is an advanced prepared-session interface, **not yet a complete guided
hardware deployment workflow**. The owner must already have a private,
digest-pinned physical recovery session and the operation's validated backup,
health and plan journals. No tool here stages recovery firmware, adopts a live
lease, or reboots the target. Hold installation/release require separate approved
jobs with independent root guards. Public physical image
deployment qualification remains open.

Policy schema 1 contains a `jobs` object with 1–64 named entries. Each entry has
exactly `workspace`, `machine_id` (the normal system's 32-hex identity), `session`,
`session_sha256`, `key`, `known_hosts`, `operation`, and `arguments`. All paths are
absolute, owner-selected host paths; all pins are explicit SHA-256 digests.
The private session binds the recovery host, pinned credentials, hardware serial,
kernel, boot UUID and lease owner. Only physical sessions are accepted here.

| Operation | Exact argument fields |
| --- | --- |
| `backup-card` | `cid`, `disk_id`, `device` (only `/dev/mmcblk0`), `destination` |
| `hash-card` | `cid`, `disk_id`, `device` (only `/dev/mmcblk0`), `destination` |
| `prepare-hold` | `input_directory`, `input_sha256`, `destination`, `transition` (`install-hold` or `release-hold`) |
| `restore-root` | `journal`, `plan_sha256`, `source_directory`, `health_directory`, `health_sha256`, `accept_filesystem_errors` (boolean) |
| `deploy-root` | `journal`, `plan_sha256`, `source_directory`, `health_directory`, `health_sha256` |
| `reconcile-root` | `journal`, `plan_sha256` |
| `install-hold` | `journal`, `plan_sha256`, `root_sha256`, `root_bytes` |
| `release-hold` | `journal`, `plan_sha256`, `root_sha256`, `root_bytes` |
| `reconcile-hold` | `journal`, `plan_sha256`, `root_sha256`, `root_bytes`, `transition` (`install-hold` or `release-hold`) |
| `retry-lease` | No fields |

An uncertain result must not be automatically retried. Existing backup destinations
and attempted root dispatches are preserved and rejected for redispatch.
Reconciliation is a separate owner-approved observation job, not a retry or hold
release. A pending lease renewal blocks other actions: only an explicitly approved
`retry-lease` job can replay its exact original request, without extending its
deadline. Host journals and the original backup remain authoritative evidence;
historical job success does not establish current boot identity or lease validity.

Hold jobs change only the recovery boot selector (`config.txt`), not root data.
`hash-card` supplies a separate read-only card/root/protected-range observation
for plan preparation; it is not a substitute for the original retained backup.
`prepare-hold` compiles a private local draft from an owner-pinned `inputs.json`
in `input_directory`. Its schema-1 object has exactly `schema`, `hold_review`,
`hash_plan`, `hash_observation`, `source_manifest`, `source_manifest_sha256`, and
`source_kind`. The source kind is explicitly `backup-root-chunk-source` or
`root-only-image-derivative`; source validation and the hold compiler must agree
on root bytes, target, nonce, boot and lease owner. The input pin is the canonical
journal digest. A successful preparation returns a new plan pin, not permission
to execute it: the owner must separately review and approve an install/release
job using that pin and source root guard. It neither contacts the target nor
changes boot files, and refuses an existing destination.
The root digest and aligned byte count must come from an independently reviewed
backup/export, not from automatically adopting the current card. Before any
target contact, the host checks the plan pin, exact operation, normal-system
machine identity, physical RAM binding and root guard. The worker then rechecks
the live lease, hashes, protected boot files and recovery-image dependency before
the bounded selector commit. Existing attempts cannot be dispatched again.
Use `reconcile-hold` to fence and inspect a retained attempt; it grants no retry
or reboot authority. Releasing the selector does not prove a successful normal
boot, and installing it does not prove a successful persistent recovery boot.

Offline backup and derivative filesystem checks support Linux and macOS. Run
`make deps` to install `dosfstools` and `e2fsprogs`. On macOS the checker resolver
also recognizes Homebrew's keg-only ext4 tools, without changing your shell's
PATH or replacing native system utilities. Checks run on private verified host
copies through inherited read-only descriptors (`/proc/self/fd` on Linux,
`/dev/fd` on macOS), with `-n`, bounded diagnostics and a deadline. No mounts,
repairs or target writes occur. A clean filesystem result is not physical boot
qualification or permission to deploy; a failed check remains retained evidence.

Disconnect waits for accepted jobs, attempts clean maintenance shutdown, then
may force-stop owned VMs under the explicit `force-stop` grant. Normal/desktop
guests should be shut down inside the guest first. A forced stop can leave an
unclean filesystem; it is not a successful deployment or clean-shutdown test.
No dedicated physical-flashing tool or unapproved host-command execution is exposed.

The serial-log resource returns at most the final 64 KiB of a regular,
singly linked `serial.log`. It rejects symlinks, hard-linked files, directories
and special files instead of following them or blocking on a pipe. Logs remain
untrusted guest output; these file checks do not create an OS sandbox.

Read-only resources under `forge://workspace/<id>/` include `state`, `serial`,
`jobs`, `tasks` and `targets`. `jobs` exposes the most recent 20 durable history
records and the existing pagination cursor; use `job_history` for older records
and `job_status` for current job state. History can be disabled and is not proof
that an unfinished job is still running. `tasks` and `targets` list only the
owner-approved definitions for that workspace, with execution grants narrowed
to the attached client. Reading them does not execute or authorize a task.

### Audio surrogate controls

Pass `"audio":"usb-null"` to `boot` to attach the opt-in USB playback surrogate;
the default is no audio device. It sends playback to a null backend and does not
open host speakers or a microphone. It is not native uConsole audio, jack
detection or capture emulation.

Pass `"audio":"usb-capture"` instead for a deterministic synthetic stereo
48 kHz capture fixture. It opens no host microphone or audio backend and does
not emulate native analog input. Workbench exposes the same choice in its Audio
selector, and the CLI accepts `--audio usb-capture`. The shared `audio_query`
and `audio_set` controls inspect and reconnect the selected device type;
capture queries explicitly report `capture: true` and `host_microphone: false`.
This mode selects capture instead of playback. For simultaneous I/O, select
`"audio":"usb-duplex"` (CLI: `run --managed --audio usb-duplex`); Workbench
exposes the same choice. Duplex attaches two distinct ALSA cards: WAV-backed
USB playback and deterministic synthetic capture, not a native full-duplex codec.
It never accesses the host microphone or speakers. The boot result retains the
private WAV path, just as for `usb-wav`.

In duplex mode, `audio_set` operates on both devices sequentially. Query results
include `devices.audio-surrogate` and `devices.audio-capture`, each with
`present` and `connected` readback. Aggregate flags are true only if both devices
satisfy them. A failure can leave one device changed; it is not an atomic
transaction or rollback. Inspect both device states and retained evidence before
reconciling. Linux must independently confirm both devices' disappearance and
reappearance. Concurrent capture/playback claims require overlapping ALSA
activity and validated samples in both directions.
Live Tk-panel and attached-MCP tests verified capture before and after hotplug
using the stock guest driver. The current synthetic PCM encodes a 32-bit frame
counter across the two channels for exact sequence checking, not a listening
tone or actual microphone input. The native fidelity and
full installed-launch qualification limits remain documented in the device plan.
The source-checkout Workbench launch path also passes automated dropdown,
Start and Audio-controls interaction with sequence-checked capture before and
after reconnect (`validate_audio_workbench.py --capture`). Installed artifacts
and desktop capture-policy behavior remain separate qualification gates.
Linux ARM64 packaged launchers now also pass an isolated-user-data capture
boot/reconnect/clean-stop exercise via the attached MCP interface
(`validate_installed_capture.py`). This uses a prebuilt QEMU cache; it does not
qualify installed QEMU compilation or other host platforms.

Experimental `"audio":"usb-wav"` instead writes a new private
`audio-*/playback.wav` beneath the workspace. Its path is returned in the boot
job result; finalize the WAV by cleanly stopping the VM before reading it.
Standalone CLI use requires `run --managed --audio usb-wav`. No caller-selected
output path is accepted and each launch retains its own recording. Recordings
consume host disk space and may contain sensitive guest audio. This backend is
not yet fidelity-qualified: the first sample-level test detected substantial
frame loss despite successful ALSA playback. Do not use it as lossless evidence
or assume hotplug preserves wall-clock recording continuity. A sample-level
hotplug test exposed truncation when the device was recreated. The bundled
`wav-backend-lifetime.patch` fixes that lifecycle defect: the rerun retained both
complete tones across disconnect/reconnect between drained streams. Removal
during active playback and wall-clock gap preservation remain unqualified.
The [candidate kernel drain fix](../Code/patch/cm4/20260414/README.md) passed
single and repeated complete-waveform tests in a disposable guest, but is not automatically
installed and still needs broader qualification.
With that candidate, sample-level reconnect qualification now passes through
both the real Tk audio panel and a separate attached stdio MCP client, retaining
both complete test tones. The fixtures and limits are recorded in the
[device plan](emulator-device-plan.md#stage-5-usb-topology-and-audio).

`audio_query` takes `{"workspace":"dev"}` and returns a job with sampled QOM
presence/connection state. `audio_set` takes
`{"workspace":"dev","connected":false}` to disconnect, or `true` to reconnect,
and requires `device-control`. Poll `job_status` to completion before submitting
another operation. Readback proves model attachment, not ALSA enumeration or
successful playback; verify those separately in the guest. Both operations
retain durable JSONL evidence and controller job history. A failed operation
does not imply rollback or authorize blindly retrying a mutation.

Workbench's **Audio controls** panel uses the same jobs. Real Tk-panel and
separate stdio MCP acceptance runs have exercised Linux disappearance,
rediscovery and playback after reconnection. These validate the null-backed
USB substitute, not native audio fidelity. The full Workbench maintenance-mode
launch path also passed `validate_audio_workbench.py`: audio selection, Start,
opening Audio controls, hotplug buttons and independent guest playback. This
is automated widget interaction, not manual usability or desktop audio-policy
qualification.

### Live SSH target transactions (whole-image recovery pending)

Live host/target development is the default physical workflow; a spare card or
reader is not required. Every target mutation must have a verified host-side
backup and a reviewed restore path first. Keep that recovery evidence even when
the user chooses to leave a successful change deployed. External-device flashing
is an optional, separate deployment path, not a substitute for live development.
Boot-affecting changes additionally need recovery independent of working SSH;
the file/service transactions below do not provide whole-system boot recovery.
Never overwrite the mounted system card.

Workbench's physical-target panel offers **Inspect recovery prerequisites** for
an already approved transaction. MCP exposes the same shared job as
`target_recovery_inspect` with `workspace` and `transaction`; poll `job_status`.
It requires the existing `target-write` grant for fixed remote execution, even
though this operation performs no target writes. Clients cannot supply a host,
command or path. The worker revalidates the pinned file/service authorization
and machine identity, uses existing SSH trust and `sudo -n`, and returns file
statuses plus watchdog observations, not boot-file contents. An active runtime
watchdog never becomes a boot-fallback qualification. This inspection neither
creates a backup nor authorizes recovery-image publication or reboot.
An attached coding CLI with the owner's explicit `target-write` grant can use
this same tool and read the GUI's workspace-scoped jobs. Live service-backed
acceptance verified bidirectional job visibility and denial of ungranted boot
authority; it did not perform a physical write or reboot.

Experimental recovery-image tooling is not yet an authorized boot-deployment
path. `build_recovery_initramfs.py` produces a private image containing the
explicitly supplied Wi-Fi and SSH credentials; treat the image, extracted tree
and build resources as secrets. The current qualification uses disposable test
credentials only. Do not copy such an image to the ordinary FAT boot mount:
its shared mount permissions can make embedded credentials readable to other
local users despite a source file's 0600 mode. Protected credential provisioning
and physical recovery qualification remain required before production use.
The emulator-only USB Ethernet path requires the exact `uconsole.emulator=1`
token and does not establish that native Wi-Fi recovery works.

Internal schema-2 recovery-image journals support explicit reconciliation after
an SSH failure. Inspection alone never authorizes a retry: only an exact durable
target receipt or a durable no-start fence can resolve the attempt. An incomplete
target effect stays blocked. Reconciliation retains the original history and
requires the approved plan digest; it is not yet exposed as a GUI/MCP deployment
operation. Legacy schema-1 recovery plans remain inspect-only.

GUI/controller/MCP integration supports reviewed file
and standalone-service apply/restore transactions, described below. The
`tools/forge_target_backup.py` command implements the read-only file preimage
step, not deployment:

```sh
python3 tools/forge_target_backup.py --host jkh@clockworkpi.local \
  --output /private/existing-directory/preimages.json \
  /usr/local/bin/my-application /etc/systemd/system/my-application.service
```

The existing SSH trust/authentication configuration is used with batch mode;
remote reads use passwordless `sudo -n python3`. No target agent is installed.
Select explicit canonical paths with existing real parent directories. Capture
supports at most 32 singly linked regular files (8 MiB each, 32 MiB total) and
records absence for new files. Symlinks, hard links and special files are
rejected rather than silently flattened. File bytes, ownership, mode, timestamps
and extended attributes are preserved in an exclusive 0600 host artifact.
Reads avoid atime changes and check metadata before and after capture. Content
hashes and the exact requested file set are verified before and after durable
host publication. A failed capture may leave an empty/invalid artifact; never
treat its existence as success. Keep backups private: they may contain secrets.

This is a per-file preimage, **not an atomic system snapshot**, package rollback,
service-state backup or boot recovery.
Other processes can change files after capture; deployment compares the current
target against these preimages before writing. The capture command itself does
not grant or perform target writes. Successful development changes may remain
deployed, with their verified host-side backup and explicit restore action
retained; qualification additionally exercises restoration.

The internal `forge_target_files.apply_file` primitive now supports a single
verified file transition and its inverse. It preserves bytes, ownership, mode,
timestamps and extended attributes, refuses changed preimages, stages in the
destination directory, and verifies the published result. Restore refuses to
overwrite intervening edits. Missing-file publication is atomic no-clobber;
re-entry at the desired state retries directory synchronization. Symlink
traversal and multiply linked target files are refused.

This primitive is exposed through pinned named MCP transactions, not arbitrary
client-supplied paths or commands. Its caller
must durably journal preimages and intended changes before invoking it. Forge
directory locks do not exclude non-cooperating processes: quiesce other writers
before replacing or removing existing files. Failure after publication is an
uncertain outcome, not rollback. Process death during staging can leave scratch
files. Exact journal staging tokens now allow recovery of a fully staged file
or a death between no-clobber link publication and scratch unlink: the latter
requires both names to identify the same inode with verified desired contents.
Partial or changed scratch files are preserved and block automatic retry.
The SSH transaction layer uses these tokens for retries. Local tests cover
apply/restore, metadata, conflicts, locks, staging
failure, late creation and retry after publication; no physical writes have
been made outside the explicitly recorded proof-application qualification.

`forge_target_journal` supplies an exclusive 0700 transaction directory with a
durably written 0600 plan containing verified before/after file states, target
machine identity, SSH destination and distinct apply/restore staging tokens.
Locked, append-only event files record dispatch, acknowledgement and uncertain
outcomes. Partial event records, sequence gaps and insecure permissions block
further use rather than being silently discarded.

`python3 tools/forge_target_ssh.py apply --journal TRANSACTION_DIRECTORY` applies
a prepared plan; `restore` reverses it. This is an explicit target-write command,
not an inspection operation. The worker is sent from installed owner code over
stdin using existing SSH plus `sudo -n`; no persistent agent is installed. It
checks the backed-up machine identity before opening its root-owned 0600 lock at
`/run/lock/uconsole-forge-target.lock`, and holds that lock across all file changes.
Dispatch is journaled before SSH starts. Only a matching acknowledgement becomes
success. Timeout, disconnect or malformed output stays uncertain; retry the same
direction to reconcile before reversing. Multi-file apply can be partial and is
not atomic. Stop unrelated writers first. Service/package recovery,
full-image deployment remain open.

#### Owner-approved physical transactions over MCP

For service-state inspection, `tools/forge_target_services.py --host HOST
--output NEW_FILE.json UNIT.service ...` captures systemd properties without
changing the target. It records canonical identity, load/active/substate,
enablement, fragment/drop-in paths, boot identity, reload-needed and transient
flags. Two consecutive observations must agree. Templates, wildcard names,
aliases, transitioning units, transient units and pending daemon reload are
refused. The output is exclusive and private; an incomplete capture cannot be
used as a successful preimage.

New captures also retain literal symlink targets, owner IDs and timestamps for
selected units and their alias chains in `/etc/systemd/system` and
`/run/systemd/system`, including real `.wants`, `.requires` and `.upholds`
directories. The two link observations must agree. Linked dependency directories
are refused, not followed or silently ignored. Missing roots remain distinct
from empty roots. This is a mutable-link preimage, not a complete vendor-unit
search path or runtime dependency graph; no links are modified by capture.

Add `--dependencies` to capture selected units' incoming and outgoing systemd
dependency properties alongside their lifecycle state. Both observations must
agree, as must machine/boot identity and mutable links. Missing properties are
rejected, not interpreted as empty lists; older captures without this option
remain valid but do not establish dependency coverage. Literal escaped unit
names and template instances are retained for review, not authorized as action
targets. This captures selected-unit adjacency, not the transitive graph or
side effects of service executables.

The read-only physical capture
`build/emulator/target-service-dependencies-20260924.json` passed for
`cron.service` and an absent fixture. Cron's observations include requirements
on `sysinit.target` and `system.slice`, a conflict with `shutdown.target`, and
incoming `WantedBy=multi-user.target`. No service was started, stopped or
modified by that capture.

`tools/forge_target_dependencies.py --host HOST --output NEW.json SERVICE...`
captures the transitive closure of those dependency properties, including
incoming and ordering edges. It is read-only, bounded to 512 units and 120
seconds by the CLI, and requires a second unchanged pass plus stable machine
and boot identity. Cycles terminate; missing nodes, changed edges, aliases,
unsupported properties and limit exhaustion fail rather than producing a
complete graph. Failure artifacts explicitly record `complete: false`.
Systemctl's quoted device-unit words are decoded while preserving literal
unit-name escapes. This remains dependency-property coverage, not a proof of
all executable effects or a grant to act on the captured units.

A physical capture rooted at `cron.service` passed with 296 units in
`build/emulator/target-dependency-graph-20260924-r3.json`. The first two failed
captures are retained as incomplete; they exposed the quoted-device-name
format and were not treated as empty graphs. Review recipes can retain this
graph only when its identity and selected-unit edges match the service capture.
Neither transitive capture nor graph attachment makes a recipe dispatchable.

The internal `forge_target_links.apply_link` primitive can apply or reverse one
explicit journaled symlink transition. It checks the original link without
following it, preserves the literal target, owner IDs and mtime, and stages in
the same real parent directory. It refuses regular files, linked parents,
unexpected user changes and unknown scratch contents. Publication of a new link
cannot clobber a late creator. Exact staging tokens allow retry after interrupted
publication, with directory synchronization and post-write verification. As with
file transitions, unrelated writers must be quiesced; a post-publication error
means uncertain completion, not rollback. Symlink atime and arbitrary xattrs are
not covered. This primitive is not yet dispatched by physical service jobs.

This is not service rollback: it does not back up unit/drop-in contents,
all enablement effects, package state, or application databases. File-only target
transactions still require unrelated writers to be quiesced by the owner.
Service stop/reload/start/enable/restore ordering and failure reconciliation
remain to be integrated before the forge can claim service-aware deployment.

`forge_target_service_plan.compile_recipe` now validates service/file identity
and complete unit/drop-in file coverage, and produces reviewable forward and
restore orderings. Affected services are quiesced before file changes;
enablement removal precedes deleting unit files; daemon reload precedes enabling
and starting the desired services. Restore targets the captured original
presence, enablement and running state. Failed, masked, transitional, transient,
aliased, static/indirect and incomplete states are not silently approximated.
When dependency observations exist, recipes retain them and list related units
outside the selection. Missing observations are explicitly marked uncaptured,
never represented as a proven empty graph. Neither capture nor this review
grants execution permission for related units.

These recipes explicitly have `dispatchable: false`: durable service phase
reconciliation, enablement-link/dependency effects and service-data consistency
still need qualification. They cannot be passed to the file-only executor,
whose journal validation now rejects unknown fields instead of ignoring an
unimplemented service action. Ordering tests are not service rollback evidence.

`forge_target_phases.run` now supplies the internal durable phase mechanism.
It pins a plan to machine and boot identities, locks a private ledger, records
intent before invoking an effect, and acknowledges only an observed postcondition.
On reconnect, an observed completed effect is not repeated. An unacknowledged
non-repeatable effect still at its prior state is uncertain and stops; only an
explicitly repeatable phase may retry from its verified prior state. Changed
plans, boot identity, observation drift, partial records and sequence gaps are
refused. The final phase must observe the aggregate transaction postcondition;
completed records alone are not proof that it still holds.

Focused tests include an actual child process exiting after its effect
but before acknowledgement, followed by recovery in another process without
repeating the effect. This runner itself issues no service commands. Wiring its
observers/actions to qualified service, file and link phases is still pending;
it is not yet evidence of physical service rollback or power-loss durability.

`forge_target_effects.TargetEffects` connects the phase runner to fixed
`systemctl start`, `stop`, and `daemon-reload` operations and the exact file/link
primitives. Services and paths must be in explicit owner-provided scopes, and
each effect must observe its affected object. Start/stop phases cannot declare
themselves repeatable. No arbitrary command, executable, unit glob, or broad
enable/disable operation is accepted. Observations retain lifecycle/configuration
properties and file/link fingerprints; service-specific application health is
still a separate check. Pending reload is observable during execution without
relaxing the stricter backup capture.

Adapter/ledger integration tests include real local file and symlink writes;
service commands are mocked in these tests. The service execution boundary must
hold the target transaction lock and bind reviewed plans, observations and
dependency effects before exposing this through GUI/MCP. General service
apply/restore acceptance, including application-data consistency, is still open.

`forge_target_service_runtime.execute` now provides an internal execution
boundary for digest-pinned phase envelopes. It validates every action and its
observation scope before effects, checks machine/boot identity, requires an
already-provisioned private ledger parent, and holds the same target-wide lock
used by ordinary file transactions. File/link postconditions must match their
intended payloads; final verification must cover every approved object. An
invalid later phase cannot run earlier phases first. The plan digest determines
the private ledger name, permitting reconnect without choosing a new ledger.

`forge_target_service_ssh.dispatch` supplies an internal owner-only SSH transport
for these envelopes. Before connecting, it checks the approved digest and saves
the envelope, target, nonce and exact owner-module sources in an exclusive
private host attempt directory. A verified acknowledgement must match the nonce,
digest, phase count and target ledger path. Timeout, malformed acknowledgement,
or remote failure leaves an explicit uncertain record; no automatic retry or
rollback is attempted. Reconciliation must retain the same target ledger and
envelope, with a new host attempt record. Six transport tests cover this boundary,
including loading the module bundle in an isolated Python process without the
repository installed. This transport neither creates a backup nor provisions the
target ledger directory; those are prerequisites. It is not yet exposed through
GUI/MCP and is not physical service restoration evidence.

`validate_target_service_roundtrip.py` has now qualified that transport and
runtime on `jkh@clockworkpi.local` using a unique inert oneshot service with
`ExecStart=/usr/bin/true`. It backs up original unit/link absence and service
state before writes, retains each approved envelope and acknowledgement, and
checks the same machine/boot identity throughout. Nine phase records covered
unit installation, reload requests, explicit enablement-link creation, start,
stop, and removal of the link and unit.
A fresh aggregate observation matched the original absent/inactive state;
the target subsequently reported `running`. Evidence is retained privately in
`build/emulator/target-service-roundtrip-20260924/acceptance.json`, with target
phase journals under `/var/tmp/uconsole-service-proof-1dotztsq`.

Review of that first run found that its three reload phases had identical
before/after properties. The original phase runner could acknowledge them
without invoking `daemon-reload`; that evidence therefore proves matching
properties, not dependency-graph refresh. The runtime now requires an actual
successful reload invocation before acknowledging an uncompleted reload phase,
even when the properties already match. An unacknowledged repeatable reload is
invoked again during explicit reconciliation; an acknowledged completed phase
is not rerun. Start/stop retain their non-repeatable recovery rules. Regression
tests cover equal-property invocation, interrupted intent and completed reentry.

The corrected runtime passed a new physical round trip, including acknowledged
reload invocations and exact final restoration. Its nine verified transitions
are retained in
`build/emulator/target-service-roundtrip-reload-20260924/acceptance.json`; target
journals remain under `/var/tmp/uconsole-service-proof-5qi02lnr`. The generated
fixture unit and link were removed, with their contents retained in the private
host records. A subsequent target health query reported `running`.

Only the uniquely named fixture unit and its enablement link were removed;
their exact generated contents remain in the host dispatch records. The private
target journal directory remains intentionally. This proves the inert fixture,
not arbitrary service dependencies, application-data rollback, power-loss
durability, or GUI/MCP service deployment. A failed or interrupted validator
retains evidence and stops without guessing that restoration succeeded.

`forge_target_service_transaction.prepare` now retains a reviewed backup and
paired apply/restore envelopes together in one exclusive private host artifact.
Both directions must bind the same machine, boot and object scope, begin with
aggregate preimage verification, and finish with aggregate verification.
Apply starts at the backup; restore starts at the applied state and returns to
the backup. Retained file/link payloads must match the backup observations.
Preparation returns separate transaction/apply/restore digests and performs no
target operation or grant. Six tests cover private retention, identity mismatch,
missing preimage verification, mismatched restoration, payload coverage and
caller-mutation isolation. This authoring boundary still requires a compiler
for supported service recipes and dependency/application-data qualification;
it is not yet wired to GUI/MCP service deployment.

`forge_target_service_dispatch` now connects a prepared pair to the journaled
SSH transport. Explicit owner authorization pins the transaction digest, SSH
host and provisioned target ledger; its exclusive authorization record cannot
be rebound. Dispatch holds a private host transaction lock, saves direction
and attempt history before SSH, and records acknowledgement or uncertainty.
An uncertain direction must be reconciled before reversal. Restore cannot
precede acknowledged apply, orphan acknowledgements are rejected, and another
apply after restoration requires a new transaction rather than reusing completed
target phase ledgers. Both envelopes are revalidated against the reviewed digest
before each attempt. Tests include actual local file effects through the paired
runtime and restoration to absence; the SSH boundary is mocked for that test.
This owner-only API is not yet a GUI/MCP service grant or physical paired-service
qualification, and does not replace dependency or application-data review.

`forge_target_service_compile.new_service` now builds a paired transaction for
a new standalone persistent service with one explicit multi-user enablement
link. It requires exact captured absence, fixes both directions and all staging
tokens before deployment, starts each direction with aggregate preimage
verification and ends with aggregate result verification. It does not silently
convert existing services into this new-service case. Unit contents, dependency
effects and application-data behavior remain separate owner-review requirements;
this is not an arbitrary unit or package migration compiler.

The physical validator's `--paired` path now uses this compiler, transaction
preparation, pinned authorization and paired dispatcher. Its first run exposed
systemd retaining a removed unit in memory with `NeedDaemonReload=yes`. It
stopped without claiming restoration. Evidence and the original approval were
preserved, a separate reviewed reload recovery was recorded, and the original
restore was reconciled successfully in
`build/emulator/target-service-paired-20260924/recovered.json`.

The corrected compiler's fresh physical run passed without intervention:
`build/emulator/target-service-paired-20260924-r2/acceptance.json` records seven
verified apply phases and six restore phases, followed by exact backup-state
comparison. Target journals remain under
`/var/tmp/uconsole-service-proof-r6rj4uj9`; the fixture unit/link were removed,
and their generated contents remain in the private host transaction. The target
reported `running` afterward. This qualifies the inert new-service pair, not
existing-service/data rollback or GUI/MCP service deployment.

`forge_target_service_compile.update_service` additionally compiles replacement
of one existing local standalone unit while preserving its enablement link.
It stops an active service before replacement, requires an acknowledged daemon
reload, and starts it only when the requested state is active. Restore returns
the original unit payload/metadata and original active or inactive state.
Drop-ins, transient/failed/masked/unreloaded units, mismatched links and broader
file sets are refused rather than approximated. The owner must still establish
application-data consistency and qualify executable/dependency effects; this
does not snapshot a service's database or writable directories.

The physical `--paired --update-cycle` validator passed on the temporary inert
fixture. It first installed the fixture, then changed its description, mode and
mtime through an update pair, restored the original payload/metadata and active
state, and finally restored the pre-installation absence through the original
pair. Evidence is retained in
`build/emulator/target-service-update-20260924/acceptance.json` and
`update-restored.json`; all four directions were verified (7/6/6/6 phases).
The fixture unit/link were removed, with original and generated contents retained
in the host transactions. Target ledgers remain under
`/var/tmp/uconsole-service-proof-f68chkp9`, and subsequent system health was
`running`. No pre-existing user service was modified. General application-data
rollback and GUI/MCP service integration remain unqualified.

`forge_target_service_prepare.author` provides read-only preparation from a
local unit file and an existing SSH target. It freezes source bytes before SSH,
captures service/dependency/link state and unit contents twice, and refuses
inconsistent identities, changed observations, or uncovered alias/enablement
links. It compiles either a new standalone unit or an existing-unit update,
preserving existing file ownership, mode and extended attributes. Backups, the
paired transaction and a private review summary are retained on the host.
Preparation does not provision a target ledger, grant permission, stop a
service, or deploy anything. Executable/dependency effects and application-data
consistency remain explicit pending review items.

Read-only preparation passed against the physical target for an absent inert
fixture in `build/emulator/target-service-author-20260924/review.json`.
The review records `deployment_performed: false` and
`authorization_performed: false`; no target service or ledger was created.
This is the owner-side preparation building block, not completed GUI service
authoring or automatic approval.

Owner policy now accepts preapproved service pairs alongside existing file
transactions, using an explicit distinct entry shape:

```json
{"kind":"service","workspace":"target","journal":"/absolute/private/transaction","authorization_sha256":"<owner-approved authorization digest>"}
```

The shared controller routes this entry to the paired dispatcher and records
service kind, policy digest and authorization digest in job provenance.
`target_transactions` lists the authorization digest without host paths or
backup contents; `target_transition` still accepts only workspace, transaction
name and direction. Clients cannot provide service contents, hosts, commands,
ledger paths or replacement approvals. The existing `target-write` grant and
attached-client grant checks apply. File-only policy entries are unchanged;
mixed or unknown entry fields are refused.

Workbench's physical-target panel can review and invoke a preapproved service
pair, rechecking its authorization and transaction before confirmation. Adding a
new file approval preserves existing service entries. End-to-end GUI service
authoring qualification is recorded below; displaying an approved pair is not a substitute for reviewing
dependencies and application data.

The real stdio MCP owner/controller/SSH path passed the physical inert fixture
round trip in `build/emulator/target-service-mcp-20260924/acceptance.json`.
Both apply and restore jobs verified their approved digests, and a fresh target
observation matched the original absence. Transcripts and job history are
retained privately; target journals remain under
`/var/tmp/uconsole-service-proof-xsk62gh_`. The fixture unit and link were removed,
with generated contents retained in the host transaction. This is MCP
new-service qualification, not arbitrary existing-service/data migration.

The physical `--paired --gui` validator also passed through actual Workbench
Apply/Restore buttons and shared controller jobs. It programmatically approved
the known inert fixture's confirmation dialogs, retained their text and job
provenance, then compared a fresh target observation with the original absence.
Evidence is in `build/emulator/target-service-gui-20260924/acceptance.json`,
`gui-apply.json` and `gui-restore.json`. Target journals remain under
`/var/tmp/uconsole-service-proof-r7vwsh13`; the fixture unit/link were removed
and their contents remain in private host records. This is automated real-button
qualification, not manual UX testing or GUI service-plan authoring.

The shipped `uconsole-forge` skill routes live-device work to
`references/physical-targets.md`, covering the default SSH backup/restore loop,
named transaction grants, approval provenance, uncertainty and current recovery
limits. It does not instruct agents to self-approve target writes or substitute
live artifact deployment for physical exported-image boot evidence.

Workbench now offers **Prepare service…**: select an SSH host, local standalone
unit, literal service name and private host backup location. The background job
uses read-only service preparation; its review does not grant deployment.
**Approve reviewed plan…** separately asks the owner to review executable,
dependency and application-data effects. Approval runs as a non-cancellable
controller job that creates a root-private target journal directory and records
the exact authorization, then registers a pinned policy for later Apply/Restore.
Neither preparation nor approval installs the unit or changes its running state.

`provision_and_authorize` saves an exclusive host intent before SSH, verifies
machine/boot identity remotely before creating the directory, and checks the
nonce and returned path before authorizing. A lost acknowledgement remains
uncertain and the intent prevents blind reprovisioning. Tests cover declined GUI
decisions, digest mismatch, successful provisioning, and timeout/retry refusal.
The backend physically provisioned
`/var/tmp/uconsole-forge-service-9must9y_` for the previously prepared inert
transaction under `target-service-author-20260924`, without deploying it.
End-to-end GUI preparation/approval qualification is recorded below.

`validate_target_service_author_gui.py` passed the complete physical flow from
an owner with no target policy: actual Prepare service, Approve, Apply and
Restore buttons, with programmatic answers for the known inert fixture.
It checked the selected source digest, absence of write grants after preparation,
approval-job provenance, unchanged fixture state after approval, and exact
restoration after deployment. Evidence is retained in
`build/emulator/target-service-gui-author-20260924/acceptance.json`,
`gui-service-author.json`, `gui-apply.json` and `gui-restore.json`.
The fixture unit/link were removed; backups, generated contents, policy,
authorization and target journal `/var/tmp/uconsole-forge-service-pn4hac37`
remain available. This qualifies the new inert
service's complete GUI authoring flow, not application-data recovery, arbitrary
unit dependencies, manual UX quality or full-image hardware boot.

Boundary tests include real local file effects, shared-lock exclusion,
changed approval/identity, malformed later actions, false postconditions,
incomplete final verification and insecure ledger directories. Repeatable,
unacknowledged phases now reconcile verified file/link scratch before observing
their result. This is limited to the exact staging token of a matching durable
intent: it can remove the temporary second name of a verified published inode
or a verified redundant staged copy, but never publishes a new object or invokes
a service command. Without that intent, scratch is not treated as authority.
Tests cover interrupted file and symlink publication through the full runtime
and refusal to clean unjournaled scratch. Physical service fixtures, dependency
effects, and controller/GUI/MCP service dispatch remain open. This internal API
does not expand the existing file-only `target_transition` tool.

Create a backed-up transaction without deploying it:

```sh
python3 tools/forge_target_prepare.py --host jkh@clockworkpi.local \
  --spec file-mappings.json --output /private/new-transaction-directory
```

The mapping file uses absolute local sources and canonical target paths:

```json
{
  "schema": 1,
  "files": [
    {"source": "/absolute/build/my-app", "target": "/usr/local/bin/my-app", "mode": "0755"}
  ]
}
```

Optional `sha256` pins a source artifact. Sources are bounded regular files and
their contents are frozen before SSH capture; later local edits do not change
the plan. Existing target ownership, mode and extended attributes are preserved
unless an ordinary mode is explicitly selected. New files default to root-owned
0644. The author refuses special permission bits/capability-bearing replacements,
explicit mode changes with an existing ACL, and direct boot/storage-configuration
paths needing separate recovery qualification. This is not a general safety
assessment of arbitrary application or configuration changes.

The new private output contains `before.json`, `transaction/plan.json`, and
`review.json`. Review includes old/new hashes, sizes, ownership, modes, preserved
attribute names, machine identity and the plan digest, without printing backup
contents. Creation performs no target writes, enables no services, and grants no
deployment permission. On failure, retained backups are not discarded; use a
new output directory for another attempt. Review the plan before using its
journal/digest in the owner policy below, or use the Workbench authoring flow.

The standalone MCP owner accepts `--target-policy POLICY.json
--target-policy-sha256 SHA256 --allow target-write`. Policy bytes must match the
explicitly approved hash. Each named transaction is bound to one registered
workspace and a private journal whose `plan_sha256` was returned by
`forge_target_journal.prepare`:

```json
{
  "schema": 1,
  "transactions": {
    "proof": {
      "workspace": "cm4",
      "journal": "/absolute/private/transaction",
      "plan_sha256": "REPLACE_WITH_APPROVED_64_HEX_DIGEST"
    }
  }
}
```

`target_transactions` lists names and approved digests without backup contents
or host paths. `target_transition` takes only `workspace`, `transaction`, and
`direction` (`apply` or `restore`); it returns a shared-controller job to poll.
Both the physical-write grant and policy are required. The plan digest is checked
under the journal lock immediately before dispatch, so modifying a plan after
approval cannot silently change the target or writes. The policy is snapshotted
at owner startup. Accepted running target jobs are not cancellable; failure may
leave partial changes and requires journal reconciliation, not blind reversal.

Attached sessions need their own owner-selected `target-write` grant; having it
on the controller alone does not grant it to a client. `--connect` cannot replace
the owner's target policy. Emulator `guest-exec`, `transfer`, `image-write` and
`device-control` grants confer no physical-write authority. This interface runs
already prepared and reviewed transactions; clients cannot approve their own
new hardware plans. Controller permission
tests pass. A separate real stdio MCP owner has also completed the physical
application apply/run/restore validator (`--mcp`); its transcript and job history
retain both approval digests. This deterministic protocol client is not the
independent coding-agent exercise, which remains open.

#### Workbench physical-target panel

Workbench accepts the same `--target-policy` and `--target-policy-sha256` startup
options. Transactions must bind to workspace ID `gui`. The **Physical target**
button opens a single panel listing approved transactions; without an approved
policy, write buttons are disabled and the panel explains the required setup.
It displays the host, affected paths, plan digest and retained journal location
without displaying backup contents. Both apply and restore require an explicit
physical-write confirmation. The plan is checked against its approved digest
before prompting and again under the dispatch lock.

The panel uses the shared controller, so workspace exclusion and durable jobs
also apply to GUI hardware actions. While a panel-submitted target job is active,
the panel and Workbench refuse close; they do not cancel or pretend to undo
hardware writes. Terminal failures explicitly instruct the user to inspect the
journal and reconcile. Attached clients still need their separately configured
`--agent-allow target-write`; the GUI owner's policy alone grants them nothing.
Tk control tests and the physical `--gui` proof-application round trip pass.
The latter invokes real buttons with programmatically approved fixture dialogs
and retains their text and job outcomes. Manual usability qualification remains
open.

**Prepare from local files** collects an SSH destination and one or more local
artifact/target-path mappings, optional ordinary octal modes, and a host backup
parent directory. After a separate read-only backup confirmation, a background
controller job creates a new private transaction directory. Its review shows
old/new hashes, sizes, ownership, modes and preserved extended-attribute names.
This step neither grants write permission nor deploys files.

**Approve reviewed plan** checks the plan digest again, asks for explicit owner
approval, then saves a private digest-pinned policy and enables the named plan
for this session. Existing approved transactions are retained. Approval itself
does not deploy; Apply/Restore still require their separate confirmations.
The displayed policy path and digest can be used at the next Workbench startup.
Owner approval is deliberately absent from the MCP surface. Existing read-only
clients remain read-only; clients already granted target-write can invoke newly
approved transactions, as the approval prompt states. Policy changes are refused
while controller jobs are active. Tk tests cover prepare-only behavior, declined
approval, changed plans, owner approval, and client permission isolation. The
physical `--gui-author` validator also passed the complete prepare/approve/apply/
run/restore flow without a preloaded policy, using programmatically answered
dialogs for the inert proof application. This remains distinct from manual UX
qualification and service/package recovery.

`tools/validate_target_roundtrip.py` exercises the exact inert emulator proof
application through backup, plan, apply, execution, restore and fresh capture.
Its physical run and an explicit `--reboot` run passed; see the validation
record. The latter requires the same device to return with a new boot ID,
unchanged native boot configuration and systemd running, then tests the app
again before restoring it. Use `--reboot` only when a physical reboot is
authorized. Neither run proves that an entire exported image boots on hardware.

## Workflow and limits

### Optional stock Desktop-path repair

Some official image templates hard-code `/home/cpi/Desktop` in PCManFM even
when onboarding creates another username. `tools/repair_stock_desktop.py` is
an explicit shared-image repair; emulator setup does **not** apply it silently.
Copy it into a trusted guest, stop desktop sessions, then preview with
`python3 repair_stock_desktop.py --root /`. Run the same command with `sudo`
and `--apply` to apply the previewed repair. For an offline, trusted root tree,
use its mount path instead of `/`; never point this at the host root.

Only byte-for-byte known stock templates in `/etc/skel` and regular homes
under `/home` qualify. A present `cpi` home, symlinks, customized settings and
existing backup files are left alone. The repair removes only the hard-coded
folder override, allowing PCManFM's normal Desktop-directory fallback, and
retains each original beside it with the suffix `.uconsole-original`.
Restart the desktop after applying. To undo, stop the desktop and restore the
selected original backup after reviewing any changes made since the repair.
The change persists in exports and applies equally on hardware. It does not
alter native display-manager or kernel settings, and does not establish a
hardware-qualification result.

### Power scenarios

The `boot` tool accepts optional `scenario_path`, relative to the configured
files root (absolute paths within that root also work). For example:

```json
{"workspace":"dev","mode":"maintenance","scenario_path":"battery-discharge.json"}
```

Copy the [example profile](scenarios/battery-discharge.json) into the exchange
directory first. The controller validates and snapshots the profile before
submitting a job; later file edits cannot alter that boot. No extra grant beyond
`boot` and `force-stop` is required, but a files root must be configured. The
boot job's context records the profile digest and requested power state. Its
result includes `scenario` with model readback and runtime identity, also saved
in the host workspace. These initial states are applied before CPU execution;
they do not change the deployable image or establish hardware fidelity. Workbench
also supports session-local profile selection.

`boot` also accepts `adc_reference: "fixed"` (default) or `"missing"`.
Workbench exposes the same setting as **ADC reference at next boot**; the
low-level CLI spells it `--adc-reference missing`. This is captured in the boot
job's context and cannot change an already running VM. The missing profile
omits the guest DT reference-supply description: ADC raw conversions still work,
but the stock driver's scale reads fail with `EINVAL`. Converter power is a
separate control (`adc_powered`); removing it produces I/O failure. Neither
profile diagnoses the physical target's EIO cause. Native boot files and the
deployable image are not modified. Use a current emulator build; older binaries
without the profile property cannot launch the missing-reference configuration.

For a running owned guest, `power_query` returns an asynchronous job containing
the sampled power fields, including `adc_input_uv`, `adc_powered`,
`pmic_temperature_mc` and the independent
`pmic_over_temperature` fault input. `power_set` requires `device-control` and exactly
one of those fields, for example `{"workspace":"dev","ac_present":false}`.
It checks model support, changes that field, then reports requested, before and
observed state. The job history retains the requested change even on failure.
Single-change evidence durably records the before sample and dispatch intent
before sending the mutation, then its acknowledgement before readback. An
acknowledged write with failed readback is reported explicitly as uncertain
effect, not rolled back; there is no automatic retry. Failed dispatch-intent
storage prevents sending the write. Absence of an acknowledgement record does
not prove absence of an effect.
Other fields can change as a consequence: removing AC stops positive charging
current. Model conditions still apply, including battery presence and enabled
charging. PMIC battery voltage is quantized to 1100-microvolt steps. Reads from a running VM
are sequential samples, not an atomic snapshot; guest drivers can change state
between them. A readback failure does not roll back a possibly completed write.
Power-key events may trigger guest shutdown policy. The over-temperature fault
input can abruptly remove emulated power if the guest enables PMIC thermal
shutdown; use a disposable or checkpointed workspace. This is not inferred from
the ADC sample and is not a clean stop. A lost readback can mean that the fault
took effect, not that no change occurred. No physical thermal faults are injected.
Running power jobs are not
cancellable; wait for their terminal outcome before another operation. These
controls do not adopt external VMs or provide arbitrary QMP access. The GUI's
Live power row submits these same controller jobs and returns immediately.
Controls and closure stay guarded until terminal readback; running single power
operations are not cancellable. Both GUI and MCP retain per-operation JSONL
evidence as well as durable job results, with the evidence path and requested
change in job context. Failed readback does not imply rollback.

`adc_input_uv` is the independent ADC101C input pin voltage (integer 0–3300000),
not the PMIC battery voltage. `adc_powered` is a boolean: removing power resets
the converter and makes its I2C address NACK; restoring power resets its
registers while retaining the external input. Reading these controls back proves
the input settings, not that a conversion completed. The stock guest IIO driver
provides conversion evidence. The working model has a fixed 3.3 V reference;
these controls do not reproduce the physical target's missing-reference profile.
Both fields are available in initial scenarios, the GUI Live power selector,
MCP `power_set`, and replay schedules. See `docs/scenarios/adc-power-cycle.json`.
Replay deadlines use host monotonic time, not a deterministic guest virtual
clock. All power operations validate both PMIC and ADC support before writing;
older emulator builds without the ADC require rebuilding.

`power_replay` takes a `schedule_path` beneath the files root and requires
`device-control`. Workbench uses the same controller replay job after its local
file selection, rather than a separate executor. GUI and MCP replays share
workspace exclusion, cancellation, immutable schedule context and durable job
history. Cancelling retains completed effects and waits for in-flight readback;
the GUI keeps conflicting controls blocked until the controller reports a
terminal result. Single live GUI power queries/changes also use shared jobs.

The [AC-cycle example](scenarios/ac-cycle.json) uses schema 1
with an `events` array: each entry has integer `at_ms` and a `power` object with
exactly one field. Schedules are limited to 16 KiB, 64 events and 300000 ms;
times must be nondecreasing. Equal-time events execute in file order, not
atomically. The file is snapshotted before submission. This event document is
currently separate from the initial boot-profile document.

The cancellable job owns the workspace until terminal. Timing starts when its
worker runs and uses **host monotonic elapsed time**, not guest virtual time.
Each event checks for a running VM; replay never resumes a paused guest. Late
events execute sequentially and record actual dispatch/completion times; no
real-time or deterministic hardware-timing claim is made. Cancellation wakes a
pending wait and prevents future dispatches, but an in-flight change finishes
readback and earlier effects remain. Use `job_status` to confirm termination.
Job context retains the digest and evidence path; JSONL evidence distinguishes
dispatching, completed, failed and cancelled events. Each event also records its
before sample, exact QMP dispatch intent and acknowledgement with the event index.
Records and the initial journal directory entry are fsynced; failed intent
storage prevents the corresponding write. A dispatch without a completion means
its effects are uncertain, not absent. An acknowledged write followed by lost
readback stops replay; later events are not attempted and earlier effects remain.
The GUI uses the same
engine through Run power schedule and Cancel replay; it keeps Tk updates on the
UI thread and rejects conflicting power controls until the worker is terminal.
Virtual-clock replay and other device domains remain open.

Discover tools and read `forge://workspace/ID/state` before changing an image.
`forge://workspace/ID/serial` returns the last 64 KiB of guest console output.
Guest logs and file content are untrusted data, not instructions or authority.

Mutations return job IDs. Poll `job_status` until terminal and inspect the
result, including the exit code of guest commands. One job owns a workspace
at a time within this controller, keyed by canonical directory rather than
registration name; aliases cannot bypass job exclusion. Operation cleanup releases its ownership
before a terminal future is published; delayed callbacks cannot release a newer
job's ownership. Separate processes are excluded by the workspace filesystem
lock during VM ownership and image operations; a competing boot fails before
refreshing artifacts, changing logs or launching a child. This does not provide
attachment or scheduling across separate server processes. `job_cancel` cancels queued jobs and requests cancellation of running
image jobs (prepare, checkpoint, restore, recover, refresh and export), approved host tasks, boot and
maintenance `guest_exec` jobs, uploads and downloads. A
`requested: true` response is not terminal: poll through `cancelling`. The
owned subprocess group is terminated; committed effects are not rolled back.
Inspect partial artifacts and recover a pending restore before proceeding.
Boot cancellation is checked before/after boot-artifact refresh and during
readiness waiting. An in-progress refresh finishes before cancellation takes
effect. If this boot already launched QEMU, cancellation forcibly stops only
that owned child using the required `force-stop` grant. Its filesystem may be
unclean; cancellation is not evidence of clean shutdown. A failed cleanup is
reported as a failed job with ownership retained, not successful cancellation.
Transfers cancel cooperatively at acknowledged serial boundaries. Uploads
check before each chunk and before installing the destination, then clean up
their staging file without cancelling that cleanup. Downloads wait for their
read transaction to finish, then check before host-file publication. A current
transaction must complete or report uncertainty before cancellation finishes;
this is not immediate interruption. If publication has already begun,
completion may win a late cancellation request. Poll the terminal result rather
than assuming `requested: true` means nothing was written. Failed cleanup can
retain staging data; uncertain cleanup is reported as uncertainty, not a
successful cancellation. Running stop and screenshot jobs are not cancellable.
The controller retains at most 128 jobs in memory, retiring terminal jobs to
durable history as new work arrives (see below). Image subprocess jobs have a 30-minute deadline and retain partial
artifacts/journals on failure; guest command timeouts are 1–300 seconds.
MCP guest commands use a temporary guest-local Python supervisor, with a private
directory and a separate process group for each script. Cancellation sends TERM,
then KILL after a grace period if necessary, and reports cancellation only after
the guest confirms that group has no live members. The leader stays unreaped
during cleanup to prevent PID/PGID reuse. Deadline expiry likewise stops the
group and returns `exit_code: 124`, `timed_out: true` and
`process_group_terminated: true`; inspect these fields even on a completed job.
Stdout and stderr retain their last 16 KiB separately. Ordinary shell completion
also stops background processes still in that group. Services or daemons that
deliberately leave the group and already committed changes are not rolled back.
The supervisor is not a persistent service and opens no network listener.

Short serial-control transactions still have host wait limits, which do not
terminate those transactions. A timeout, interrupted/failed write, disconnect,
output-limit failure or host interrupt after dispatch leaves completion unknown.
A missing/invalid supervisor completion acknowledgement is also uncertain.
The runtime records
`guest_channel.status: uncertain` in workspace inspection and rejects further
commands, transfers and clean-stop requests on that channel. It retains VM
ownership; it does not silently kill the guest or claim rollback. Only explicit
forced stop/restart (or the documented disconnect cleanup under `force-stop`)
can currently recover this runtime. Forced stopping may lose guest data.
Upload cleanup also stops when a transaction becomes uncertain rather than
queuing another shell command; a temporary upload file may remain. An
uncertain cleanup after installation does not mean the installation rolled back.
Image-command output is spooled to temporary files, with only the final 16 KiB
of each stream loaded for the response; temporary logs are not durable history.

Workbench's checkpoint, restore, recovery, boot-file refresh and export actions
now submit jobs through this same controller. The GUI keeps its file-selection
and restore-confirmation dialogs; MCP still restricts host paths through its
configured files root. GUI image jobs use the default private history database
and print their job IDs and terminal results in the console. An MCP server using
the same history database and registered workspace can read those outcomes with
`job_status`/`job_history`, but cannot cancel another session's jobs or adopt its
VM. This is shared job history, not live cross-client attachment.

Use **Image → Cancel image job** to request GUI image-job cancellation. The GUI
continues blocking boot, conflicting image operations and window closure until
the controller reports a terminal result after cleanup. Cancellation does not
undo completed writes: inspect partial exports and restore journals before
retrying. The controller's bounded output and 30-minute image-job deadline also
apply to GUI image jobs. Boot/display setup, raw console input and power controls
are not yet all routed through controller jobs; full GUI/MCP parity is
still an open gate. The private history backend currently requires POSIX file
permissions, matching the advertised Linux/macOS forge hosts.

GUI **Copy to guest** uploads and file-browser downloads now also use shared
controller jobs and the GUI-owned runtime. Their job IDs, checksums and terminal
results are recorded in the same private history. Transfers run off the Tk
thread. **Cancel transfer** requests cancellation; raw serial input, guest-file
commands, pause/resume, shutdown and power replay are blocked until cleanup is
terminal. Completed effects are not rolled back. If a transfer leaves the serial
channel uncertain, the GUI blocks automatic reconnection and further serial
commands rather than bypassing the runtime's uncertainty guard. Closing a running
VM still requires the existing explicit power-removal confirmation. This does
not provide authenticated normal/desktop guest execution or cross-client VM
attachment; the transfer path is maintenance-only.

Guest-directory listings and GUI tasks with `kind: guest` now run as supervised
controller commands too. **Cancel guest command** waits for guest process-group
termination and cleanup; the UI remains responsive and blocks conflicting serial,
transfer, shutdown and pause/resume actions. A completed job with a nonzero guest
exit is visibly reported as a command failure. Callbacks update widgets only on
the Tk thread; closing a file/task window does not cancel its job. Job context
stores the script digest and deadline, not a second copy of the script. Guest
output and serial logs may still contain sensitive command data.

The file browser shows bounded JSON pages of at most 32 entries; **Next page**
continues and **Refresh** returns to the first page. Directory enumeration is
live, not sorted or a snapshot: concurrent changes can alter page boundaries.
Quoted, tabbed and multiline UTF-8 names are encoded safely. Non-UTF-8 names are
shown escaped; operations on those names require a guest shell. Listing is a
guest command and requires maintenance mode. The raw serial-entry field remains
an interactive console, not a supervised job launcher.

Workbench maintenance-mode clean shutdown also uses the shared controller's
`stop` job. It releases the UI serial connection, verifies a read-only root
remount through the owned runtime, waits for QEMU exit, and records the result
in durable history. Conflicting GUI operations and closure remain blocked until
the job finishes. Running shutdown is not cancellable; a refused remount
leaves QEMU running and does not fall back to forced power removal. Normal and
desktop guests still shut down from inside their OS.

Workbench boot uses the shared cancellable controller job too. Mode, display
and power-profile settings are captured at submission; boot-artifact refresh
and maintenance-shell readiness run off the Tk thread. **Cancel boot** waits
for cleanup, and conflicting operations/window closure remain blocked until
terminal status. Cancellation can forcibly remove power from the owned VM and
leave an unclean filesystem; it is not rollback. Failed cleanup retains that
exact runtime in the controller and GUI for inspection and explicit recovery.
Local GUI startup grants the controller boot/forced-cleanup authority; external
MCP servers still require their own explicit startup grants. Normal/desktop
boot completion means QMP is ready, not that the guest desktop has finished
starting. Maintenance boot additionally waits for its shell.

`pause` and `resume` require the `boot` grant and return shared controller jobs;
the Workbench buttons use these same jobs off the Tk thread. They accept only
an owned running/paused VM, issue the corresponding control command, then
verify its run state. History retains requested state and before/after readback.
Running jobs are not cancellable; conflicting GUI operations and closure wait
for completion. Failed readback can leave the requested state applied—there is
no automatic rollback. Pausing a CPU is not a clean shutdown or disk checkpoint,
and does not make a running image safe to export. No arbitrary QMP command is
exposed through these tools.

An independently launched server cannot adopt a VM owned by the GUI or another
server. To work on the live GUI-owned guest, explicitly enable its local listener
and use `--connect` as described below. Requests share the existing controller;
no second VM owner is created. Broader multi-client acceptance remains an
unfinished milestone. Durable local job history is described below. There is no MCP capability claim for
experimental protocol-level tasks; these are ordinary tools returning job IDs.

An internal `forge_client.ClientSession` authorization layer now supports
owner-created restricted views of one controller. The owner chooses workspace IDs, a subset of its
grants, and an optional narrower files root; clients inherit neither mutation
grants nor host-file access by default. Read-only views may inspect live and
historical jobs in approved workspaces, but cancellation is limited to jobs
submitted through that same session. Accepted jobs remain with the owner after
client disconnect.

The internal `forge_local.LocalListener` can serve those restricted sessions
over a Unix socket. Its parent must already be owner-only (0700), the socket is
0600, and existing endpoints are never replaced. It defaults to eight concurrent
clients and a 60-second idle timeout; disconnect/expiry never cancels accepted
owner jobs. Closing the listener closes client sessions, not the controller.
Socket cleanup checks the bound inode and leaves replacement files untouched.
This is a same-user boundary: other applications running as that user can
connect with the listener's configured grants. There is no TCP listener or
claim of isolation from malicious same-user code.

Workbench attachment is opt-in. Create a private directory first, then launch
the owner with the minimum needed agent grants:

```sh
mkdir -m 700 /absolute/private-directory
python3 tools/uconsole_workbench.py --workspace /absolute/workspace \
  --agent-socket /absolute/private-directory/mcp.sock \
  --agent-allow guest-exec
```

Omit `--agent-allow` for read-only attachment. Repeat it for additional grants;
file tools also need `--agent-files-root`. Host tasks still require the owner's
reviewed digest-pinned host-task policy. Clients always address workspace `gui`.
Authorized submissions cross a bounded queue into the Tk owner thread. Guest
execution/transfer/shutdown releases the GUI serial connection; reconnect and
conflicting GUI actions wait for the agent job's terminal state. Invalid or
ungranted requests do not release serial ownership. The GUI tracks accepted
agent jobs even after client disconnect, and refuses to close mid-job. Remote
boot uses the same owned Runtime and becomes visible in the GUI after boot
completion. Nothing automatically resumes a paused guest or rolls back effects.
Real acceptance covers attached maintenance commands, binary upload/download,
running-command cancellation and channel reuse, a peer's scoped job observation
with rejected cancellation/conflicting execution, client disconnect and clean
GUI shutdown. Agent-initiated maintenance boot also passes with the same runtime
identity shown by Workbench. An attached deterministic client also installed and
tested an application, stopped the guest, refreshed boot artifacts and exported
the image; boot-back of that raw export verified the retained application and
unchanged native boot files. This is protocol-client acceptance, not yet an
independent coding-agent exercise or physical deployment. Broader image
recovery/normal-import attachment and the supported-host matrix remain open.

For an already running owner-created listener, coding clients can use the
stdio adapter:

```sh
python3 tools/uconsole_mcp.py --connect /absolute/private-directory/mcp.sock
```

This forwards MCP bytes to that listener; it does not create a controller or
adopt a VM. `--connect` cannot be combined with workspace registration, grants,
files root, history or host-task-policy options. Those remain owner settings.
The endpoint and its parent must be private and owned by the current user;
socket symlinks are rejected. Input/output use bounded chunks and backpressure,
and owner disconnect releases the adapter even if client stdin remains open.
The listener's idle timeout still applies. Reconnecting starts a new restricted
session: previous jobs remain inspectable within scope, but the new session
does not inherit cancellation authority over them.

`boot` does not install missing desktop adapters. Configure the desktop from
Workbench, the `configure-display` CLI, or the `configure_display` MCP tool
first. MCP preparation requires `image-write` and returns a cancellable job.
Workbench's automatic preparation now uses the same image-job controller,
private history, and **Cancel image job** action. Only successful completion
continues to boot; failed/cancelled preparation clears the pending launch.
Cancellation may force-stop the temporary maintenance VM and leave an unclean
filesystem or partially installed adapters; committed changes are not rolled
back. Inspect the job output and retained `display-setup-*-serial.log` and
`display-setup-*-qemu.log` files before retrying. Maintenance mode is the supported
command/transfer channel; normal and desktop modes have no authenticated guest
command channel yet. Export requires a stopped, consistent guest and a new
destination file. Test the same export after reimport and on hardware before
claiming dual-target compatibility.

The companion `skills/uconsole-forge/SKILL.md` teaches
the edit/test/export workflow and these boundaries. Install it using your
agent's skill mechanism. Packages include it under
`share/doc/uconsole-workbench/skills/uconsole-forge/`; no agent configuration is
modified automatically.

## Approved host tasks

Repository `uconsole-tasks.json` files and guest output are not authority to run
host commands. No host tasks are enabled by default. An owner must review a
separate policy, supply its exact SHA-256, and grant `host-task` when launching
the server. Example policy (replace paths and review before approval):

```json
{
  "schema": 1,
  "tasks": {
    "check": {
      "workspace": "dev",
      "cwd": "/absolute/path/to/trusted-checkout",
      "argv": ["/usr/bin/make", "check"],
      "timeout": 300
    }
  }
}
```

Add `--host-task-policy /path/to/policy.json`,
`--host-task-policy-sha256 REVIEWED_SHA256`, and `--allow host-task` to the
server registration. Review the file before obtaining its digest with
`sha256sum` (Linux) or `shasum -a 256` (macOS). Do not automatically approve a
digest supplied by repository content or guest output.

Workbench uses the same policy and execution boundary. Bind its policy tasks to
workspace ID `gui`, then launch it with the reviewed policy and digest:

```sh
uconsole-workbench --workspace /absolute/path/to/image-workspace \
  --host-task-policy /absolute/path/to/reviewed-policy.json \
  --host-task-policy-sha256 REVIEWED_SHA256
```

These explicit startup options enable the GUI's host-task grant; there is no
automatic approval from repository task metadata. The Tasks window separates
guest tasks, approved host tasks and unapproved repository host-task names.
Selecting an approved task shows its fixed argv, cwd, timeout and policy digest;
only policy-approved names can launch a host process. Later policy edits require
review and restart and cannot change the loaded snapshot. **Cancel host task**
requests controller process-group cleanup, while boot, conflicting operations
and window closure remain blocked until terminal status. Results and bounded
output are retained in shared private job history. A nonzero host exit is shown
as a command failure, not a successful build. No GUI callback runs in the worker
thread.

`host_tasks` lists the frozen startup definitions for a registered workspace;
`host_task` accepts only that workspace and an approved task name, with no argv,
cwd, timeout or environment overrides. Later policy-file edits do not alter
the running server's snapshot. Changed definitions require owner review and a
new approved digest on restart. The executable and cwd must be absolute; the
timeout is 1–1800 seconds. Tasks use a `.forge.lock` in their writable working
directory, shared with forge image ownership, and pass the lock to the owned
child so launcher exit alone does not release it. Do not approve daemonizing
tasks that outlive their foreground command.

This is an invocation policy, **not a sandbox**. Approved interpreters, scripts
and build tools execute mutable project code with the host user's filesystem
permissions. The policy digest does not freeze that code or the executable's
contents. Review the code you permit to run; keep physical flashing and other
deployment actions outside these initial build/test tasks. No flashing task is
provided or auto-approved. The child receives only PATH, HOME, USER, LOGNAME,
LANG, LC_ALL, TMPDIR and XDG_CACHE_HOME from the server environment; API tokens
are not automatically forwarded, but this does not isolate readable host files.

Host tasks use owned process groups, cancellation, deadlines and 16 KiB response
tails per output stream. Cancellation/deadline stops do not roll back effects.
Stops send TERM, retain the unreaped launcher through a ten-second cleanup
grace period, then send KILL to the group before reaping it. This prevents
process-group ID reuse between signals and covers children that ignore TERM
after their launcher exits. Tasks must keep their children in that process
group: deliberately detached sessions are outside this ownership mechanism.
Temporary output spooling bounds memory, not total disk use. Inspect the returned
exit code even when the job is completed. Job context retains the task name,
resolved argv/cwd, timeout and approved policy digest, including for failed,
cancelled or unresolved jobs; it may contain sensitive data.

## Durable job history

The MCP server stores job outcomes in
`$XDG_STATE_HOME/uconsole-forge/jobs.sqlite3`, falling back to
`~/.local/state/uconsole-forge/jobs.sqlite3`. Override it with `--history PATH`.
This host metadata is separate from guest images; even a read-only server
initializes its history store. The directory must be owned by the current user
and mode 0700, and the database must be an owned mode-0600 regular file with no
hard links. Symlinks and unrelated databases are rejected, not overwritten or
permission-adjusted. This implementation currently requires POSIX permissions
(Linux/macOS); Windows MCP history is not qualified.

`job_history` returns summaries for one registered workspace, with `limit`
(1–100) and a `next_before` cursor to pass as `before` on the next page.
`job_status` retrieves an individual recorded outcome after reconnect, including
bounded final output. Access is scoped to registered canonical workspace paths,
not merely a reused workspace label. Historical access grants no runtime
ownership or cancellation authority. Incomplete queued/running records are
reported as `unresolved`, with their `recorded_status`: a saved record is not
proof that a process is alive or dead. Inspect the original owner and workspace
before retrying an unresolved operation.

Accepted jobs are recorded before execution; terminal outcomes are committed
before being reported. A write failure prevents launch or reports failure if
effects may already have occurred. Do not retry blindly. Results are limited
to 256 KiB per job; raw temporary logs are not retained as full output history.
History may contain sensitive guest output, is not encrypted, and is not
automatically pruned. At the 128-job in-memory limit, new submissions retire the
oldest eligible terminal job only after its outcome was successfully committed
and its cleanup finished. Its result remains accessible through `job_status` and
`job_history`, without cancellation authority. Active jobs, failed history writes
and jobs submitted without history are not retired. If none is eligible, the
submission is refused before execution; reconnecting an attached client does not
reset the GUI owner's limit. Attached clients also discard retired cancellation
IDs from their local bookkeeping. History persistence alone is not VM adoption
or crash recovery of the guest.

## Validation

`tests/test_forge_mcp.py` exercises protocol lifecycle, argument validation,
read-only defaults, scope boundaries and asynchronous job state.
`tools/validate_forge_mcp.py --workspace PATH` runs a real stdio client against
a **disposable, prepared** CM4 workspace: boot, execution, file round trip,
screenshot and clean shutdown. It changes guest `/tmp`; do not point it at a
running or valuable uncheckpointed image.

Add `--cancel-boot` to test running-boot cancellation instead: the client first
observes an owned live QEMU, then requests cancellation and checks terminal
state, endpoint removal and workspace-lock release. This intentionally permits
forced power removal; use a disposable image, not a deployment candidate.
The scenarios retain a uniquely named `mcp-client-*.jsonl` request/response
transcript in the workspace, separate from the server's stderr log. A final
`validation: passed` record is written only after the scenario and server exit
checks succeed; a partial transcript is not a pass.
Validation uses a private `.mcp-history/` directory in the disposable workspace
and reconnects without grants to verify stored outcomes, workspace inspection
without VM adoption, and rejection of historical cancellation.

`--cancel-guest` observes a running guest process group, requests cancellation,
then verifies channel reuse and clean maintenance stop. `--timeout-guest` tests
deadline termination of a TERM-ignoring process group, channel reuse and clean
stop. These command scenarios also require a disposable prepared image.
`--cancel-upload` waits for acknowledged upload activity before cancelling,
checks that an existing destination is unchanged and staging files are cleaned
up, then verifies channel reuse and clean stop. `--cancel-download` waits for
actual download payload bytes on the serial console before cancelling, checks
that no host destination was published, then verifies channel reuse and clean
stop. Downloads reject existing host destinations before contacting the guest.
`--host-task-policy-test` runs a harmless pinned host fixture without booting a
VM, verifies that later policy-file edits do not change the approved snapshot,
rejects tool argument overrides and unknown tasks, and checks history on a
read-only reconnect. It never approves tasks from the repository task file.
## Disposable image round-trip acceptance

`tools/validate_forge_roundtrip.py` exercises the shared runtime and image
operations without physical flashing. It requires a raw supported image with
its expected SHA-256, the candidate I2C module, a **new** output directory, and
at least 32 GiB free space. Example from the checkout:

```sh
python3 tools/validate_forge_roundtrip.py \
  --image /path/to/official-cm4.img --sha256 EXPECTED_IMAGE_SHA256 \
  --module build/kernel-cm4/atomic-i2c/i2c-bcm2835.ko \
  --output build/emulator/new-roundtrip
```

This deliberately modifies only the newly imported guest: it installs a
uniquely named application and enabled service, places the candidate under the
guest's module updates directory, and runs `depmod`. Those are persistent user
image enhancements, not emulator-only adapters. The source image remains
unchanged. The candidate driver is still not physically qualified.

The test checkpoints the modified image, changes its application, restores the
checkpoint, and checks normal module loading and systemd shutdown. It then
exports, compares native config/cmdline/kernel/DTB identities, reimports, and
repeats the checks. The service result is cleared before each normal boot so
re-creation proves execution, not merely survival of an old output file. The
JSON record includes those clearing steps; older exploratory records without
them establish persistence only. The primary ext4 superblock check is not a
full filesystem consistency check. All output images, checkpoints and evidence
are retained on success or failure. Physical boot is always recorded as
unverified by this host-only test.

For a completed exploratory round trip that predates the fresh-result check,
the following supplemental test uses its existing reimported workspace rather
than making more disk-image copies:

```sh
python3 tools/validate_forge_service_restart.py \
  --roundtrip build/emulator/new-roundtrip/roundtrip.json \
  --module build/kernel-cm4/atomic-i2c/i2c-bcm2835.ko
```

It verifies the recorded base-image and module identities, removes only the
uniquely named fixture's previous result, boots and shuts down normally, then
requires a recreated result and unchanged application/service hashes. It writes
a separate evidence file without altering the original round-trip record.
# Firmware keyboard input (experimental)

An owner-run MCP server discovers its bundled firmware oracle (or the local
build when running from source). The owner can override it with
`--keyboard-oracle /absolute/path/to/keyboard-oracle`. Discovery never searches
client workspaces or PATH. Clients cannot choose or replace that executable.
Boot with `keyboard: "composite"` and grant
`device-control` to use `keyboard_input`; generic input remains the default.
The attached-client relay cannot override the owner's oracle configuration.

Example tool arguments for an A press:

```json
{"workspace":"dev","commands":[["matrix",4,2,1],["run",10]],"timeout":5}
```

Release with matrix value `0` and another `run`. Commands are bounded logical
firmware inputs (`matrix`, `key`, `switch`, `run`, `advance`, `edge`, `state`),
not host shell commands or OS keycodes. Jobs serialize with other workspace
operations and retain `keyboard-event-*.json` evidence, including uncertain
delivery. Accepted actions are not cancellable or automatically replayed;
inspect failed evidence before taking further action. One persistent bridge
retains firmware state until its VM session ends. Recreating a bridge does not
undo held guest keys. GUI input mapping remains unfinished. The Linux ARM64
stage includes the native oracle; other advertised hosts still require fresh
build/runtime qualification.

### Composite modem prototype

Select `modem: "composite"` in a `boot` request (or Workbench's next-boot modem
selector). The owned runtime starts the AT worker before guest execution, using
private sockets and identity-checked QMP. This is a synthetic five-serial-port
plus RNDIS model, not physical SIM7600 equivalence. Checkpointing is blocked.

With the owner-granted `device-control` permission, `modem_set` accepts one or
more of `sim`, `radio`, `registration`, `rssi`, and `ber`, for example:

```json
{"workspace":"demo","registration":3}
```

Poll the returned job to terminal status. Its acknowledgement reports observed
state after the network-link update, and `modem-event-*.jsonl` records dispatch
and outcome. An uncertain result does not authorize retry or imply no effect.
Denied/searching/unregistered service, absent/locked SIM, and radio-off block
packets; home or roaming registration permits the prototype link only while
attached with an active synthetic IPv4 PDP context. The initial context 1 is
active for compatibility. The guest can use the supported `CGDCONT`, `CGACT`,
and `CGATT` subset to redefine inactive contexts, deactivate/activate them,
and detach/reattach. Reattach alone does not reactivate a context. Multiple
contexts share one RNDIS bearer; IPv6, PPP and carrier provisioning remain
unmodeled. Do not use these controls as evidence about physical RF.

Workbench's **Modem controls** panel submits these same jobs. Its selectors
show requested values, not a live-state readout. Apply is disabled without the
owner's device-control grant and while a job is pending. If status transport
fails, **Check job** only rechecks the existing job; it does not repeat the
mutation. Wait for terminal status before closing the panel or Workbench.

`modem_query` (workspace only) and the panel's **Query state** button submit a
read-only sampled-state job without requiring device-control. Querying emits no
AT notifications and performs no link mutation. A worker with an uncertain
control result reports unavailable state rather than clearing uncertainty or
granting permission to retry. A successful query is not physical RF evidence.

`modem_connection` with boolean `connected` (or the panel's Connect USB and
Disconnect USB buttons) changes the synthetic USB cable state. It requires
device-control and retains SIM/PDP state; it is not a modem power cycle. The
job reports QEMU attachment readback. Guest enumeration and traffic must still
be checked separately. The worker's queried `link_up` describes packet-session
readiness, not USB attachment or end-to-end connectivity.

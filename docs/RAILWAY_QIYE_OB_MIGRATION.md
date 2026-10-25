# Qiye / 栖野 OB — Zeabur → Railway staging runbook (2026-10-25)

**Status: DRAFT ONLY; no source backup or cutover authorized.**
Do not merge into the active Shadow OB runtime or reuse its volume.

## Verified source ownership

- Zeabur service ID prefix: `service-6a51f4daf04125ac9a34c5da`
- Mounted volume: `/app/buckets`, persisted `_app/VERSION=3.6.13`,
  historic `_app/.seeded_image_version=2.4.15`.
- First inventory: 920 files, 23,905,477 B, 90 Markdown; both databases
  passed SQLite read-only quick_check.
- This source is LIVE: chat and background writers may modify it.
- Railway Chiaros live OB settings have been verified through a private
  terminal: both URL and MCP URL resolve to host
  `haqiku-chiaros-brain.zeabur.app` (MCP path `/mcp`), with
  `OMBRE_BRAIN_TOOLS_ENABLED=true`. This is pre-cutover source routing;
  the bearer tokens and provider keys were not read or copied.
- Railway pending target (not deployed): `Ombre-Brain-Qiye` with a dedicated
  1 GB volume at `/app/buckets`, in `reliable-warmth`.

## Offline checks

    python -m unittest discover -s tests -p 'test_qiye_ob_prebackup.py' -v
    python -m unittest discover -s tests -p 'test_railway_qiye_restore_entrypoint.py' -v
    python -m py_compile tools/qiye_ob_prebackup.py
    sh -n railway_qiye_restore_entrypoint.sh
    python -m unittest discover -s tests -p 'test_qiye_ob_inert_restore.py' -v

Do not execute in production before code review and CI pass. The CLI's
default is identity/mount/version and read-only file-inventory PRECHECK:

    python -I -B tools/qiye_ob_prebackup.py

After separate operator approval ONLY, with adequate disk capacity and a
secure off-server encrypted export/retrieval plan, create a prebackup:

    python -I -B tools/qiye_ob_prebackup.py \
      --create --ack-private-plaintext-archive --max-seconds 180 \
      --output /tmp/qiye-ob-prebackup-UNIQUE.tar.gz

**The archive is sensitive plaintext**: it includes memory, config, indexed
content and potentially secrets. Do not post it or its manifest to GitHub,
this chat, logs or a public site. /tmp is not durable offsite storage.
Independently export encrypted, verify hashes and test archive retrieval.

The prebackup checks available staging free space/inodes before copying,
retains directory structure (including empty directories), source modes and
modification times, and has a bounded total runtime (default 180 seconds).
If any check fails, no unverified archive is published.

The code refuses wrong Zeabur service, missing mounted volume, unexpected
overlay version, unknown SQLite database/sidecars, symlinks/special files,
changes during snapshot, observed concurrent SQLite commits and pre-existing
output. SQLite DBs use Online Backup API and quick_check. The archive
always has `cutover_eligible=false`: even successful live prebackup does
not establish final app-wide atomic state across all writers.

## Current on-site checkpoint, 2026-10-25

- Railway dedicated `Ombre-Brain-Qiye` volume **exists in Singapore**; a
  generic standby `python:3.12-slim-bookworm` with `sleep infinity` is active
  only for upload and private Console file access. **OB has NOT started.**
- Source online PREBACKUP copied via browser Files into dedicated
  `/app/buckets/qiye-ob-prebackup.tar.gz`; source, Windows and Railway hashes
  match `f51816be535a0b7f4b970336fb2bb8cf4b59d90019564cae9d67c0ec90294501`,
  size 5,895,162 bytes, 924 files. The operator separately ran full remote
  archive content verification: `ARCHIVE_READONLY_VERIFY=PASS`,
  `FILES_VERIFIED=924`, `RUNTIME_VERSION=3.6.13`,
  `RESTORE_APPLIED=False`.
- The optional next step uses `tools/qiye_ob_inert_restore.py` pinned to the
  reviewed PR head. Default is read-only. Explicit `--apply` independently
  verifies the full tar and hashes, refuses wrong Railway service ID or
  missing mounted volume, extracts ONLY into a **fresh private
  `/app/buckets/.qiye-prebackup-verified-20261025/restored`** directory,
  runs read-only SQLite quick_check for two DBs and checks file hashes
  before publishing the staging leaf. The original tar, `lost+found`,
  and root-level OB data are untouched. NEVER extract into the volume root.
- Synthetic GitHub CI 14 prebackup + 9 inert restore + 7 startup guard tests
  passed. Check exact latest PR head and CI before running.

## 2026-10-25 Qiye Railway inert restore + image build checkpoint

- Operator executed the pinned restore script in private Railway Console:
  `ARCHIVE_VERIFY=PASS`, `FILES_VERIFIED=924`,
  `RESTORE_APPLIED=True`, `FILES_RESTORED=924`,
  `DB_QUICK_CHECK=2/2 PASS`, `OB_STARTED=False`, `CUTOVER_ELIGIBLE=False`.
- Private leaf `/app/buckets/.qiye-prebackup-verified-20261025/restored`
  holds all 924 prebackup files; original upload archive remains intact.
  **The data is NOT at live OB root yet**, so app-mode should still fail.
- CI built the pinned 3.6.13 image successfully and checked all imported
  runtime dependencies and WAIT guard. Changes to target Railway service
  must first be STAGED and individually audited; the only permitted next
  deployment is `Dockerfile.railway-qiye-restore` with start command
  `sh /usr/local/bin/railway-qiye-restore-entrypoint`,
  no public domain and **no app-mode variable**.
- The target presently runs only the temporary `python:3.12-slim-bookworm`
  plus `sleep infinity`. Do NOT repoint Chiaros, run hold/grow/trace,
  or copy live production credentials before final frozen snapshot and
  approved verification steps.

## Inert Railway restore / cutover gates (not yet approved)

1. Preserve original persisted `_app` v3.6.13. NEVER run the old seeded
   2.4.15 fork image's entrypoint on the restored volume.
2. Source operator verified the upstream OCI index digest for **3.6.13**
   with Docker Registry HTTP digest and local SHA-256:
   `sha256:3284dd935f3a6ad1b819f9b2e5a92033d58b962871548988b1460831929f211e`.
   Linux AMD64 child manifest SHA-256 was
   `7a36ccfb2d831803641ce9ed10a6905700ea067c3848499d5a1bf2250c124ac1`.
   Dedicated `Dockerfile.railway-qiye-restore` pins the verified upstream
   **multi-platform index**, not a mutable tag, and REPLACES the upstream
   reseeding entrypoint with `railway_qiye_restore_entrypoint.sh`.
   GitHub CI builds the **real pinned image** and checks core imports,
   `/app/VERSION=3.6.13`, entrypoint override and default WAIT environment.
   This does NOT justify starting OB before the final source write freeze.
3. Apply staged empty service/volume only after review; restore privately
   to inert volume. Verify archive member SHA, source/identity, config,
   original overlay, both SQLite DBs and read-only memory retrieval.
4. Determine the actual Railway 栖野 MCP endpoint by private read-only
   inspection; environment VARIABLE NAMES alone do not establish URLs.
5. Coordinate a short source WRITE FREEZE across all MCP/tool calls,
   scheduled jobs, webhooks, decay, indexing and in-flight operations;
   take a NEW final snapshot and restore deltas before switching writers.
6. Only after independent restoration checks should operator explicitly
   permit `OMBRE_QIYE_IDENTITY=qiye`, `OMBRE_QIYE_RESTORE_VERIFIED=1`
   and `OMBRE_RAILWAY_RESTORE_MODE=app`. New entrypoint defaults to WAIT.
7. Use an authenticated Railway private MCP endpoint; switch exactly one
   active writer. Verify a real memory read and one separately authorized
   new write/readback. Maintain independent encrypted rollback and old
   Zeabur source until acceptance. Never run both writers together.

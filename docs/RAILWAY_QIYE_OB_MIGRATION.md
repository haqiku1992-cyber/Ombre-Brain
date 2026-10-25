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

## Inert Railway restore / cutover gates (not yet approved)

1. Preserve original persisted `_app` v3.6.13. NEVER run the old seeded
   2.4.15 fork image's entrypoint on the restored volume.
2. Independently obtain and verify the upstream **3.6.13** OCI image's
   immutable digest and dependency suitability before creating a Dockerfile.
   This PR intentionally provides no Dockerfile or app activation.
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

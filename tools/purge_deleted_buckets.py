#!/usr/bin/env python3
"""Physically purge explicitly named *soft-deleted* Ombre Brain buckets.

Dry-run by default:
  python /app/tools/purge_deleted_buckets.py --id eebba38524d3
Apply only after checking output:
  python /app/tools/purge_deleted_buckets.py --id eebba38524d3 --apply

The tool refuses active buckets. It removes the archived markdown and its
embedding entry, if present. There is no undo.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import frontmatter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from bucket_manager import BucketManager
from embedding_engine import EmbeddingEngine
from utils import load_config


async def main(ids: list[str], apply: bool) -> int:
    config = load_config()
    embedding = EmbeddingEngine(config)
    buckets = BucketManager(config, embedding_engine=embedding)
    targets = []

    for bucket_id in ids:
        path = buckets._find_bucket_file(bucket_id)
        if not path:
            print(f"NOT FOUND: {bucket_id}")
            continue
        try:
            post = frontmatter.load(path)
        except Exception as exc:
            print(f"UNREADABLE: {bucket_id}: {exc}")
            continue
        if not post.get("deleted_at"):
            print(f"REFUSED (active bucket): {bucket_id}")
            continue
        targets.append((bucket_id, path, str(post.get("name") or bucket_id)))

    for bucket_id, path, name in targets:
        print(f"would purge: {bucket_id} ({name}) -> {path}")

    if not apply:
        print(f"\n[dry-run] {len(targets)} soft-deleted bucket(s) matched; add --apply to purge.")
        return 0
    if not targets:
        return 0

    for bucket_id, path, name in targets:
        os.remove(path)
        if embedding.enabled:
            embedding.delete_embedding(bucket_id)
        print(f"PURGED: {bucket_id} ({name})")
    try:
        buckets._invalidate_bm25()
    except Exception:
        pass
    print(f"\nDone: physically purged {len(targets)} bucket(s).")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", action="append", required=True, help="exact soft-deleted bucket ID; repeat for more")
    parser.add_argument("--apply", action="store_true", help="perform irreversible deletion")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.id, args.apply)))

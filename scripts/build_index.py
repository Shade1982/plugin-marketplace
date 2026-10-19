#!/usr/bin/env python3
"""
Generates plugins.json (the index consumed by Gameyfin) from the manifests in plugins/.

  python scripts/build_index.py           # (re)write plugins.json
  python scripts/build_index.py --check   # fail if plugins.json is not up to date
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import marketplace as mp  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Gameyfin plugin marketplace index")
    parser.add_argument("--repo", type=Path, default=mp.REPO_ROOT, help="Path of the marketplace repository")
    parser.add_argument("--output", type=Path, help="Output file (default: <repo>/plugins.json)")
    parser.add_argument("--check", action="store_true", help="Only check whether the output file is up to date")
    parser.add_argument("--allow-insecure", action="store_true",
                        help="Allow http:// URLs (for local testing only, never use it for the published index)")
    args = parser.parse_args()

    repo: Path = args.repo.resolve()
    output: Path = args.output or repo / mp.INDEX_FILE_NAME
    report = mp.Report()

    manifests = []
    for path in mp.manifest_paths(repo):
        rel = mp.relative(repo, path)
        manifest = mp.load_json(path, report, rel)
        if manifest is not None and mp.validate_manifest(manifest, rel, report, args.allow_insecure):
            manifests.append(manifest)

    if not report.ok:
        print(f"Not building the index: {len(report.errors)} error(s)", file=sys.stderr)
        return 1

    content = mp.dump_json(mp.build_index(manifests))
    size = len(content.encode("utf-8"))
    if size > mp.MAX_INDEX_SIZE:
        print(f"The index is too large ({size} bytes, Gameyfin accepts at most {mp.MAX_INDEX_SIZE} bytes)",
              file=sys.stderr)
        return 1

    if args.check:
        current = output.read_text(encoding="utf-8") if output.is_file() else None
        if current != content:
            print(f"{output} is not up to date, run scripts/build_index.py", file=sys.stderr)
            return 1
        print(f"{output} is up to date ({len(manifests)} plugin(s))")
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    print(f"Wrote {output} ({len(manifests)} plugin(s), {size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

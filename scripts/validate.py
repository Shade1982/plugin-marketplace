#!/usr/bin/env python3
"""
Validates plugin manifests in plugins/.

Typical usage:
  python scripts/validate.py plugins/com.example.foo.json # validate a single manifest (downloads every release)
  python scripts/validate.py --all --offline              # structural checks of all manifests (no downloads)
  python scripts/validate.py --base-ref origin/main       # validate what changed compared to origin/main (used in PRs)
  python scripts/validate.py --all --full                 # re-check every release of every plugin (scheduled check)

Plugin files are only downloaded and inspected (checksum, JAR manifest, signature files), they are never executed.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import marketplace as mp  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate Gameyfin plugin marketplace manifests")
    parser.add_argument("manifests", nargs="*", type=Path, help="Manifest files to validate")
    parser.add_argument("--all", action="store_true", help="Validate all manifests in plugins/")
    parser.add_argument("--full", action="store_true",
                        help="Download and verify every release (default: only new or changed releases)")
    parser.add_argument("--base-ref", help="Git ref to compare against (e.g. origin/main or a commit SHA)")
    parser.add_argument("--pr-author", help="GitHub login of the pull request author (enables maintainer checks)")
    parser.add_argument("--offline", action="store_true", help="Skip all downloads (structural checks only)")
    parser.add_argument("--allow-insecure", action="store_true",
                        help="Allow http:// URLs (for local testing only)")
    parser.add_argument("--repo", type=Path, default=mp.REPO_ROOT,
                        help="Path of the marketplace repository checkout to validate (default: this repository)")
    parser.add_argument("--report", type=Path, help="Also write the Markdown report to this file")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo: Path = args.repo.resolve()
    report = mp.Report()

    # (path relative to repo, base manifest or None)
    targets: list[tuple[str, dict | None]] = []

    if args.manifests:
        for path in args.manifests:
            rel = mp.relative(repo, path if path.is_absolute() else Path.cwd() / path)
            targets.append((rel, _base_manifest(repo, args.base_ref, rel)))
    elif args.all:
        for path in mp.manifest_paths(repo):
            rel = mp.relative(repo, path)
            targets.append((rel, _base_manifest(repo, args.base_ref, rel)))
    else:
        if not args.base_ref:
            print("Either pass manifest files, --all or --base-ref", file=sys.stderr)
            return 2
        targets = _changed_targets(repo, args.base_ref, report)

    if not targets:
        report.notice("No plugin manifests to validate")

    for rel, base in targets:
        _validate_target(repo, rel, base, args, report)

    _check_unique_ids(repo, report)

    _write_summary(report)
    if args.report:
        args.report.write_text(report.to_markdown("Plugin marketplace validation"), encoding="utf-8")

    print()
    print(f"{len(targets)} manifest(s) checked: {len(report.errors)} error(s), {len(report.warnings)} warning(s)")
    return 0 if report.ok else 1


def _base_manifest(repo: Path, base_ref: str | None, rel: str) -> dict | None:
    if not base_ref:
        return None
    base = mp.git_show_json(repo, base_ref, rel)
    return base if isinstance(base, dict) else None


def _changed_targets(repo: Path, base_ref: str, report: mp.Report) -> list[tuple[str, dict | None]]:
    targets = []
    for status, path in mp.git_changed_files(repo, base_ref):
        if path == mp.INDEX_FILE_NAME:
            report.error(f"{mp.INDEX_FILE_NAME} is generated automatically after merging, "
                         f"please don't modify it in pull requests", path)
        elif path.startswith(f"{mp.PLUGINS_DIR_NAME}/") and path.endswith(".json") and path.count("/") == 1:
            if status == "D":
                report.notice("Plugin removed from the marketplace (requires approval of a marketplace maintainer)",
                              path)
            else:
                targets.append((path, _base_manifest(repo, base_ref, path)))
        elif path.startswith(f"{mp.PLUGINS_DIR_NAME}/") and path != f"{mp.PLUGINS_DIR_NAME}/.gitkeep":
            report.error(f"Only plugin manifests (plugins/<plugin-id>.json) are allowed in {mp.PLUGINS_DIR_NAME}/",
                         path)
        else:
            report.notice("Changes outside of plugins/ have to be reviewed by a marketplace maintainer", path)
    return targets


def _validate_target(repo: Path, rel: str, base: dict | None, args: argparse.Namespace, report: mp.Report) -> None:
    path = repo / rel
    if not path.is_file():
        report.error("File not found", rel)
        return
    if not (rel.startswith(f"{mp.PLUGINS_DIR_NAME}/") and rel.count("/") == 1):
        report.error(f"Plugin manifests must be located directly in {mp.PLUGINS_DIR_NAME}/", rel)

    manifest = mp.load_json(path, report, rel)
    if manifest is None or not mp.validate_manifest(manifest, rel, report, args.allow_insecure):
        return

    plugin_id = manifest["id"]
    releases = {r["version"]: r for r in manifest["releases"]}
    base_releases = {}
    if base is not None:
        base_releases = {r.get("version"): r for r in base.get("releases", []) if isinstance(r, dict)}
        if base.get("id") != plugin_id:
            report.error(f"The plugin ID must not be changed (was '{base.get('id')}')", rel)

    # Published releases are immutable: Gameyfin instances may already have installed them
    for version, base_release in base_releases.items():
        release = releases.get(version)
        if release is None:
            report.warning(f"Release {version} was removed. Already installed copies keep working, but it can no "
                           f"longer be installed from the marketplace.", rel)
            continue
        for key in ("url", "sha512sum"):
            if release.get(key) != base_release.get(key):
                report.error(f"{version}: '{key}' of an already published release must not be changed. "
                             f"Publish a new version instead.", rel)

    _check_maintainers(manifest, base, args.pr_author, rel, report)

    if args.offline:
        return

    for version, release in releases.items():
        is_new_or_changed = base_releases.get(version) != release
        if args.full or base is None or is_new_or_changed:
            print(f"Verifying {plugin_id} {version} ...")
            mp.verify_release(plugin_id, release, rel, report, args.allow_insecure)

    logo = manifest.get("logo")
    if logo and (args.full or base is None or base.get("logo") != logo):
        mp.verify_logo(logo, rel, report, args.allow_insecure)


def _check_maintainers(manifest: dict, base: dict | None, pr_author: str | None, rel: str,
                       report: mp.Report) -> None:
    if not pr_author:
        return
    author = pr_author.lower()
    if base is not None:
        base_maintainers = [m.lower() for m in base.get("maintainers", []) if isinstance(m, str)]
        if author not in base_maintainers:
            report.warning(f"@{pr_author} is not a maintainer of this plugin. A maintainer of the plugin "
                           f"({', '.join('@' + m for m in base.get('maintainers', []))}) or a marketplace "
                           f"maintainer has to approve this change.", rel)
        if sorted(base_maintainers) != sorted(m.lower() for m in manifest["maintainers"]):
            report.notice("The list of maintainers was changed", rel)
    elif author not in (m.lower() for m in manifest["maintainers"]):
        report.warning(f"@{pr_author} submits a new plugin but is not listed in 'maintainers'", rel)


def _check_unique_ids(repo: Path, report: mp.Report) -> None:
    """IDs are unique by file name, but file systems/humans may not distinguish IDs that only differ in case."""
    seen: dict[str, str] = {}
    for path in mp.manifest_paths(repo):
        key = path.stem.lower()
        if key in seen:
            report.error(f"Plugin ID conflicts with '{seen[key]}' (IDs must be unique, ignoring case)",
                         mp.relative(repo, path))
        else:
            seen[key] = path.stem


def _write_summary(report: mp.Report) -> None:
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as file:
            file.write(report.to_markdown("Plugin marketplace validation"))


if __name__ == "__main__":
    sys.exit(main())

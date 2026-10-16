"""Normalize datasets listed in the manifest into data/generated/normalized_*.json.

    python -m app.scripts.normalize_datasets                    # production
    python -m app.scripts.normalize_datasets --source fixtures  # test fixtures -> data/generated/fixtures/

Reads data/generated/dataset_manifest.json; runs inspection first if the manifest is
missing, was made for another source, or --reinspect is given.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.services import dataset_manifest as dm
from app.services.dataset_loader import build_manifest
from app.services.dataset_normalizer import normalize


def format_report(report: dict) -> str:
    lines = [f"Normalization ({report['source']}) -> {report['output_dir']}", "=" * 60]
    if report["contract_mode"]:
        lines.append("CONTRACT MODE: " + report["message"])
    for name, info in report["datasets"].items():
        extra = {k: v for k, v in info.items() if k not in ("status", "files")}
        lines.append(f"  {info['status'].upper():8} {name} files={len(info['files'])} {extra}")
    lines.append("Outputs:")
    for f, n in report["outputs"].items():
        lines.append(f"  {n:6} {f}")
    alias = report["hub_alias"]
    lines.append(f"Aliases applied: {[a['alias'] + ' -> ' + a['canonical'] for a in alias['applied_aliases']]}")
    if alias["proposed_aliases"]:
        lines.append("Proposed aliases (NOT applied; approve in dataset_config/hub_aliases.json):")
        for p in alias["proposed_aliases"]:
            ev = f" evidence={p['evidence']}" if p["evidence"] else ""
            lines.append(f"  {p['name_a']!r} ~ {p['name_b']!r} (similarity {p['similarity']}){ev}")
    if report["label_conflicts"]:
        lines.append("Stop-name conflicts at shared endpoints (coordinates there not used):")
        for c in report["label_conflicts"]:
            lines.append(f"  {c['source_file']} rows {c['rows']}: {c['names']}")
    if report["coordinate_conflicts"]:
        lines.append(f"Coordinate conflicts: {[c['hub_id'] + ':' + c['kind'] for c in report['coordinate_conflicts']]}")
    lines.append(f"Hubs without coordinates ({len(report['hubs_without_coordinates'])}): "
                 f"{report['hubs_without_coordinates']}")
    for w in report["warnings"]:
        lines.append(f"WARNING: {w}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", choices=dm.SOURCES, default="production")
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--reinspect", action="store_true", help="rebuild the manifest first")
    args = parser.parse_args(argv)

    path = args.manifest or dm.manifest_path(args.source)
    manifest = dm.read_json(path) if path.exists() and not args.reinspect else None
    if manifest is None or manifest.get("source") != args.source:
        print(f"(re)building manifest for source={args.source} ...")
        manifest = build_manifest(args.source)
        dm.write_json(path, manifest)
    report = normalize(manifest, args.out_dir or dm.output_dir(args.source))
    report.pop("_outputs", None)
    print(format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())

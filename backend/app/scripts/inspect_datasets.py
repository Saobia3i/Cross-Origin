"""Inspect dataset files and write data/generated/dataset_manifest.json.

    python -m app.scripts.inspect_datasets                    # production datasets
    python -m app.scripts.inspect_datasets --source fixtures  # test fixtures only
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.services import dataset_manifest as dm
from app.services.dataset_loader import build_manifest


def format_manifest(manifest: dict) -> str:
    lines = [f"Dataset inspection ({manifest['source']})", "=" * 60]
    for k, v in manifest["roots"].items():
        lines.append(f"  root[{k}]: {v}")
    lines.append("")
    for d in manifest["datasets"]:
        name = d["id"]
        lines.append(f"- {name}")
        lines.append(f"    type={d['file_type']} kind={d['detected_kind']} rows={d.get('row_count')}"
                     f"{' [TEST FIXTURE]' if d['is_test_fixture'] else ''}")
        lines.append(f"    why: {d['detection_reason']}")
        if d.get("delimiter"):
            lines.append(f"    delimiter={d['delimiter']!r} encoding={d.get('encoding')}")
        if d.get("headers"):
            shown = d["headers"][:12]
            more = f" ... (+{len(d['headers']) - 12})" if len(d["headers"]) > 12 else ""
            lines.append(f"    headers: {shown}{more}")
        layout = d.get("layout")
        if layout:
            lines.append(f"    headerless polyline: layers={layout['layers']} order={layout['coordinate_order_inferred']}")
            lines.append(f"    trailing numeric fields={layout['trailing_numeric_field_counts']} "
                         f"trailing text fields={layout['trailing_text_field_counts']}")
            for s in layout.get("trailing_numeric_field_stats", []):
                lines.append(f"      attr[-{s['index_from_end']}]: min={s['min']} max={s['max']} "
                             f"distinct={s['distinct_values']} ratio_to_len_km={s.get('ratio_to_geometry_length_km')}")
        if d.get("sections"):
            lines.append(f"    sections: {[(s['kind'], s['rows']) for s in d['sections']]}")
        for w in d.get("warnings", []):
            lines.append(f"    WARNING: {w}")
    lines.append("")
    lines.append("Dataset groups:")
    for g, info in manifest["dataset_groups"].items():
        lines.append(f"  {'OK     ' if info['present'] else 'MISSING'} {g} ({len(info['entries'])} entries)")
    for w in manifest["warnings"]:
        lines.append(f"WARNING: {w}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", choices=dm.SOURCES, default="production")
    parser.add_argument("--out", type=Path, default=None, help="manifest path (default: data/generated/...)")
    args = parser.parse_args(argv)
    manifest = build_manifest(args.source)
    out = args.out or dm.manifest_path(args.source)
    dm.write_json(out, manifest)
    print(format_manifest(manifest))
    print(f"\nManifest written to {dm.display_path(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

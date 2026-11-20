"""Export and import crawled documents for transfer across an air gap."""

import argparse
import gzip
import hashlib
import json
from pathlib import Path

import blob_store
from usnat_url_converter import convert_url, normalize_target_origin


def export_documents(output_path: Path) -> int:
    count = 0
    with gzip.open(output_path, "wt", encoding="utf-8", newline="\n") as output:
        for document in blob_store.iter_documents():
            output.write(json.dumps(document, ensure_ascii=False, separators=(",", ":")))
            output.write("\n")
            count += 1
    return count


def transform_document(document: dict, target_origin: str | None) -> dict:
    transformed = dict(document)
    if not target_origin:
        return transformed

    for field in ("url", "canonical_url"):
        if transformed.get(field):
            transformed[field] = convert_url(transformed[field], target_origin)
    canonical = transformed.get("canonical_url") or transformed.get("url")
    if canonical:
        transformed["id"] = hashlib.sha256(canonical.encode()).hexdigest()
    return transformed


def import_documents(input_path: Path, target_origin: str | None = None) -> int:
    normalized_origin = normalize_target_origin(target_origin) if target_origin else None
    count = 0
    with gzip.open(input_path, "rt", encoding="utf-8") as source:
        for line_number, raw_line in enumerate(source, 1):
            if not raw_line.strip():
                continue
            try:
                document = json.loads(raw_line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{input_path}:{line_number}: invalid JSON") from error
            blob_store.save_json(transform_document(document, normalized_origin))
            count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("--output", type=Path, required=True)

    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("--input", type=Path, required=True)
    import_parser.add_argument("--target-origin")

    args = parser.parse_args()
    if args.command == "export":
        count = export_documents(args.output)
        print(f"Exported {count} documents to {args.output}")
    else:
        count = import_documents(args.input, args.target_origin)
        print(f"Imported {count} documents from {args.input}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .compiler import StandardPackageCompiler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compile and validate ESG standard packages.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    compile_parser = subparsers.add_parser("compile", help="Compile a source module package.")
    compile_parser.add_argument("--core", required=True, type=Path)
    compile_parser.add_argument("--module", required=True, type=Path)
    compile_parser.add_argument("--output", required=True, type=Path)

    check_parser = subparsers.add_parser("check", help="Validate a compiled package.")
    check_parser.add_argument("--package", required=True, type=Path)

    schemas_parser = subparsers.add_parser("schemas", help="Write JSON Schema documents.")
    schemas_parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    compiler = StandardPackageCompiler()

    if args.command == "compile":
        package = compiler.compile_to_path(
            core_path=args.core,
            module_dir=args.module,
            output_path=args.output,
        )
        print(
            json.dumps(
                {
                    "status": "compiled",
                    "package_id": package.manifest.package_id,
                    "package_version": package.manifest.package_version,
                    "metrics": len(package.metrics),
                    "elements": len(package.elements),
                    "concepts": len(package.concepts),
                    "source_digest": package.compilation.source_digest,
                    "output": str(args.output),
                },
                ensure_ascii=False,
            )
        )
        return 0

    if args.command == "check":
        package = compiler.validate_compiled(args.package)
        print(
            json.dumps(
                {
                    "status": "valid",
                    "package_id": package.manifest.package_id,
                    "package_version": package.manifest.package_version,
                    "source_digest": package.compilation.source_digest,
                },
                ensure_ascii=False,
            )
        )
        return 0

    compiler.write_schemas(args.output_dir)
    print(json.dumps({"status": "schemas_written", "output": str(args.output_dir)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

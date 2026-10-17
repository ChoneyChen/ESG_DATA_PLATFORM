"""Verify source migration receipts without opening databases or touching artifacts.

--finalize records canonical paths, before-image locations and post-migration
checksums. The default verifies that snapshot and reports rollback feasibility.
No mode performs a rollback or writes source/runtime files.
"""
from pathlib import Path
import argparse
import hashlib
import json


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(root, receipt_path, finalize=False):
    receipt = json.loads(receipt_path.read_text())
    errors, restored_before_images, current_count = [], 0, 0
    for entry in receipt['files']:
        old = root / entry['old_path']
        current = old.resolve()
        if not current.is_file():
            errors.append('Missing source: ' + entry['old_path'])
            continue
        current_sha = sha(current)
        if finalize:
            entry['canonical_path'] = str(current.relative_to(root))
            entry['after_sha256'] = current_sha
            if current_sha != entry['sha256']:
                candidates = [root / 'docs/migrations/2026-10-05-p6-originals' / entry['old_path']]
                if entry['old_path'].startswith('ExtractESG/standard_packages/'):
                    relative = entry['old_path'].removeprefix('ExtractESG/standard_packages/')
                    if relative == 'README.md':
                        candidates.append(root / 'ExtractESG/standards/history/docs/pre-p6-README.md')
                    if relative == 'catalogs/esrs/2023-set1/registry.json':
                        candidates.append(root / 'ExtractESG/standards/history/registry/pre-p6-registry.json')
                    if relative.startswith('scripts/'):
                        candidates.append(root / 'ExtractESG/standards/history/builders' / Path(relative).name)
                candidates.append(root / 'docs/migrations/2026-10-05-p6-originals' / current.relative_to(root))
                matching = next((p for p in candidates if p.is_file() and sha(p) == entry['sha256']), None)
                if matching is None:
                    errors.append('Missing exact before-image: ' + entry['old_path'])
                else:
                    entry['before_image'] = str(matching.relative_to(root))
        elif current_sha != entry.get('after_sha256', entry['sha256']):
            errors.append('Post-migration source drift: ' + entry['old_path'])
        if current_sha != entry['sha256']:
            original = root / entry.get('before_image', '__no_before_image__')
            if not original.is_file() or sha(original) != entry['sha256']:
                errors.append('Invalid rollback before-image: ' + entry['old_path'])
            else:
                restored_before_images += 1
        current_count += 1
    for link in receipt['compatibility_links']:
        path = root / link['path']
        if not path.is_symlink() or path.readlink() != Path(link['target']) or not path.exists():
            errors.append('Broken compatibility alias: ' + link['path'])
    for protected in receipt.get('runtime_artifacts', []):
        path = root / protected['path']
        if not path.is_file() or sha(path) != protected['sha256']:
            errors.append('Historical runtime artifact changed: ' + protected['path'])
    result = dict(batch=receipt['batch'], sources=current_count, compatibility_aliases=len(receipt['compatibility_links']),
                  exact_before_images=restored_before_images, historical_artifacts=len(receipt.get('runtime_artifacts', [])),
                  rollback_feasible=not errors, errors=errors)
    if finalize and not errors:
        receipt['state'] = 'verified'
        receipt['verification'] = result
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--finalize', action='store_true')
    args=parser.parse_args()
    receipts=[args.root/'docs/migrations/2026-10-05-p6-standards-database.json',
              args.root/'docs/migrations/2026-10-05-p6-python-domains.json']
    results=[verify(args.root,p,args.finalize) for p in receipts]
    print(json.dumps(results,ensure_ascii=False,indent=2))
    return int(any(r['errors'] for r in results))


if __name__=='__main__':
    raise SystemExit(main())

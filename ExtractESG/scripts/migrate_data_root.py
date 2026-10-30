#!/usr/bin/env python3
"""Explicit copy/verify migration. Never opens a publication database or token.

Frozen artifact bytes are copied unchanged; only control records' filesystem
locations move. Existing source assets are retained until an independent cleanup.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3

ROOT = Path(__file__).resolve().parents[2]


def fingerprint(path):
    value = sha256()
    with path.open('rb') as source:
        while chunk := source.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def plan(data_root):
    old_doc = ROOT / 'ExtractESG/document_ir_extract'
    old_control = ROOT.parent / '.local/platform-runtime'
    pairs = [
        (old_doc/'ocr_output', data_root/'documents/ocr', 'frozen'),
        (old_doc/'document_ir_output', data_root/'documents/ir', 'frozen'),
        (old_doc/'.local/jobs/ocr', data_root/'control/jobs/ocr', 'control'),
        (old_doc/'.local/jobs/document-ir', data_root/'control/jobs/ir', 'control'),
        (old_doc/'.local/uploads', data_root/'documents/uploads', 'frozen'),
        (old_doc/'.local/storage-cleanup', data_root/'control/cleanup', 'control'),
        (ROOT/'ExtractESG/extraction/.local/artifacts', data_root/'extractions', 'frozen'),
        (ROOT/'ExtractESG/extraction/.local/runs.sqlite3', data_root/'control/extractions.sqlite3', 'sqlite'),
        (old_control/'pipeline-queue.sqlite3', data_root/'control/queue.sqlite3', 'sqlite'),
        (old_control/'tasks', data_root/'control/tasks', 'control'),
        (ROOT.parent/'pdf/.report-resources.sqlite3', data_root/'reports/registry.sqlite3', 'sqlite'),
        (ROOT.parent/'pdf/.report-lifecycle.json', data_root/'reports/.report-lifecycle.json', 'control'),
    ]
    for source in sorted((ROOT.parent/'pdf').glob('*.pdf')):
        pairs.append((source,data_root/'reports/blobs'/(fingerprint(source)+'.pdf'),'frozen'))
    return [(source,destination,kind) for source,destination,kind in pairs if source.exists()]


def copy_one(source, destination):
    destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists():
        if fingerprint(source) != fingerprint(destination):
            raise ValueError('Destination already has different content: '+str(destination))
        return
    shutil.copy2(source,destination)
    if fingerprint(source) != fingerprint(destination):
        raise ValueError('Copied content differs: '+str(destination))


def relocate_value(value, mappings):
    if isinstance(value,dict):return {key:relocate_value(item,mappings) for key,item in value.items()}
    if isinstance(value,list):return [relocate_value(item,mappings) for item in value]
    if isinstance(value,str):
        for source,destination in mappings:
            if value == source or value.startswith(source+'/'):
                return destination+value[len(source):]
    return value


def apply(data_root, pairs):
    manifest = []
    mappings=sorted([(str(source),str(destination)) for source,destination,_ in pairs],key=lambda item:-len(item[0]))
    for source,destination,kind in pairs:
        if kind == 'sqlite':
            if destination.exists():
                raise ValueError('Control database already exists; do not overwrite: '+str(destination))
            destination.parent.mkdir(parents=True,exist_ok=True)
            with sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True) as old, sqlite3.connect(destination) as new:
                old.backup(new)
                tables=[row[0] for row in new.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                counts={table:new.execute('SELECT count(*) FROM "'+table+'"').fetchone()[0] for table in tables}
                old_counts={table:old.execute('SELECT count(*) FROM "'+table+'"').fetchone()[0] for table in tables}
                if counts != old_counts:raise ValueError('Control database copy counts differ')
            manifest.append({'kind':kind,'source':str(source),'destination':str(destination),'counts':counts})
            continue
        files=list(source.rglob('*')) if source.is_dir() else [source]
        copied=[]
        for file in files:
            if not file.is_file() or file.name=='.DS_Store':continue
            target=destination/file.relative_to(source) if source.is_dir() else destination
            copy_one(file,target)
            copied.append({'relative':file.relative_to(source).as_posix() if source.is_dir() else source.name,'sha256':fingerprint(file)})
            if kind=='control' and target.suffix=='.json':
                try:body=json.loads(target.read_text())
                except (ValueError,UnicodeDecodeError):continue
                relocated=relocate_value(body,mappings)
                if relocated!=body:target.write_text(json.dumps(relocated,ensure_ascii=False,indent=2)+'\n')
        manifest.append({'kind':kind,'source':str(source),'destination':str(destination),'files':copied})
    output=data_root/'control/data-migration.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps({'schema_version':'data-migration-1.0','entries':manifest,'frozen_content_unchanged':True},ensure_ascii=False,indent=2)+'\n')
    return {'copied_groups':len(manifest),'frozen_files':sum(len(row.get('files',[])) for row in manifest if row['kind']=='frozen'),'publication_databases_opened':0}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-root',type=Path,default=ROOT/'.local/data')
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();data_root=args.data_root.expanduser().resolve()
    pairs=plan(data_root)
    print(json.dumps(apply(data_root,pairs) if args.apply else {'mode':'dry-run','groups':len(pairs),'files':sum(sum(1 for p in source.rglob('*') if p.is_file()) if source.is_dir() else 1 for source,_,_ in pairs),'database_migration':False}))


if __name__=='__main__':main()

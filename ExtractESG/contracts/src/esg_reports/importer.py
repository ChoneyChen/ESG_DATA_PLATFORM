"""Import a validated folder: manifest.json plus pdf/<files>.pdf. No silent overwrite."""

import json, shutil, tempfile, os
from pathlib import Path
from .contract import (
    BatchManifest,
    ReportResource,
    ReportImportResult,
    ReportUploadResult,
)
from .repository import ReportRepository, file_digest


def import_upload(stream, filename, metadata, library, *, max_bytes=100 * 1024 * 1024):
    """A browser upload enters through exactly the same validated batch contract."""
    name = Path(str(filename).replace("\\", "/")).name
    if not name or Path(name).suffix.lower() != ".pdf":
        raise ValueError("Choose a PDF file")
    if not isinstance(metadata, dict):
        raise ValueError("Report metadata must be an object")
    reserved = {"file", "sha256", "asset_id"} & set(metadata)
    if reserved:
        raise ValueError("File identity is determined from the uploaded PDF")
    with tempfile.TemporaryDirectory(prefix="esg-report-upload-") as temporary:
        root = Path(temporary)
        (root / "pdf").mkdir()
        path = root / "pdf" / name
        size = 0
        with path.open("wb") as out:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("PDF exceeds the upload size limit")
                out.write(chunk)
        sha = file_digest(path)
        item = {
            **metadata,
            "pdf_id": metadata.get("pdf_id") or "pdf-" + sha[:24],
            "file": "pdf/" + name,
            "sha256": sha,
        }
        manifest = BatchManifest.model_validate({"reports": [item]})
        (root / "manifest.json").write_text(
            manifest.model_dump_json(), encoding="utf-8"
        )
        result = import_folder(root, library)
        resource = ReportRepository(library).get(item["pdf_id"])
        return ReportUploadResult(result=result, resource=resource).model_dump(
            mode="json"
        )


def prepare(folder):
    root = Path(folder).resolve()
    manifest = BatchManifest.model_validate_json(
        (root / "manifest.json").read_text(encoding="utf-8")
    )
    prepared = []
    for item in manifest.reports:
        relative = Path(item.file)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or len(relative.parts) != 2
            or relative.parts[0] != "pdf"
            or relative.suffix.lower() != ".pdf"
        ):
            raise ValueError("Batch files must be direct children of pdf/")
        path = root / relative
        if path.is_symlink() or (root / "pdf").is_symlink() or not path.is_file():
            raise ValueError("Missing or symbolic PDF source")
        if file_digest(path) != item.sha256:
            raise ValueError("PDF SHA-256 differs: " + item.file)
        with path.open("rb") as stream:
            if stream.read(5) != b"%PDF-":
                raise ValueError("Not a PDF: " + item.file)
        body = item.model_dump(exclude={"file"})
        resource = ReportResource(
            **body, asset_id="pdf-" + item.sha256[:24], original_filename=path.name
        )
        prepared.append((path, resource))
    return prepared


def import_folder(folder, library, *, preview=False):
    prepared = prepare(folder)
    repo = ReportRepository(library)
    results = []
    # Validate the entire batch before copying any PDF or registering any metadata.
    for path, resource in prepared:
        existing = repo.by_sha(resource.sha256)
        try:
            same_id = repo.get(resource.pdf_id)
        except KeyError:
            same_id = None
        if existing and existing.pdf_id != resource.pdf_id:
            raise ValueError("PDF content already registered under another number")
        if same_id and same_id.sha256 != resource.sha256:
            raise ValueError("PDF number already bound to different content")
        destination = repo.library / (resource.pdf_id + ".pdf")
        if destination.exists() and file_digest(destination) != resource.sha256:
            raise ValueError("Destination file differs")
        if existing and existing.model_dump(
            exclude={"revision", "original_filename"}
        ) != resource.model_dump(exclude={"revision", "original_filename"}):
            raise ValueError("Existing metadata differs; update it explicitly")
        results.append(
            {
                "pdf_id": resource.pdf_id,
                "asset_id": resource.asset_id,
                "status": "already_present" if existing else "ready",
            }
        )
    if preview:
        return ReportImportResult(preview=True, reports=results).model_dump(mode="json")
    created = []
    try:
        with repo.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for (path, resource), result in zip(prepared, results):
                destination = repo.library / (resource.pdf_id + ".pdf")
                if not destination.exists():
                    fd, temporary = tempfile.mkstemp(
                        prefix=".resource-import-", dir=repo.library
                    )
                    try:
                        with os.fdopen(fd, "wb") as out, path.open("rb") as source:
                            shutil.copyfileobj(source, out)
                            out.flush()
                            os.fsync(out.fileno())
                        if file_digest(temporary) != resource.sha256:
                            raise ValueError("Source changed during import")
                        os.link(temporary, destination)
                        created.append(destination)
                    finally:
                        Path(temporary).unlink(missing_ok=True)
                old = db.execute(
                    "SELECT body FROM resources WHERE pdf_id=? OR sha256=?",
                    (resource.pdf_id, resource.sha256),
                ).fetchone()
                if old:
                    existing = ReportResource.model_validate_json(old[0])
                    if existing.model_dump(
                        exclude={"revision", "original_filename"}
                    ) != resource.model_dump(exclude={"revision", "original_filename"}):
                        raise ValueError("Concurrent resource registration differs")
                else:
                    db.execute(
                        "INSERT INTO resources VALUES(?,?,?)",
                        (resource.pdf_id, resource.sha256, resource.model_dump_json()),
                    )
                result["status"] = (
                    "imported" if result["status"] == "ready" else result["status"]
                )
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return ReportImportResult(preview=False, reports=results).model_dump(mode="json")


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("folder")
    parser.add_argument("--library", required=True)
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            import_folder(args.folder, args.library, preview=args.preview),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

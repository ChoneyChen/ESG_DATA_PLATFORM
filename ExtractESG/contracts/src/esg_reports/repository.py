"""PDF resource metadata store. No clause, extraction engine or publication dependency."""
from pathlib import Path
import hashlib,json,sqlite3
from .contract import ReportResource

def file_digest(path):
    hash=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk:=stream.read(1024*1024):hash.update(chunk)
    return hash.hexdigest()

class ReportRepository:
    def __init__(self,library):
        self.library=Path(library).resolve();self.library.mkdir(parents=True,exist_ok=True)
        self.db_path=self.library/'.report-resources.sqlite3'
        with self.connect() as db:db.execute('CREATE TABLE IF NOT EXISTS resources (pdf_id TEXT PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL, body TEXT NOT NULL)')
    def connect(self):return sqlite3.connect(self.db_path,timeout=30)
    def by_sha(self,sha):
        with self.connect() as db:row=db.execute('SELECT body FROM resources WHERE sha256=?',(sha,)).fetchone()
        return ReportResource.model_validate_json(row[0]) if row else None
    def get(self,pdf_id):
        with self.connect() as db:row=db.execute('SELECT body FROM resources WHERE pdf_id=?',(pdf_id,)).fetchone()
        if not row:raise KeyError(pdf_id)
        return ReportResource.model_validate_json(row[0])
    def list(self,*,company=None,industry=None,publication_year=None,reporting_year=None):
        with self.connect() as db:rows=[ReportResource.model_validate_json(row[0]) for row in db.execute('SELECT body FROM resources ORDER BY pdf_id')]
        if company:rows=[r for r in rows if r.company and company in {r.company.name,r.company.company_id}]
        if industry:rows=[r for r in rows if r.industry and industry in {r.industry.name,r.industry.code}]
        if publication_year is not None:rows=[r for r in rows if r.publication_year==publication_year]
        if reporting_year is not None:rows=[r for r in rows if r.reporting_year==reporting_year]
        return rows
    def save(self,resource,*,expected_revision=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT body FROM resources WHERE pdf_id=? OR sha256=?',(resource.pdf_id,resource.sha256)).fetchone()
            if row:
                old=ReportResource.model_validate_json(row[0])
                if old.pdf_id!=resource.pdf_id or old.sha256!=resource.sha256:raise ValueError('PDF number/content already belongs to another resource')
                if expected_revision!=old.revision:raise ValueError('Resource metadata changed; reload before updating')
                resource=resource.model_copy(update={'revision':old.revision+1})
                db.execute('UPDATE resources SET body=? WHERE pdf_id=?',(resource.model_dump_json(),resource.pdf_id))
            else:
                if expected_revision is not None:raise ValueError('Resource does not exist')
                resource=resource.model_copy(update={'revision':1})
                db.execute('INSERT INTO resources VALUES(?,?,?)',(resource.pdf_id,resource.sha256,resource.model_dump_json()))
        return resource
    def ensure_source(self,sha,filename):
        existing=self.by_sha(sha)
        if existing:return existing
        resource=ReportResource(pdf_id='pdf-'+sha[:24],asset_id='pdf-'+sha[:24],sha256=sha,original_filename=filename)
        try:return self.save(resource)
        except ValueError:
            existing=self.by_sha(sha)
            if existing:return existing
            raise
    def ensure_file(self,path,*,filename=None):
        path=Path(path)
        with path.open('rb') as stream:
            if path.suffix.lower()!='.pdf' or stream.read(5)!=b'%PDF-':raise ValueError('Source is not a PDF')
        return self.ensure_source(file_digest(path),filename or path.name)

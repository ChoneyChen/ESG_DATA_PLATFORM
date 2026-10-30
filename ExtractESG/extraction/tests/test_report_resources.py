import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from esg_reports.contract import ReportResource,Company,Industry
from esg_reports.repository import ReportRepository,file_digest
from esg_reports.importer import import_folder
from test_backbone import documents,repository,quantitative
from esg_extraction.pipeline.binding import numeric_fact
from esg_extraction.pipeline.decisions import NumericMatch
from esg_contracts.evidence import SearchIntent

def folder(tmp_path,entries=2):
    from pypdf import PdfWriter
    root=tmp_path/'batch';(root/'pdf').mkdir(parents=True);reports=[]
    for index in range(entries):
        writer=PdfWriter();writer.add_blank_page(width=100+index,height=100);path=root/'pdf'/f'r{index}.pdf'
        with path.open('wb') as stream:writer.write(stream)
        reports.append({'pdf_id':f'PDF-{index}','file':f'pdf/r{index}.pdf','sha256':file_digest(path),
            'company':{'name':'企业A' if index==0 else '企业B','company_id':f'C{index}'},'publication_year':2026-index,
            'industry':{'name':'电池制造' if index==0 else '钢铁','code':f'I{index}'},'reporting_year':2025-index})
    (root/'manifest.json').write_text(json.dumps({'schema_version':'report-resource-batch-1.0','reports':reports},ensure_ascii=False))
    return root,reports

def test_folder_import_preview_repeat_and_filters(tmp_path):
    root,reports=folder(tmp_path);library=tmp_path/'library'
    result=import_folder(root,library,preview=True);assert result['preview'] and not list(library.glob('*.pdf'))
    assert len(import_folder(root,library)['reports'])==2
    repo=ReportRepository(library);assert len(repo.list())==2
    assert len(repo.list(company='C0',industry='I0',publication_year=2026,reporting_year=2025))==1
    assert not repo.list(company='C0',publication_year=2025)
    assert all(r['status']=='already_present' for r in import_folder(root,library)['reports'])

def test_invalid_batch_is_rejected_before_any_file_copy(tmp_path):
    root,reports=folder(tmp_path);(root/'pdf/r1.pdf').write_bytes(b'%PDF-corrupt-content')
    with pytest.raises(ValueError):import_folder(root,tmp_path/'library')
    assert not list((tmp_path/'library').glob('*.pdf'))

@pytest.mark.parametrize('source',['../other.pdf','/tmp/report.pdf','pdf/../../report.pdf','nested/report.pdf'])
def test_batch_requires_the_declared_pdf_subdirectory(tmp_path,source):
    root,reports=folder(tmp_path,1);reports[0]['file']=source
    (root/'manifest.json').write_text(json.dumps({'schema_version':'report-resource-batch-1.0','reports':reports}))
    with pytest.raises(ValueError):import_folder(root,tmp_path/'library')

def test_metadata_update_is_revision_checked_and_does_not_rebind_pdf(tmp_path):
    root,reports=folder(tmp_path,1);import_folder(root,tmp_path/'library');repo=ReportRepository(tmp_path/'library');old=repo.get('PDF-0')
    new=repo.save(old.model_copy(update={'industry':Industry(name='新能源')}),expected_revision=old.revision)
    assert new.revision==2 and new.sha256==old.sha256
    with pytest.raises(ValueError):repo.save(old,expected_revision=1)
    with pytest.raises(ValidationError):ReportResource(**{**old.model_dump(),'asset_id':'pdf-'+'0'*24})

def test_incomplete_raw_pdf_metadata_is_explicit_not_guessed(tmp_path):
    root,reports=folder(tmp_path,1);repo=ReportRepository(tmp_path/'library');r=repo.ensure_file(root/'pdf/r0.pdf')
    assert set(r.missing_metadata)=={'company','publication_year','industry'}
    assert r.pdf_id==r.asset_id
    assert not repo.list(company='Invented')


def test_upload_and_batch_accept_pdf_without_business_metadata(tmp_path):
    from esg_reports.importer import import_upload
    root,reports=folder(tmp_path,1)
    reports[0]={key:reports[0][key] for key in ('pdf_id','file','sha256')}
    (root/'manifest.json').write_text(json.dumps({'reports':reports}))
    batch_library=tmp_path/'batch-library'
    import_folder(root,batch_library)
    resource=ReportRepository(batch_library).get('PDF-0')
    assert set(resource.missing_metadata)=={'company','publication_year','industry'}
    assert resource.reporting_year is None
    with (root/'pdf/r0.pdf').open('rb') as source:
        result=import_upload(source,'original.pdf',{},tmp_path/'upload-library')
    assert result['resource']['company'] is None
    assert result['resource']['publication_year'] is None
    assert result['resource']['industry'] is None
    assert result['resource']['sha256']==reports[0]['sha256']

def test_report_identity_reaches_each_fact_without_becoming_measurement_identity(documents,repository):
    c=quantitative(repository);document=documents.document('ir-example')
    resource=ReportResource(pdf_id='PDF-2026-001',asset_id='pdf-'+'1'*24,sha256='1'*64,original_filename='report.pdf',company=Company(name='档案企业'),publication_year=2026,industry=Industry(name='电池制造'),reporting_year=2025)
    document=document.model_copy(update={'report_resource':resource})
    intent=SearchIntent(target_id=c.clause_key,contract_digest=c.clause_digest,data_kind='quantitative',positive_terms=['能源'])
    frame=documents.read('ir-example',documents.search('ir-example',intent)[0]);row=frame.rows[0];cell=next(v for v in row.cells if v.column_id=='c1')
    fact,_,_=numeric_fact(c,document,frame,NumericMatch(selection="measurement",row_id=row.row_id,columns=['c1'],reason='test'),row,cell,'i')
    assert fact.report_resource==resource
    assert fact.identity.reporting_entity.state=='not_read'
    assert fact.identity.reporting_period.value=='2025' # Publication year must not overwrite measurement period.

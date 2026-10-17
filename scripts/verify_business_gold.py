"""Read-only independent source check; never calls OCR, a model or database."""
from collections import defaultdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
from pypdf import PdfReader


def verify(root):
    root=Path(root).resolve()
    fixture=json.loads((root/'ExtractESG/tests/fixtures/business/cnpc-2025-esrs-major-gold.v1.json').read_text())
    pdf_path=root.parent/'pdf'/fixture['source']['pdf_filename']
    assert hashlib.sha256(pdf_path.read_bytes()).hexdigest()==fixture['source']['pdf_sha256']
    reader=PdfReader(pdf_path)
    assert len(reader.pages)==fixture['source']['pdf_page_count']
    compact=lambda value:re.sub(r'\s+','',value)
    texts={page:compact(reader.pages[page-1].extract_text()) for page in [2,90,91]}
    assert '报告主体中国石油天然气集团有限公司' in texts[2]
    assert '2025年1月1日至12月31日' in texts[2]
    assert '内容范畴公司全系统' in texts[2]
    assert fixture['source']['forbidden_subject_auto_replacement']!='中国石油天然气集团有限公司'
    rows=defaultdict(list)
    for fact in fixture['facts']:
        assert fact['evidence']['pdf_page_index']==fact['evidence']['pdf_page_number']-1
        assert fact['period_start']==fact['column_year']+'-01-01'
        assert fact['period_end']==fact['column_year']+'-12-31'
        assert isinstance(fact['raw_value'],str) and isinstance(fact['canonical_value'],str)
        assert Decimal(fact['raw_value'])*Decimal(fact['conversion_factor'])==Decimal(fact['canonical_value'])
        assert not set(fact['allowed_standard_datapoint_ids']) & set(fact['forbidden_standard_datapoint_ids'])
        assert fact['publication_state']=='not_published'
        rows[fact['source_concept']].append(fact)
    for concept,facts in rows.items():
        assert len(facts)==5
        assert [f['column_year'] for f in facts]==['2021','2022','2023','2024','2025']
        first=facts[0];page=first['evidence']['pdf_page_number']
        expected=compact(first['reported_label']+'（'+first['raw_unit']+'）'+''.join(f['raw_value'] for f in facts))
        assert expected in texts[page], (concept,expected)
        assert '业绩指标20212022202320242025' in texts[page]
        assert first['evidence']['printed_page_label'] in texts[page]
    major=json.loads((root/'ExtractESG/standards/profiles/esrs/2023-set1/platform-major.json').read_text())
    selected=[i for i in major['source_datapoint_ids'] if i.startswith(('E1','E2','E3'))]
    assert fixture['selection']['source_datapoint_ids']==selected and len(selected)==16
    assert len(major['source_datapoint_ids'])==49
    assert fixture['audit']['global_non_disclosure_claim'] is False
    historical=json.loads((root/'ExtractESG/document_ir_extract/document_ir_output'/fixture['source']['historical_ir_run_id']/'manifest.json').read_text())
    assert historical['source']['pdf_sha256']==fixture['source']['pdf_sha256']
    return dict(status='source_verified',independent_pdf_pages=[2,90,91],gold_source_cells=len(fixture['facts']),
                mapped_source_cells=sum(bool(f['allowed_standard_datapoint_ids']) for f in fixture['facts']),
                selected_original_ids=len(selected),overall_major_original_ids=len(major['source_datapoint_ids']),
                actual_model_quality_evaluated=False,publication_evaluated=False)


if __name__=='__main__':
    print(json.dumps(verify(Path(__file__).resolve().parents[1]),ensure_ascii=False,indent=2))

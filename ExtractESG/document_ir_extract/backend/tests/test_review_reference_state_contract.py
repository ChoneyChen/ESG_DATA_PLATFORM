"""Recorded real responses replayed against anonymous candidates; no live model/service."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from PIL import Image
import pytest

from esg_v2.document.artifact_loader import OcrPageArtifact, OcrRunArtifact
from esg_v2.document.contracts import (
    AgentModelCall, ArtifactRef, AtomicPatch, DocumentIR, DocumentIRMetadata, ReviewScopeItem,
    ReviewerPayload, SourceTrace, SpreadIR, VlmReviewTask,
)
from esg_v2.document.evidence_references import ReviewEvidenceReferences
from esg_v2.document.operation_registry import OperationRegistry
from esg_v2.document.paddleocr_converter import PaddleOcrDocumentConverter
from esg_v2.document.patch_guard import DocumentPatchApplier, PatchGuard
from esg_v2.document.review_context import ReviewContextCompiler
from esg_v2.document.review_plan_compiler import ReviewPlanCompiler
from esg_v2.document.review_response_adapter import ReviewResponseAdapter
from esg_v2.models.contracts import CloudChatResult
from esg_v2.models.local_nuextract_adapter import LocalNuExtractAdapter
from esg_v2.models.model_registry import ModelHealthRegistry
from test_document_ir_pipeline import _layout_page
from test_patch_guard import _table_document, _visual_document
from test_review_provider import _settings
from test_review_routing import AgentAdapter, _document, _orchestrator


FIXTURE = json.loads((Path(__file__).parent / 'fixtures/real_ir_review_responses_v4.json').read_text())
CONTEXT_FIXTURE = json.loads((Path(__file__).parent / 'fixtures/real_ir_review_contexts_v4.json').read_text())


def _recorded(task_id):
    row = next(c for c in FIXTURE['calls'] if c['task_id'] == task_id)
    assert hashlib.sha256(row['content'].encode()).hexdigest() == row['content_sha256']
    return {'choices': [{'finish_reason': 'stop', 'message': {'content': row['content']}}],
            'capability_receipt': {'schema_delivery': 'prompt_schema_and_server_validation',
                                   'token_level_schema_enforcement': False}}


class RecordedAdapter(AgentAdapter):
    def __init__(self, task_id):
        super().__init__('correction')
        self.task_id = task_id

    def chat_completions(self, request):
        if request.metadata['role'] == 'reviewer':
            return CloudChatResult(request_id=request.request_id, model_id=request.model_id,
                                   raw_response=_recorded(self.task_id), latency_ms=1)
        return super().chat_completions(request)


def _executor(tmp_path, task_id):
    ModelHealthRegistry.reset()
    orchestrator = _orchestrator(tmp_path, 'correction')
    adapter = RecordedAdapter(task_id)
    orchestrator.adapter = adapter
    orchestrator.registry.adapter = adapter
    return orchestrator


def _chart_document(tmp_path, task_id='review-000008'):
    document = _visual_document()
    number = 60 if task_id == 'review-000008' else 61
    figure_id = f'figure-p{number:04d}-0001' if number == 60 else 'figure-p0061-0002'
    image = tmp_path / 'page.png'
    Image.new('RGB', (100, 100), 'white').save(image)
    for obj in [*document.pages, *document.blocks, *document.figures]:
        obj.page_index = number - 1
    figure = document.figures[0]
    figure.figure_id = figure_id
    figure.crop_artifact_id = 'image-source'
    document.artifacts = [ArtifactRef(artifact_id='image-source', kind='page_image', path=str(image), page_index=number - 1),
                          ArtifactRef(artifact_id='text-source', kind='page_markdown', path='notes.md', page_index=number - 1)]
    document.pages[0].figure_ids = [figure_id]
    task = VlmReviewTask(task_id=task_id, task_type='figure_chart_review', target_type='figure',
                        target_id=figure_id, page_index=number - 1, blocking=True, prompt_intent='Anonymous contract replay',
                        input_refs=[str(image), 'unregistered-input.jpg', 'notes.md'],
                        scope=[ReviewScopeItem(target_type='figure', target_id=figure_id)])
    document.review_tasks = [task]
    ReviewPlanCompiler().compile(document, task)
    return document


def test_real_empty_chart_point_refs_survive_whole_record_guard_chain_as_context_only(tmp_path):
    document = _chart_document(tmp_path)
    task = document.review_tasks[0]
    executor = _executor(tmp_path, task.task_id)
    normalized = executor.response_adapter.normalize_reviewer(document,
        executor.response_adapter.message_json(_recorded(task.task_id)), task,
        alias_normalizer=executor.response_adapter.normalize_legacy_aliases)
    profile = executor.registry.candidates('reviewer')[0]
    result = CloudChatResult(request_id='anonymous-replay', model_id=profile.model_id,
                             raw_response=_recorded(task.task_id), latency_ms=1)
    _, patches = executor._record_reviewer(document, task, 1, ReviewerPayload.model_validate(normalized), result, profile)
    guard = PatchGuard().evaluate(document, task.task_id, patches,
        allowed_target_ids=set(task.review_plan.mutable_target_ids), allowed_operations=set(task.review_plan.allowed_operations))
    assert guard.passed
    chart_patch = next(p for p in patches if p.operation == 'upsert_chart_spec')
    assert chart_patch.proposed_value['visual_evidence_refs'] == [str(tmp_path / 'page.png')]
    assert 'notes.md' in chart_patch.evidence_refs  # Retained textual context is not image proof.
    assert all(not p['evidence_refs'] for s in chart_patch.proposed_value['series'] for p in s['points'])
    DocumentPatchApplier().apply(document, patches)
    ledger = document.figures[0].chart_spec.evidence_support
    assert ledger['quality'] == 'context_only'
    assert ledger['blocking_count'] == 0
    assert {p['status'] for p in ledger['points']} == {'source_context_only'}
    assert ledger['visual_pixel_values_verified'] is False
    assert 'agent_chart_spec_verified' not in document.figures[0].quality_flags


@pytest.mark.parametrize('explicit', ['notes.md', 'invented.png', 'unregistered-input.jpg', 'other-page.png'])
def test_explicit_invalid_visual_claim_is_not_erased_by_registered_fallback(tmp_path, explicit):
    document = _chart_document(tmp_path)
    document.artifacts.append(ArtifactRef(artifact_id='other-image', kind='page_image', path='other-page.png', page_index=9))
    task = document.review_tasks[0]
    payload = ReviewResponseAdapter.message_json(_recorded(task.task_id))
    raw_chart = next(p for p in payload['patches'] if p['operation'] == 'upsert_chart_spec')
    raw_chart['proposed_value']['visual_evidence_refs'] = [explicit]
    adapter = ReviewResponseAdapter()
    normalized = adapter.normalize_reviewer(document, payload, task, alias_normalizer=adapter.normalize_legacy_aliases)
    value = next(p for p in normalized['patches'] if p['operation'] == 'upsert_chart_spec')['proposed_value']
    assert explicit in value['visual_evidence_refs']
    patch = AtomicPatch(patch_id='patch-1', source_task_id=task.task_id, target_id=task.target_id,
                        target_type='figure', operation='upsert_chart_spec', proposed_value=value,
                        evidence_refs=[str(tmp_path / 'page.png')])
    guard = PatchGuard().evaluate(document, task.task_id, [patch])
    assert not guard.passed
    with pytest.raises(ValueError, match='unsupported'):
        DocumentPatchApplier().apply(document, [patch])


def test_general_object_refs_preserve_identity_and_text_is_not_visual_context():
    document = _visual_document()
    document.artifacts.append(ArtifactRef(artifact_id='text', kind='page_markdown', path='notes.md'))
    refs = ReviewEvidenceReferences(document)
    assert refs.tracked_general(['block-1', 'unknown-claim'], ['page.png', 'notes.md']) == ['block-1', 'unknown-claim', 'page.png', 'notes.md']
    assert refs.registered_visual(['block-1', 'unknown.jpg', 'notes.md', 'artifact-page']) == ['page.png']


def test_ambiguous_registered_alias_does_not_grant_visual_evidence():
    document = _visual_document()
    document.artifacts.extend([
        ArtifactRef(artifact_id='image-a', kind='page_image', path='one.png', remote_uri='shared-image'),
        ArtifactRef(artifact_id='image-b', kind='page_image', path='two.png', remote_uri='shared-image'),
        ArtifactRef(artifact_id='image-c', kind='page_image', path='three.png', remote_uri='shared-image'),
    ])
    resolver = ReviewEvidenceReferences(document)
    assert resolver.artifact('shared-image') is None
    assert resolver.registered_visual(['shared-image']) == []
    assert resolver.declared_visual(['shared-image']) == ['shared-image']


def test_real_spread_confirmation_is_materialized_despite_existing_link(tmp_path):
    document = _table_document()
    left = document.tables[0]
    left.table_id = 'table-p0022-0001'
    right = left.model_copy(deep=True, update={'table_id': 'table-p0023-0001', 'page_index': 1})
    document.tables.append(right)
    document.spreads.append(SpreadIR(spread_id='spread-p0022-p0023', page_ids=['page-1', 'page-2'],
        page_indices=[0, 1], composite_artifact_id='spread-image', source_trace=SourceTrace(parser='anonymous')))
    task = VlmReviewTask(task_id='review-000001', task_type='horizontal_spread_review', target_type='spread',
                        target_id='spread-p0022-p0023', page_index=0, prompt_intent='Anonymous semantic assembly replay',
                        scope=[ReviewScopeItem(target_type='spread', target_id='spread-p0022-p0023'),
                               ReviewScopeItem(target_type='table', target_id=left.table_id),
                               ReviewScopeItem(target_type='table', target_id=right.table_id)], input_refs=['page.png'])
    document.review_tasks = [task]
    ReviewPlanCompiler().compile(document, task)
    executor = _executor(tmp_path, task.task_id)
    normalized = executor.response_adapter.normalize_reviewer(document,
        executor.response_adapter.message_json(_recorded(task.task_id)), task,
        alias_normalizer=executor.response_adapter.normalize_legacy_aliases)
    profile = executor.registry.candidates('reviewer')[0]
    _, patches = executor._record_reviewer(document, task, 1, ReviewerPayload.model_validate(normalized),
        CloudChatResult(request_id='anonymous-spread', model_id=profile.model_id, raw_response=_recorded(task.task_id), latency_ms=1), profile)
    assert sum(p.operation == 'confirm_spread' for p in patches) == 1
    assert sum(p.operation == 'link_horizontal_continuation' for p in patches) == 1
    assert all(p.proposed_value is None for p in patches if p.operation == 'set_table_grid')
    guard = PatchGuard().evaluate(document, task.task_id, patches, required_any_operations={'confirm_spread', 'reject_spread'})
    assert next(c for c in guard.checks if c.code == 'required_classification_operation').passed
    assert not guard.passed  # Real null grids still fail; no grid is invented.
    assert any(not c.passed and c.code.endswith('operation_payload_schema') for c in guard.checks)
    assert {left.table_id, right.table_id} <= set(task.review_plan.mutable_target_ids)


def test_single_round_real_invalid_payload_has_separate_validation_and_linked_defer(tmp_path):
    document = _chart_document(tmp_path, 'review-000009')
    result = _executor(tmp_path, 'review-000009').execute(document, max_auto_review_rounds=1)
    call = result.model_calls[0]
    assert call.status == 'succeeded'
    assert call.envelope_validation == 'passed'
    assert call.operation_payload_validation == 'failed'
    assert call.operation_payload_issues[0]['operation'] == 'set_visual_type'
    assert call.capability_receipt.schema_delivery == 'prompt_schema_and_server_validation'
    assert call.capability_receipt.token_level_schema_enforcement is False
    task = result.review_tasks[0]
    assert task.attempt_count == 1 and task.failure_class != 'repeated_failure'
    assert task.result['reason'] == 'automation_proposal_invalid'
    assert task.result['run_review_rounds'] == 1 and task.result['repair_budget_exhausted'] is True
    decision = result.final_decisions[-1]
    assert decision.reviewer_result_ids == task.reviewer_result_ids
    assert decision.guard_result_ids == task.guard_result_ids
    assert decision.verifier_result_ids == []
    assert not any('operation_payload_safe' in c.code for g in result.guard_results for c in g.checks)
    assert result.figures[0].chart_spec is None


def test_real_noop_is_not_fake_repair_or_automatic_confirmation(tmp_path):
    document = _document(tmp_path)
    payload = ReviewResponseAdapter.message_json(_recorded('review-000010'))
    original = payload['patches'][0]['proposed_value']
    block = document.blocks[0]
    block.block_id = 'block-p0080-0007'
    block.text = original
    page = document.pages[0]
    page.page_id = 'page-0080'
    page.text = original
    page.block_ids = [block.block_id]
    document.layout_objects[0].block_id = block.block_id
    document.artifacts = [ArtifactRef(artifact_id='anonymous-page-image', kind='page_image', path=page.page_image_path, page_index=0)]
    task = VlmReviewTask(task_id='review-000010', task_type='ocr_native_conflict_review', target_type='page',
        target_id=page.page_id, page_index=0, prompt_intent='Anonymous existing-text replay',
        input_refs=[page.page_image_path], reason_codes=['native_ocr_text_length_divergence'],
        scope=[ReviewScopeItem(target_type='page', target_id=page.page_id)])
    document.review_tasks = [task]
    ReviewPlanCompiler().compile(document, task)
    result = _executor(tmp_path, task.task_id).execute(document, max_auto_review_rounds=1)
    assert result.blocks[0].text == original
    assert result.atomic_patches[0].effect == 'no_change'
    assert result.review_tasks[0].failure_class == 'no_material_change'
    assert result.review_tasks[0].result['reason'] == 'no_material_change_requires_review'
    assert result.final_decisions[-1].outcome == 'deferred'
    assert not result.verifier_results and not any(p.status == 'accepted' for p in result.atomic_patches)


def test_writable_text_is_complete_when_bounded_and_previews_mark_truncation(tmp_path):
    document = _document(tmp_path)
    task = document.review_tasks[0]
    task.task_type = 'ocr_native_conflict_review'
    task.reason_codes = ['native_ocr_text_length_divergence']
    task.scope = [ReviewScopeItem(target_type='page', target_id=document.pages[0].page_id)]
    document.blocks[0].text = 'x' * 4000
    document.pages[0].text = 'x' * 6000
    task.review_plan = None
    ReviewPlanCompiler().compile(document, task)
    candidate = ReviewContextCompiler().candidate(document, task)
    block = next(b for b in candidate['targets'] if b.get('block_id') == 'block-1')
    assert block['text_length'] == 4000 and block['text_complete'] is False and len(block['text']) == 3000
    page = candidate['pages'][0]
    assert page['text_length'] == 6000 and page['text_complete'] is False and len(page['text']) == 5000
    assert page['block_inventory'][0]['text_complete'] is False
    document.blocks[0].text = '<table>existing complete source rows</table>'
    candidate = ReviewContextCompiler().candidate(document, task)
    block = next(b for b in candidate['targets'] if b.get('block_id') == 'block-1')
    assert block['text_complete'] is True and block['text'] == document.blocks[0].text


def test_old_call_ledgers_remain_unknown_without_fabricated_new_validation():
    call = AgentModelCall(call_id='legacy', task_id='task', role='reviewer', round_index=1,
                         model_id='legacy', model_family='legacy', status='succeeded')
    assert call.envelope_validation == 'not_evaluated'
    assert call.operation_payload_validation == 'not_evaluated'
    assert call.capability_receipt.schema_delivery == 'unknown'
    assert call.capability_receipt.token_level_schema_enforcement is None


@pytest.mark.parametrize('delivery', [{}, [], 1])
def test_malformed_worker_capability_receipt_stays_unknown_without_coercion(delivery):
    from esg_v2.document.model_runner import ModelRunner
    result = CloudChatResult(request_id='anonymous-capability', model_id='anonymous', latency_ms=1,
        raw_response={'capability_receipt': {'schema_delivery': delivery, 'token_level_schema_enforcement': 'false'}})
    receipt = ModelRunner._capability_receipt(result)
    assert receipt.schema_delivery == 'unknown'
    assert receipt.token_level_schema_enforcement is None


def test_worker_capability_receipt_is_retained_without_coercing_enforcement(tmp_path):
    adapter = LocalNuExtractAdapter(_settings(tmp_path))
    class Worker:
        def request(self, payload, *, timeout):
            return {'ok': True, 'text': '{}', 'schema_delivery': 'prompt_schema_and_server_validation',
                    'token_level_schema_enforcement': False}
    adapter.worker = Worker()
    from esg_v2.models.contracts import CloudChatRequest
    result = adapter.chat_completions(CloudChatRequest(request_id='anonymous', model_id='anonymous', messages=[]))
    assert result.raw_response['capability_receipt'] == {
        'schema_delivery': 'prompt_schema_and_server_validation', 'token_level_schema_enforcement': False}


def test_converter_registers_only_real_package_images_with_identity_and_sha(tmp_path):
    root = tmp_path / 'ocr-package'
    root.mkdir()
    valid = root / 'source.jpg'
    Image.new('RGB', (30, 20), 'white').save(valid)
    invalid = root / 'not-an-image.jpg'
    invalid.write_text('not image bytes')
    outside = tmp_path / 'outside.jpg'
    Image.new('RGB', (10, 10), 'white').save(outside)
    raw = root / 'result.jsonl'
    raw.write_text('{}')
    page = OcrPageArtifact(page_index=0, raw_line_number=1, raw_result_index=0, raw_result=_layout_page(0),
        markdown_path=None, markdown_text='', markdown_images={'source': str(valid)}, output_images={},
        local_markdown_images={'source': valid, 'invalid': invalid, 'outside': outside}, local_output_images={})
    artifact = OcrRunArtifact(run_id='ocr-anonymous', root_dir=root, manifest_path=root / 'manifest.json',
                             manifest={}, raw_jsonl_path=raw, pages=[page])
    document = PaddleOcrDocumentConverter().convert(artifact, run_id='ir-anonymous')
    images = [a for a in document.artifacts if a.kind == 'ocr_image']
    assert len(images) == 1 and images[0].path == str(valid)
    assert images[0].remote_uri == 'ocr-package://ocr-anonymous/source.jpg'
    assert images[0].sha256 == hashlib.sha256(valid.read_bytes()).hexdigest()
    assert (images[0].width_pixels, images[0].height_pixels) == (30, 20)
    assert ReviewEvidenceReferences(document).registered_visual([images[0].remote_uri]) == [str(valid)]


@pytest.mark.parametrize('task_id', [c['task_id'] for c in FIXTURE['calls']])
def test_all_eight_real_responses_preserve_safe_gate_outcomes_in_declaration_replay(tmp_path, task_id):
    """Includes recorded geometry, not a synthetic ideal chart; no pixel-proof claim."""
    assert CONTEXT_FIXTURE['source_snapshot_sha256'] == FIXTURE['source_snapshot_sha256']
    document = DocumentIR.model_validate(deepcopy(CONTEXT_FIXTURE['document']))
    task = next(t for t in document.review_tasks if t.task_id == task_id)
    ReviewPlanCompiler().compile(document, task)
    executor = _executor(tmp_path, task_id)
    normalized = executor.response_adapter.normalize_reviewer(document,
        executor.response_adapter.message_json(_recorded(task_id)), task,
        alias_normalizer=executor.response_adapter.normalize_legacy_aliases)
    profile = executor.registry.candidates('reviewer')[0]
    _, patches = executor._record_reviewer(document, task, 1, ReviewerPayload.model_validate(normalized),
        CloudChatResult(request_id='recorded-declaration-only', model_id=profile.model_id,
                        raw_response=_recorded(task_id), latency_ms=1), profile)
    guard = PatchGuard().evaluate(document, task_id, patches,
        allowed_target_ids=set(task.review_plan.mutable_target_ids),
        allowed_operations=set(task.review_plan.allowed_operations),
        required_any_operations={'confirm_spread', 'reject_spread'} if task.target_type == 'spread' else None)
    failed = {c.code.split(':', 1)[-1] for c in guard.checks if not c.passed}
    expected = {'review-000003': 'visual_text_not_duplicate', 'review-000001': 'operation_payload_schema',
                'review-000004': 'figure_binding_geometry', 'review-000005': 'referenced_write_scope',
                'review-000007': 'visual_text_not_duplicate', 'review-000009': 'operation_payload_schema',
                'review-000010': 'ocr_correction_is_change'}
    if task_id == 'review-000008':
        assert guard.passed
    else:
        assert not guard.passed and expected[task_id] in failed
    assert 'required_classification_operation' not in failed
    assert 'operation_payload_safe' not in failed
    for check in guard.checks:
        if check.code.endswith('chart_point_references'):
            assert check.passed
            assert check.details['quality'] == 'context_only'
            assert check.details['visual_pixel_values_verified'] is False
            assert {p['status'] for p in check.details['points']} == {'source_context_only'}
    # This direct guard replay never applies a transaction or creates a chart.
    assert not any(f.chart_spec is not None for f in document.figures)

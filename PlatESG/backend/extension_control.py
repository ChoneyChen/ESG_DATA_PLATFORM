"""Authenticated definition decisions and recoverable runtime registry projection.

The database is the approval authority. The local registry is a read projection
for extraction, and every selected task freezes its own immutable release.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from esg_standard_packages.extensions import (
    ExtensionProposal, ExtensionRegistry, ReviewAuthority, compile_extension,
    decide_proposal, definition_digest, register_extension, validate_parent,
    validate_release, write_extension_release, load_registered_extension,
)
from esg_standard_packages.io import pretty_json_bytes
from esg_targeted.standards.catalog import StandardPackageCatalog


class ExtensionControl:
    def __init__(self, bridge, *, standards_root: Path, runtime_root: Path):
        self.bridge = bridge
        self.standards_root = standards_root.resolve()
        self.runtime_root = runtime_root.resolve()
        self.catalog = StandardPackageCatalog(self.standards_root / 'releases')

    def parent(self, definition):
        return self.catalog.load(definition.parent_package_id, definition.parent_package_version)

    def validate_bundle(self, bundle_path: str, allowed_roots: list[Path]):
        root=Path(bundle_path).resolve()
        if not any(root.is_relative_to(path.resolve()) for path in allowed_roots):
            raise ValueError('Bundle is outside configured import roots')
        snapshot_path=root/'input/runtime-standard-snapshot.json'
        request=json.loads((root/'input/request.json').read_text())
        internal_path=root/'results/internal-dependencies.json'
        internal=json.loads(internal_path.read_text()) if internal_path.exists() else None
        if not snapshot_path.exists():
            if request.get('extension_selections'):
                raise ValueError('Extension import requires a frozen runtime snapshot')
            if not internal or internal.get('schema_version')!='internal-dependency-facts-v2':
                return
        from esg_targeted.standards.runtime_snapshot import validate_runtime_snapshot,RuntimeStandardView,validate_internal_dependency_scope
        from esg_standard_packages.extensions import ExtensionSelection
        parent=self.catalog.load(request['package_id'],request['package_version'])
        frozen_parent=json.loads((root/'input/standard-package.json').read_text())
        if frozen_parent!=parent.model_dump(mode='json'):
            raise ValueError('Frozen base standard differs from its registered release')
        snapshot=(validate_runtime_snapshot(json.loads(snapshot_path.read_text()),parent,
            expected_selections=[ExtensionSelection.model_validate(s) for s in request.get('extension_selections',[])])
            if snapshot_path.exists() else None)
        if snapshot and request.get('runtime_standard_digest')!=snapshot.runtime_digest:
            raise ValueError('Import request differs from frozen runtime identity')
        view=RuntimeStandardView(parent,snapshot) if snapshot else parent
        exported=json.loads((root/'exports/extraction-result.json').read_text())
        if snapshot and (exported.get('runtime_standard_snapshot')!=snapshot.model_dump(mode='json') or exported.get('execution_metric_catalog')!=[m.model_dump(mode='json') for m in view.metrics]):
            raise ValueError('Exported extension catalog differs from its compiled execution contract')
        if snapshot and exported.get('extension_computations')!=view.extension_computations:
            raise ValueError('Exported calculation recipes differ from explicit extension choices')
        if internal and internal.get('schema_version')=='internal-dependency-facts-v2':
            if exported.get('internal_dependency_scope')!=internal:
                raise ValueError('Exported internal operand scope differs from preserved artifact')
            validated=validate_internal_dependency_scope(internal,view)
            if set(validated.selected_output_metric_ids)!=set(request.get('metric_ids',[])):
                raise ValueError('Internal operand scope differs from explicit selected outputs')
        if not snapshot:
            return
        stored=self.bridge.call('extension_registry',{})
        authority=[row.get('release',row) for row in stored.get('releases',[])]
        if any(release.model_dump(mode='json') not in authority for release in snapshot.extension_releases):
            raise ValueError('Extension Bundle has no authoritative registered release')

    def catalog_view(self):
        stored = self.bridge.call('extension_registry', {})
        drafts, issues = [], []
        for path in sorted((self.standards_root / 'extensions/authoring').rglob('proposal.json')):
            try:
                proposal = ExtensionProposal.model_validate_json(path.read_bytes())
                validate_parent(proposal.definition, self.parent(proposal.definition))
                drafts.append({**proposal.model_dump(mode='json'), 'definition_digest': definition_digest(proposal.definition), 'state':'authoring_draft'})
            except (ValueError, OSError) as exc:
                issues.append({'code':'draft_invalid','message':str(exc)})
        return {**stored, 'authoring_drafts': drafts, 'diagnostics': issues,
                'authority':'database', 'runtime_projection': self.projection_status(stored.get('releases', []))}

    def propose(self, proposal: ExtensionProposal, principal):
        validate_parent(proposal.definition, self.parent(proposal.definition))
        # Human submissions are owned by their authenticated actor. A submitted
        # model identity is provenance only and grants no approval capability.
        if proposal.proposer_kind == 'human' and proposal.proposer_ref != principal['operator_ref']:
            raise ValueError('Human proposal identity must match the authenticated actor')
        return self.bridge.call('extension_propose', {'proposal':proposal.model_dump(mode='json'),
            'submitted_by':principal['operator_ref']})

    def decide(self, proposal_id: str, *, decision: str, reason: str, principal):
        stored = self.bridge.call('extension_registry', {})
        row = next((row for row in stored.get('proposals', []) if self._proposal(row).get('proposal_id') == proposal_id), None)
        if row is None:
            raise ValueError('Extension proposal does not exist')
        proposal = ExtensionProposal.model_validate(self._proposal(row))
        parent = self.parent(proposal.definition)
        validate_parent(proposal.definition, parent)
        authority = ReviewAuthority(actor_ref=principal['operator_ref'],actor_kind='human',
            verified_by='server_authentication',capabilities=['approve_extension_definition'])
        approval = decide_proposal(proposal, authority=authority, decision=decision, reason=reason)
        saved = self.bridge.call('extension_decide', {'receipt':approval.model_dump(mode='json'),
            'authority_roles':principal['roles']})
        return {**saved, 'approval':approval.model_dump(mode='json'), 'definition_state':decision,
                'registration_state':'not_registered'}

    def register(self, proposal_id: str, approval_id: str):
        stored = self.bridge.call('extension_registry', {})
        p = next((self._proposal(row) for row in stored.get('proposals', []) if self._proposal(row).get('proposal_id') == proposal_id), None)
        a = next((row.get('receipt', row) for row in stored.get('decisions', []) if row.get('approval_id', row.get('receipt', {}).get('approval_id')) == approval_id), None)
        if p is None or a is None:
            raise ValueError('The authoritative proposal and decision must exist')
        from esg_standard_packages.extensions import ExtensionApproval
        proposal = ExtensionProposal.model_validate(p)
        parent = self.parent(proposal.definition)
        release = compile_extension(proposal, parent=parent, approval=ExtensionApproval.model_validate(a))
        saved = self.bridge.call('extension_register', {'release':release.model_dump(mode='json')})
        try:
            projection = self.project(release)
        except (ValueError, OSError) as exc:
            projection = {'registration_state':'registered','runtime_projection_state':'sync_pending',
                'reason':str(exc)}
        return {**saved, **projection, 'release':release.model_dump(mode='json')}

    @staticmethod
    def _proposal(row):
        return row.get('proposal', row)

    def projection_status(self, releases):
        try:
            registry = ExtensionRegistry.model_validate_json((self.runtime_root / 'registry.json').read_bytes())
            known = set()
            authority = {row.get('release',row)['release_digest']:row.get('release',row) for row in releases}
            for registration in registry.registrations:
                expected = authority.get(registration.release_digest)
                if expected is None:
                    continue
                parent = self.catalog.load(registration.parent_package_id,registration.parent_package_version)
                release = load_registered_extension(self.runtime_root,registration,parent=parent)
                if release.model_dump(mode='json') == expected:
                    known.add((registration.extension_id,registration.definition_version,registration.release_digest))
        except (OSError, ValueError):
            known = set()
        pending = []
        for row in releases:
            release = row.get('release', row)
            d = release['definition']
            if (d['extension_id'],d['definition_version'],release['release_digest']) not in known:
                pending.append(d['extension_id'])
        return {'state':'sync_pending' if pending else 'ready','pending_extension_ids':pending}

    def sync(self):
        stored = self.bridge.call('extension_registry', {})
        return {'items':[self.project(row.get('release',row)) for row in stored.get('releases', [])]}

    def project(self, raw):
        from esg_standard_packages.extensions import CompiledExtension
        release = raw if isinstance(raw, CompiledExtension) else CompiledExtension.model_validate(raw)
        parent = self.parent(release.definition)
        validate_release(release,parent=parent)
        # Only a DB-resolved authoritative release can reach this projection.
        stored = self.bridge.call('extension_registry', {})
        authority = [row.get('release',row) for row in stored.get('releases', [])]
        if not any(row == release.model_dump(mode='json') for row in authority):
            raise ValueError('Runtime projection requires an authoritative registered release')
        self.runtime_root.mkdir(parents=True,exist_ok=True)
        with (self.runtime_root / '.projection.lock').open('a+b') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            registry_path = self.runtime_root / 'registry.json'
            registry = ExtensionRegistry.model_validate_json(registry_path.read_bytes()) if registry_path.exists() else ExtensionRegistry(schema_version='project-extension-registry-1.0')
            path = f'releases/{release.definition.extension_id}/{release.definition.definition_version}/release.json'
            registry = register_extension(registry,release,path=path,parent=parent)
            registration = next(r for r in registry.registrations if r.extension_id == release.definition.extension_id and r.definition_version == release.definition.definition_version)
            write_extension_release(self.runtime_root,registration,release,parent=parent)
            with NamedTemporaryFile(dir=self.runtime_root,prefix='.registry-',suffix='.json',delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(pretty_json_bytes(registry.model_dump(mode='json')))
                handle.flush(); os.fsync(handle.fileno())
            try:
                os.replace(temporary,registry_path)
            finally:
                temporary.unlink(missing_ok=True)
        return {'extension_id':release.definition.extension_id,'definition_version':release.definition.definition_version,
                'release_digest':release.release_digest,'registration_state':'registered','runtime_projection_state':'ready'}

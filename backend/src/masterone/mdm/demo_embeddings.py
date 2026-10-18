"""Reuse real embeddings only for the immutable, server-supplied demo dataset.

Decisions, groups, explanations and exports are always computed in the new owner scope.
The normal embedding adapter validates every reused vector as usual.
"""
import gzip
import json
import logging
import os
from pathlib import Path
from uuid import uuid4

from masterone.config import get_settings
from masterone.mdm.demo_dataset import VERSION
from masterone.mdm.generation_staging import digest

log = logging.getLogger(__name__)


def cache_path(profile_digest):
    root = Path(get_settings().workflow_artifact_dir) / 'demo-embeddings'
    return root / (digest([VERSION, profile_digest]) + '.json.gz')


def context(db, operation_id):
    from masterone.auth.models import DemoGrant
    from masterone.mdm.workflow_models import WorkflowOperation
    from masterone.mdm.matching_foundation_service import verify_operation_binding
    from masterone.mdm.matching_foundation_models import MatchingProfile
    from masterone.mdm.models import ResolutionRun
    op = db.get(WorkflowOperation, operation_id)
    grant = db.query(DemoGrant).filter_by(run_id=op.run_id).first() if op else None
    if not grant or grant.dataset_version != VERSION or op.operation_type != 'DECIDE': return None
    binding = verify_operation_binding(db, db.get(ResolutionRun, op.run_id), op)
    if not binding: return None
    profile = db.get(MatchingProfile, binding.profile_id)
    config = profile.config_json
    if config.get('preprocessing', {}).get('cache_version') != 'text_vectors_v2': return None
    return op, grant, binding, config


def identities(records, scope, config):
    from masterone.mdm.matching_agents.embedding_agent import _digest
    from masterone.mdm.matching_agents.contracts import eligible_text
    encoder, prep, runtime = config['encoder_identity']['bundle_digest'], digest(config['preprocessing']), digest(config['encoder_identity'])
    for row in records:
        normalized = row['normalized_fields']
        for field, column in [('name','name_unicode'),('address','address')]:
            quality = normalized.get('_field_quality', {}).get(field, 'MISSING')
            text, unavailable = eligible_text(normalized.get(column), quality)
            if unavailable: continue
            identity = _digest(['text_vectors_v2', scope, field, quality, _digest(text),
                               encoder, prep, runtime, config['encoder_identity']['dimension']])
            yield field, quality, text, identity


def seed(factory, operation_id, records):
    try:
        with factory() as db:
            found = context(db, operation_id)
            if not found: return 0
            op, grant, binding, config = found
            profile_digest, run_id, owner = binding.profile_digest, op.run_id, grant.user_id
        path = cache_path(profile_digest)
        if not path.is_file(): return 0
        with gzip.open(path, 'rb') as stream:
            raw = stream.read(256 * 1024**2 + 1)
        if len(raw) > 256 * 1024**2: return 0
        saved = json.loads(raw)
        if saved['version'] != VERSION or saved['profile_digest'] != profile_digest: return 0
        vectors = saved['vectors']
        if len(vectors) > 5000: return 0
        old = {(field, quality, text): key for field, quality, text, key in identities(records, saved['scope'], config)}
        target = {key: vectors[old[(field,quality,text)]] for field,quality,text,key in identities(records,(owner,run_id),config)
                  if old[(field,quality,text)] in vectors}
        if not target: return 0
        from masterone.mdm.matching_agents.workflow_agents import seal_agent
        seal_agent(factory, run_id, operation_id, 'AGENT_VECTOR_CACHE', {
            'input_hash': digest(records), 'profile_digest': profile_digest, 'vectors': target,
            'origin': 'REAL_DEMO_EMBEDDING_CACHE', 'dataset_version': VERSION})
        return len(target)
    except Exception:
        # Caching is optional; the real provider path remains authoritative on any miss/error.
        log.warning('DEMO_EMBEDDING_CACHE_MISS')
        return 0


def warm(factory, operation_id):
    try:
        from masterone.mdm.workflow_models import WorkflowArtifact
        from masterone.mdm.workflow_artifacts import load
        with factory() as db:
            found = context(db, operation_id)
            if not found: return
            op, grant, binding, config = found
            if op.status != 'COMPLETED': return
            path = cache_path(binding.profile_digest)
            if path.is_file(): return
            vectors = {}
            for row in db.query(WorkflowArtifact).filter_by(run_id=op.run_id, phase='AGENT_VECTOR_CACHE'):
                value = load(db, row.id, op.run_id, 'AGENT_VECTOR_CACHE')
                if value.get('profile_digest') == binding.profile_digest: vectors.update(value['vectors'])
            if not vectors or len(vectors) > 5000: return
            value = {'version': VERSION, 'profile_digest': binding.profile_digest,
                     'scope': [grant.user_id, op.run_id], 'vectors': vectors}
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.' + uuid4().hex + '.tmp')
        try:
            with gzip.open(temporary, 'wt', encoding='utf-8', compresslevel=1) as stream:
                json.dump(value, stream, ensure_ascii=False, allow_nan=False, separators=(',',':'))
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    except Exception:
        log.warning('DEMO_EMBEDDING_CACHE_WARM_FAILED')

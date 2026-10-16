"""Approved B/T10/R1/W1 immutable integration config; diagnostic, not calibrated."""
from copy import deepcopy

from masterone.config import get_settings
from masterone.mdm.generation_staging import digest

from .embedding_runtime import advanced_config


def integration_config(base: dict, experiment: str = 'E3', fallback: str = 'fail_operation', *, global_lexical=False, person_recovery=False) -> dict:
    if experiment not in ('E0', 'E1', 'E2', 'E3'):
        raise ValueError('UNAPPROVED_EXPERIMENT')
    if person_recovery and experiment == 'E2':
        raise ValueError('PERSON_RECOVERY_REQUIRES_RULE_CANDIDATES')
    config = advanced_config(base, fallback)
    settings = get_settings()
    config.update(kind='RULE_HYBRID_DETERMINISTIC', config_schema_version='matching_profile_hybrid_v1',
        experiment=experiment, validation_scope='TC1_SYNTHETIC_DIAGNOSTIC_NO_QUALITY_CERTIFICATE',
        retrieval={'version': 't10_v1', 'tfidf': {'top_k': 10, 'min_similarity': .30},
                   'embedding': {'top_k': 10, 'min_similarity': .85},
                   'query_batch_size': 64, 'target_batch_size': 256},
        coordinator={'version': 'r1_v1', 'high': .85, 'low': .50},
        execution_budget={'unique_pairs': settings.advanced_matching_candidate_cap,
                          'warning_pairs': min(150000, settings.advanced_matching_candidate_cap),
                          'similarity_cells': 100000000, 'workspace_bytes': 512 * 1024**2,
                          'disk_bytes': 12 * 1024**3, 'run_seconds': 1800,
                          'rule_workers': settings.advanced_matching_rule_workers},
        candidate_contract='candidate_union_v1', normalization_identity=deepcopy(base['runtime_identity']))
    config['feature_schema_identity'] = digest({'version': 'matching_hybrid_v1',
                                               'evidence': config['feature_schema_identity']})
    if config['encoder_identity'].get('provider') == 'openrouter':
        config['config_schema_version'] = 'matching_profile_openrouter_hybrid_v1'
        config['validation_scope'] = 'OPENROUTER_MIGRATION_UNCALIBRATED_NO_QUALITY_CERTIFICATE'
        config['retrieval']['version'] = 'openrouter_t10_v1_uncalibrated'
        config['embedding_threshold_validation'] = 'UNCALIBRATED_REQUIRES_INDEPENDENT_CALIBRATION_AND_TEST'
    if global_lexical:
        config['retrieval']['global_lexical'] = True
        config['retrieval']['version'] += '_global_lexical_v1'
    if person_recovery:
        config['retrieval']['person_recovery'] = True
        config['retrieval']['version'] += '_person_name_dob_v1'
    return config

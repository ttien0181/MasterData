"""Complementary PERSON review blocks; keys never authorize a merge."""
from itertools import combinations

from masterone.mdm.pipeline.standardization import fold_text, parse_dob

PERSON_RECOVERY_SOURCES = ('person_token_full_dob', 'person_tokens_month_day')
PERSON_RECOVERY_BLOCK_SIZE = 30
MAX_NAME_TOKENS = 12


def person_recovery_keys(record):
    fields = record['normalized_fields']
    quality = fields.get('_field_quality', {})
    if (record.get('entity_type', 'PERSON') != 'PERSON'
            or quality.get('name') != 'VALID' or quality.get('dob') != 'VALID'):
        return
    dob = parse_dob(fields.get('dob') or '')
    name = fields.get('name') or ''
    tokens = sorted({t for t in fold_text(name).split() if len(t) >= 2 and t.isalpha()})
    if (dob['quality'] != 'VALID' or not dob['month'] or not dob['day']
            or not 2 <= len(tokens) <= MAX_NAME_TOKENS):
        return
    for token in tokens:
        yield PERSON_RECOVERY_SOURCES[0], (dob['canonical'], token)
    for a, b in combinations(tokens, 2):
        yield PERSON_RECOVERY_SOURCES[1], (dob['month'], dob['day'], a, b)

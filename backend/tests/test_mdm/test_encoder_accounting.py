"""An interrupted encoder call must leave an explicit durable accounting intent."""
import pytest


def test_encoder_failure_keeps_request_intent_and_unknown_tokens():
    from masterone.mdm.matching_agents.encoder_accounting import encode_accounted

    class Runtime:
        batch_attempts = 0
        usage = {}

        def encode(self, texts, field):
            self.batch_attempts += 1
            raise ValueError('ENCODER_TIMEOUT')

    receipts = []
    with pytest.raises(ValueError, match='TIMEOUT'):
        encode_accounted(Runtime(), ['example'], 'name', receipts.append)
    assert receipts[0]['event'] == 'INTENT'
    assert receipts[-1]['event'] == 'FAILED'
    assert receipts[-1]['input_tokens'] is None
    assert receipts[-1]['batch_attempts'] == 1
    assert receipts[0]['batch_id'] == receipts[-1]['batch_id']


def test_encoder_receipt_records_increment_not_cumulative_tokens():
    from masterone.mdm.matching_agents.encoder_accounting import encode_accounted

    class Runtime:
        batch_attempts = 2
        usage = {'name': {'input_tokens': 20}}

        def encode(self, texts, field):
            self.batch_attempts += 1
            self.usage[field]['input_tokens'] += 7
            return [[1.0]]

    receipts = []
    assert encode_accounted(Runtime(), ['example'], 'name', receipts.append) == [[1.0]]
    assert receipts[-1]['event'] == 'SUCCESS'
    assert receipts[-1]['input_tokens'] == 7
    assert receipts[-1]['batch_attempts'] == 1

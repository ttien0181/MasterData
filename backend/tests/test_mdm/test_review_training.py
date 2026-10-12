import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('review_trainer', Path(__file__).parents[3] / 'scripts/train_pair_review_model.py')
trainer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trainer)


def test_training_rejects_duplicate_source_templates_before_entity_split():
    template = dict(record_id='r', source_record_id='source-1',source_system='CRM',
                    name='NAME',phone='',email='',dob='',address='ADDRESS')
    with pytest.raises(ValueError, match='DUPLICATE_SYNTHETIC_TEMPLATE'):
        trainer.build_splits([dict(template,record_id=str(i)) for i in range(200)], {})

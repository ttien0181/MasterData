"""The diagnostic must honor raw source mappings without leaking gold labels."""
import importlib.util
from datetime import datetime
from pathlib import Path

import pytest
import yaml


def verifier():
    path = Path(__file__).resolve().parents[3] / 'scripts/verify_pair_correctness.py'
    spec = importlib.util.spec_from_file_location('stage1_verifier', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mapped_xlsx_header_offset_and_excel_dob_serial(tmp_path):
    openpyxl = pytest.importorskip('openpyxl')
    from openpyxl.utils.datetime import to_excel
    raw = tmp_path / 'raw'
    raw.mkdir()
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(['Exported customer data'])
    sheet.append(['id', 'source', 'person', 'born'])
    sheet.append(['r1', 'CRM', 'Example One', to_excel(datetime(1990, 5, 15))])
    sheet.append(['id', 'source', 'person', 'born'])  # repeated export header
    book.save(raw / 'customers.xlsx')
    mapping = {'files': [{'path': 'raw/customers.xlsx', 'format': 'xlsx', 'header_row': 1,
        'date_serial': True, 'batch_id': '2026-Q1',
        'columns': {'source_record_id': 'id', 'source_system': 'source', 'name': 'person', 'dob': 'born'}}]}
    (raw / 'source_mapping.yaml').write_text(yaml.safe_dump(mapping), encoding='utf-8')
    diagnostics = {}
    rows, available = verifier().raw_records(tmp_path, 10, diagnostics)
    assert available == 1
    assert diagnostics['rejected_source_rows'] == 1
    assert rows == [('CRM:r1:2026-Q1', {'source_record_id': 'r1', 'source_system': 'CRM',
                                     'name': 'Example One', 'dob': '1990-05-15'})]


def test_mapped_csv_header_offset_and_gold_columns_are_not_matcher_input(tmp_path):
    raw = tmp_path / 'raw'
    raw.mkdir()
    (raw / 'customers.csv').write_text('Exported customer data\nid,source,person,gold\nr1,CRM,Example One,E1\n', encoding='utf-8')
    mapping = {'files': [{'path': 'raw/customers.csv', 'format': 'csv', 'header_row': 1,
        'batch_id': '2026-Q1', 'columns': {'source_record_id': 'id', 'source_system': 'source',
                                       'name': 'person', 'entity_id': 'gold'}}]}
    (raw / 'source_mapping.yaml').write_text(yaml.safe_dump(mapping), encoding='utf-8')
    rows, available = verifier().raw_records(tmp_path, 10)
    assert available == 1
    assert rows == [('CRM:r1:2026-Q1', {'source_record_id': 'r1', 'source_system': 'CRM', 'name': 'Example One'})]


def test_source_footer_and_malformed_csv_rows_are_reported_without_becoming_fake_records(tmp_path):
    raw = tmp_path / 'raw'
    raw.mkdir()
    (raw / 'customers.csv').write_text('id,source,person\nr1,CRM,Example One\nTotal\nbad,CRM,Bad Row,extra\n', encoding='utf-8')
    mapping = {'files': [{'path': 'raw/customers.csv', 'format': 'csv', 'batch_id': '2026-Q1',
        'columns': {'source_record_id': 'id', 'source_system': 'source', 'name': 'person'}}]}
    (raw / 'source_mapping.yaml').write_text(yaml.safe_dump(mapping), encoding='utf-8')
    diagnostics = {}
    rows, available = verifier().raw_records(tmp_path, 10, diagnostics)
    assert available == 1
    assert rows == [('CRM:r1:2026-Q1', {'source_record_id': 'r1', 'source_system': 'CRM', 'name': 'Example One'})]
    assert diagnostics['rejected_source_rows'] == 2


def test_duplicate_omission_diagnostic_separates_floor_and_blocked_pairs():
    metrics = verifier().candidate_truth_metrics([
        ('a', 'b', 'REVIEW', True, 'MISSING_REQUIRED_NAME_ANCHOR'),
        ('a', 'c', 'INSUFFICIENT_EVIDENCE', False, 'NO_VALID_IDENTITY_ANCHOR'),
        ('b', 'c', 'NOT_MERGEABLE', False, 'PERSON_POLICY_OUT_OF_SCOPE'),
        ('a', 'd', 'INSUFFICIENT_EVIDENCE', False, 'NO_VALID_IDENTITY_ANCHOR'),
        ('a', 'unknown', 'INSUFFICIENT_EVIDENCE', False)],
        {rid: rid for rid in ('a', 'b', 'c', 'd', 'unknown')}, {'a':'E1','b':'E1','c':'E1','d':'E2'})
    assert metrics['known_candidate_pairs'] == 4
    assert metrics['same_entity_candidate_pairs'] == 3
    assert metrics['same_entity_outside_required'] == 2
    assert metrics['same_entity_outside_required_by_decision'] == {
        'INSUFFICIENT_EVIDENCE': 1, 'NOT_MERGEABLE': 1}
    assert metrics['same_entity_outside_required_by_reason'] == {
        'NO_VALID_IDENTITY_ANCHOR': 1, 'PERSON_POLICY_OUT_OF_SCOPE': 1}

"""Export a revision-fenced selection of saved Golden values without changing decisions."""
import csv
import json
from io import TextIOWrapper
from tempfile import SpooledTemporaryFile

from masterone.mdm.golden_service import selected_golden
from masterone.mdm.workflow_cluster_reads import current_generation
from masterone.mdm.workflow_generation_reads import GenerationReader
from masterone.mdm.workflow_service import active_operation, audit, check_revision, conflict, enabled_run

LABELS = {'name': 'Họ tên', 'phone': 'Điện thoại', 'email': 'Email', 'dob': 'Ngày sinh',
          'government_id': 'Giấy tờ định danh', 'address': 'Địa chỉ'}


def cell(value):
    text = '' if value is None else value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    # CSV is opened as a spreadsheet by business users; keep values as text.
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@', '\t', '\r', '\n')) else text


def prepare_golden_csv(db, run_id, actor, body):
    run = enabled_run(db, run_id, actor)
    payload = body.model_dump()
    check_revision(run, payload)
    if active_operation(db, run.id):
        conflict('ACTIVE_OPERATION')
    generation = current_generation(db, run)
    reader = GenerationReader(db, run, generation)
    fields, count = set(LABELS), 0
    # Spill large exports to disk; no client page limit applies to selection.
    with SpooledTemporaryFile(max_size=1024 * 1024, mode='w+', encoding='utf-8') as records:
        for row in reader.rows({key: payload[key] for key in ('cluster_ids', 'excluded_cluster_ids')}):
            values = {key: selected.get('value') for key, selected in selected_golden(db, row, reader).get('fields', {}).items()}
            fields.update(values)
            records.write(json.dumps({'id': row.id, 'values': values}, ensure_ascii=False) + '\n')
            count += 1
        if not count:
            conflict('NO_GOLDEN_SELECTED')
        keys = [*LABELS, *sorted(fields - LABELS.keys())]
        output = SpooledTemporaryFile(max_size=1024 * 1024, mode='w+b')
        try:
            text = TextIOWrapper(output, encoding='utf-8-sig', newline='')
            writer = csv.writer(text)
            writer.writerow(['Mã Golden record', *[LABELS.get(key, key) for key in keys]])
            records.seek(0)
            for line in records:
                row = json.loads(line)
                writer.writerow([cell(row['id']), *[cell(row['values'].get(key)) for key in keys]])
            text.flush()
            text.detach()
            output.seek(0)
            audit(db, run, actor, 'EXPORT_GOLDEN_SELECTION', {'row_count': count, 'cluster_revision': run.cluster_revision,
                'golden_revision': run.golden_revision, 'all': body.cluster_ids is None})
            db.commit()
            return output, count
        except BaseException:
            output.close()
            raise

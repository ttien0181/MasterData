"""Local browser bridge to actual API + claimed worker using synthetic isolated data.

The preauthenticated test identity and synthetic D assessment are explicit test
fixtures. No requests reach the developer's DB, object store or AI providers.
Only existing API routes are forwarded; no product test endpoints are added.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from run_merge_baseline import isolated_environment  # noqa: E402


def main():
    out = ROOT / 'artifacts/handoff-browser-20261013'
    out.mkdir(parents=True, exist_ok=True)
    os.environ.update(isolated_environment(out / 'state'))
    sys.path[:0] = [str(ROOT / 'backend/src'), str(ROOT / 'backend')]
    from masterone.config import get_settings
    from masterone.mdm.workflow_models import WorkflowCluster, WorkflowGeneration, WorkflowPair
    from pytest import MonkeyPatch
    from tests.test_mdm.conftest import client as client_fixture
    from tests.test_mdm.test_workflow_stages import ready_decisions, run_one
    patch = MonkeyPatch()
    patch.setattr(get_settings(), 'session_workflow_enabled', True)
    patch.setattr(get_settings(), 'pair_assessment_auto_enabled', True)
    fixture = client_fixture.__wrapped__(patch)
    client = next(fixture)
    csv = 'source_record_id,source_system,entity_type,name,phone,email,address\n'
    csv += ''.join(f'{i},{source},PERSON,Nguyen Van An,0901234567,an@example.test,123 Hanoi\n'
                   for i, source in enumerate(('CRM', 'BILL', 'ERP', 'CSV')))
    uploaded = client.post('/api/v1/mdm/runs', data={'workflow_version': '2'},
                           files={'files': ('synthetic-browser.csv', csv.encode(), 'text/csv')})
    assert uploaded.status_code == 201, uploaded.text
    run_id = uploaded.json()['run_id']
    ready_decisions(client, run_id)
    with client.session_factory.begin() as db:
        pair = db.query(WorkflowPair).filter_by(run_id=run_id).first()
        pair.assessment = {**pair.assessment, 'primary_group': 'D'}
    (out / 'fixture.json').write_text(json.dumps({'run_id': run_id, 'mode': 'synthetic preauthenticated fixture',
        'bridge': 'http://127.0.0.1:8014', 'real_developer_backend_requests': 0}), encoding='utf8')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def forward(self):
            if not (self.path.startswith('/api/v1/') or self.path == '/health'):
                self.send_error(404)
                return
            body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            headers = {'Content-Type': self.headers.get('Content-Type', 'application/json')}
            response = client.request(self.command, self.path, content=body or None, headers=headers)
            content = response.content
            self.send_response(response.status_code)
            self.send_header('Content-Type', response.headers.get('Content-Type', 'application/json'))
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            self.wfile.flush()
            # Return the queued receipt first, then run the real claim/worker path.
            if response.status_code == 202 and response.json().get('operation_type'):
                run_one(client)
                if response.json()['operation_type'] == 'CLUSTER':
                    with client.session_factory.begin() as db:
                        generation = db.query(WorkflowGeneration).filter_by(run_id=run_id).order_by(
                            WorkflowGeneration.revision.desc()).first()
                        cluster = db.query(WorkflowCluster).filter_by(generation_id=generation.id).first()
                        cluster.payload = {**cluster.payload, 'pending_selections': {'obsolete_extra': [{
                            'selection_mode': 'SOURCE_CHOICE', 'source_record_id': 'removed-synthetic-source',
                            'value': 'Old supplemental choice', 'ancestor_cluster_id': 'synthetic-old-group'}]},
                            'requires_manual_review': ['obsolete_extra']}

        do_GET = forward  # noqa: N815 -- BaseHTTPRequestHandler protocol names
        do_POST = forward  # noqa: N815
        do_PATCH = forward  # noqa: N815
        do_PUT = forward  # noqa: N815

    server = HTTPServer(('127.0.0.1', 8014), Handler)
    print(json.dumps({'ready': True, 'run_id': run_id}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        fixture.close()
        patch.undo()


if __name__ == '__main__':
    main()

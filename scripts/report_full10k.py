"""Render measured full10k receipts and a clearly separated Gemini projection."""
import argparse
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from qa_api_multiagent import save


def derive(report):
    stages=report['stages']
    quality=json.loads(json.dumps(stages['posthoc-evaluation']['result']))
    # Older receipts omit scorers with no positive predictions. An empty
    # prediction set has zero recall, while its precision is undefined.
    for name in ('rule_A','rule_mergeable','splink','gemini_selected'):
        quality['scores'].setdefault(name, {'tp':0,'fp':0,
            'fn':report['fixture']['truth_positive_pairs'],
            'precision':None,'recall':0.0,'f1':0.0})
    sample=stages['gemini-explanation-sample']['result']
    n=math.ceil(.3*quality['candidate_count'])
    base=report['upload_to_golden_seconds']-stages['posthoc-evaluation']['seconds']-stages['gemini-explanation-sample']['seconds']
    telemetry=report['telemetry']
    worker=[]
    phases=defaultdict(float)
    for op in telemetry['operations']:
        attempts=(op.get('telemetry') or {}).get('attempts',[])
        if not attempts: continue
        row={'operation':op['operation_type'],'attempts':len(attempts),
             'seconds':sum(a['total_seconds'] for a in attempts),
             'unknown_queue_attempts':sum(a['queue_wait_seconds'] is None for a in attempts),
             'known_queue_seconds':sum(a['queue_wait_seconds'] or 0 for a in attempts)}
        worker.append(row)
        for attempt in attempts:
            for stage,seconds in attempt['stages'].items(): phases[stage]+=seconds
    def purpose_totals(purpose):
        rows=[r for r in telemetry['llm_by_purpose'] if r['purpose']==purpose]
        return {key:sum(r.get(key,0) for r in rows) for key in
                ('http_attempts','logical_requests','latency_seconds')}
    timed_ids={a['attempt_id'] for op in telemetry['operations'] for a in (op.get('telemetry') or {}).get('attempts',[])}
    untimed=[{**a,'timestamp_duration_seconds':
        (datetime.fromisoformat(a['finished_at'])-datetime.fromisoformat(a['started_at'])).total_seconds() if a['finished_at'] else None}
        for a in telemetry['worker_attempts'] if a['attempt_id'] not in timed_ids]
    advice=purpose_totals('PAIR_ASSESSMENT')
    explanations=purpose_totals('EXPLANATION')
    return {'records':report['fixture']['records'],'candidates':quality['candidate_count'],
        'measured_upload_to_golden_seconds':report['upload_to_golden_seconds'],
        'measured_upload_to_finalized_seconds':stages['golden-confirm-finalize']['finished_epoch']-report['upload_started_epoch'],
        'base_without_posthoc_and_explanation_sample_seconds':base,
        'worker_operations':worker,'worker_phases_seconds':dict(phases),'worker_attempts_without_phase_timing':untimed,
        'workflow_operation_counts':dict(Counter(op['operation_type'] for op in telemetry['operations'])),
        'ai_requests_actual':{'rule':0,'splink':0,
            'embedding':sum(r['http_attempts'] for r in telemetry['embedding_by_position'].values()),
            'gemini_advice':advice.get('http_attempts',0),'gemini_explanations':explanations.get('http_attempts',0)},
        'embedding_http_receipts':telemetry['embedding_by_position'],
        'gemini_http_receipts':telemetry['llm_by_purpose'],
        'known_api_latency_seconds':{
            'embedding':sum(r['latency_seconds'] for r in telemetry['embedding_by_position'].values()),
            'gemini_advice':advice.get('latency_seconds',0),
            'gemini_explanations':explanations.get('latency_seconds',0)},
        'matching_local_evaluations':{'rule_candidate_pairs_per_pass':quality['candidate_count'],
            'rule_main_passes':['candidate-rule-scoring','rule-assessment'],
            'splink_pairs':sum(r.get('evaluated_pairs',0) for r in telemetry['local_model_evaluations']),
            'splink_sql_batches_at_most_2048_pairs':sum(math.ceil(r.get('evaluated_pairs',0)/2048) for r in telemetry['local_model_evaluations']),
            'note':'Unique universe per completed full pass; excludes repeated work in interrupted attempts and additional local guard comparisons. These are not external requests.'},
        'gemini_30_percent_scenario':{'denominator':'ALL_CANDIDATES','explanation_logical_requests':n,
            'advice_logical_requests':advice.get('logical_requests',0),
            'minimum_physical_requests_no_retries':n+advice.get('logical_requests',0),
            'measured_explanation_logical_requests':sample['measured_logical_requests'],
            'mean_measured_http_seconds':sample['mean_seconds'],
            'serial_explanations_seconds':n*sample['mean_seconds'],
            'serial_upload_to_golden_seconds':base+n*sample['mean_seconds'],
            'ideal_concurrency_2_upload_to_golden_seconds':base+math.ceil(n/2)*sample['mean_seconds'],
            'qualification':'PROJECTION, NOT A FULL 30% LIVE RUN; fixed mean latency and no retries/quota waits. Current per-run/user/global caps do not permit this volume.'},
        'machine_quality':quality,'after_scripted_truth_review':stages['golden-quality']['result']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--markdown',type=Path,required=True)
    args=parser.parse_args()
    cases={p.parent.name:json.loads(p.read_text(encoding='utf8')) for p in sorted(args.input.glob('TC*/report.json'))}
    assert len(cases)==2 and all(r['status']=='PASS' for r in cases.values()), 'BOTH_FULL_RUNS_MUST_PASS'
    derived={name:derive(report) for name,report in cases.items()}
    for name in cases:
        calls=json.loads((args.input/name/'explanation-sample.json').read_text(encoding='utf8'))
        counts=defaultdict(int)
        for call in calls: counts[call['response']['status']]+=1
        derived[name]['explanation_sample_statuses']=dict(counts)
        derived[name]['explanation_sample_latency_seconds']=sorted(c['seconds'] for c in calls)
        assert counts['READY']==len(calls), 'DO_NOT_PROJECT_VALID_EXPLANATIONS_FROM_TEMPLATE_LATENCY'
        embedding_path=args.input/name/'embedding-quality.json'
        if embedding_path.exists():
            derived[name]['embedding_diagnostics']=json.loads(embedding_path.read_text(encoding='utf8'))
    save(args.input/'accounting-summary.json',derived)
    names=list(cases)
    def table(rows):
        return '| Bước / phép đo | '+ ' | '.join(names)+' |\n| --- | ---: | ---: |\n'+''.join('| '+label+' | '+' | '.join(str(value(name)) for name in names)+' |\n' for label,value in rows)+'\n'
    def secs(value): return f'{value:,.2f} s ({value/60:,.2f} phút)'
    def percent(value): return 'n/a' if value is None else f'{value*100:.3f}%'
    text='# TC1 và TC3: chạy toàn bộ dữ liệu 10k từ upload đến Golden Record\n\nNgày đo: 16/10/2026.\n\n'
    text+='Kết quả dưới đây lấy từ các lượt chạy HTTP và worker thật trên stack QA local. “Duyệt hết” là replay truth qua API để gộp cặp đúng khi constraints cho phép, từ chối các cặp còn lại; không mô phỏng thời gian người đọc/suy nghĩ. Truth được giữ riêng để kiểm tra fixture, đánh giá và replay duyệt; không được tải vào scorer hoặc prompt. Đánh giá/replay chỉ chạy sau matching.\n\n'
    text+='Profile đo là `RULE_WITH_EMBEDDING_EVIDENCE`: candidate do blocking/retrieval rule tạo, embedding bổ sung evidence, Splink hỗ trợ review trong scope synthetic; không chạy E3 union và không bật auto-merge. Embedding trong profile này không thay quyền gộp của rule. Chỉ số ranking/coverage của embedding không tự chứng minh cải thiện quyết định đầu-cuối.\n\n'
    text+=table([('Số record',lambda n:derived[n]['records']),('Candidate',lambda n:derived[n]['candidates']),
        ('Upload → Golden đo thực tế, gồm instrumentation và mẫu giải thích',lambda n:secs(derived[n]['measured_upload_to_golden_seconds'])),
        ('Upload → xác nhận FINALIZED, gồm hậu kiểm membership',lambda n:secs(derived[n]['measured_upload_to_finalized_seconds'])),
        ('Upload → Golden bỏ thời gian đánh giá offline và mẫu giải thích; vẫn gồm replay duyệt',lambda n:secs(derived[n]['base_without_posthoc_and_explanation_sample_seconds']))])
    text+='\nThời gian từng chặng do client đo, gồm HTTP, queue và polling. Các dòng con worker ở bảng tiếp theo nằm bên trong các chặng này; không cộng hai bảng.\n\n'
    labels={'prepare':'Chuẩn hóa / chuẩn bị sau upload','matching-advice-preview':'Matching + advice + preview cụm',
        'posthoc-evaluation':'Đánh giá offline bằng truth (instrumentation QA)',
        'gemini-explanation-sample':'Mẫu giải thích Gemini thật', 'scripted-truth-review':'Lưu toàn bộ quyết định duyệt mô phỏng qua API',
        'cluster-and-golden':'Sau duyệt cặp → tạo Golden (gồm xác nhận cụm nếu cần)',
        'scripted-group-review':'Xác nhận cụm theo truth (phần con của chặng ngay trên)',
        'golden-quality':'Kiểm tra membership và chất lượng cuối (sau mốc Golden)',
        'golden-confirm-finalize':'Xác nhận và finalize Golden (sau mốc tạo Golden)'}
    rows=[('Gửi file upload',lambda n:secs(cases[n]['upload_seconds']))]
    rows += [(label,lambda n,key=key:secs(cases[n]['stages'].get(key,{}).get('seconds',0))) for key,label in labels.items()]
    text+=table(rows)
    text+='\nThời gian worker theo operation, không gồm thời gian chờ queue/khởi động process; đọc chi tiết các phase trong JSON accounting.\n\n'
    operations=sorted({r['operation'] for data in derived.values() for r in data['worker_operations']})
    text+=table([(op,lambda n,op=op:secs(sum(r['seconds'] for r in derived[n]['worker_operations'] if r['operation']==op))) for op in operations])
    text+= '\n'+table([('Attempt thiếu timing phase, đo theo timestamp bắt đầu/kết thúc',
        lambda n:secs(sum(a['timestamp_duration_seconds'] or 0 for a in derived[n]['worker_attempts_without_phase_timing'])))])
    for name,data in derived.items():
        if data['worker_attempts_without_phase_timing']:
            text+=f'\n{name}: có attempt thiếu timing từng phase; xem timestamp/duration trong accounting JSON. Tổng client vẫn bao gồm các attempt này.\n'
    text+='\nTC1 gặp `MATCHING_WORKER_TIMEOUT` ở attempt DECIDE đầu sau khoảng 1.800 giây; queue tự retry và dùng lại cache embedding. Attempt hoàn tất có timing phase, attempt bị ngắt chỉ có timestamp. Không loại thời gian lỗi/retry khỏi tổng đo, không thay bằng số của lượt chẩn đoán trước đó.\n'
    text+='\nThời gian chờ queue đã đo (nằm trong thời gian client, chưa cộng lại vào bảng):\n\n'
    text+=table([(op,lambda n,op=op:secs(sum(r['known_queue_seconds'] for r in derived[n]['worker_operations'] if r['operation']==op))) for op in operations])
    text+='\nCác số queue trên chỉ cộng receipt có giá trị; `null` là chưa đo được, không chứng minh thời gian chờ bằng 0. Attempt bị ngắt có thể thiếu cả receipt timing.\n\n'
    phases=sorted({phase for data in derived.values() for phase in data['worker_phases_seconds']})
    text+='\n'+table([(phase,lambda n,phase=phase:secs(derived[n]['worker_phases_seconds'].get(phase,0))) for phase in phases])
    text+='\nSố request API ra ngoài đã đo thực tế:\n\n'
    text+=table([(label,lambda n,key=key:derived[n]['ai_requests_actual'][key]) for key,label in [('rule','Rule'),('embedding','Embedding OpenRouter'),('splink','Splink'),('gemini_advice','Gemini tư vấn matching'),('gemini_explanations','Gemini giải thích (mẫu thật)')]])
    text+=table([
        ('Gemini advice — phản hồi hợp lệ',lambda n:sum(r['successes'] for r in derived[n]['gemini_http_receipts'] if r['purpose']=='PAIR_ASSESSMENT')),
        ('Gemini advice — SCHEMA_INVALID',lambda n:sum(r['statuses'].get('SCHEMA_INVALID',0) for r in derived[n]['gemini_http_receipts'] if r['purpose']=='PAIR_ASSESSMENT'))])
    text+='\nTC3 phát sinh 1 request Gemini advice nhưng phản hồi bị kiểm tra schema từ chối (`SCHEMA_INVALID`); không có khuyến nghị hợp lệ để chấm accuracy ở bước này. Mười giải thích thành công là mục đích khác, không dùng chúng để suy ra advice đã hoạt động đúng.\n\n'
    text+='\nPhân bổ request theo bước: upload, chuẩn hóa, Rule, Splink, lưu duyệt, clustering và Golden không gọi AI ngoài. Các request embedding của lượt này nằm ở bước bổ sung evidence; retrieval embedding bằng 0. Gemini advice thuộc matching, Gemini explanation thuộc chặng giải thích trước duyệt. Các request lấy/cập nhật trạng thái workflow không phải request đến AI.\n\n'
    text+=table([(label,lambda n,key=key:derived[n]['embedding_http_receipts'][key]['http_attempts']) for key,label in [
        ('retrieval','Request embedding ở candidate retrieval'),('evidence','Request embedding ở matching evidence')]])
    text+='\nEmbedding đếm mỗi HTTP intent một lần, gồm attempt bị gián đoạn có thể đã đến provider. Receipt chưa có phản hồi cuối được giữ ở trạng thái chưa xác định, không tự tính là thành công.\n\n'
    text+=table([('Embedding: phản hồi cuối đã xác nhận',lambda n:sum(r['responses'] for r in derived[n]['embedding_http_receipts'].values())),
        ('Embedding: phản hồi lỗi đã xác nhận',lambda n:sum(r['failed_responses'] for r in derived[n]['embedding_http_receipts'].values())),
        ('Embedding: intent chưa có phản hồi cuối',lambda n:sum(r['unresolved_intents'] for r in derived[n]['embedding_http_receipts'].values()))])
    text+='\nTổng độ trễ API có receipt (nằm trong thời gian các chặng; không cộng lần nữa, không bao gồm intent thiếu phản hồi):\n\n'
    text+=table([(label,lambda n,key=key:secs(derived[n]['known_api_latency_seconds'][key])) for key,label in [
        ('embedding','Embedding'),('gemini_advice','Gemini advice'),('gemini_explanations','Gemini giải thích')]])
    text+='\nRule, Splink, Coordinator, clustering và chọn Golden chạy bằng code/model cục bộ. Splink đánh giá toàn bộ candidate theo batch tối đa 2.048 cặp; embedding gọi theo batch tối đa 32 text đã khử trùng lặp rồi tính cosine cục bộ. Số request không bằng số cặp được đánh giá.\n\n'
    text+=table([('Cặp được Rule đánh giá trong mỗi pass matching chính',lambda n:derived[n]['matching_local_evaluations']['rule_candidate_pairs_per_pass']),
        ('Cặp Splink inference',lambda n:derived[n]['matching_local_evaluations']['splink_pairs']),
        ('Batch SQL Splink (không phải HTTP)',lambda n:derived[n]['matching_local_evaluations']['splink_sql_batches_at_most_2048_pairs'])])
    text+='Số cặp/batch local ở trên mô tả một lượt inference hoàn tất, không cộng phần tính lại trong attempt bị ngắt hoặc so sánh bổ sung của guard. Request HTTP embedding/Gemini vẫn cộng tất cả attempt có ledger.\n\n'
    text+='Gemini 30% toàn bộ candidate: `ceil(0,30 × candidate_count)`. Đây là **dự phóng** từ 10 yêu cầu giải thích thật mỗi bộ, không phải thời gian đã chạy đủ 30%. Mẫu 10 đã nằm trong 30%, không cộng lại. Advice matching là mục đích khác và được cộng riêng.\n\n'
    text+=table([(label,lambda n,key=key: derived[n]['gemini_30_percent_scenario'][key] if 'requests' in key else secs(derived[n]['gemini_30_percent_scenario'][key])) for key,label in [
        ('explanation_logical_requests','Số yêu cầu giải thích cho 30%'),('advice_logical_requests','Yêu cầu advice đã chạy'),
        ('minimum_physical_requests_no_retries','Tổng request Gemini tối thiểu trong kịch bản 30%, không retry'),
        ('mean_measured_http_seconds','Thời gian trung bình mẫu giải thích'),('serial_explanations_seconds','Chỉ giải thích, tuần tự'),
        ('serial_upload_to_golden_seconds','Upload → Golden nếu giải thích tuần tự đủ 30%'),
        ('ideal_concurrency_2_upload_to_golden_seconds','Upload → Golden giả định concurrency 2 lý tưởng')]])
    text+='\nDự phóng giữ nguyên độ trễ trung bình, chưa cộng retry, throttling hay chờ quota; concurrency 2 là giả định chưa kiểm thử tải tương ứng. Cấu hình QA hiện giới hạn 20 explanation/run, 5.000 call/user và 10.000 global; không thể thực thi nguyên kịch bản này với quota hiện tại.\n\n'
    text+='Chất lượng so với truth của bộ sinh synthetic:\n\n'
    rows=[('Cặp trùng theo truth',lambda n:cases[n]['fixture']['truth_positive_pairs']),
        ('Cặp trùng tìm thấy trong candidate',lambda n:derived[n]['machine_quality']['true_candidates']),
        ('Candidate recall',lambda n:percent(derived[n]['machine_quality']['candidate_recall']))]
    for kind in ('PERSON','ORGANIZATION'):
        rows.append((kind+' — candidate recall',lambda n,k=kind:percent(derived[n]['machine_quality']['by_entity_type'][k]['candidate_recall'])))
    for scorer,label in [('rule_A','Rule nhóm A'),('rule_mergeable','Rule MERGEABLE'),('splink','Splink LIKELY_SAME_PERSON')]:
        for metric in ['precision','recall','f1']:
            rows.append((label+' '+metric,lambda n,scorer=scorer,metric=metric:percent(derived[n]['machine_quality']['scores'].get(scorer,{}).get(metric))))
    rows += [('Cặp đúng bị chặn manual merge',lambda n:derived[n]['machine_quality']['true_candidates_merge_blocked']),
        ('Golden sau replay: precision',lambda n:percent(derived[n]['after_scripted_truth_review']['pairwise']['precision'])),
        ('Golden sau replay: recall',lambda n:percent(derived[n]['after_scripted_truth_review']['pairwise']['recall'])),
        ('Golden sau replay: F1',lambda n:percent(derived[n]['after_scripted_truth_review']['pairwise']['f1'])),
        ('Số cụm Golden sau replay',lambda n:derived[n]['after_scripted_truth_review']['clusters']),
        ('Overmerge / undermerge sau replay',lambda n:str(derived[n]['after_scripted_truth_review']['overmerge_pairs'])+' / '+str(derived[n]['after_scripted_truth_review']['undermerge_pairs'])),
        ('Record được bảo toàn',lambda n:derived[n]['after_scripted_truth_review']['records_preserved'])]
    text+=table(rows)
    if all('embedding_diagnostics' in data for data in derived.values()):
        text+='\nHậu kiểm embedding trên candidate đã có vector, không chọn ngưỡng trên tập test. Cosine không phải xác suất; AUC dưới đây chỉ đo thứ hạng trên candidate có evidence, không đo recall retrieval hay quyền gộp.\n\n'
        rows=[]
        for field in ('name','address'):
            rows.extend([
                (field+' — cặp có cosine',lambda n,f=field:derived[n]['embedding_diagnostics']['fields'][f]['available_pair_count']),
                (field+' — ROC AUC',lambda n,f=field:round(derived[n]['embedding_diagnostics']['fields'][f]['roc_auc_on_available_candidates'],6)),
                (field+' — cosine trung bình cặp đúng',lambda n,f=field:round(derived[n]['embedding_diagnostics']['fields'][f]['score_means']['truth_match'],6)),
                (field+' — cosine trung bình cặp khác',lambda n,f=field:round(derived[n]['embedding_diagnostics']['fields'][f]['score_means']['truth_non_match'],6))])
        text+=table(rows)
    text+='\nCác recall của scorer dùng toàn bộ cặp đúng, gồm cặp mất ở candidate generation. Chất lượng Golden ở trên **có trợ giúp truth khi replay duyệt**, không phải chất lượng matching tự động. Không dùng kết quả này để bật auto-merge. Chưa chấm độ đúng từng trường Golden: file entity có giá trị tham chiếu, nhưng cần chốt mốc thời gian và cách xử lý history/contacts trước khi dùng làm đáp án survivorship. Chất lượng diễn giải ngôn ngữ chưa có nhãn độc lập để chấm. Splink chỉ được bật với scope synthetic-benchmark; manifest chưa phê duyệt production. Chi tiết PERSON/ORGANIZATION và Brier score nằm trong accounting JSON.\n\n'
    text+='TC1 còn 728 cặp trùng ORGANIZATION chưa gộp do phạm vi merge hiện tại. TC3 còn 2.335 cặp: 1.606 không được candidate generation tìm thấy và 729 ORGANIZATION bị chặn merge. Rule không tạo nhóm A trên TC3; Splink chỉ khuyến nghị đúng 1 cặp. AUC embedding tên/địa chỉ trên candidate TC3 lần lượt khoảng 0,457/0,455, chưa chứng minh khả năng phân biệt tốt. Các kết quả này cho thấy luồng đã đi đến Golden nhưng chất lượng TC3 chưa đạt mục tiêu; cần đánh giá lại retrieval, model và calibration trên dữ liệu tách biệt trước khi mở tự động hóa.\n\n'
    text+='Xác nhận cặp chưa hoàn tất mọi yêu cầu duyệt: guard cấp cụm có thể yêu cầu thêm group approval cho missing bridge. Runner chỉ dùng luồng exception đã có với lý do rõ ràng và thành viên đúng theo truth; không vượt Deny/hard constraints. Thời gian tạo Golden gồm cả chặng xác nhận cụm này; dòng xác nhận cụm là dòng con, không cộng lần nữa.\n\n'
    text+=table([('Lệnh bulk quyết định cặp',lambda n:derived[n]['workflow_operation_counts'].get('BULK_DECISIONS',0)),
        ('Lệnh quyết định cặp riêng lẻ',lambda n:derived[n]['workflow_operation_counts'].get('PAIR_DECISION',0)),
        ('Lệnh xác nhận cụm',lambda n:derived[n]['workflow_operation_counts'].get('GROUP_APPROVAL',0))])
    text+='\nRunner giải quyết mọi candidate, gồm các cặp insufficient/blocked không bắt buộc phải duyệt trong UI. Do đó thời gian replay là kịch bản duyệt tất cả candidate, không phải thời gian chỉ làm hết hàng chờ bắt buộc. Các thao tác này được script mô phỏng; không phải hệ thống tự có ground truth.\n\n'
    for name,data in derived.items():
        text+=f'{name}: nhóm {data["machine_quality"]["groups"]}; Gemini advice trên tập đã chọn: {data["machine_quality"].get("gemini_selected_accuracy")}; giải thích: {data["explanation_sample_statuses"]}.\n\n'
    runtime_path=args.input/'runtime.json'
    if runtime_path.exists():
        runtime=json.loads(runtime_path.read_text(encoding='utf8'))
        text+='Điều kiện chạy: Docker local, chạy hai bộ lần lượt, một worker; API 1 CPU/1 GiB, worker 2 CPU/8 GiB, DB 1,5 CPU/3 GiB; guard RSS QA 6 GiB. Embedding OpenRouter `openai/text-embedding-3-small`, 1.536 chiều, batch 32; Gemini theo identity ghi trong telemetry; Splink '+runtime['versions']['splink']+'. Mỗi bộ một lượt với cache embedding riêng theo run/owner; chưa có phân vị nhiều lượt/cold–warm hoặc kiểm thử nhiều người dùng.\n\n'
    memory_path=args.input/'stack-memory.json'
    if memory_path.exists():
        memory=json.loads(memory_path.read_text(encoding='utf8'))
        peak=memory['peak_stack_working_set_bytes']
        text+=f'Đỉnh working set lấy mẫu của backend + worker + frontend: {peak:,} byte ({peak/1024**3:.3f} GiB). Phép đo không gồm PostgreSQL, Docker VM hoặc RAM toàn host; các service được lấy mẫu lần lượt nên không phải đỉnh đồng thời chính xác. Xem `stack-memory.json` để biết phạm vi và trạng thái đo. Mốc tài nguyên cũ trong artifact không phải tiêu chí nghiệm thu của phạm vi này.\n\n'
    text+='Chạy lại bằng `scripts/qa_full10k.py`, hậu kiểm embedding bằng `scripts/qa_full10k_embedding_quality.py`, dựng báo cáo bằng `scripts/report_full10k.py`. Dùng Compose hiện hữu; không tạo Compose mới. Mỗi lượt dùng actor QA riêng, lưu log HTTP/usage, run ID, profile digest, SHA-256 và số record thật trong artifact. Upload dùng CSV canonical đã ghép từ tất cả source theo mapping có sẵn; thời gian ghép/hash offline không thuộc mốc upload. Đây là upload HTTP local, không đại diện băng thông Internet của người dùng.\n\n'
    text+='Điều kiện bản sửa: migration `0034_pair_endpoint_index` thêm chỉ mục endpoint PostgreSQL; group approval lọc conflict theo đúng tập thành viên ở SQL trước khi hydrate ORM. Không đổi ngưỡng, model, eligibility hoặc constraints. Lượt chẩn đoán `run-01` được lưu riêng, không dùng làm số liệu cuối. Kiểm tra: 61 test migration/review/clustering qua; build và khởi động lại stack Compose hiện hữu.\n\n'
    text+='`PASS` trong receipt chỉ xác nhận luồng API tới FINALIZED và các kiểm tra bảo toàn record; không phải chứng nhận chất lượng production. Mỗi bộ chỉ đo một lượt sau bản sửa; mọi retry của chính lượt đó nằm trong tổng thời gian.\n\n'
    root=args.input.as_posix()
    text+=f'Artifact gốc: `{root}`.\n\n'
    for name,report in cases.items(): text+=f'- {name}: run `{report["run_id"]}`; trạng thái `{report["status"]}`; [receipt](../../{root}/{name}/report.json).\n'
    text+=f'\n[Accounting và dự phóng dạng JSON](../../{root}/accounting-summary.json). Artifact có thể bị Git ignore và chỉ tồn tại trên máy chạy.\n'
    args.markdown.write_text(text,encoding='utf8')


if __name__=='__main__': main()

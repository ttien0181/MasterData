'use client';
import { useCallback } from 'react';
import Link from 'next/link';
import { getData, mutation, processData } from '@/lib/workflowApi';
import { useWorkflow } from './SessionWorkspace';
import { useWorkflowAction } from './useWorkflowAction';
import { ReadNotice } from './WorkflowReads';
import { useSessionRead } from './useSessionRead';
import { useStoredState } from './SessionCache';
import { useTabView } from './useTabView';
import DataLoadProgress from './DataLoadProgress';
import MetricHelp from './MetricHelp';
import {completenessAverage,metricPercent,metricExplanation as explain} from '@/lib/metricInsights';
import styles from './DataOverview.module.css';

const number = (value: number) => value.toLocaleString('vi-VN');
const fields: Record<string, string> = { name: 'Họ tên', phone: 'Điện thoại', email: 'Email', address: 'Địa chỉ', dob: 'Ngày sinh', government_id: 'Giấy tờ tùy thân' };
const flags: Record<string, string> = { MISSING_NAME: 'Thiếu họ tên', NO_CONTACT_POINT: 'Chưa có số điện thoại và email', INVALID_EMAIL_FORMAT: 'Email sai định dạng', UNUSUAL_PHONE_LENGTH: 'Số điện thoại không hợp lệ', PLACEHOLDER_PHONE: 'Số điện thoại có dấu hiệu là số mẫu', SHARED_OR_PLACEHOLDER_EMAIL: 'Email dùng chung hoặc có dấu hiệu là email mẫu', INVALID_DOB_FORMAT: 'Ngày sinh sai định dạng', INVALID_GOV_ID_FORMAT: 'Số giấy tờ chưa đúng định dạng' };
const label = (key: string) => fields[key] || ({ record_id: 'Mã hồ sơ', source: 'Nguồn tải lên', source_system: 'Nguồn tải lên', external_id: 'Mã hồ sơ gốc' } as Record<string, string>)[key] || key;
function valueText(value: unknown): string {
  if (value == null || (typeof value === 'string' && !value.trim())) return '—';
  if (Array.isArray(value)) return value.map(valueText).join(' · ') || '—';
  if (typeof value === 'object') return Object.entries(value).map(([key, item]) => `${label(key)}: ${valueText(item)}`).join('; ') || '—';
  return String(value);
}
export default function DataOverview() {
  const { workflow: w } = useWorkflow();
  const action = useWorkflowAction();

  const view = useTabView(w.run_id, 'data', true);
  return <section ref={view} className={`workflow-panel ${styles.workspace}`} aria-label="Hồ sơ khách hàng">
    <header className={styles.heading}><h1>Hồ sơ khách hàng</h1>
      {['DECIDE_REVIEW','CLUSTER_QUEUED','CLUSTER_RUNNING','GOLDEN_REVIEW','FINALIZED'].includes(w.workflow_state)&&<Link data-demo="open-decisions" className="button primary" href={`/app/sessions/${encodeURIComponent(w.run_id)}/decisions`}>Xem các cặp cần đối chiếu →</Link>}
    </header>
    {w.workflow_state==='DECIDE_RUNNING'&&<p role="status">Đang đối chiếu hồ sơ… Bạn có thể tiếp tục xem tổng quan dữ liệu.</p>}
    {w.workflow_state==='DATA_READY'&&w.allowed_actions.includes('process-data')&&<p role="status">Dữ liệu đã kiểm tra. Nếu đối chiếu chưa bắt đầu, bạn có thể <button disabled={action.busy} onClick={()=>void action.run(()=>processData(w.run_id,mutation(w)))}>thử lại</button>.</p>}
    {action.error && <p role="alert">{action.error}</p>}
    {w.workflow_state === 'DATA_PREPARING' && <DataLoadProgress workflow={w} />}
    <DataContent key={`${w.run_id}:analysis`} mode="analysis" />
    <DataContent key={`${w.run_id}:records`} mode="records" />
  </section>;
}
function DataContent({ mode }: { mode: 'analysis' | 'records' }) {
  const { workflow: w } = useWorkflow();
  const [offset, setOffset] = useStoredState(w.run_id, 'data:offset', 0);
  const records = mode === 'records';
  const pageOffset = records ? offset : 0;
  const load = useCallback(async (signal: AbortSignal) => ({
    ...await getData(w.run_id, new URLSearchParams({ offset: String(pageOffset), limit: '10', include_profile: String(!records), include_records: String(records) }), signal), loadedOffset: pageOffset,
  }), [w.run_id, pageOffset, records]);
  const preparing = w.workflow_state === 'DATA_PREPARING';
  const read = useSessionRead(w.run_id, ['data', mode, preparing ? w.workflow_revision : 'ready'], { offset: pageOffset, limit: 10 }, load, true, true, preparing ? 2000 : Infinity);
  const goToPage = (target: number) => target === offset ? read.retry() : setOffset(target);
  const data = read.data;
  const profile = data?.profile;
  const total = data?.samples.total || 0;
  const loadedOffset = data?.loadedOffset || 0;
  const pageCount = Math.max(1, Math.ceil(total / 10));
  const busy = records && !!data && data.loadedOffset !== offset && !read.error;
  const warningCount = profile?.records_with_quality_flags;
  return <section id={records ? "data-records" : "data-overview"} className={styles.overview}>
    <header className={styles.heading}><h2>{records ? 'Danh sách hồ sơ khách hàng' : 'Tổng quan hồ sơ'}</h2>
    </header>
    {data && read.error && <ReadNotice error={read.error} retry={read.retry} />}
    {!data ? <ReadNotice error={read.error} retry={read.retry} /> : <>
      {!records && <>
      <div className={styles.metrics} aria-label="Thống kê toàn bộ dữ liệu">
        {[
          ['Tổng hồ sơ khách hàng',number(total),'Số hồ sơ đầu vào trong phiên, trước khi đối chiếu và tổng hợp. Một khách hàng có thể xuất hiện ở nhiều hồ sơ.'],
          ['Nguồn tải lên',profile?number(Object.keys(profile.source_counts).length):'—','Số hệ thống nguồn khác nhau, chẳng hạn CRM hoặc ERP. Nhiều tệp cùng nguồn vẫn tính là một nguồn.'],
          ['Tệp đã tải lên',number(data.files.length),'Số tệp dữ liệu đã nạp vào phiên. Chỉ số này giúp bạn kiểm tra đã tải đủ các tệp cần xử lý chưa.'],
          ['Hồ sơ cần kiểm tra',warningCount==null?'—':number(warningCount),'Số hồ sơ có ít nhất một cảnh báo chất lượng dữ liệu. Mỗi hồ sơ chỉ được tính một lần, dù có nhiều cảnh báo. Đây chưa phải số hồ sơ trùng.'],
          ['Tỷ lệ hồ sơ cần kiểm tra',metricPercent(warningCount,profile?.record_count),'Số hồ sơ có cảnh báo chia cho tổng hồ sơ đầu vào. Bạn có thể dùng tỷ lệ này để ước lượng khối lượng làm sạch dữ liệu.'],
          ['Đầy đủ thông tin trung bình',completenessAverage(profile),'Tổng số ô đã điền của 6 trường họ tên, điện thoại, email, địa chỉ, ngày sinh và giấy tờ, chia cho tổng hồ sơ nhân 6. Có thông tin chưa có nghĩa là thông tin chính xác.'],
        ].map(([title,value,meaning])=><div data-demo={title==='Hồ sơ cần kiểm tra'?'data-quality':undefined} data-bot-metric key={title}><span>{title}</span><strong>{value}</strong><MetricHelp label={title} message={explain(title,value,meaning)}/></div>)}
      </div>
      {!profile ? <p role="status">Đang tổng hợp thông tin khách hàng. Vui lòng chờ trong giây lát.</p> : <div id="data-quality" className={styles.insights}>
        <section className={styles.insight}><h3 data-bot-metric>Hồ sơ theo nguồn tải lên<MetricHelp label="Hồ sơ theo nguồn tải lên" message="Biểu đồ cho biết mỗi nguồn đóng góp bao nhiêu hồ sơ vào phiên. Tỷ lệ tính trên toàn bộ hồ sơ đầu vào; bạn có thể nhận ra nguồn có khối lượng lớn để ưu tiên kiểm tra."/></h3>
          {Object.entries(profile.source_counts).sort((a, b) => b[1] - a[1]).map(([source, count]) => <div data-bot-metric className={styles.distribution} key={source}><div><span>{source}</span><strong>{number(count)} <small>({profile.record_count ? Math.round(100 * count / profile.record_count) : 0}%)</small></strong></div><progress aria-label={`Tỷ lệ nguồn ${source}`} value={count} max={profile.record_count || 1} /><MetricHelp label={`Nguồn ${source}`} message={explain(`Nguồn ${source}`,`${number(count)} hồ sơ (${metricPercent(count,profile.record_count)})`,"Tỷ lệ bằng số hồ sơ của nguồn chia cho tổng hồ sơ đầu vào. Nguồn lớn hơn chưa đồng nghĩa với chất lượng cao hơn.")}/></div>)}
        </section>
        <section className={styles.insight}><h3 data-bot-metric>Mức độ đầy đủ thông tin<MetricHelp label="Mức độ đầy đủ thông tin" message="Mỗi thanh cho biết tỷ lệ hồ sơ đã có giá trị ở trường tương ứng trong tệp gốc. Bạn có thể ưu tiên bổ sung các trường còn thiếu nhiều trước khi phân tích hoặc liên hệ khách hàng. Đầy đủ không đồng nghĩa với đã xác minh chính xác."/></h3>
          {profile.field_completeness ? Object.entries(fields).map(([field, title]) => {
            const metric = profile.field_completeness?.[field];
            const percent = metric && profile.record_count ? 100 * metric.present / profile.record_count : 0;
            return <div data-bot-metric className={styles.completeness} key={field}><span>{title}</span><progress aria-label={`Mức độ đầy đủ ${title}`} value={percent} max={100} /><strong title={metric ? `${number(metric.present)} có thông tin · ${number(metric.missing)} thiếu` : 'Chưa có thống kê'}>{metric ? `${Math.round(percent)}%` : '—'}</strong><MetricHelp label={`Đầy đủ ${title}`} message={explain(title,metric?metricPercent(metric.present,profile.record_count):'—',metric?`${number(metric.present)} hồ sơ có thông tin và ${number(metric.missing)} hồ sơ còn thiếu. Tỷ lệ tính trên toàn bộ hồ sơ đầu vào; giá trị đã điền vẫn có thể cần kiểm tra.`:'Chưa có thống kê cho trường này.')}/></div>;
          }) : <p>Chưa có thống kê mức độ đầy đủ thông tin.</p>}
        </section>
        <section className={styles.insight}><h3 data-bot-metric>Thông tin cần kiểm tra<MetricHelp label="Thông tin cần kiểm tra" message="Đây là số hồ sơ có từng loại cảnh báo. Một hồ sơ có thể xuất hiện ở nhiều loại nên không cộng các số này để tính tổng hồ sơ cần kiểm tra nhé."/></h3>
          <ul className={styles.warnings}>{Object.entries(profile.quality_flags).sort((a, b) => b[1] - a[1]).map(([flag, count]) => <li data-bot-metric key={flag}><span>{flags[flag] || flag}</span><strong>{number(count)}</strong><MetricHelp label={flags[flag]||flag} message={explain(flags[flag]||flag,`${number(count)} hồ sơ`,"Đây là số hồ sơ được gắn cảnh báo này. Bạn nên kiểm tra lại dữ liệu gốc; cảnh báo không tự kết luận hồ sơ là giả hoặc hai hồ sơ thuộc cùng khách hàng.")}/></li>)}</ul>
          {!Object.keys(profile.quality_flags).length && <p>Chưa phát hiện thông tin cần kiểm tra.</p>}
        </section>
      </div>}
      </>}
      {records && <>
      <div className={styles.tableHeading}><strong>{number(total)} hồ sơ khách hàng</strong></div>
      {busy && <p role="status">Đang tải trang {Math.floor(offset / 10) + 1}…</p>}
      <div className={`workflow-scroll ${styles.tableScroll}`} data-scroll-key="records" tabIndex={0} role="region" aria-label="Bảng hồ sơ khách hàng, có thể cuộn ngang" aria-busy={busy}>
        <table aria-label="Danh sách hồ sơ khách hàng"><thead><tr><th scope="col">STT</th><th scope="col">Nguồn tải lên / Mã hồ sơ</th><th scope="col">Họ tên</th><th scope="col">Điện thoại</th><th scope="col">Email</th><th scope="col">Địa chỉ</th><th scope="col">Ghi chú</th></tr></thead><tbody>
          {data.samples.items.map((record, index) => {
            const values = record.raw_fields;
            return <tr key={record.record_id}><td>{number(loadedOffset + index + 1)}</td><td><span className={styles.source}>{record.source_system}</span><small className={styles.recordId}>{record.external_id || record.record_id}</small></td>
              {['name', 'phone', 'email', 'address'].map(field => <td key={field}>{valueText(values[field])}</td>)}
              <td>{!!record.quality_flags?.length && <ul className={styles.notes}>{record.quality_flags.map(flag => <li key={flag}>{flags[flag] || 'Thông tin cần kiểm tra'}</li>)}</ul>}</td></tr>;
          })}
        </tbody></table>
      </div>
      {!data.samples.items.length && <p>Không có hồ sơ khách hàng để hiển thị.</p>}
      <nav className={styles.pagination} aria-label="Phân trang dữ liệu"><span aria-live="polite">{total ? `${number(loadedOffset + 1)}–${number(loadedOffset + data.samples.items.length)} / ${number(total)} hồ sơ khách hàng` : '0 hồ sơ khách hàng'}<small>Trang {Math.floor(loadedOffset / 10) + 1} / {pageCount}</small></span><div>
        <button disabled={busy || loadedOffset === 0} onClick={() => goToPage(0)}>Trang đầu</button><button disabled={busy || loadedOffset === 0} onClick={() => goToPage(Math.max(0, loadedOffset - 10))}>Trang trước</button><button disabled={busy || loadedOffset + 10 >= total} onClick={() => goToPage(loadedOffset + 10)}>Trang sau</button><button disabled={busy || loadedOffset + 10 >= total} onClick={() => goToPage((pageCount - 1) * 10)}>Trang cuối</button>
      </div></nav>
      </>}
    </>}
  </section>;
}

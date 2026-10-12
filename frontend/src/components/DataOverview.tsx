'use client';
import { useCallback, useState } from 'react';
import { getData, mutation, startDecide } from '@/lib/workflowApi';
import type { RecordRead } from '@/lib/workflowContracts';
import { useWorkflow } from './SessionWorkspace';
import { useWorkflowAction } from './useWorkflowAction';
import { ReadNotice } from './WorkflowReads';
import { useSessionRead } from './useSessionRead';
import { useStoredState } from './SessionCache';
import { useTabView } from './useTabView';
import styles from './DataOverview.module.css';

const number = (value: number) => value.toLocaleString('vi-VN');
const fields: Record<string, string> = { name: 'Họ tên', phone: 'Điện thoại', email: 'Email', address: 'Địa chỉ', dob: 'Ngày sinh', government_id: 'Giấy tờ định danh' };
const flags: Record<string, string> = { MISSING_NAME: 'Thiếu họ tên', NO_CONTACT_POINT: 'Thiếu thông tin liên hệ', INVALID_EMAIL_FORMAT: 'Email sai định dạng', UNUSUAL_PHONE_LENGTH: 'Số điện thoại không hợp lệ', PLACEHOLDER_PHONE: 'Số điện thoại mẫu', SHARED_OR_PLACEHOLDER_EMAIL: 'Email dùng chung hoặc mẫu', INVALID_DOB_FORMAT: 'Ngày sinh sai định dạng', INVALID_GOV_ID_FORMAT: 'Giấy tờ sai định dạng' };
const label = (key: string) => fields[key] || key;
function valueText(value: unknown): string {
  if (value == null || (typeof value === 'string' && !value.trim())) return '—';
  if (Array.isArray(value)) return value.map(valueText).join(' · ') || '—';
  if (typeof value === 'object') return Object.entries(value).map(([key, item]) => `${label(key)}: ${valueText(item)}`).join('; ') || '—';
  return String(value);
}
function RecordDetails({ record, normalized }: { record: RecordRead; normalized: boolean }) {
  const values = normalized ? record.normalized_fields : record.raw_fields;
  return <details className={styles.details} data-view-key={`${record.record_id}:detail`}><summary>Chi tiết</summary>
    <dl><div><dt>Mã bản ghi hệ thống</dt><dd>{record.record_id}</dd></div>
      {Object.entries(values).filter(([key]) => !key.startsWith('_')).map(([key, value]) => <div key={key}><dt>{label(key)}</dt><dd>{valueText(value)}</dd></div>)}
      {Object.entries(record.extra_fields).map(([key, value]) => <div key={`extra-${key}`}><dt>{label(key)} (bổ sung)</dt><dd>{valueText(value)}</dd></div>)}
    </dl>
  </details>;
}

export default function DataOverview() {
  const { workflow } = useWorkflow();
  return <DataContent key={workflow.run_id} />;
}
function DataContent() {
  const { workflow: w } = useWorkflow();
  const action = useWorkflowAction();
  const [offset, setOffset] = useStoredState(w.run_id, 'data:offset', 0);
  const [normalized, setNormalized] = useStoredState(w.run_id, 'data:normalized', false);
  const load = useCallback(async (signal: AbortSignal) => ({
    ...await getData(w.run_id, new URLSearchParams({ offset: String(offset), limit: '10' }), signal), loadedOffset: offset,
  }), [w.run_id, offset, w.workflow_state]);
  const preparing = w.workflow_state === 'DATA_PREPARING';
  const read = useSessionRead(w.run_id, ['data', preparing ? w.workflow_revision : 'ready'], { offset, limit: 10 }, load, true, true, preparing ? 2000 : Infinity);
  const goToPage = (target: number) => target === offset ? read.retry() : setOffset(target);
  const data = read.data;
  const view = useTabView(w.run_id, 'data', !!data);
  const profile = data?.profile;
  const total = data?.samples.total || 0;
  const loadedOffset = data?.loadedOffset || 0;
  const pageCount = Math.max(1, Math.ceil(total / 10));
  const busy = !!data && data.loadedOffset !== offset && !read.error;
  const warningCount = profile?.records_with_quality_flags;
  return <section ref={view} className={`workflow-panel ${styles.overview}`}>
    <header className={styles.heading}><div><p className={styles.eyebrow}>DỮ LIỆU ĐẦU VÀO</p><h2>Tổng quan dữ liệu</h2><p>Kiểm tra nguồn và chất lượng dữ liệu trước khi phân loại các cặp bản ghi.</p></div>
      <button disabled={action.busy || !w.allowed_actions.includes('start-decide')} onClick={() => void action.run(() => startDecide(w.run_id, mutation(w)))}>Bắt đầu phân loại cặp</button>
    </header>
    {action.error && <p role="alert">{action.error}</p>}
    {!data ? <ReadNotice error={read.error} retry={read.retry} /> : <>
      <div className={styles.metrics}>
        <div><span>Tổng bản ghi</span><strong>{number(total)}</strong><small>Trong toàn bộ phiên</small></div>
        <div><span>Nguồn dữ liệu</span><strong>{profile ? number(Object.keys(profile.source_counts).length) : '—'}</strong><small>Hệ thống cung cấp dữ liệu</small></div>
        <div><span>Tệp đã kiểm tra</span><strong>{number(data.files.length)}</strong><small>Tệp được nạp vào phiên</small></div>
        <div><span>Bản ghi có cảnh báo</span><strong>{warningCount == null ? '—' : number(warningCount)}</strong><small>{warningCount == null ? 'Chưa có thống kê' : `${total ? (100 * warningCount / total).toLocaleString('vi-VN', { maximumFractionDigits: 1 }) : 0}% tổng bản ghi`}</small></div>
      </div>
      {!profile ? <p role="status">Đang chuẩn bị thống kê dữ liệu. Các chỉ số sẽ xuất hiện khi chuẩn bị hoàn tất.</p> : <div className={styles.insights}>
        <section className={styles.insight}><h3>Phân bố theo nguồn</h3><p>Số bản ghi từ mỗi hệ thống.</p>
          {Object.entries(profile.source_counts).sort((a, b) => b[1] - a[1]).map(([source, count]) => <div className={styles.distribution} key={source}><div><span>{source}</span><strong>{number(count)} <small>({profile.record_count ? Math.round(100 * count / profile.record_count) : 0}%)</small></strong></div><progress aria-label={`Tỷ lệ nguồn ${source}`} value={count} max={profile.record_count || 1} /></div>)}
        </section>
        <section className={styles.insight}><h3>Mức độ đầy đủ</h3><p>Tỷ lệ bản ghi có thông tin ở từng trường.</p>
          {profile.field_completeness ? Object.entries(fields).map(([field, title]) => {
            const metric = profile.field_completeness?.[field];
            const percent = metric && profile.record_count ? 100 * metric.present / profile.record_count : 0;
            return <div className={styles.completeness} key={field}><span>{title}</span><progress aria-label={`Mức độ đầy đủ ${title}`} value={percent} max={100} /><strong title={metric ? `${number(metric.present)} có thông tin · ${number(metric.missing)} thiếu` : 'Chưa có thống kê'}>{metric ? `${Math.round(percent)}%` : '—'}</strong></div>;
          }) : <p>Chưa có thống kê độ đầy đủ.</p>}
          <small className={styles.note}>Tính trên dữ liệu gốc khác rỗng; có thông tin chưa có nghĩa là hợp lệ.</small>
        </section>
        <section className={styles.insight}><h3>Cảnh báo chất lượng</h3><p>Số bản ghi theo từng loại cảnh báo.</p>
          <ul className={styles.warnings}>{Object.entries(profile.quality_flags).sort((a, b) => b[1] - a[1]).map(([flag, count]) => <li key={flag}><span>{flags[flag] || flag}</span><strong>{number(count)}</strong></li>)}</ul>
          {!Object.keys(profile.quality_flags).length && <p>Không phát hiện cảnh báo.</p>}
          <small className={styles.note}>Một bản ghi có thể có nhiều loại cảnh báo.</small>
        </section>
      </div>}
      <details className={styles.files} data-view-key="files"><summary>{data.files.length} tệp đã kiểm tra · {data.schema.length} trường dữ liệu</summary><ul>{data.files.map((file, i) => <li key={`${file.sha256}-${i}`}><span>{file.filename}</span><strong>{number(file.row_count)} bản ghi</strong></li>)}</ul><p>Các trường: {data.schema.map(label).join(', ') || 'Chưa có thông tin'}</p></details>
      <div className={styles.tableHeading}><div><h3>Bản ghi dữ liệu</h3><p>10 bản ghi mỗi trang · {number(total)} bản ghi</p></div><div className={styles.toggle} role="group" aria-label="Cách hiển thị dữ liệu"><button aria-pressed={!normalized} onClick={() => setNormalized(false)}>Dữ liệu gốc</button><button aria-pressed={normalized} onClick={() => setNormalized(true)}>Chuẩn hóa</button></div></div>
      {read.error && <ReadNotice error={read.error} retry={read.retry} />}
      {busy && <p role="status">Đang tải trang {Math.floor(offset / 10) + 1}…</p>}
      <div className={`workflow-scroll ${styles.tableScroll}`} data-scroll-key="records" tabIndex={0} role="region" aria-label="Bảng bản ghi, có thể cuộn ngang" aria-busy={busy}>
        <table aria-label="Bản ghi dữ liệu"><thead><tr><th scope="col">STT</th><th scope="col">Nguồn / Mã bản ghi</th><th scope="col">Họ tên</th><th scope="col">Điện thoại</th><th scope="col">Email</th><th scope="col">Địa chỉ</th><th scope="col">Chất lượng / Chi tiết</th></tr></thead><tbody>
          {data.samples.items.map((record, index) => {
            const values = normalized ? record.normalized_fields : record.raw_fields;
            return <tr key={record.record_id}><td>{number(loadedOffset + index + 1)}</td><td><span className={styles.source}>{record.source_system}</span><small className={styles.recordId}>{record.external_id || record.record_id}</small></td>
              {['name', 'phone', 'email', 'address'].map(field => <td key={field}>{valueText(values[field])}</td>)}
              <td>{record.quality_flags?.length ? <details className={styles.flagDetail} data-view-key={`${record.record_id}:flags`}><summary>{record.quality_flags.length} cảnh báo</summary><ul>{record.quality_flags.map(flag => <li key={flag}>{flags[flag] || flag}</li>)}</ul></details> : <span className={styles.clean}>Không có cảnh báo</span>}<RecordDetails record={record} normalized={normalized} /></td></tr>;
          })}
        </tbody></table>
      </div>
      {!data.samples.items.length && <p>Không có bản ghi để hiển thị.</p>}
      <nav className={styles.pagination} aria-label="Phân trang dữ liệu"><span aria-live="polite">{total ? `${number(loadedOffset + 1)}–${number(loadedOffset + data.samples.items.length)} / ${number(total)} bản ghi` : '0 bản ghi'}<small>Trang {Math.floor(loadedOffset / 10) + 1} / {pageCount}</small></span><div>
        <button disabled={busy || loadedOffset === 0} onClick={() => goToPage(0)}>Trang đầu</button><button disabled={busy || loadedOffset === 0} onClick={() => goToPage(Math.max(0, loadedOffset - 10))}>Trang trước</button><button disabled={busy || loadedOffset + 10 >= total} onClick={() => goToPage(loadedOffset + 10)}>Trang sau</button><button disabled={busy || loadedOffset + 10 >= total} onClick={() => goToPage((pageCount - 1) * 10)}>Trang cuối</button>
      </div></nav>
    </>}
  </section>;
}

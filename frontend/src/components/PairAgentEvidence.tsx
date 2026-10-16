import type { PairBaseline } from '@/lib/workflowContracts';

export default function PairAgentEvidence({ baseline }: { baseline: PairBaseline }) {
  if (!baseline.embedding_evidence) return null;
  return <section aria-label="Độ tương đồng theo trường">
    {(['name', 'address'] as const).map(field => {
      const evidence = baseline.embedding_evidence?.[field];
      const label = field === 'name' ? 'Tên' : 'Địa chỉ';
      const text = evidence?.status === 'OK' && typeof evidence.cosine === 'number' && Number.isFinite(evidence.cosine)
        ? evidence.cosine.toFixed(3).replace('.', ',')
        : evidence?.status === 'ERROR' ? 'Lỗi đối chiếu'
        : evidence?.status === 'OUT_OF_SCOPE' ? 'Ngoài phạm vi' : 'Chưa có bằng chứng';
      return <p key={field}>{`${label}: ${text}`}</p>;
    })}
    {baseline.agent_disagreement && <p>Bằng chứng quy tắc và độ tương đồng chưa thống nhất.</p>}
  </section>;
}

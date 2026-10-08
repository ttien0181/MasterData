"use client";

import { useState, useEffect, useCallback } from "react";
import type { AuthUser } from "@/lib/api";
import { getPolicyYaml, simulatePolicy, type PolicySimulateResult } from "@/lib/api";
import { can } from "@/lib/permissions";

export default function PolicyAdmin({ user, runId }: { user: AuthUser; runId: string | null }) {
  const [yamlContent, setYamlContent] = useState<string>("");
  const [policyVersion, setPolicyVersion] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [simResult, setSimResult] = useState<PolicySimulateResult | null>(null);
  const [simRunning, setSimRunning] = useState(false);
  const [simError, setSimError] = useState<string | null>(null);

  const loadPolicy = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await getPolicyYaml();
      setYamlContent(resp.yaml_content);
      setPolicyVersion(resp.version);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không tải được chính sách");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void loadPolicy(); }, [loadPolicy]);

  const handleSimulate = async () => {
    if (!runId) { setSimError("Chưa chọn phiên để mô phỏng"); return; }
    setSimRunning(true);
    setSimError(null);
    setSimResult(null);
    try {
      const result = await simulatePolicy(runId, yamlContent);
      setSimResult(result);
    } catch (e) {
      setSimError(e instanceof Error ? e.message : "Mô phỏng thất bại");
    } finally {
      setSimRunning(false);
    }
  };

  const canEdit = can(user, "edit_policy");
  const canSimulate = can(user, "simulate_policy");

  return (
    <section className="card policy-admin" aria-label="Quản lý chính sách gộp">
      <div className="policy-admin-header">
        <div>
          <p className="section-kicker">CHÍNH SÁCH</p>
          <h2>Cấu hình quy tắc phân giải</h2>
          {policyVersion && <small>Phiên bản: <code>{policyVersion}</code></small>}
        </div>
        <div className="policy-admin-actions">
          {canSimulate && (
            <button
              type="button"
              className="button outline"
              disabled={simRunning || !runId || !yamlContent}
              onClick={() => void handleSimulate()}
            >
              {simRunning ? "Đang mô phỏng…" : "Mô phỏng tác động"}
            </button>
          )}
          {!canEdit && <span className="muted-copy">Chỉ xem (không có quyền chỉnh sửa)</span>}
        </div>
      </div>

      {error && <p className="error-message">{error}</p>}

      {loading ? (
        <p role="status" className="muted-copy">Đang tải chính sách…</p>
      ) : (
        <div className="policy-yaml-editor">
          <label htmlFor="policy-yaml-textarea" className="visually-hidden">Nội dung YAML chính sách</label>
          <textarea
            id="policy-yaml-textarea"
            className="policy-yaml-textarea"
            value={yamlContent}
            readOnly={!canEdit}
            onChange={(e) => setYamlContent(e.target.value)}
            rows={30}
            spellCheck={false}
            aria-label="Nội dung YAML chính sách"
          />
        </div>
      )}

      {simError && <p className="error-message">{simError}</p>}

      {simResult && (
        <div className="policy-sim-result card">
          <h3>Kết quả mô phỏng</h3>
          <div className="stat-grid">
            <div className="stat-card">
              <span className="stat-value">{simResult.total_pairs.toLocaleString("vi-VN")}</span>
              <span className="stat-label">Tổng cặp</span>
            </div>
            <div className="stat-card stat-primary">
              <span className="stat-value">{simResult.changed.toLocaleString("vi-VN")}</span>
              <span className="stat-label">Thay đổi quyết định</span>
            </div>
            <div className="stat-card">
              <span className="stat-value">{simResult.review_to_merge.toLocaleString("vi-VN")}</span>
              <span className="stat-label">Review → Gộp</span>
            </div>
            <div className="stat-card">
              <span className="stat-value">{simResult.merge_to_review.toLocaleString("vi-VN")}</span>
              <span className="stat-label">Gộp → Review</span>
            </div>
          </div>
          {simResult.details.length > 0 && (
            <details className="policy-sim-details">
              <summary>Chi tiết thay đổi ({simResult.details.filter(d => d.changed).length} cặp)</summary>
              <table className="policy-sim-table">
                <thead>
                  <tr>
                    <th>Cặp</th>
                    <th>Nhóm</th>
                    <th>Hiện tại</th>
                    <th>Mới</th>
                  </tr>
                </thead>
                <tbody>
                  {simResult.details.filter(d => d.changed).slice(0, 100).map(d => (
                    <tr key={d.pair_id}>
                      <td><code>{d.pair_id.slice(0, 8)}…</code></td>
                      <td>{d.primary_group}</td>
                      <td>{d.current_decision}</td>
                      <td>{d.new_decision}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
        </div>
      )}
    </section>
  );
}

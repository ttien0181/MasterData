import type { ReactNode } from "react";
import type { Candidate } from "./contracts";

export const fields = ["name", "phone", "email", "address", "tax_id"] as const;
export const labels: Record<string, string> = {
  name: "Tên khách hàng",
  phone: "Điện thoại",
  email: "Email",
  address: "Địa chỉ",
  tax_id: "Mã số thuế",
};

const matcherFields = new Set(["name", "phone", "email", "tax_id"]);

export function compareField(field: string, candidate: Candidate) {
  if (candidate.evidence.some((signal) => signal.field === field)) return { kind: "match", label: "✓ Tín hiệu khớp" };
  const left = candidate.left.raw_fields[field]?.trim();
  const right = candidate.right.raw_fields[field]?.trim();
  if (!left && !right) return { kind: "neutral", label: "Cả hai chưa có dữ liệu" };
  if (!left || !right) return { kind: "missing", label: "Thiếu dữ liệu một phía" };
  if (left.localeCompare(right, undefined, { sensitivity: "accent" }) !== 0) {
    return { kind: "different", label: matcherFields.has(field) ? "Không khớp chính xác" : "Giá trị khác" };
  }
  return { kind: "neutral", label: "Không dùng làm tín hiệu" };
}

function splitCharacters(value: string) {
  if (typeof Intl.Segmenter !== "undefined") {
    return Array.from(new Intl.Segmenter("vi", { granularity: "grapheme" }).segment(value), (part) => part.segment);
  }
  return Array.from(value);
}

export function highlightDifferences(value: string, other: string): ReactNode {
  if (value === other) return value;
  const left = splitCharacters(other);
  const right = splitCharacters(value);

  let changed: boolean[] = [];

  if (left.length * right.length > 40000) {
    let prefix = 0;
    while (prefix < left.length && prefix < right.length && left[prefix] === right[prefix]) prefix++;
    let suffix = 0;
    while (suffix < left.length - prefix && suffix < right.length - prefix && left[left.length - suffix - 1] === right[right.length - suffix - 1]) suffix++;
    changed = right.map((_, index) => index >= prefix && index < right.length - suffix);
  } else {
    const lengths = Array.from({ length: left.length + 1 }, () => new Uint16Array(right.length + 1));
    for (let i = left.length - 1; i >= 0; i--) {
      for (let j = right.length - 1; j >= 0; j--) {
        lengths[i][j] = left[i] === right[j] ? lengths[i + 1][j + 1] + 1 : Math.max(lengths[i + 1][j], lengths[i][j + 1]);
      }
    }

    let i = 0;
    let j = 0;
    while (i < left.length || j < right.length) {
      if (i < left.length && j < right.length && left[i] === right[j]) { changed.push(false); i++; j++; }
      else if (i < left.length && (j === right.length || lengths[i + 1][j] >= lengths[i][j + 1])) i++;
      else { changed.push(true); j++; }
    }
  }

  const chunks: ReactNode[] = [];
  let currentText = "";
  let isCurrentMark = false;

  for (let idx = 0; idx < right.length; idx++) {
    const isMark = Boolean(changed[idx]);
    if (idx === 0) {
      isCurrentMark = isMark;
      currentText = right[idx];
    } else if (isMark === isCurrentMark) {
      currentText += right[idx];
    } else {
      chunks.push(isCurrentMark ? <mark key={chunks.length}>{currentText}</mark> : currentText);
      isCurrentMark = isMark;
      currentText = right[idx];
    }
  }

  if (currentText) chunks.push(isCurrentMark ? <mark key={chunks.length}>{currentText}</mark> : currentText);
  return chunks;
}

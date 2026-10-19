// Public, versioned sample assets. No Supabase credentials are needed in the browser.
const SAMPLE_BASE = "https://idqebipkhnmvpzmmpvzg.supabase.co/storage/v1/object/public/sample-data/v1";
export const SAMPLE_FILES = [
  { name: "erp-2.csv", bytes: 1904946, rows: 12500 },
  { name: "bill-1.csv", bytes: 1912749, rows: 12500 },
] as const;

export async function loadSampleFiles(signal: AbortSignal): Promise<File[]> {
  const controller = new AbortController();
  const cancel = () => controller.abort();
  signal.addEventListener("abort", cancel, { once: true });
  if (signal.aborted) cancel();
  const timer = setTimeout(cancel, 30_000);
  try {
    return await Promise.all(SAMPLE_FILES.map(async ({ name, bytes }) => {
      const response = await fetch(`${SAMPLE_BASE}/${name}`, {
        signal: controller.signal,
        credentials: "omit",
      });
      if (!response.ok) throw new Error(`Không lấy được ${name} (${response.status}). Hãy thử lại.`);
      const blob = await response.blob();
      if (blob.size !== bytes) throw new Error(`File mẫu ${name} chưa tải đủ hoặc đã thay đổi. Hãy thử lại.`);
      return new File([blob], name, { type: "text/csv", lastModified: 0 });
    }));
  } catch (error) {
    controller.abort();
    if (error instanceof Error && error.name !== "AbortError" && error.name !== "TypeError") throw error;
    throw new Error("Không lấy được dữ liệu mẫu. Kiểm tra kết nối và bấm thử lại.");
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", cancel);
  }
}

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ResolutionIntake from "./ResolutionIntake";
import { ApiError } from "@/lib/api";
import type { UploadSession } from "@/lib/contracts";

const api = vi.hoisted(() => ({
  commitUploadPart: vi.fn(), commitUploadSession: vi.fn(), createUploadSession: vi.fn(), deleteUploadSession: vi.fn(),
  getUploadSession: vi.fn(), getUploadSessions: vi.fn(), presignUploadParts: vi.fn(), putPartToStorage: vi.fn(),
  renameUploadSession: vi.fn(), replaceUploadSessionFile: vi.fn(), uploadResolutionUploadPart: vi.fn(),
  validateUploadSession: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error { constructor(message: string, readonly status: number) { super(message); } },
  ...api,
}));

function uploadSession(size: number, status: UploadSession["status"], receivedSize = 0,
                       uploadMode: UploadSession["upload_mode"] = "proxy"): UploadSession {
  return {
    session_id: "upload-1", name: null, status, total_bytes: size, committed_run_id: null, upload_mode: uploadMode,
    committed_profile: null, error_message: null, created_at: "2026-10-07T00:00:00Z", updated_at: "2026-10-07T00:00:00Z",
    files: [{ file_id: "file-1", position: 0, filename: "large.csv", expected_size: size,
      received_size: receivedSize, status, sha256: null, row_count: status === "READY" ? 1 : null,
      source_counts: {}, validation_error: null,
      parts: receivedSize ? [{ part_number: 0, byte_size: receivedSize, sha256: "00".repeat(32) }] : [],
    }],
  };
}

describe("ResolutionIntake", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.stubGlobal("crypto", { subtle: { digest: vi.fn(async () => new Uint8Array(32).buffer) } });
    api.getUploadSessions.mockResolvedValue({ items: [] });
    api.createUploadSession.mockImplementation(async (_name: string, files: File[]) => uploadSession(files[0].size, "UPLOADING"));
    api.uploadResolutionUploadPart.mockResolvedValue(undefined);
    api.getUploadSession.mockImplementation(async (id: string) => ({ ...uploadSession(5 * 1024 * 1024 + 1, "UPLOADING", 5 * 1024 * 1024 + 1), session_id: id }));
    api.validateUploadSession.mockImplementation(async (id: string) => {
      const ready = { ...uploadSession(5 * 1024 * 1024 + 1, "READY", 5 * 1024 * 1024 + 1), session_id: id };
      api.getUploadSession.mockResolvedValue(ready);
      return ready;
    });
    api.commitUploadSession.mockResolvedValue({ run_id: "run-1", status: "UPLOADED", profile: {
      row_count: 1, file_count: 1, source_counts: { CRM: 1 },
      files: [{ filename: "large.csv", row_count: 1, sha256: "hash" }],
    } });
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); localStorage.clear(); });

  it("uploads and commits a file larger than the old 5 MiB limit", async () => {
    const onRunCreated = vi.fn().mockResolvedValue(undefined);
    const { container } = render(<ResolutionIntake onRunCreated={onRunCreated} />);
    expect(screen.getByText(/Không giới hạn dung lượng từng file/)).toBeInTheDocument();

    const size = 5 * 1024 * 1024 + 1;
    const file = new File([new Uint8Array(size)], "large.csv", { type: "text/csv" });
    fireEvent.change(container.querySelector('input[type="file"][multiple]')!, { target: { files: [file] } });
    expect(screen.queryByRole("button", { name: /Tạo draft/ })).not.toBeInTheDocument();

    await waitFor(() => expect(api.uploadResolutionUploadPart).toHaveBeenCalledTimes(1));
    expect(api.uploadResolutionUploadPart).toHaveBeenCalledWith(
      "upload-1", "file-1", 0, expect.any(Blob), "00".repeat(32), expect.any(Function),
    );
    await waitFor(() => expect(screen.getByRole("button", { name: "Bắt đầu xử lý" })).toBeEnabled());
    expect(api.commitUploadSession).not.toHaveBeenCalled();
    fireEvent.change(screen.getByPlaceholderText("Mặc định dùng tên file"), { target: { value: "Customer tháng 10" } });
    fireEvent.click(screen.getByRole("button", { name: "Bắt đầu xử lý" }));
    await waitFor(() => expect(onRunCreated).toHaveBeenCalledWith(expect.objectContaining({ run_id: "run-1" })));
    expect(api.validateUploadSession).toHaveBeenCalledWith("upload-1");
    expect(api.commitUploadSession).toHaveBeenCalledWith("upload-1");
    expect(api.renameUploadSession).toHaveBeenCalledWith("upload-1", "Customer tháng 10");
    expect(api.renameUploadSession.mock.invocationCallOrder[0]).toBeLessThan(api.commitUploadSession.mock.invocationCallOrder[0]);
  });

  it("automatically uploads three dropped CSVs into one session, then follows validation to its result", async () => {
    const files = ["crm.csv", "erp.csv", "billing.csv"].map((name) => new File(["csv"], name));
    const pending = uploadSession(9, "UPLOADING");
    pending.files = files.map((file, index) => ({ ...pending.files[0], file_id: `file-${index}`,
      filename: file.name, position: index, expected_size: file.size }));
    const uploaded = { ...pending, files: pending.files.map((file) => ({ ...file, received_size: file.expected_size })) };
    const validating: UploadSession = { ...uploaded, status: "VALIDATING" };
    const ready: UploadSession = { ...uploaded, status: "READY" };
    api.createUploadSession.mockResolvedValue(pending);
    let validationStarted = false;
    let polls = 0;
    api.validateUploadSession.mockImplementation(async () => { validationStarted = true; return validating; });
    api.getUploadSession.mockImplementation(async () => {
      if (!validationStarted) return uploaded;
      polls += 1;
      return polls < 2 ? validating : ready;
    });
    const { container } = render(<ResolutionIntake onRunCreated={vi.fn()} />);
    fireEvent.drop(container.querySelector(".resolution-dropzone")!, { dataTransfer: { files } });

    expect(await screen.findByText("Đang kiểm tra dữ liệu trong các file CSV. File lớn có thể mất vài phút; trang sẽ tự cập nhật."))
      .toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Đang kiểm tra dữ liệu…" })).toBeDisabled();
    expect(api.createUploadSession).toHaveBeenCalledExactlyOnceWith("", files);
    expect(api.uploadResolutionUploadPart).toHaveBeenCalledTimes(3);
    for (let index = 0; index < files.length; index += 1) {
      expect(api.uploadResolutionUploadPart).toHaveBeenNthCalledWith(index + 1,
        "upload-1", `file-${index}`, 0, expect.any(Blob), "00".repeat(32), expect.any(Function));
    }
    await waitFor(() => expect(screen.getByRole("button", { name: "Bắt đầu xử lý" })).toBeEnabled(), { timeout: 4000 });
    expect(screen.getByText("Kiểm tra xong: dữ liệu hợp lệ. Bạn có thể bắt đầu xử lý.")).toBeInTheDocument();
    expect(api.commitUploadSession).not.toHaveBeenCalled();
  });

  it("keeps uploading the other files when one fails, and names the file to resume", async () => {
    const files = ["crm.csv", "erp.csv", "billing.csv"].map((name) => new File(["csv"], name));
    const pending = uploadSession(9, "UPLOADING");
    pending.files = files.map((file, index) => ({ ...pending.files[0], file_id: `file-${index}`,
      filename: file.name, position: index, expected_size: file.size }));
    api.createUploadSession.mockResolvedValue(pending);
    api.getUploadSession.mockResolvedValue(pending);
    api.uploadResolutionUploadPart.mockImplementation(async (_sessionId: string, fileId: string) => {
      if (fileId === "file-1") throw new Error("Mất kết nối");
    });
    const { container } = render(<ResolutionIntake onRunCreated={vi.fn()} />);
    fireEvent.drop(container.querySelector(".resolution-dropzone")!, { dataTransfer: { files } });

    expect(await screen.findByText(/Chưa tải xong 1 file: erp\.csv/)).toBeInTheDocument();
    expect(screen.getByText(/Tải lên gián đoạn: Mất kết nối/)).toBeInTheDocument();
    expect(api.uploadResolutionUploadPart).toHaveBeenCalledTimes(3);
    expect(api.validateUploadSession).not.toHaveBeenCalled();
  });

  it("rejects unsupported selections and too many files without uploading a subset", async () => {
    const { container } = render(<ResolutionIntake onRunCreated={vi.fn()} />);
    const input = container.querySelector('input[type="file"][multiple]')!;
    fireEvent.change(input, { target: { files: [new File(["csv"], "one.csv"), new File(["txt"], "two.txt")] } });
    expect(screen.getByText(/Chỉ hỗ trợ file CSV/)).toBeInTheDocument();
    fireEvent.change(input, { target: { files: Array.from({ length: 11 }, (_, index) => new File(["csv"], `${index}.csv`)) } });
    expect(screen.getByText("Mỗi đợt xử lý hiện hỗ trợ tối đa 10 file CSV.")).toBeInTheDocument();
    expect(api.createUploadSession).not.toHaveBeenCalled();
    expect(api.uploadResolutionUploadPart).not.toHaveBeenCalled();
  });

  it("keeps interrupted uploads available to resume and allows retrying commit", async () => {
    const onRunCreated = vi.fn().mockResolvedValue(undefined);
    const file = new File(["csv"], "large.csv");
    api.getUploadSession.mockResolvedValueOnce(uploadSession(3, "UPLOADING"))
      .mockResolvedValue(uploadSession(3, "UPLOADING", 3));
    api.uploadResolutionUploadPart.mockRejectedValueOnce(new Error("Mất kết nối"));
    api.commitUploadSession.mockRejectedValueOnce(new Error("Không thể bắt đầu. Hãy thử lại."));
    const { container } = render(<ResolutionIntake onRunCreated={onRunCreated} />);
    fireEvent.change(container.querySelector('input[type="file"][multiple]')!, { target: { files: [file] } });
    await waitFor(() => expect(screen.getByRole("button", { name: "Để sau" })).toBeEnabled());
    expect(screen.getByText(/Mất kết nối/)).toBeInTheDocument();
    fireEvent.change(container.querySelector('.upload-replace-control input')!, { target: { files: [file] } });
    await waitFor(() => expect(screen.getByRole("button", { name: "Bắt đầu xử lý" })).toBeEnabled());
    expect(api.createUploadSession).toHaveBeenCalledTimes(1);
    expect(api.uploadResolutionUploadPart).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name: "Bắt đầu xử lý" }));
    await screen.findByText("Không thể bắt đầu. Hãy thử lại.");
    expect(screen.getByRole("button", { name: "Bắt đầu xử lý" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Bắt đầu xử lý" }));
    await waitFor(() => expect(onRunCreated).toHaveBeenCalledTimes(1));
    expect(api.commitUploadSession).toHaveBeenCalledTimes(2);
  });

  it("restores the saved committed upload even when the open-upload list excludes it", async () => {
    localStorage.setItem("masterone.upload-session.v1.user-1", "upload-1");
    api.getUploadSessions.mockResolvedValue({ items: [] });
    api.getUploadSession.mockResolvedValue({ ...uploadSession(3, "COMMITTED", 3), committed_run_id: "run-1" });
    const onRunCreated = vi.fn().mockResolvedValue(undefined);
    render(<ResolutionIntake userId="user-1" onRunCreated={onRunCreated} />);
    const resume = await screen.findByRole("button", { name: "Tiếp tục đợt xử lý" });
    expect(resume).toBeEnabled();
    fireEvent.click(resume);
    await waitFor(() => expect(onRunCreated).toHaveBeenCalledWith(expect.objectContaining({ run_id: "run-1" })));
    expect(api.createUploadSession).not.toHaveBeenCalled();
    expect(localStorage.getItem("masterone.upload-session.v1.user-1")).toBeNull();
  });

  it("refreshes a restored validating upload until it is ready", async () => {
    const validating = uploadSession(3, "VALIDATING", 3);
    const ready = uploadSession(3, "READY", 3);
    localStorage.setItem("masterone.upload-session.v1.user-1", validating.session_id);
    api.getUploadSessions.mockResolvedValue({ items: [validating] });
    api.getUploadSession.mockResolvedValueOnce(validating).mockResolvedValue(ready);

    render(<ResolutionIntake userId="user-1" onRunCreated={vi.fn()} />);

    await waitFor(() => expect(api.getUploadSession).toHaveBeenCalledTimes(2), { timeout: 3000 });
    await waitFor(() => expect(screen.getByRole("button", { name: "Bắt đầu xử lý" })).toBeEnabled());
  });

  it("restores partial progress after remount and uploads only the missing chunk", async () => {
    const chunkSize = 8 * 1024 * 1024;
    const partial = uploadSession(chunkSize + 3, "UPLOADING", chunkSize);
    localStorage.setItem("masterone.upload-session.v1.user-1", partial.session_id);
    api.getUploadSessions.mockResolvedValue({ items: [partial] });
    api.getUploadSession.mockResolvedValueOnce(partial).mockResolvedValue(uploadSession(chunkSize + 3, "UPLOADING", chunkSize + 3));
    render(<ResolutionIntake userId="user-1" onRunCreated={vi.fn()} />);
    const fileInput = await screen.findByLabelText("Đổi hoặc tiếp tục tải large.csv");
    await waitFor(() => expect(fileInput).toBeEnabled());
    fireEvent.change(fileInput, { target: { files: [new File([new Uint8Array(chunkSize + 3)], "large.csv")] } });
    await waitFor(() => expect(screen.getByRole("button", { name: "Bắt đầu xử lý" })).toBeEnabled());
    expect(api.uploadResolutionUploadPart).toHaveBeenCalledExactlyOnceWith("upload-1", "file-1", 1, expect.any(Blob), "00".repeat(32), expect.any(Function));
    expect(api.createUploadSession).not.toHaveBeenCalled();
  });

  it("does not restore another account's upload hint", async () => {
    localStorage.setItem("masterone.upload-session.v1.other-user", "other-upload");
    render(<ResolutionIntake userId="user-1" onRunCreated={vi.fn()} />);
    await waitFor(() => expect(screen.getByLabelText("Chọn các file CSV")).toBeEnabled());
    expect(api.getUploadSession).not.toHaveBeenCalled();
  });

  describe("direct upload to storage", () => {
    const chunkSize = 8 * 1024 * 1024;
    const presignEcho = () => api.presignUploadParts.mockImplementation(async (_id: string, _fileId: string,
      parts: { part_number: number }[]) => ({
      parts: parts.map((part) => ({ part_number: part.part_number, upload_url: `https://r2.test/${part.part_number}`,
        headers: { "Content-Type": "application/octet-stream", "x-amz-checksum-sha256": "AAAA" }, expires_in: 900 })),
    }));

    it("sends every part to R2 through a presigned URL, three at a time, then commits each one", async () => {
      const size = 2 * chunkSize + 5;
      let active = 0;
      let peak = 0;
      api.createUploadSession.mockResolvedValue(uploadSession(size, "UPLOADING", 0, "direct"));
      api.getUploadSession.mockResolvedValue(uploadSession(size, "UPLOADING", size, "direct"));
      presignEcho();
      api.putPartToStorage.mockImplementation(async () => {
        active += 1;
        peak = Math.max(peak, active);
        await new Promise((resolve) => setTimeout(resolve, 10));
        active -= 1;
      });
      api.commitUploadPart.mockResolvedValue({ part_number: 0, byte_size: 0, sha256: "", received_size: 0 });
      const { container } = render(<ResolutionIntake onRunCreated={vi.fn()} />);
      fireEvent.change(container.querySelector('input[type="file"][multiple]')!, {
        target: { files: [new File([new Uint8Array(size)], "large.csv")] },
      });

      await waitFor(() => expect(api.commitUploadPart).toHaveBeenCalledTimes(3));
      expect(api.uploadResolutionUploadPart).not.toHaveBeenCalled();
      expect(api.presignUploadParts).toHaveBeenCalledTimes(3);
      expect(peak).toBe(3);
      expect(api.commitUploadPart).toHaveBeenCalledWith("upload-1", "file-1", 0, chunkSize, "00".repeat(32));
      expect(api.commitUploadPart).toHaveBeenCalledWith("upload-1", "file-1", 1, chunkSize, "00".repeat(32));
      expect(api.commitUploadPart).toHaveBeenCalledWith("upload-1", "file-1", 2, 5, "00".repeat(32));
      expect(api.putPartToStorage.mock.calls[0][0]).toMatchObject({ upload_url: "https://r2.test/0" });
      await waitFor(() => expect(screen.getByRole("button", { name: "Bắt đầu xử lý" })).toBeEnabled());
    });

    it("asks for a fresh URL and retries when R2 reports the presigned URL expired", async () => {
      api.createUploadSession.mockResolvedValue(uploadSession(3, "UPLOADING", 0, "direct"));
      api.getUploadSession.mockResolvedValue(uploadSession(3, "UPLOADING", 3, "direct"));
      presignEcho();
      api.putPartToStorage.mockRejectedValueOnce(new ApiError("expired", 403)).mockResolvedValue(undefined);
      api.commitUploadPart.mockResolvedValue({ part_number: 0, byte_size: 3, sha256: "", received_size: 3 });
      const { container } = render(<ResolutionIntake onRunCreated={vi.fn()} />);
      fireEvent.change(container.querySelector('input[type="file"][multiple]')!, {
        target: { files: [new File(["csv"], "large.csv")] },
      });

      await waitFor(() => expect(api.commitUploadPart).toHaveBeenCalledTimes(1));
      expect(api.presignUploadParts).toHaveBeenCalledTimes(2);
      expect(api.putPartToStorage).toHaveBeenCalledTimes(2);
    });

    it("does not retry a checksum rejection, because the same bytes would fail again", async () => {
      api.createUploadSession.mockResolvedValue(uploadSession(3, "UPLOADING", 0, "direct"));
      presignEcho();
      api.putPartToStorage.mockRejectedValue(new ApiError("Kho lưu trữ từ chối khối (400)", 400));
      const { container } = render(<ResolutionIntake onRunCreated={vi.fn()} />);
      fireEvent.change(container.querySelector('input[type="file"][multiple]')!, {
        target: { files: [new File(["csv"], "large.csv")] },
      });

      await screen.findByText(/Kho lưu trữ từ chối khối \(400\)/);
      expect(api.presignUploadParts).toHaveBeenCalledTimes(1);
      expect(api.commitUploadPart).not.toHaveBeenCalled();
    });
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";
import { loadSampleFiles, SAMPLE_FILES } from "./sampleDatasets";

afterEach(() => vi.unstubAllGlobals());

describe("sample datasets", () => {
  it("downloads public CSVs as named Files without credentials", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      const file = SAMPLE_FILES.find((item) => url.endsWith(item.name))!;
      return { ok: true, blob: async () => new Blob([new Uint8Array(file.bytes)]) };
    });
    vi.stubGlobal("fetch", fetchMock);
    const files = await loadSampleFiles(new AbortController().signal);
    expect(files.map((file) => [file.name, file.size, file.type])).toEqual(
      SAMPLE_FILES.map((file) => [file.name, file.bytes, "text/csv"]));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][0]).toContain("/object/public/sample-data/v1/");
    expect(fetchMock).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ credentials: "omit" }));
  });

  it("rejects the entire set and cancels remaining downloads on HTTP failure", async () => {
    const fetchMock = vi.fn(async () => ({ ok: false, status: 404 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(loadSampleFiles(new AbortController().signal)).rejects.toThrow("404");
  });

  it("rejects incomplete files instead of uploading them", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, blob: async () => new Blob(["partial"]) })));
    await expect(loadSampleFiles(new AbortController().signal)).rejects.toThrow("chưa tải đủ");
  });
});

import { describe, expect, it } from "vitest";
import { differenceSegments, groupFieldOptions } from "./resolutionFields";

describe("resolution field display", () => {
  it("groups identical values while retaining every source record", () => {
    const groups = groupFieldOptions([
      { source_record_id: "a", source_system: "BILL", external_id: "A", value: "Quảng Nam" },
      { source_record_id: "b", source_system: "CRM", external_id: "B", value: "Quảng Nam" },
      { source_record_id: "c", source_system: "ERP", external_id: "C", value: "Quang Nam" },
    ]);
    expect(groups).toHaveLength(2);
    expect(groups[0].sources.map((source) => source.source_record_id)).toEqual(["a", "b"]);
  });

  it("marks distinct characters against all alternatives", () => {
    const segments = differenceSegments("aXbYc", ["abc", "aXbc"]);
    expect(segments.filter((segment) => segment.different).map((segment) => segment.text)).toEqual(["X", "Y"]);
    expect(segments.map((segment) => segment.text).join("")).toBe("aXbYc");
  });
});

import { describe, expect, it } from "vitest";
import {
  readDimensions,
  readStringArray,
  safePublicUrl,
} from "./public-contract";
describe("public URLs and shareable filters", () => {
  it("rejects executable links and URLs carrying credentials", () => {
    expect(safePublicUrl("javascript:alert(1)")).toBeNull();
    expect(
      safePublicUrl("https://token:secret@example.com/report.pdf"),
    ).toBeNull();
    expect(safePublicUrl("file:///private/report.pdf")).toBeNull();
    expect(safePublicUrl("https://example.com/report.pdf")).toBe(
      "https://example.com/report.pdf",
    );
    expect(
      safePublicUrl("/api/v1/sources/abc123/document?dataset_version=2"),
    ).toBe("/api/v1/sources/abc123/document?dataset_version=2");
    expect(safePublicUrl("/v1/sources/abc123/document?dataset_version=2")).toBe(
      "/api/v1/sources/abc123/document?dataset_version=2",
    );
    expect(safePublicUrl("//private.example/report.pdf")).toBeNull();
    expect(safePublicUrl("/api/internal/v1/candidates")).toBeNull();
  });
  it("recovers malformed filters without guessing comparison axes", () => {
    expect(readStringArray("broken")).toEqual([]);
    expect(readStringArray('["a","a",1]')).toEqual(["a"]);
    expect(readDimensions('["x"]')).toEqual({});
    expect(readDimensions('{"pollutant":"SOx","bad":1}')).toEqual({
      pollutant: "SOx",
    });
  });
});

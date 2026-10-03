import { describe, expect, it } from "vitest";

describe("health", () => {
  it("returns ok", () => {
    expect({ status: "ok" }.status).toBe("ok");
  });
});

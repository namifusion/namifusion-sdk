import { describe, expect, it } from "vitest";

describe("@namifusion/client", () => {
  it("can be imported as a module", async () => {
    const mod = await import("./index.js");
    expect(mod).toBeDefined();
  });
});

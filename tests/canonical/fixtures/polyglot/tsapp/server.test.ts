// Jest-style tests (is_test via the .test.ts suffix).
import { Friendly, joinHi } from "./server";

describe("Friendly", () => {
  it("greets by name", () => {
    expect(new Friendly().greet("ada")).toBe("hi ada");
  });

  it("joins parts", () => {
    expect(joinHi("hi", "ada")).toBe("hi ada");
  });
});

import { describe, it, expect } from "vitest";

import { MARQUEE_MAX_CHARS, normalizeMarqueeText } from "./marqueeText";

describe("normalizeMarqueeText", () => {
  it("collapses newlines and runs of whitespace into single spaces", () => {
    expect(normalizeMarqueeText("  fix\r\n  the\t\tbug \n")).toBe("fix the bug");
  });

  it("leaves a normal task description untouched", () => {
    const task = "Refactoring the summary service";
    expect(normalizeMarqueeText(task)).toBe(task);
  });

  it("caps an agent prompt so its texture stays within WebGL limits", () => {
    // Measured in the wild: an 11,748-char subagent prompt reached the marquee
    // and Pixi asked the GPU for a 262144x64 canvas — far past MAX_TEXTURE_SIZE
    // (16384 on most GPUs), so every upload failed with "no canvas".
    const prompt = "You are applying the fix wave from the final review. ".repeat(226);
    expect(prompt.length).toBeGreaterThan(11_000);

    const out = normalizeMarqueeText(prompt);

    expect(out.length).toBeLessThanOrEqual(MARQUEE_MAX_CHARS);
    // fontSize 18 at resolution 2 ⇒ ~22px per monospace char.
    expect(out.length * 22).toBeLessThan(16_384);
    expect(out.endsWith("…")).toBe(true);
  });

  it("does not add an ellipsis to text that fits", () => {
    const out = normalizeMarqueeText("short task");
    expect(out.endsWith("…")).toBe(false);
  });
});

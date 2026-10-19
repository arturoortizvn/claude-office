import { describe, it, expect } from "vitest";
import { getPlanSummary } from "@/components/game/whiteboard/planSummary";
import type { TodoItem } from "@/types";

function planTodo(
  status: TodoItem["status"],
  extra: Record<string, unknown> = {},
): TodoItem {
  return {
    content: "Task",
    status,
    metadata: { source: "plan", plan_title: "Stream Auth", ...extra },
  };
}

describe("getPlanSummary", () => {
  it("returns null for empty todos", () => {
    expect(getPlanSummary([])).toBeNull();
  });

  it("returns null when any todo is not from a plan", () => {
    const todos: TodoItem[] = [
      planTodo("pending"),
      { content: "Manual", status: "pending" },
    ];
    expect(getPlanSummary(todos)).toBeNull();
  });

  it("counts completed tasks", () => {
    const todos = [
      planTodo("completed"),
      planTodo("in_progress"),
      planTodo("pending"),
    ];
    expect(getPlanSummary(todos)).toEqual({
      title: "Stream Auth",
      done: 1,
      total: 3,
    });
  });

  it('falls back to "Plan" without a title', () => {
    const todos: TodoItem[] = [
      { content: "T", status: "pending", metadata: { source: "plan" } },
    ];
    expect(getPlanSummary(todos)?.title).toBe("Plan");
  });
});

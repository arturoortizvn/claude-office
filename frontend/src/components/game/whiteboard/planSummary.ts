import type { TodoItem } from "@/types";

export interface PlanSummary {
  title: string;
  done: number;
  total: number;
}

export function getPlanSummary(todos: TodoItem[]): PlanSummary | null {
  if (todos.length === 0) return null;
  if (!todos.every((t) => t.metadata?.source === "plan")) return null;

  const rawTitle = todos[0].metadata?.plan_title;
  return {
    title: typeof rawTitle === "string" && rawTitle ? rawTitle : "Plan",
    done: todos.filter((t) => t.status === "completed").length,
    total: todos.length,
  };
}

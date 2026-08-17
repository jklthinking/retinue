import { api } from "../api";
import type { TodoHome, TodoItem, TodoProposal } from "../types";

/** Thin wrappers over the private-todo REST surface; see server/routers/todos.py. */

export function fetchTodoHome(): Promise<TodoHome> {
  return api.get<TodoHome>("/api/todos/home");
}

export function fetchTodos(): Promise<TodoItem[]> {
  return api.get<{ todos: TodoItem[] }>("/api/todos").then((body) => body.todos);
}

export function confirmTodoProposal(proposalId: string): Promise<TodoItem> {
  return api.post<TodoItem>(`/api/todos/proposals/${proposalId}/confirm`);
}

export function rejectTodoProposal(proposalId: string, note = ""): Promise<TodoProposal> {
  return api.post<TodoProposal>(`/api/todos/proposals/${proposalId}/reject`, { note });
}

export function completeTodoItem(itemId: string): Promise<TodoItem> {
  return api.post<TodoItem>(`/api/todos/${itemId}/complete`);
}

/** Postpone an item to a new local due date (YYYY-MM-DD). */
export function snoozeTodoItem(itemId: string, dueAt: string): Promise<TodoItem> {
  return api.post<TodoItem>(`/api/todos/${itemId}/snooze`, { due_at: dueAt });
}

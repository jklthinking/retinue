import { api } from "../api";
import type { TodoHome, TodoItem, TodoProposal } from "../types";

/** Thin wrappers over the private-todo REST surface; see server/routers/todos.py. */

export interface TodoCreateInput {
  title: string;
  notes?: string;
  due_at?: string | null;
  event_on?: string | null;
  parent_id?: string | null;
  progress?: number;
}

export function fetchTodoHome(): Promise<TodoHome> {
  return api.get<TodoHome>("/api/todos/home");
}

export function fetchTodos(): Promise<TodoItem[]> {
  return api.get<{ todos: TodoItem[] }>("/api/todos").then((body) => body.todos);
}

export function createTodoItem(body: TodoCreateInput): Promise<TodoItem> {
  return api.post<TodoItem>("/api/todos", body);
}

export function updateTodoItem(
  itemId: string,
  body: { progress?: number; title?: string; due_at?: string | null; event_on?: string | null }
): Promise<TodoItem> {
  return api.post<TodoItem>(`/api/todos/${itemId}/update`, body);
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

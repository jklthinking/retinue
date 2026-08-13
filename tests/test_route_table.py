"""Pin the HTTP route table so the create_app split cannot move behaviour.

Written against the pre-refactor single-closure ``server/app.py``: this test
passed before any router module existed and must pass unchanged after, which is
what makes the refactor a refactor. It asserts the full set of (path, methods,
endpoint name, dependency chain) tuples — the dependencies are the
status-code-bearing part (``require_auth`` raises 401/403, ``require_admin``
403) — plus the precedence rule that the panel mount and the SPA catch-all are
registered after every API route.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.routing import APIRoute, Mount, iter_route_contexts

from server.app import create_app
from server.db import make_session_factory


def _dep_names(dependant) -> tuple[str, ...]:
    names: list[str] = []
    for dep in dependant.dependencies:
        call = getattr(dep, "call", None)
        names.append(getattr(call, "__name__", repr(call)))
        names.extend(_dep_names(dep))
    return tuple(names)


def _api_routes(app) -> list[APIRoute]:
    # iter_route_contexts flattens both directly-registered routes and routers
    # included with include_router (deferred in newer FastAPI), so this test
    # reads the same table before and after the create_app split.
    return [
        context.route
        for context in iter_route_contexts(app.routes)
        if isinstance(context.route, APIRoute)
    ]


# (path, methods, endpoint name, dependency chain) for every API route.
EXPECTED_ROUTES = {
    ("/api/orientation/context", ("GET",), "orientation_context", ("require_auth", "get_db", "get_db")),
    ("/api/data-catalog", ("GET",), "data_catalog", ("require_auth", "get_db", "get_db")),
    ("/api/health", ("GET",), "health", ()),
    ("/api/login-config", ("GET",), "login_config", ()),
    ("/api/status", ("GET",), "status", ("require_auth", "get_db", "get_db")),
    ("/api/summary", ("GET",), "get_summary", ("require_auth", "get_db", "get_db")),
    ("/api/dashboard/overview", ("GET",), "dashboard_overview", ("get_db",)),
    ("/api/auth/login", ("POST",), "login", ("get_db",)),
    ("/api/auth/demo-login", ("POST",), "demo_login", ("get_db",)),
    ("/api/auth/logout", ("POST",), "logout", ("get_db",)),
    ("/api/auth/me", ("GET",), "me", ("require_auth", "get_db")),
    ("/api/actors", ("GET",), "actors", ("require_auth", "get_db", "get_db")),
    ("/api/actors", ("POST",), "create_actor", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/actors/{actor_id}/update", ("POST",), "update_actor_profile", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/agent-match", ("GET",), "agent_match", ("require_auth", "get_db", "get_db")),
    ("/api/agent-discovery", ("GET",), "agent_discovery", ("require_auth", "get_db", "get_db")),
    ("/api/tasks", ("GET",), "get_tasks", ("require_auth", "get_db", "get_db")),
    ("/api/tasks", ("POST",), "post_task", ("require_auth", "get_db", "get_db")),
    ("/api/tasks/ready", ("GET",), "get_ready_tasks", ("require_auth", "get_db", "get_db")),
    ("/api/tasks/reclaim", ("POST",), "post_task_reclaim", ("require_auth", "get_db", "get_db")),
    ("/api/dispatch", ("POST",), "post_dispatch", ("require_auth", "get_db", "get_db")),
    ("/api/squads", ("GET",), "get_squads", ("require_auth", "get_db", "get_db")),
    ("/api/squads", ("POST",), "post_squad", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/squads/{squad_id}/members", ("POST",), "post_squad_member", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/dispatch/schedules", ("GET",), "get_dispatch_schedules", ("require_auth", "get_db", "get_db")),
    ("/api/dispatch/schedules", ("POST",), "post_dispatch_schedule", ("require_auth", "get_db", "get_db")),
    ("/api/dispatch/events", ("POST",), "post_dispatch_event", ("require_auth", "get_db", "get_db")),
    ("/api/tasks/{task_id}", ("GET",), "get_task", ("require_auth", "get_db", "get_db")),
    ("/api/tasks/{task_id}/drift", ("GET",), "get_task_drift", ("require_auth", "get_db", "get_db")),
    ("/api/tasks/{task_id}/dependencies", ("POST",), "post_task_dependency", ("require_auth", "get_db", "get_db")),
    ("/api/tasks/{task_id}/dependencies/{prerequisite_id}", ("DELETE",), "delete_task_dependency", ("require_auth", "get_db", "get_db")),
    ("/api/tasks/{task_id}/attempts", ("GET",), "get_task_attempts", ("require_auth", "get_db", "get_db")),
    ("/api/tasks/{task_id}/attempts", ("POST",), "post_task_attempt", ("require_auth", "get_db", "get_db")),
    ("/api/artifacts/{task_id}", ("GET",), "get_task_artifact", ("require_auth", "get_db", "get_db")),
    ("/api/tasks/{task_id}/reviews", ("GET",), "get_task_reviews", ("require_auth", "get_db", "get_db")),
    ("/api/tasks/{task_id}/reviews", ("POST",), "post_task_review", ("require_auth", "get_db", "get_db")),
    ("/api/tasks/{task_id}/reviews/{review_id}/replies", ("POST",), "post_task_review_reply", ("require_auth", "get_db", "get_db")),
    ("/api/tasks/{task_id}/update", ("POST",), "post_task_update", ("require_auth", "get_db", "get_db")),
    ("/api/tasks/{task_id}/apply-proposal", ("POST",), "post_apply_roster_proposal", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/tasks/{task_id}/squad", ("POST",), "post_task_squad", ("require_auth", "get_db", "get_db")),
    ("/api/tasks/{task_id}/squad-route", ("POST",), "post_task_squad_route", ("require_auth", "get_db", "get_db")),
    ("/api/tasks/{task_id}/claim", ("POST",), "post_task_claim", ("require_auth", "get_db", "get_db")),
    ("/api/tasks/{task_id}/stage-done", ("POST",), "post_stage_done", ("require_auth", "get_db", "get_db")),
    ("/api/tasks/{task_id}/stage-reject", ("POST",), "post_stage_reject", ("require_auth", "get_db", "get_db")),
    ("/api/tasks/{task_id}/heartbeat", ("POST",), "post_task_heartbeat", ("require_auth", "get_db", "get_db")),
    ("/api/tasks/{task_id}/precheck", ("POST",), "post_task_precheck", ("require_auth", "get_db", "get_db")),
    ("/api/tasks/{task_id}/escalate", ("POST",), "post_task_escalate", ("require_auth", "get_db", "get_db")),
    ("/api/tasks/{task_id}/retry", ("POST",), "post_task_retry", ("require_auth", "get_db", "get_db")),
    ("/api/approvals", ("GET",), "get_approvals", ("require_auth", "get_db", "get_db")),
    ("/api/approvals/{approval_id}/decide", ("POST",), "post_approval_decide", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/approvals/{approval_id}/act", ("GET",), "approval_act_confirm", ("get_db",)),
    ("/api/approvals/{approval_id}/act", ("POST",), "approval_act_submit", ("get_db",)),
    ("/api/pipeline-templates", ("GET",), "get_pipeline_templates", ("require_auth", "get_db", "get_db")),
    ("/api/pipeline-templates", ("POST",), "post_pipeline_template", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/card-pipelines", ("GET",), "get_card_pipelines", ("require_auth", "get_db", "get_db")),
    ("/api/card-pipelines", ("POST",), "post_card_pipeline", ("require_auth", "get_db", "get_db")),
    ("/api/card-pipelines/{template_id}", ("GET",), "get_card_pipeline", ("require_auth", "get_db", "get_db")),
    ("/api/card-pipelines/{template_id}/instantiate", ("POST",), "post_card_pipeline_instantiate", ("require_auth", "get_db", "get_db")),
    ("/api/card-pipeline-instances/{instance_id}", ("GET",), "get_card_pipeline_instance", ("require_auth", "get_db", "get_db")),
    ("/api/card-pipeline-instances/{instance_id}/resume", ("POST",), "post_card_pipeline_instance_resume", ("require_auth", "get_db", "get_db")),
    ("/api/metrics/ingest", ("POST",), "ingest_metrics", ("require_auth", "get_db", "get_db")),
    ("/api/metrics/summary", ("GET",), "metrics_summary", ("require_auth", "get_db", "get_db")),
    ("/api/sessions/sync", ("POST",), "sync_runtime_session", ("require_auth", "get_db", "get_db")),
    ("/api/sessions", ("GET",), "list_runtime_sessions", ("require_auth", "get_db", "get_db")),
    ("/api/sessions/{session_id}", ("GET",), "get_runtime_session", ("require_auth", "get_db", "get_db")),
    ("/api/sessions/{session_id}/captures", ("GET",), "get_session_captures", ("require_auth", "get_db", "get_db")),
    ("/api/sessions/{session_id}/capture-obsidian", ("POST",), "queue_obsidian_capture", ("require_auth", "get_db", "get_db")),
    ("/api/session-captures/pending", ("GET",), "get_pending_session_captures", ("require_auth", "get_db", "get_db")),
    ("/api/session-captures/{capture_id}/exported", ("POST",), "mark_session_capture_exported", ("require_auth", "get_db", "get_db")),
    ("/api/sessions/{session_id}/create-task", ("POST",), "create_task_from_session", ("require_auth", "get_db", "get_db")),
    ("/api/todos/home", ("GET",), "get_todo_home", ("require_auth", "get_db", "get_db")),
    ("/api/todos/reminders/due", ("GET",), "get_due_reminders", ("require_auth", "get_db", "get_db")),
    ("/api/todos/grants", ("GET",), "get_todo_grants", ("require_auth", "get_db", "get_db")),
    ("/api/todos/grants", ("POST",), "post_todo_grant", ("require_auth", "get_db", "get_db")),
    ("/api/todos/grants/{actor_id}", ("DELETE",), "delete_todo_grant", ("require_auth", "get_db", "get_db")),
    ("/api/todos/proposals", ("GET",), "get_todo_proposals", ("require_auth", "get_db", "get_db")),
    ("/api/todos/proposals", ("POST",), "post_todo_proposal", ("require_auth", "get_db", "get_db")),
    ("/api/todos/proposals/{proposal_id}", ("GET",), "get_todo_proposal", ("require_auth", "get_db", "get_db")),
    ("/api/todos/proposals/{proposal_id}/confirm", ("POST",), "post_todo_proposal_confirm", ("require_auth", "get_db", "get_db")),
    ("/api/todos/proposals/{proposal_id}/reject", ("POST",), "post_todo_proposal_reject", ("require_auth", "get_db", "get_db")),
    ("/api/todos", ("GET",), "get_todos", ("require_auth", "get_db", "get_db")),
    ("/api/todos", ("POST",), "post_todo", ("require_auth", "get_db", "get_db")),
    ("/api/todos/{item_id}", ("GET",), "get_todo", ("require_auth", "get_db", "get_db")),
    ("/api/todos/{item_id}/events", ("GET",), "get_todo_events", ("require_auth", "get_db", "get_db")),
    ("/api/todos/{item_id}/update", ("POST",), "post_todo_update", ("require_auth", "get_db", "get_db")),
    ("/api/todos/{item_id}/complete", ("POST",), "post_todo_complete", ("require_auth", "get_db", "get_db")),
    ("/api/todos/{item_id}/cancel", ("POST",), "post_todo_cancel", ("require_auth", "get_db", "get_db")),
    ("/api/todos/{item_id}/snooze", ("POST",), "post_todo_snooze", ("require_auth", "get_db", "get_db")),
    ("/api/todos/{item_id}/reminders", ("POST",), "post_todo_reminder", ("require_auth", "get_db", "get_db")),
    ("/api/todos/{item_id}/promote", ("POST",), "post_todo_promote", ("require_auth", "get_db", "get_db")),
    ("/api/admin/users", ("GET",), "admin_users", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/admin/users", ("POST",), "admin_create_user", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/admin/onboarding/prepare", ("POST",), "admin_prepare_onboarding", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/admin/tokens", ("POST",), "admin_create_token", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/admin/tokens", ("GET",), "admin_tokens", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/admin/tokens/{token_id}/revoke", ("POST",), "admin_revoke_token", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/admin/tokens/{token_id}/rotate", ("POST",), "admin_rotate_token", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/admin/node-tokens", ("POST",), "admin_create_node_token", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/admin/node-tokens", ("GET",), "admin_node_tokens", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/admin/nodes", ("POST",), "admin_admit_node", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/admin/nodes/{node_id}", ("DELETE",), "admin_retire_node", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/admin/channel-tokens", ("POST",), "admin_create_channel_token", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/admin/channel-tokens", ("GET",), "admin_channel_tokens", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/admin/channel-tokens/{token_id}/revoke", ("POST",), "admin_revoke_channel_token", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/admin/channel-users", ("GET",), "admin_channel_users", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/admin/channel-users", ("PUT",), "admin_upsert_channel_user", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/admin/channel-users/{mapping_id}", ("DELETE",), "admin_delete_channel_user", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/intake/{channel_id}/webhook", ("POST",), "post_intake_webhook", ("require_channel", "get_db", "get_db")),
    ("/api/enroll", ("POST",), "post_enroll", ("get_db",)),
    ("/api/admin/enroll-applications", ("GET",), "list_enroll_applications", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/admin/enroll-applications/{application_id}/decide", ("POST",), "decide_enroll_application", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/skills", ("GET",), "get_skills", ("require_auth", "get_db", "get_db")),
    ("/api/skills", ("POST",), "post_skill", ("require_auth", "get_db", "get_db")),
    ("/api/skills/import", ("POST",), "post_skill_import", ("require_auth", "get_db", "get_db")),
    ("/api/me/skill-briefing", ("GET",), "get_my_skill_briefing", ("require_auth", "get_db", "get_db")),
    ("/api/actors/{actor_id}/skills", ("GET",), "get_actor_skills", ("require_auth", "get_db", "get_db")),
    ("/api/actors/{actor_id}/skills", ("POST",), "post_actor_skill", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/actors/{actor_id}/skills/{skill_id}/update", ("POST",), "post_actor_skill_update", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/actors/{actor_id}/skills/{skill_id}/unbind", ("POST",), "post_actor_skill_unbind", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/actors/{actor_id}/skill-events", ("GET",), "get_actor_skill_events", ("require_auth", "get_db", "get_db")),
    ("/api/skills/pilot-bindings", ("POST",), "post_pilot_bindings", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/nodes", ("GET",), "get_nodes", ("require_auth", "get_db", "get_db")),
    ("/api/nodes/heartbeat", ("POST",), "node_heartbeat", ("get_db",)),
    ("/api/nodes/{node_id}/runtimes", ("GET",), "get_node_runtimes", ("require_auth", "get_db", "get_db")),
    ("/api/nodes/runtimes", ("POST",), "post_node_runtimes", ("get_db",)),
    ("/api/nodes/{node_id}/tasks/{task_id}/attempts", ("POST",), "post_node_task_attempt", ("get_db",)),
    ("/api/knowledge", ("GET",), "get_knowledge", ("require_auth", "get_db", "get_db")),
    ("/api/knowledge", ("POST",), "post_knowledge", ("require_admin", "require_auth", "get_db", "get_db")),
    ("/api/metrics/throughput", ("GET",), "throughput", ("require_auth", "get_db", "get_db")),
    ("/.well-known/agent-card.json", ("GET",), "agent_card", ("get_db",)),
}


def _app(tmp_path: Path, **kwargs):
    factory = make_session_factory(tmp_path / "route-table.db")
    return create_app(factory, **kwargs)


def test_route_table_matches_expected_set(tmp_path):
    app = _app(tmp_path)
    actual = {
        (
            route.path,
            tuple(sorted(route.methods)),
            route.name,
            _dep_names(route.dependant),
        )
        for route in _api_routes(app)
    }
    assert actual == EXPECTED_ROUTES


def test_panel_mount_and_catch_all_come_after_api_routes(tmp_path):
    static_dir = tmp_path / "static"
    (static_dir / "assets").mkdir(parents=True)
    (static_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    app = _app(tmp_path, static_dir=static_dir)

    routes = [context.route for context in iter_route_contexts(app.routes)]
    catch_all = [
        route
        for route in routes
        if getattr(route, "path", None) == "/{path:path}"
    ]
    api_routes = [
        route for route in routes if isinstance(route, APIRoute) and route not in catch_all
    ]
    mounts = [route for route in routes if isinstance(route, Mount)]

    assert [mount.path for mount in mounts] == ["/assets"]
    assert len(catch_all) == 1
    # Route ordering is behaviour: nothing API-shaped may be shadowed by the
    # panel mount or the SPA fallback.
    assert routes[-1] is catch_all[0]
    assert routes.index(mounts[0]) > max(routes.index(r) for r in api_routes)

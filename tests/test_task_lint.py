import shutil

from core.protocol.task import create_task, lint_path


def test_lint_reports_synthetic_conflict_copies_without_false_positives(tmp_path):
    tasks = tmp_path / "tasks"
    canonical = create_task(
        tasks,
        task_id="task-20260720-701",
        title="Neutral conflict fixture",
        created_by="lead-1",
        holder="worker-1",
    )
    assert lint_path(tasks) == [(canonical, None)]

    sync_copy = tasks / "task-20260720-701.sync-conflict-20260720-113000-node.yaml"
    git_copy = tasks / "task-20260720-701.yaml.orig"
    renamed_copy = tasks / "stray-copy_task-20260720-701.yaml"
    for copy in (sync_copy, git_copy, renamed_copy):
        shutil.copyfile(canonical, copy)

    results = {path.name: error for path, error in lint_path(tasks)}
    assert results[canonical.name] is None
    assert results[sync_copy.name] == "Syncthing conflict copy detected"
    assert results[git_copy.name] == "Git merge conflict copy detected"
    assert "possible duplicate or conflict copy" in results[renamed_copy.name]

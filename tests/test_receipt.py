from core.protocol.task import (
    create_task,
    load_task,
    render_receipt,
    update_task,
    validate_task,
)


def test_receipt_format_for_transition(tmp_path):
    path = create_task(
        tmp_path,
        task_id="task-20260719-005",
        title="Prepare release notes",
        created_by="boss",
        holder="writer-1",
        at="2026-07-19T10:00+08:00",
    )
    update_task(
        path,
        status="doing",
        holder="editor-1",
        note="accepted handoff",
        at="2026-07-19T10:10+08:00",
    )
    assert render_receipt(load_task(path)) == (
        "【任务回执】task-20260719-005 Prepare release notes\n"
        "状态：queued → doing　持棒：writer-1 → editor-1　备注：accepted handoff"
    )


def test_creation_receipt_uses_placeholder(tmp_path):
    path = create_task(
        tmp_path,
        task_id="task-20260719-006",
        title="Prepare release notes",
        created_by="boss",
        holder="writer-1",
    )
    assert "状态：— → queued　持棒：— → writer-1" in render_receipt(load_task(path))


def test_legacy_local_offset_card_still_validates_and_renders_receipt():
    task = {
        "id": "task-20260719-006",
        "title": "Prepare release notes",
        "created_by": "boss",
        "status": "doing",
        "holder": "writer-1",
        "chain": [
            {
                "who": "writer-1",
                "did": "started draft",
                "at": "2026-07-19T10:05+08:00",
                "from_status": "queued",
                "to_status": "doing",
                "from_holder": "writer-1",
                "to_holder": "writer-1",
            }
        ],
        "refs": [],
    }

    validate_task(task)
    assert render_receipt(task) == (
        "【任务回执】task-20260719-006 Prepare release notes\n"
        "状态：queued → doing　持棒：writer-1 → writer-1　备注：started draft"
    )

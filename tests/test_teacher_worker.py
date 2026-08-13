from tools.teacher_worker import build_prompt, stage_identity


def test_stage_identity_increments_for_rework_handoff():
    task = {
        "id": "task-20260730-999",
        "pipeline": [
            {"name": "教案初稿", "holder": "lesson-planner", "gate": "auto"},
            {"name": "教研审阅", "holder": "reviewer", "gate": "review"},
        ],
        "pipeline_stage": 1,
        "chain": [
            {"to_holder": "reviewer", "to_status": "handoff", "did": "首次交棒"},
            {"to_holder": "reviewer", "to_status": "handoff", "did": "老师驳回：练习太难"},
        ],
    }

    marker, label = stage_identity(task, "reviewer")

    assert marker.endswith(":reviewer:r2")
    assert label == "教研审阅（返工第 1 轮）"


def test_build_prompt_includes_latest_rework_feedback():
    task = {
        "id": "task-20260730-999",
        "title": "七年级英语备课",
        "acceptance": ["练习适合基础薄弱学生"],
        "chain": [
            {"who": "teacher", "did": "人工审批驳回：练习太难，请降低难度"},
        ],
    }

    prompt = build_prompt(task, "reviewer", "教研审阅（返工第 1 轮）", "# 旧产物")

    assert "练习太难，请降低难度" in prompt
    assert "必须优先落实" in prompt

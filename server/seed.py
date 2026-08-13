"""Deterministic demo templates (de-identified samples, no real content).

Templates:
- writing: 写作工作室 — internal daily-driver scenario.
- teacher: 普通老师   — single-teacher onboarding and pilot scenario.
- edu:     教培机构   — primary sales demo for education companies.
- company: 普通公司   — generic-company sales demo.
"""

from __future__ import annotations

import datetime as dt
import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

import json

from .db import (
    Actor,
    KnowledgeSource,
    Node,
    PipelineTemplate,
    RuntimeSession,
    Skill,
    Task,
    TokenUsage,
    User,
)
from .skill_ops import apply_pilot_bindings, bind_skill
from .engine import create_task, update_task
from .flow import stage_done
from .security import hash_password

WRITING_TEMPLATE = {
    "actors": [
        ("chief-editor", "human", "主编", "", "", "workstation"),
        ("ops-lead", "human", "运营负责人", "", "", "laptop"),
        ("scribe-a", "agent", "撰稿智能体 A", "claude-code", "claude-fable-5", "throne"),
        ("scribe-b", "agent", "撰稿智能体 B", "codex", "gpt-5", "throne"),
        ("reviewer", "agent", "审校智能体", "claude-code", "claude-opus-4-8", "throne"),
        ("researcher", "agent", "选题研究智能体", "claude-code", "claude-sonnet-4-6", "castle"),
    ],
    "users": [("editor", "editor-demo-2026", "member", "主编", "chief-editor")],
    "creator": "chief-editor",
    "tasks": [
        dict(
            title="下周选题清单:行业热点扫描",
            holder="researcher",
            dept="editorial",
            priority="high",
            acceptance=["10 个候选选题", "每个附 3 条参考来源"],
        ),
        dict(
            title="专栏稿:多智能体协作实践(草稿)",
            holder="scribe-a",
            dept="editorial",
            priority="urgent",
            acceptance=["2500 字以上", "含案例与数据"],
            flow=[("doing", "开始撰写草稿")],
            progress=70,
        ),
        dict(
            title="行业周报选题池补充",
            dept="editorial",
            priority="medium",
            acceptance=["新增 8 个候选选题"],
            open=True,
        ),
        dict(
            title="公众号推文:产品发布预热",
            holder="scribe-b",
            dept="marketing",
            priority="medium",
            acceptance=["3 个标题备选", "800 字正文"],
            flow=[("doing", "领卡开写"), ("handoff", "初稿完成,移交审校")],
            handoff_to="reviewer",
        ),
        dict(title="旧文改写:知识库文章更新", holder="scribe-a", dept="editorial", priority="low"),
        dict(
            title="客户白皮书:章节三返工",
            holder="scribe-b",
            dept="client",
            priority="high",
            flow=[("doing", "开始返工")],
            block="等待客户提供最新数据表",
        ),
        dict(
            title="月度内容复盘报告",
            holder="reviewer",
            dept="editorial",
            priority="medium",
            flow=[("doing", "汇总各栏目数据"), ("done", "报告已交付主编")],
        ),
    ],
}

EDU_TEMPLATE = {
    "actors": [
        ("principal", "human", "校长", "", "", "office"),
        ("dean", "human", "教务主任", "", "", "office"),
        ("lesson-planner", "agent", "教案生成智能体", "claude-code", "claude-fable-5", "school-server"),
        ("grader", "agent", "作业批改智能体", "claude-code", "claude-sonnet-4-6", "school-server"),
        ("advisor", "agent", "课程顾问智能体", "claude-code", "claude-haiku-4-5", "school-server"),
        ("copywriter", "agent", "招生文案智能体", "codex", "gpt-5", "school-server"),
        ("liaison", "agent", "家校沟通智能体", "claude-code", "claude-sonnet-4-6", "school-server"),
    ],
    "users": [("dean", "dean-demo-2026", "admin", "教务主任", "dean")],
    "creator": "dean",
    "tasks": [
        dict(
            title="九年级物理:电磁学单元教案(含互动游戏)",
            holder="lesson-planner",
            dept="教学",
            priority="urgent",
            acceptance=["对齐课标考点", "含 2 个课堂互动环节", "附课后作业与答案"],
            flow=[("doing", "已读取课标与往期教案,开始生成")],
            progress=65,
        ),
        dict(
            title="初二数学期中复习专题包",
            dept="教学",
            priority="high",
            acceptance=["按知识点分 6 个专题", "每专题含例题与练习"],
            open=True,
        ),
        dict(
            title="校区周年庆活动方案与物料文案",
            dept="招生",
            priority="medium",
            acceptance=["活动流程", "海报与短信文案"],
            open=True,
        ),
        dict(
            title="周测试卷批改与错题分析(初三 2 班)",
            holder="grader",
            dept="教学",
            priority="high",
            acceptance=["每份卷含逐题批注", "班级错题 TOP5 汇总"],
            flow=[("doing", "42 份试卷已入队"), ("handoff", "批改完成,错题分析移交教务复核")],
            handoff_to="dean",
        ),
        dict(
            title="试听课学员跟进话术(本周 18 名)",
            holder="advisor",
            dept="招生",
            priority="high",
            acceptance=["按学员画像分 3 组话术", "每组含异议处理"],
            flow=[("doing", "拉取试听记录,分组画像中")],
            progress=40,
        ),
        dict(
            title="暑期班招生海报与朋友圈文案",
            holder="copywriter",
            dept="招生",
            priority="medium",
            acceptance=["海报文案 3 版", "朋友圈连发 5 条排期"],
        ),
        dict(
            title="家长会通知与月度学习报告(全体学员)",
            holder="liaison",
            dept="家校",
            priority="medium",
            acceptance=["通知含日程与到场确认", "报告按学员个性化生成"],
            flow=[("doing", "汇总本月出勤与成绩数据")],
            block="等待教务主任确认家长会日期",
        ),
        dict(
            title="新教师入职培训材料整理",
            holder="lesson-planner",
            dept="教学",
            priority="low",
        ),
        dict(
            title="上月续费率复盘简报",
            holder="advisor",
            dept="招生",
            priority="medium",
            flow=[("doing", "统计各班续费与流失原因"), ("done", "简报已提交校长")],
        ),
    ],
}

COMPANY_TEMPLATE = {
    "actors": [
        ("gm", "human", "总经理", "", "", "office"),
        ("pm", "human", "项目经理", "", "", "office"),
        ("copywriter", "agent", "市场文案智能体", "claude-code", "claude-fable-5", "hq-server"),
        ("analyst", "agent", "数据分析智能体", "claude-code", "claude-sonnet-4-6", "hq-server"),
        ("support", "agent", "客服应答智能体", "claude-code", "claude-haiku-4-5", "hq-server"),
        ("dev-assist", "agent", "研发助理智能体", "codex", "gpt-5", "hq-server"),
        ("admin-assist", "agent", "行政助理智能体", "claude-code", "claude-haiku-4-5", "hq-server"),
    ],
    "users": [("pm", "pm-demo-2026", "admin", "项目经理", "pm")],
    "creator": "pm",
    "tasks": [
        dict(
            title="竞品动态月报(本月三家重点对象)",
            holder="analyst",
            dept="市场",
            priority="high",
            acceptance=["价格与功能变动对比表", "3 条应对建议"],
            flow=[("doing", "开始抓取与整理公开信息")],
            progress=55,
        ),
        dict(
            title="内部知识库月度整理与去重",
            dept="行政",
            priority="medium",
            acceptance=["过期文档归档", "重复条目合并"],
            open=True,
        ),
        dict(
            title="官网产品页文案改版",
            holder="copywriter",
            dept="市场",
            priority="medium",
            acceptance=["首屏标语 3 版备选", "功能区文案全量更新"],
            flow=[("doing", "领卡开写"), ("handoff", "初稿完成,移交项目经理审阅")],
            handoff_to="pm",
        ),
        dict(
            title="客服知识库季度更新",
            holder="support",
            dept="客服",
            priority="medium",
            acceptance=["新增 20 条高频问答", "过期条目清理"],
        ),
        dict(
            title="内部工具:报销流程自动化脚本",
            holder="dev-assist",
            dept="研发",
            priority="high",
            flow=[("doing", "梳理现有流程,开始编写")],
            block="等待财务确认新报销单字段",
        ),
        dict(
            title="全员周会纪要与行动项跟踪",
            holder="admin-assist",
            dept="行政",
            priority="low",
            flow=[("doing", "整理录音转写稿"), ("done", "纪要已分发,行动项已建卡")],
        ),
        dict(
            title="季度经营数据看板初稿",
            holder="analyst",
            dept="市场",
            priority="urgent",
        ),
    ],
}

EDU_TEMPLATE["skills"] = [
    ("教案生成", "教学", "按课标与年级生成完整教案,含互动环节与作业", ["lesson-planner"]),
    ("课件排版", "教学", "教案转 PPT 结构与讲义排版", ["lesson-planner"]),
    ("作业批改", "教学", "客观题自动判分,主观题逐题批注", ["grader"]),
    ("错题分析", "教学", "班级错题聚类与知识点薄弱报告", ["grader"]),
    ("试听跟进话术", "招生", "按学员画像生成分组跟进话术与异议处理", ["advisor"]),
    ("续费预警", "招生", "出勤与成绩信号识别流失风险", ["advisor"]),
    ("招生文案", "招生", "海报、朋友圈、短信多渠道招生文案", ["copywriter"]),
    ("活动策划", "招生", "开放日与体验课活动方案", ["copywriter"]),
    ("家长通知", "家校", "通知、日程与到场确认的家校消息", ["liaison"]),
    ("学习报告", "家校", "按学员数据生成个性化月度学习报告", ["liaison"]),
]
EDU_TEMPLATE["nodes"] = [
    ("school-server", "校区服务器", "edu-hq", 96.5, 42.3, 8),
    ("front-desk", "前台工作站", "front-01", 22.1, 61.0, 2),
]
EDU_TEMPLATE["pipelines"] = {
    "templates": [
        (
            "教案三审流程",
            [
                {"name": "撰写", "holder": "lesson-planner", "gate": "auto"},
                {"name": "审阅", "holder": "grader", "gate": "review"},
                {"name": "校长门", "holder": "principal", "gate": "queen"},
            ],
        ),
        (
            "招生物料流程",
            [
                {"name": "文案", "holder": "copywriter", "gate": "auto"},
                {"name": "顾问审核", "holder": "advisor", "gate": "review"},
                {"name": "校长门", "holder": "principal", "gate": "queen"},
            ],
        ),
    ],
    "live": {
        "title": "七年级英语:期末总复习讲义(流程演示)",
        "dept": "教学",
        "priority": "high",
        "template": "教案三审流程",
        "advance": 2,  # 撰写完成→审阅完成→现停在校长门待批
    },
}
EDU_TEMPLATE["knowledge"] = [
    ("教材与课标库", "corpus", 1240, 380_000_000, "各年级教材、课标与考纲"),
    ("题库", "corpus", 8600, 120_000_000, "分知识点标注,含解析"),
    ("学员档案(脱敏)", "dataset", 356, 24_000_000, "出勤、成绩与学习画像,已脱敏"),
    ("教务 Wiki", "wiki", 210, 8_000_000, "流程、制度与常见问答"),
]

COMPANY_TEMPLATE["skills"] = [
    ("竞品监测", "市场", "定期抓取公开信息生成竞品动态报告", ["analyst"]),
    ("数据看板", "市场", "经营数据聚合与可视化初稿", ["analyst"]),
    ("品牌文案", "市场", "官网、公众号与广告投放文案", ["copywriter"]),
    ("SEO 优化", "市场", "关键词研究与页面优化建议", ["copywriter"]),
    ("客服问答", "客服", "基于知识库的高频问题自动应答", ["support"]),
    ("工单摘要", "客服", "会话转工单与升级摘要", ["support"]),
    ("代码评审辅助", "研发", "PR 摘要与风险点提示", ["dev-assist"]),
    ("流程自动化", "研发", "内部流程脚本与集成", ["dev-assist"]),
    ("会议纪要", "行政", "录音转写、纪要与行动项跟踪", ["admin-assist"]),
    ("招聘初筛", "行政", "简历结构化与初筛报告", ["admin-assist"]),
]
COMPANY_TEMPLATE["nodes"] = [
    ("hq-server", "总部服务器", "hq-01", 88.2, 55.7, 21),
    ("office-nas", "办公 NAS", "nas-01", 8.4, 71.2, 4),
]
COMPANY_TEMPLATE["pipelines"] = {
    "templates": [
        (
            "内容发布流程",
            [
                {"name": "撰写", "holder": "copywriter", "gate": "auto"},
                {"name": "数据核对", "holder": "analyst", "gate": "review"},
                {"name": "总经理门", "holder": "gm", "gate": "queen"},
            ],
        ),
    ],
    "live": {
        "title": "季度经营简报(流程演示)",
        "dept": "市场",
        "priority": "high",
        "template": "内容发布流程",
        "advance": 1,  # 撰写完成→现停在数据核对
    },
}
COMPANY_TEMPLATE["knowledge"] = [
    ("产品知识库", "wiki", 480, 26_000_000, "产品文档、FAQ 与发布记录"),
    ("客服话术库", "corpus", 320, 6_000_000, "分场景标准话术与升级路径"),
    ("经营数据(脱敏)", "dataset", 96, 48_000_000, "月度经营指标,已脱敏"),
    ("制度与流程", "wiki", 150, 4_000_000, "行政、财务与人事流程"),
]

WRITING_TEMPLATE["skills"] = [
    ("选题研究", "编辑", "热点扫描与选题评估", ["researcher"]),
    ("长文撰写", "编辑", "深度稿件结构化写作", ["scribe-a", "scribe-b"]),
    ("审校润色", "编辑", "事实核查、风格统一与润色", ["reviewer"]),
    ("多平台分发", "运营", "按平台调性改写与排期", ["scribe-b"]),
]
WRITING_TEMPLATE["nodes"] = [
    ("studio-server", "工作室服务器", "studio-01", 74.0, 48.9, 12),
]
WRITING_TEMPLATE["knowledge"] = [
    ("稿件库", "corpus", 620, 90_000_000, "历史稿件与素材"),
    ("风格指南", "wiki", 45, 1_000_000, "写作规范与栏目定位"),
]

TEACHER_TEMPLATE = {
    "actors": [
        ("teacher", "human", "老师本人", "", "", "browser"),
        (
            "lesson-planner",
            "agent",
            "备课助理",
            "claude-code",
            "claude-sonnet-4-6",
            "retinue-cloud",
        ),
        (
            "material-maker",
            "agent",
            "课件与练习助理",
            "codex",
            "gpt-5",
            "retinue-cloud",
        ),
        (
            "reviewer",
            "agent",
            "教研审阅助理",
            "claude-code",
            "claude-opus-4-8",
            "retinue-cloud",
        ),
        (
            "parent-liaison",
            "agent",
            "家长沟通助理",
            "claude-code",
            "claude-sonnet-4-6",
            "retinue-cloud",
        ),
    ],
    "users": [
        ("teacher", "teacher-pilot-2026", "admin", "试点老师", "teacher"),
    ],
    "creator": "teacher",
    "tasks": [
        dict(
            title="七年级英语暑期班第一课备课包",
            holder="lesson-planner",
            dept="备课",
            priority="urgent",
            acceptance=[
                "一份 45 分钟课堂流程",
                "明确教学目标与重点词句",
                "至少 2 个课堂互动练习",
                "含课后作业及参考答案",
                "附一段可直接发送的家长课后反馈",
            ],
            flow=[("doing", "已收到课程要求,正在整理教学目标与课堂节奏")],
            progress=35,
        ),
        dict(
            title="把本周课堂内容整理成家长反馈",
            holder="parent-liaison",
            dept="家校",
            priority="medium",
            acceptance=["语言亲切易懂", "包含本周进步与下周建议"],
        ),
        dict(
            title="下周随堂练习：一般现在时",
            dept="练习",
            priority="high",
            acceptance=["10 道分层练习", "含答案与易错点说明"],
            open=True,
        ),
    ],
    "skills": [
        ("备课包生成", "备课", "把课程要求整理为可直接上课的完整备课包", ["lesson-planner"]),
        ("课件与练习", "备课", "根据教案制作课件提纲、互动练习和作业", ["material-maker"]),
        ("教研审阅", "质检", "检查目标、难度、时间安排和练习答案", ["reviewer"]),
        ("家长反馈", "家校", "把课堂记录转成自然、具体的家长反馈", ["parent-liaison"]),
    ],
    "pipelines": {
        "templates": [
            (
                "普通老师备课流程",
                [
                    {"name": "教案初稿", "holder": "lesson-planner", "gate": "auto"},
                    {"name": "课件与练习", "holder": "material-maker", "gate": "auto"},
                    {"name": "教研审阅", "holder": "reviewer", "gate": "review"},
                    {"name": "老师确认", "holder": "teacher", "gate": "queen"},
                ],
            ),
        ],
        "live": {
            "title": "七年级英语第一课完整材料（审批演示）",
            "dept": "备课",
            "priority": "high",
            "template": "普通老师备课流程",
            "advance": 3,
        },
    },
    "knowledge": [
        ("个人教材资料", "corpus", 12, 8_000_000, "老师上传的教材章节、课程目标与往期资料"),
        ("班级教学记录", "dataset", 18, 1_500_000, "仅保存脱敏后的课堂记录与学习难点"),
    ],
}

TEACHER_TEMPLATE["sessions"] = [
    {
        "actor_id": "lesson-planner",
        "runtime": "claude-code",
        "external_id": "demo-teacher-lesson-1",
        "title": "七年级英语第一课怎么安排？",
        "summary": "老师希望为基础较弱的学生准备 45 分钟英语课；备课助理已给出课堂节奏、互动和课后任务。",
        "privacy": "full",
        "message_count": 4,
        "task_title": "七年级英语暑期班第一课备课包",
        "messages": [
            {
                "role": "user",
                "text": "下周要给基础较弱的七年级学生上第一节英语课，45 分钟，应该怎么安排？",
            },
            {
                "role": "assistant",
                "text": "建议分成热身、核心词句、情境练习、当堂检测和课后任务五段。我先按可直接上课的格式整理。",
            },
            {
                "role": "user",
                "text": "再加一个全班都能参与、不需要额外教具的互动。",
            },
            {
                "role": "assistant",
                "text": "已加入“站队选择”互动：老师读出句子，学生按答案移动到教室左右两侧，并安排两轮纠错。",
            },
        ],
    },
    {
        "actor_id": "material-maker",
        "runtime": "codex",
        "external_id": "demo-teacher-material-1",
        "title": "把教案做成课件和分层练习",
        "summary": "课件与练习助理已规划 12 页课件，并按基础、巩固、挑战三档生成练习和答案。",
        "privacy": "summary",
        "message_count": 5,
        "messages": [],
    },
    {
        "actor_id": "reviewer",
        "runtime": "claude-code",
        "external_id": "demo-teacher-review-1",
        "title": "Claude Code 会话 · 教研审阅",
        "summary": "",
        "privacy": "metadata",
        "message_count": 7,
        "messages": [],
    },
]

TEMPLATES = {
    "writing": WRITING_TEMPLATE,
    "teacher": TEACHER_TEMPLATE,
    "edu": EDU_TEMPLATE,
    "company": COMPANY_TEMPLATE,
}


def seed_demo(db: Session, template: str = "writing") -> dict[str, int]:
    if template not in TEMPLATES:
        raise ValueError(f"unknown template: {template!r}; choose from {sorted(TEMPLATES)}")
    spec = TEMPLATES[template]

    for actor_id, kind, name, runtime, model, node in spec["actors"]:
        if db.get(Actor, actor_id) is None:
            db.add(
                Actor(
                    id=actor_id,
                    kind=kind,
                    display_name=name,
                    runtime=runtime,
                    model=model,
                    node=node,
                )
            )
    db.flush()

    for username, password, role, display, actor_id in spec["users"]:
        user = db.execute(select(User).where(User.username == username)).scalar()
        if user is None:
            db.add(
                User(
                    username=username,
                    password_hash=hash_password(password),
                    role=role,
                    display_name=display,
                    actor_id=actor_id,
                )
            )
        else:
            user.role = role
            user.display_name = display
            user.actor_id = actor_id

    existing_titles = set(db.execute(select(Task.title)).scalars())
    created_tasks = 0
    for task_spec in spec["tasks"]:
        if task_spec["title"] in existing_titles:
            continue  # re-running seed-demo must not duplicate cards
        task = create_task(
            db,
            title=task_spec["title"],
            created_by=spec["creator"],
            holder=task_spec.get("holder") or spec["creator"],
            dept=task_spec.get("dept"),
            priority=task_spec.get("priority", "none"),
            acceptance=task_spec.get("acceptance", ()),
            open_dispatch=task_spec.get("open", False),
        )
        created_tasks += 1
        for status, note in task_spec.get("flow", ()):
            if status == "handoff":
                update_task(
                    db,
                    task,
                    who=task.holder,
                    is_privileged=False,
                    status="handoff",
                    holder=task_spec.get("handoff_to", task.holder),
                    note=note,
                )
            else:
                update_task(db, task, who=task.holder, is_privileged=False, status=status, note=note)
        if task_spec.get("progress") is not None and task.status == "doing":
            update_task(
                db,
                task,
                who=task.holder,
                is_privileged=False,
                progress=task_spec["progress"],
                note=f"进度上报:{task_spec['progress']}%",
            )
        if task_spec.get("block"):
            update_task(
                db,
                task,
                who=task.holder,
                is_privileged=False,
                status="blocked",
                blocked_reason=task_spec["block"],
                note="遇阻:" + task_spec["block"],
            )

    for name, category, description, owners in spec.get("skills", []):
        skill = db.execute(select(Skill).where(Skill.name == name)).scalar()
        if skill is None:
            skill = Skill(
                name=name,
                category=category,
                description=description,
                owners_json=json.dumps(owners),
            )
            db.add(skill)
            db.flush()
        for owner in owners:
            if db.get(Actor, owner) is None:
                continue
            bind_skill(db, actor_id=owner, name=name, who="seed-demo", enabled=True)
    apply_pilot_bindings(db, who="seed-demo")

    for node_id, label, hostname, mem_pct, disk_pct, uptime_days in spec.get("nodes", []):
        if db.get(Node, node_id) is None:
            total_mem = 16 * 1024**3
            total_disk = 500 * 1024**3
            db.add(
                Node(
                    id=node_id,
                    label=label,
                    admitted_by="seed-demo",
                    hostname=hostname,
                    platform="Linux-6.8 x86_64",
                    uptime_seconds=uptime_days * 86400,
                    load_json=json.dumps([round(mem_pct / 60, 2), 0.4, 0.3]),
                    disk_json=json.dumps(
                        {
                            "total": total_disk,
                            "used": int(total_disk * disk_pct / 100),
                            "free": int(total_disk * (100 - disk_pct) / 100),
                            "percent": disk_pct,
                        }
                    ),
                    memory_json=json.dumps(
                        {
                            "total": total_mem,
                            "available": int(total_mem * (100 - mem_pct) / 100),
                        }
                    ),
                    services_json=json.dumps(
                        [
                            {"unit": "retinue-server.service", "active": "active", "sub": "running", "restarts": 0, "healthy": True},
                            {"unit": "agent-gateway.service", "active": "active", "sub": "running", "restarts": 0, "healthy": True},
                        ]
                    ),
                )
            )

    for name, kind, docs, size_bytes, notes in spec.get("knowledge", []):
        if not db.execute(
            select(KnowledgeSource).where(KnowledgeSource.name == name)
        ).scalar():
            db.add(
                KnowledgeSource(
                    name=name, kind=kind, docs=docs, size_bytes=size_bytes, notes=notes
                )
            )

    pipelines = spec.get("pipelines", {})
    for name, stages in pipelines.get("templates", []):
        if not db.execute(
            select(PipelineTemplate).where(PipelineTemplate.name == name)
        ).scalar():
            db.add(
                PipelineTemplate(name=name, stages_json=json.dumps(stages, ensure_ascii=False))
            )
    live = pipelines.get("live")
    if live and live["title"] not in existing_titles:
        stages = dict(pipelines["templates"])[live["template"]]
        flow_task = create_task(
            db,
            title=live["title"],
            created_by=spec["creator"],
            holder=stages[0]["holder"],
            dept=live.get("dept"),
            priority=live.get("priority", "medium"),
        )
        flow_task.pipeline_json = json.dumps(stages, ensure_ascii=False)
        flow_task.pipeline_stage = 0
        db.flush()
        for step in range(live.get("advance", 0)):
            update_task(
                db, flow_task, who=flow_task.holder, is_privileged=False,
                status="doing", note=f"接棒「{stages[step]['name']}」,开工",
            )
            stage_done(
                db, flow_task, who=flow_task.holder, is_privileged=False,
                note=f"「{stages[step]['name']}」完成", confidence=0.9,
            )

    created_sessions = 0
    now = dt.datetime.now(dt.timezone.utc)
    for index, session_spec in enumerate(spec.get("sessions", [])):
        exists = db.execute(
            select(RuntimeSession)
            .where(RuntimeSession.actor_id == session_spec["actor_id"])
            .where(RuntimeSession.runtime == session_spec["runtime"])
            .where(RuntimeSession.external_id == session_spec["external_id"])
        ).scalar()
        if exists:
            continue
        updated = now - dt.timedelta(hours=index * 3)
        started = updated - dt.timedelta(minutes=18)
        raw_messages = session_spec.get("messages", [])
        messages = [
            {
                **message,
                "at": (started + dt.timedelta(minutes=step * 4)).isoformat(),
            }
            for step, message in enumerate(raw_messages)
        ]
        task = None
        if session_spec.get("task_title"):
            task = db.execute(
                select(Task).where(Task.title == session_spec["task_title"])
            ).scalar()
        digest = hashlib.sha256(
            json.dumps(session_spec, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        db.add(
            RuntimeSession(
                actor_id=session_spec["actor_id"],
                runtime=session_spec["runtime"],
                external_id=session_spec["external_id"],
                node=db.get(Actor, session_spec["actor_id"]).node,
                title=session_spec["title"],
                summary=session_spec["summary"],
                privacy=session_spec["privacy"],
                cursor=session_spec["message_count"],
                content_hash=digest,
                message_count=session_spec["message_count"],
                messages_json=json.dumps(messages, ensure_ascii=False),
                task_id=task.id if task else None,
                started_at=started,
                updated_at=updated,
                synced_at=now,
            )
        )
        created_sessions += 1

    today = dt.date.today()
    agents = [a for a in spec["actors"] if a[1] == "agent"]
    for offset in range(7):
        date = (today - dt.timedelta(days=offset)).isoformat()
        for index, (actor_id, _kind, _name, runtime, _model, _node) in enumerate(agents):
            exists = db.execute(
                select(TokenUsage)
                .where(TokenUsage.actor_id == actor_id)
                .where(TokenUsage.date == date)
                .where(TokenUsage.runtime == runtime)
            ).scalar()
            if exists:
                continue
            base = (index + 2) * 9000 + offset * 3100
            db.add(
                TokenUsage(
                    actor_id=actor_id,
                    date=date,
                    runtime=runtime,
                    input_tokens=base * 4,
                    output_tokens=base,
                )
            )
    db.flush()
    return {
        "actors": len(spec["actors"]),
        "tasks": created_tasks,
        "sessions": created_sessions,
    }

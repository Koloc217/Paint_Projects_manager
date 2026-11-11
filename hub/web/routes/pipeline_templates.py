"""Pipeline templates: editing them and adding their steps to a project as tasks."""
import re
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from hub.core import pipeline
from hub.core.common import TASK_NOT_STARTED, edit_config, live_project, load_config
from hub.core.database import Task
from hub.core.i18n import tr
from hub.web.deps import get_db
from hub.web.responses import fail
from hub.web.schemas import TemplateRequest, TemplatesUpdate
from hub.web.validation import parse_date_field

router = APIRouter()


def _step_match_key(name: str) -> str:
    """A step name for comparison: without a leading number ("2. ", "3) "), outer spaces and case.
    This way tasks created by older numbered templates are recognized too."""
    return re.sub(r"^\s*\d+\s*[.)]\s*", "", name or "").strip().casefold()


def existing_template_steps(db: Session, project_id: str, templates: list) -> Dict[str, List[int]]:
    """Step numbers of every template that already exist among the project's tasks (in any UI language)."""
    have = {_step_match_key(name) for (name,) in db.query(Task.task_name).filter(Task.project_id == project_id)}
    return {t["id"]: [i for i, raw in enumerate(t["steps"])
                      if any(_step_match_key(v) in have for v in pipeline.step_variants(raw))]
            for t in templates}


@router.get("/api/pipeline-templates")
def get_pipeline_templates(project_id: Optional[str] = None, db: Session = Depends(get_db)):
    """Templates in the UI language, the built-in ones (for "Reset") and, for project_id, the steps the project already has."""
    cfg = load_config()
    raw = pipeline.get_templates(cfg)
    existing = existing_template_steps(db, project_id, raw) if project_id else {t["id"]: [] for t in raw}
    return {"templates": pipeline.for_client(cfg), "defaults": pipeline.defaults_for_client(),
            "existing": existing, "custom": "PIPELINE_TEMPLATES" in cfg}


@router.post("/api/pipeline-templates")
def save_pipeline_templates(data: TemplatesUpdate):
    with edit_config() as cfg:
        if data.reset:
            cfg.pop("PIPELINE_TEMPLATES", None)
        else:
            err = pipeline.save(cfg, data.templates)
            if err:
                return fail(err)
    return {"status": "ok", "templates": pipeline.for_client(cfg)}

@router.post("/api/projects/{project_id}/template")
def apply_project_template(project_id: str, data: Optional[TemplateRequest] = None, db: Session = Depends(get_db)):
    proj = live_project(db, project_id)
    if not proj:
        return fail(tr("err.project_not_found"))

    cfg = load_config()
    kind = (data.kind if data else "print")
    template = pipeline.find(cfg, kind)
    if template is None:
        return fail(tr("err.unknown_template"))
    keys = template["steps"]

    if data is None or data.steps is None:
        plan = [(i, proj.estimate) for i in range(len(keys))]
    else:
        plan, seen = [], set()
        for step in sorted(data.steps, key=lambda x: x.index):
            if not 0 <= step.index < len(keys) or step.index in seen:
                return fail(tr("err.bad_template_step"))
            seen.add(step.index)
            dead, err = parse_date_field(step.deadline)
            if err:
                return fail(tr("err.bad_template_date"))
            plan.append((step.index, dead))
        if not plan:
            return fail(tr("template.none_selected"))

    # Re-applying a template does not create duplicates: steps the project already has are skipped
    already = set(existing_template_steps(db, project_id, [template])[kind])
    skipped = sum(1 for i, _ in plan if i in already)
    plan = [(i, dead) for i, dead in plan if i not in already]
    if not plan:
        return fail(tr("template.all_exist"))

    for i, dead in plan:
        db.add(Task(
            project_id=project_id,
            task_name=pipeline.text(keys[i]),
            deadline=dead,
            status=TASK_NOT_STARTED,
            postponed=0
        ))
    proj.updated_at = datetime.now()
    db.commit()
    return {"status": "ok", "added": len(plan), "skipped": skipped}

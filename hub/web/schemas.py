"""Request bodies of the JSON API."""
from typing import Dict, List, Optional

from pydantic import BaseModel

from hub.core.constants import STATUS_PLANNED

class ActionPayload(BaseModel):
    action: str
    id: Optional[str] = None
    status: Optional[str] = None
    stage: Optional[str] = None

class ProjectCreate(BaseModel):
    name: str
    priority: str = "Medium"
    estimate: Optional[str] = None
    token_reward: float = 0         # a whole number; float here only so that 2.5 gets a readable error, not a 422
    status: str = STATUS_PLANNED

class ProjectEdit(BaseModel):
    name: str
    priority: str
    estimate: Optional[str] = None

class ProjectRename(BaseModel):
    name: str
    priority: Optional[str] = None      # None: leave the priority as is

class PhotoUpload(BaseModel):
    image: str  # data:image/jpeg;base64,... (the browser resizes and converts to JPEG)
    thumb: Optional[str] = None    # small JPEG preview for the phone layout (the browser makes it too)

class RefUpload(BaseModel):
    image: str                     # the original file as is (data URL, base64)
    thumb: Optional[str] = None    # small JPEG preview made by the browser

class ProgressUpload(BaseModel):
    image: str                     # JPEG resized by the browser (data URL)
    thumb: Optional[str] = None    # small preview
    note: str = ""

class RefOrder(BaseModel):
    ids: List[int]                 # reference ids in the new order

class ProgressNote(BaseModel):
    note: str = ""

class PaintNotesUpdate(BaseModel):
    paint_notes: str = ""

class TaskCreate(BaseModel):
    project_id: Optional[str] = None        # empty = a general task outside any project
    task_name: str
    deadline: Optional[str] = None

class TaskEdit(BaseModel):
    task_name: str
    deadline: Optional[str] = None
    spent: Optional[str] = None        # time spent as typed ("90", "1:30", "1.5ч"); "" clears it; None: keep

class ProjectSpent(BaseModel):
    extra: Optional[str] = None        # time on the project outside its tasks, as typed; "" clears it

class DaySchedule(BaseModel):
    enabled: bool = True
    time: str = "18:05"

class ConfigUpdate(BaseModel):
    dev_mode: bool
    project_cost: float = 10        # a whole number, checked by parse_token_field
    delete_keeps_balance: bool = True
    calendar_export: Optional[bool] = None      # None: an older page without the field, keep the setting
    tg_cleanup: Optional[str] = None            # keep_46h | latest_only | off; None keeps the setting
    allow_postpone: bool = True
    stagnation_alert: bool = True
    stagnation_days: int = 10
    friday_digest: bool = True
    monthly_report: bool = True
    notify_enabled: bool = True
    notify_days: Optional[Dict[str, DaySchedule]] = None
    language: Optional[str] = None
    date_format: Optional[str] = None
    ask_spent_time: Optional[bool] = None       # the bot asks for the time when a task is closed; None: keep

class TemplateStep(BaseModel):
    index: int                         # step number in the template (from 0)
    deadline: Optional[str] = None     # 'YYYY-MM-DD' or empty

class TemplateRequest(BaseModel):
    kind: str = "print"                # template id from config.json (PIPELINE_TEMPLATES)
    # None: all steps with the project deadline; a list: only these steps, each with its own deadline
    steps: Optional[List[TemplateStep]] = None

class TemplatesUpdate(BaseModel):
    templates: Optional[List[dict]] = None
    reset: bool = False

class VacationRequest(BaseModel):
    days: int

class ProxyForm(BaseModel):
    enabled: bool = False
    url: Optional[str] = None          # empty: keep the current address

class CalendarFeedBody(BaseModel):
    enabled: bool = False
    new_token: bool = False

"""The API split by area; server.py includes every router listed here."""
from hub.web.routes import (backup, calendar_feed, data, device_api, journal, pages, photos, pipeline_templates, projects,
                            settings, tasks, telegram, trash)

ROUTERS = [pages.router, data.router, settings.router, telegram.router, calendar_feed.router, device_api.router,
           journal.router, backup.router, projects.router, photos.router, tasks.router, pipeline_templates.router,
           trash.router]

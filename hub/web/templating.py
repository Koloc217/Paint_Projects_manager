"""The Jinja2 environment of the pages."""
import os

from fastapi.templating import Jinja2Templates

from hub.bot.bot import FEATURES as BOT_FEATURES, PHOTO_HELP as BOT_PHOTO_HELP, active_commands as bot_active_commands
from hub.core.i18n import tr
from hub.paths import BASE_DIR
from hub.web.static import static_url

templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
# The translation table goes into the page as is (UTF-8) rather than as \uXXXX: the page is smaller;
# <, > and & are still escaped, so the data cannot close the <script> tag.
templates.env.globals["tr"] = tr          # in templates: {{ tr('common.save') }}
# Bot help in Settings -> Telegram is built from the same registry as /help in bot.py, so the texts never diverge
templates.env.globals["bot_help"] = lambda: {"commands": bot_active_commands(), "photos": BOT_PHOTO_HELP, "features": BOT_FEATURES}
templates.env.policies["json.dumps_kwargs"] = {"ensure_ascii": False, "sort_keys": True}
templates.env.globals["static_url"] = static_url

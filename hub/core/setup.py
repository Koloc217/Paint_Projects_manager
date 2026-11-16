"""The first start of a new installation.

A new project costs tokens and tokens come from finished projects, so an empty installation could not create its
first project at all. config.json written by a brand-new installation carries "FIRST_RUN": true; while it is set
and there are no projects yet, the page asks once how to start:

  • tokens  a starting balance (INITIAL_TOKENS), then the regular rules;
  • dev     DEV_MODE: projects are free and deadlines editable, automatic notifications are silent.

Either answer removes the flag. Installations that existed before this (their config.json has no flag) are never asked.
"""
from hub.core.appconfig import edit_config, load_config
from hub.core.database import Project

MODE_TOKENS = "tokens"
MODE_DEV = "dev"


def needed(db, config: dict = None) -> bool:
    config = config if config is not None else load_config()
    return config.get("FIRST_RUN") is True and db.query(Project.id).first() is None


def finish(db, mode: str, tokens: int = 0) -> bool:
    """Applies the choice. False if there is nothing to set up (already done, or projects exist)."""
    with edit_config() as cfg:
        if not needed(db, cfg):
            return False
        if mode == MODE_TOKENS:
            cfg["INITIAL_TOKENS"] = tokens
        else:
            cfg["DEV_MODE"] = True
        cfg.pop("FIRST_RUN", None)
    return True

"""Folders the app works with. The one place where they are decided.

BASE_DIR  the repository root: templates/, static/, locales.json, tools/ (the code and its assets).
DATA_DIR  tracker.db, config.json, .env, auth.json, photos/, logs/ and service files. Next to the code by default;
          PROJECT_HUB_DATA_DIR moves it elsewhere (the tests point it to a temp folder).
Moving modules between hub/ subpackages does not change either path, so existing installations keep their data.
"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.abspath(os.environ.get("PROJECT_HUB_DATA_DIR") or BASE_DIR)

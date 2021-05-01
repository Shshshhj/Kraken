import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _replace(key: str) -> None:
    """replace empty string '' to None"""
    if key == "":
        return None

    return key


@dataclass
class BotConfig:
    """
    Bot configuration
    """

    def __init__(self) -> "BotConfig":
        if os.path.isfile("config.env"):
            load_dotenv("config.env")

        # Optional
        path = _replace(os.environ.get("DOWNLOAD_PATH"))
        self.downloadPath = Path(path) if path else Path.home() / "downloads"

        self.token = _replace(os.environ.get("BOT_TOKEN"))

        # Core config
        self.api_id = int(os.environ.get("API_ID", 0))
        self.api_hash = os.environ.get("API_HASH")
        self.db_uri = os.environ.get("DB_URI")
        self.string_session = os.environ.get("STRING_SESSION")

        # GoogleDrive
        try:
            self.gdrive_secret = json.loads(os.environ.get("G_DRIVE_SECRET"))
        except (TypeError, json.decoder.JSONDecodeError):
            self.gdrive_secret = None
        self.gdrive_folder_id = _replace(os.environ.get("G_DRIVE_FOLDER_ID"))
        self.gdrive_index_link = _replace(os.environ.get("G_DRIVE_INDEX_LINK"))

        # Checker
        self.secret = bool(os.environ.get("CONTAINER") == "True")

        # Github
        self.github_repo = (_replace(os.environ.get("GITHUB_REPO"))
                            or "adekmaulana/caligo")
        self.github_token = _replace(os.environ.get("GITHUB_TOKEN"))

        # Heroku
        self.heroku_app_name = _replace(os.environ.get("HEROKU_APP"))
        self.heroku_api_key = _replace(os.environ.get("HEROKU_API_KEY"))

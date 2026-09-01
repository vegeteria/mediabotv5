import json
import os
from pathlib import Path

SETTINGS_FILE = Path(__file__).parent.parent / "user_settings.json"

class UserSettingsManager:
    def __init__(self, filepath: Path):
        self.filepath = filepath
        self.settings = {}
        self._load()

    def _load(self):
        if self.filepath.exists():
            try:
                with open(self.filepath, "r") as f:
                    self.settings = json.load(f)
            except (json.JSONDecodeError, OSError):
                self.settings = {}
        else:
            self.settings = {}

    def _save(self):
        try:
            with open(self.filepath, "w") as f:
                json.dump(self.settings, f, indent=4)
        except OSError:
            pass

    def set_user_throttle(self, user_id: int, tasks: int):
        user_str = str(user_id)
        if user_str not in self.settings:
            self.settings[user_str] = {}
        self.settings[user_str]["throttle"] = tasks
        self._save()

    def get_user_throttle(self, user_id: int) -> int:
        user_str = str(user_id)
        if user_str in self.settings and "throttle" in self.settings[user_str]:
            return self.settings[user_str]["throttle"]
            
        try:
            return int(os.environ.get("THROTTLE_TASKS", 10))
        except ValueError:
            return 10

user_settings = UserSettingsManager(SETTINGS_FILE)

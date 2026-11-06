"""The settings page for the user"""

import asyncio
import logging
import toga
from toga.style import Pack
from toga.style.pack import COLUMN, ROW

from osaeru import claude_sync

logger = logging.getLogger(__name__)


def _describe_stored_key() -> str:
    key = claude_sync.get_api_key()
    if not key:
        return "No API ket stored"
    return f"API key saved, ending with {key[-4:]}"


def build(conn) -> toga.Box:
    current_label = toga.Label(_describe_stored_key(), style=Pack(padding_top=8))
    key_input = toga.PasswordInput(placeholder="sk-ant-...", style=Pack(flex=1))
    status_label = toga.Label("", style=Pack(padding_top=8))
    
    async def on_save(widget):
        key = (key_input.value or "").strip()
        if not key:
            status_label.text = "Please enter a valid API key"
            return
        if not key.startswith("sk-ant-"):
            status_label.text = "Please enter a valid API key starting with 'sk-ant-'"
            return

        save_button.enabled = False
        status_label.text = "Checking key..."
        try:
            ok = await asyncio.get_running_loop().run_in_executor(None, claude_sync.verify_api_key, key)
            if ok is False:
                status_label.text = "Anthropic rejected that key, Not saved."
                return
            
            where = claude_sync.set_api_key(key)
            key_input.value = ""
            current_label.text = _describe_stored_key()
            status_label.text = (
                f"Key verified and saved to {where}."
                if ok
                else f"Saved to {where}, but couldn't verify it (offline)."
            )
        except Exception as e:
            logger.error("Saving API key failed: %s", e)
            status_label.text = f"Couldn't save key: {e}"
        finally:
            save_button.enabled = True
    
    def on_remove(widget):
        claude_sync.clear_api_key()
        current_label.text = _describe_stored_key()
        status_label.text = "API key removed."
    
    save_button = toga.Button("Save", on_press=on_save, style=Pack(padding_left=8))
    remove_button = toga.Button("Remove saved key", on_press=on_remove, style=Pack(padding_top=8))
    
    key_row = toga.Box(style=Pack(direction=ROW, padding_top=4))
    key_row.add(key_input)
    key_row.add(save_button)
    
    box = toga.Box(style=Pack(direction=COLUMN, padding=16))
    box.add(toga.Label("Claude API key", style=Pack(font_weight="bold")))
    box.add(current_label)
    box.add(key_row)
    box.add(remove_button)
    box.add(status_label)
    box.add(
        toga.Label(
            "Create a keyt at https://console.anthropic.com/keys and paste it here.",
            style=Pack(padding_top=16),
        )
    )
    
    return box

"""Containing funcs to render the session notes interface and handle user input."""

import toga
from toga.style import Pack
from toga.style.pack import COLUMN

from osaeru import database as db


def build(conn) -> toga.Box:
    """Builds the session notes interface.

    Args:
        conn (_type_): Database connection.

    Returns:
        toga.Box: The session notes interface box containing the note input, 
        feeling selection, save button, status label, and history list.
    """
    note_input = toga.MultilineTextInput(
        placeholder="What happened in today's session?"
    )
    feeling_input = toga.Selection(
        items=["great", "good", "okay", "tired", "frustrated"]
    )
    status_label = toga.Label("")
    history_list = toga.Table(
        headings=["Date", "Feeling", "Notes"],
        data=[]
    )
    
    def refresh_history():
        """Refreshes the history list with the latest session notes from the database.
        """
        rows = db.list_sessions(conn)
        history_list.data = [
            (r["occurred_at"][:10], r["feeling"] or "", r["notes"][:60]) for r in rows
        ]
    
    def on_save(widget):
        """Func to handle the save button press event. It saves the session 
        note to the database and updates the status label. 
        If the note input is empty, it prompts the user to write something first.

        Args:
            widget (_type_): The widget that triggered the event (save button).
        """
        if not note_input.value.strip():
            status_label.text = "Write something first."
            return
        
        db.add_session_note(
            conn,
            note_input.value.strip(),
            feeling_input.value,
        )
        status_label.text = "Saved - will sync to Claude when you're online."
        note_input.value = ""
        refresh_history()
    
    save_button = toga.Button("Save note", on_press=on_save)
    
    box = toga.Box(style=Pack(direction=COLUMN, padding=16))
    box.add(toga.Label("How did it feel?"))
    box.add(feeling_input)
    box.add(toga.Label("Notes", style=Pack(padding_top=8)))
    box.add(note_input)
    box.add(save_button)
    box.add(status_label)
    box.add(toga.Label("Past sessions", style=Pack(padding_top=16)))
    box.add(history_list)
    
    refresh_history()
    
    return box

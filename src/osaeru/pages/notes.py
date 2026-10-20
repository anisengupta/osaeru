import toga
from toga.style import Pack
from toga.style.pack import COLUMN

from osaeru import database as db


def build(conn) -> toga.Box:
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
        rows = db.list_sessions(conn)
        history_list.data = [
            (r["occurred_at"][:10], r["feeling"] or "", r["notes"][:60]) for r in rows
        ]
    
    
    def on_save(widget):
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

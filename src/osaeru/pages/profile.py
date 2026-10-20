import toga
from toga.style import Pack
from toga.style.pack import COLUMN

from osaeru import database as db


def build(conn) -> toga.Box:
    existing = db.get_profile(conn)
    
    age_input = toga.TextInput(value=str(existing["age"]) if existing else "")
    sex_input = toga.Selection(items=["male", "female", "other"])
    
    if existing and existing["sex"] in ("male", "female", "other"):
        sex_input.value = existing["sex"]
    
    weight_input = toga.TextInput(
        value=str(existing["weight_kg"]) if existing else ""
    )
    
    frequency_input = toga.TextInput(
        value=str(existing["sessions_per_week"]) if existing else "",
        placeholder="sessions per week",
    )
    
    status_label = toga.Label("")
    
    def on_save(widget):
        try:
            db.save_profile(
                conn,
                age=int(age_input.value),
                sex=sex_input.value,
                weight_kg=float(weight_input.value),
                sessions_per_week=float(frequency_input.value),
            )
            status_label.text = "Saved."
        except (ValueError, TypeError):
            status_label.text = "Check that age/weight/frequency are numbers."
    
    save_button = toga.Button("Save", on_press=on_save)
    
    box = toga.Box(style=Pack(direction=COLUMN, padding=16))
    for label_text, widget in [
        ("Age", age_input),
        ("Sex", sex_input),
        ("Weight (kg)", weight_input),
        ("Sessions per week", frequency_input),
    ]:
        box.add(toga.Label(label_text, style=Pack(padding_top=8)))
        box.add(widget)
    
    box.add(save_button)
    box.add(status_label)
    
    return box

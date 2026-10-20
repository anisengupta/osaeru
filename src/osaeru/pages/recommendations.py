import toga
from toga.style import Pack
from toga.style.pack import COLUMN, ROW

from osaeru import database as db


def build(conn) -> toga.Box:
    list_box = toga.Box(style=Pack(direction=COLUMN))
    
    def refresh():
        list_box.clear()
        recs = db.list_recommendations(conn, status="new")
        if not recs:
            list_box.add(toga.Label("No recommendations - add session notes to generate some"))
            return
        
        for rec in recs:
            row = toga.Box(style=Pack(direction=ROW, padding=8))
            row.add(toga.Label(rec["text"], style=Pack(flex=1)))
            
            def make_handler(rec_id, new_status):
                def handler(widget):
                    db.set_recommendation_status(conn, rec_id, new_status)
                    refresh()
                return handler
            
            row.add(toga.Button("Tried", on_press=make_handler(rec["id"], "tried")))
            row.add(toga.Button("Skip", on_press=make_handler(rec["id"], "skipped")))
            list_box.add(row)
        
    refresh_button = toga.Button("Refresh", on_press=lambda w: refresh())
    
    box = toga.Box(style=Pack(direction=COLUMN, padding=16))
    box.add(refresh_button)
    box.add(list_box)
    refresh()
    
    return box

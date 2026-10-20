import toga
from toga.style import Pack
from toga.style.pack import COLUMN, ROW

from osaeru import claude_sync, database as db


def build(conn) -> toga.Box:
    history_view = toga.MultilineTextInput(readonly=True, style=Pack(flex=1))
    message_input = toga.TextInput(
        placeholder="Talk about the session...", 
        style=Pack(flex=1)
    )
    status_label = toga.Label("")
    
    def render_history():
        lines = []
        for m in db.list_chat_messages(conn):
            speaker = "You" if m["role"] == "user" else "Coach"
            lines.append(f"{speaker}: {m['content']}")
        
        history_view.value = "\n\n".join(lines)
    
    def on_send(widget):
        text = message_input.value.strip()
        if not text:
            return
        
        db.add_chat_message(conn, "user", text)
        message_input.value = ""
        render_history()
        
        try:
            history = [
                {"role": m["role"], "content": m["content"]}
                for m in db.list_chat_messages(conn)
                if m["role"] in ("user", "assistant")
            ]
            flowchart = db.get_flowchart(conn)
            reply = claude_sync.chat_reply(history, flowchart)
            db.add_chat_message(conn, "assistant", reply)
            status_label.text = ""
        except Exception:
            status_label.text = "Couldn't reach Claude - message saved, will need a reply once you're online"
        
        render_history()
    
    send_button = toga.Button("Send", on_press=on_send)
    
    input_row = toga.Box(style=Pack(direction=ROW, padding_top=8))
    input_row.add(message_input)
    input_row.add(send_button)
    
    box = toga.Box(style=Pack(direction=COLUMN, padding=16))
    box.add(history_view)
    box.add(input_row)
    box.add(status_label)
    
    render_history()
    
    return box

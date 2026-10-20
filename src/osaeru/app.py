import threading
import toga

from osaeru import claude_sync, database as db
from osaeru.pages import chat, flowchart, notes, profile, recommendations


class OsaeruApp(toga.App):
    def startup(self):
        self.conn = db.connect()
        
        tabs = toga.OptionContainer(
            content=[
                ("Profile", profile.build(self.conn)),
                ("Notes", notes.build(self.conn)),
                ("Flowchart", flowchart.build(self.conn)),
                ("Chat", chat.build(self.conn)),
                ("Recommendations", recommendations.build(self.conn)),
            ]
        )
        
        self.main_window = toga.MainWindow(title=self.formal_name)
        self.main_window.content = tabs
        self.main_window.show()
        
        threading.Thread(target=self._try_sync, daemon=True).start()
    
    def _try_sync(self):
        try:
            claude_sync.run_sync(self.conn)
        except Exception:
            pass


def main():
    return OsaeruApp("BJJ Coach", "com.yourname.bjjcoach")

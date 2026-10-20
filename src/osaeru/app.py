"""App module for the Osaeru application."""

import threading
import toga

from osaeru import claude_sync, database as db
from osaeru.pages import chat, flowchart, notes, profile, recommendations


class OsaeruApp(toga.App):
    """The main application class for Osaeru, a BJJ coaching app.

    Args:
        toga (_type_): The Toga library used for building the GUI.
    """

    def startup(self):
        """Starts the application by establishing a database connection,
        building the main window with tabs for different features,
        and starting a background thread for syncing with Claude.
        """
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
        """Tries to run the sync process with Claude in a background thread."""
        try:
            claude_sync.run_sync(self.conn)
        except Exception:
            pass


def main():
    """The main entry point for the Osaeru application.

    Returns:
        _type_: An instance of the OsaeruApp class,
        initialized with the app name and identifier.
    """
    return OsaeruApp("BJJ Coach", "com.yourname.bjjcoach")

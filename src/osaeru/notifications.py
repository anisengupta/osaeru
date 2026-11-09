"""Local on-device notifications - eg "how are you feeing before you start your session?
Fired with zero internet connection since they are scheduled entirely on your device.
"""

from __future__ import annotations
import datetime as dt


def schedule_pre_session_check_in(
    fire_at: dt.datetime, message: str = "How are you feeling before your session?"
) -> None:
    """Schedules a local notification on the device to check in with the user
    before their session.

    Args:
        fire_at (dt.datetime): The datetime when the notification should be fired.
        message (str, optional): The message to send to the user. Defaults to
        "How are you feeling before your session?".

    Raises:
        NotImplementedError: Raises if the function is not implemented for
        the current platform.
    """
    raise NotImplementedError(
        "This function is a placeholder for scheduling a local notification on the device. "
    )


def cancel_all_scheduled() -> None:
    """Cancels all scheduled local notifications on the device.

    Raises:
        NotImplementedError: Raises if the function is not implemented for
        the current platform.
    """
    raise NotImplementedError(
        "This function is a placeholder for canceling all scheduled local notifications on the device. "
    )

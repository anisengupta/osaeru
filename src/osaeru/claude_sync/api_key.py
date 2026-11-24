"""Funcs for API key operations for the database."""

import keyring
from anthropic import Anthropic
import logging

from osaeru.database.helper import API_KEY_SETTING
from osaeru.config import KEYRING_SERVICE, KEYRING_KEY

logger = logging.getLogger(__name__)


def get_api_key() -> Optional[str]:
    """Retrieves the Anthropic API key from the OS keychain.

    Returns:
        Optional[str]: The Anthropic API key if it exists, otherwise None.
    """
    if _use_keychain():
        try:
            key = keyring.get_password(KEYRING_SERVICE, KEYRING_KEY)
            if key:
                return key
        except keyring.errors.KeyringError as e:
            logger.warning("Keyring read failed, trying the database: %s", e)

    return _db_get_key()


def set_api_key(api_key: str) -> str:
    """Sets the Anthropic API key in the OS keychain.

    Args:
        api_key (str): The Anthropic API key to store.
    """
    api_key = api_key.strip()
    if _use_keychain():
        try:
            keyring.set_password(KEYRING_SERVICE, KEYRING_KEY, api_key)
            return "keychain"
        except keyring.errors.KeyringError as e:
            logger.warning("Keyring write failed, using the database: %s", e)

    conn = db.connect()
    try:
        db.set_setting(conn, API_KEY_SETTING, api_key)
    finally:
        conn.close()

    return "the database"


def clear_api_key() -> None:
    """Cleared the stored Anthropic API key from both the OS keychain
    and the local file storage.
    """
    if _keyring_available():
        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_KEY)
        except keyring.errors.KeyringError:
            pass

    conn = db.connect()
    try:
        db.delete_setting(conn, API_KEY_SETTING)
    finally:
        conn.close()


def verify_api_key(api_key: str) -> Optional[bool]:
    """Verifies the provided Anthropic API key by attempting to list models.

    Args:
        api_key (str): The Anthropic API key to verify.

    Returns:
        Optional[bool]: True if the key is valid, False if invalid, and None
        if verification failed due to an unexpected error.
    """
    try:
        Anthropic(api_key=api_key.strip(), timeout=10.0, max_retries=0).models.list(
            limit=1
        )
        return True
    except anthropic.AuthenticationError:
        return False
    except anthropic.PermissionDeniedError:
        return False
    except Exception as e:
        logger.info("Couldn't verify API key: %s", e)
        return None
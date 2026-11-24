"""Helper funcs for database operations."""

import keyring
from anthropic import Anthropic
import json
from typing import Dict, Any, Optional


API_KEY_SETTING = "anthropic_api_key"


def _keyring_available() -> bool:
    """Checks if the OS keyring is available for storing and retrieving
    the Anthropic API key.

    Returns:
        bool: True if the keyring is available, False otherwise.
    """
    try:
        return not isinstance(keyring.get_keyring(), keyring.backends.fail.Keyring)
    except Exception:
        return False


def _client() -> Anthropic:
    """Creates an Anthropic client using the stored API key.

    Raises:
        RuntimeError: If no Anthropic API key is stored in the OS keychain.

    Returns:
        Anthropic: An instance of the Anthropic client initialized
        with the stored API key.
    """
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError(
            "No Anthropic API key stored. Prompt the user to enter one in "
            "Settings before calling anything in this module."
        )

    return Anthropic(api_key=api_key)


def _extract_json(text: str) -> Dict[str, Any]:
    """Extracts JSON content from a given text string, handling cases where
    the JSON is wrapped in code block formatting.

    Args:
        text (str): The input text containing JSON content, possibly
        wrapped in code block formatting.

    Returns:
        Dict[str, Any]: The parsed JSON content as a dictionary.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]

    return json.loads(cleaned.strip())


def _use_keychain() -> bool:
    """Sets whether to use the keychain.

    Returns:
        bool: AS bool to indicate if the keychain
        is to be used.
    """
    return (
        db.backend() == "sqlite"
        and not os.environ.get("K_SERVICE")
        and _keyring_available()
    )


def _db_get_key() -> Optional[str]:
    """Gets the API stored in the database.

    Returns:
        Optional[str]: The Anthropic API key
        retrieved from the database.
    """
    conn = db.connect()
    try:
        value = db.get_setting(conn, API_KEY_SETTING)
    finally:
        conn.close()

    return (value or "").strip() or None
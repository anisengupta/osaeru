"""
Module containing funcs to sync data with Claude API.

Only Module that requires an internet connetion to perform the following:

1. Store/retrieve the Anthropic API key from OS keychain
2. Drain the sync_queue: for each queued session note or chat message,
ask Claude to
    (a) Extract skills/edges to merge into the flowchart,
    (b) Suggest recommendations
3. Handle ordinary chat replies.
"""

from __future__ import annotations
import json
from typing import Any, Optional, Dict, List
import os
from pathlib import Path
import keyring
import keyring.errors
import keyring.backends.fail
import anthropic
from anthropic import Anthropic
import logging
import traceback

from osaeru.config import KEYRING_KEY, KEYRING_SERVICE, MODEL, FLOWCHART_SYSTEM_PROMPT
from osaeru import database as db

logger = logging.getLogger(__name__)


API_KEY_SETTING = "anthropic_api_key"


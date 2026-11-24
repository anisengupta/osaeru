"Local storage for app, when working in Cloud Run, the storage is persistant, when working locally, SQLite is used."

from __future__ import annotations
import atexit
import datetime as dt
import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional
from sqlalchemy import (
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    func,
    select,
    text,
    update,
)
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import Connection, Engine, RowMapping
from sqlalchemy.pool import NullPool

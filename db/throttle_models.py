"""
Rate limit config for key actions eg log in, submit flag....
"""

from datetime import datetime, timezone
from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from db.orm import db


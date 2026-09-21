"""Initial schema bootstrap. Run explicitly before production app startup.

This MVP uses SQLAlchemy metadata; adopt Alembic revisions before changing deployed schema.
"""

from app.db import engine
from app.models import Base

Base.metadata.create_all(engine)

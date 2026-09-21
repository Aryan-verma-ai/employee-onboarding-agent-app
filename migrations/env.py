import os

from alembic import context
from sqlalchemy import create_engine

from app.jobs import ExtractionJob
from app.models import Base

assert ExtractionJob.__table__.metadata is Base.metadata

config = context.config
url = os.environ.get("DATABASE_URL", "sqlite:///./onboarding.db")
if context.is_offline_mode():
    context.configure(url=url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with create_engine(url).connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()

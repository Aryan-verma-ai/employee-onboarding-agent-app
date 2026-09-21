"""Apply versioned migrations; never bypass PostgreSQL RLS with create_all."""

from alembic import command
from alembic.config import Config

if __name__ == "__main__":
    command.upgrade(Config("alembic.ini"), "head")

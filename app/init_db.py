"""Initialize JobRadar database tables for the configured backend."""
from .api.job_data import init_db as initialize_storage


def init_db():
    initialize_storage()
    print("Database initialized.")


if __name__ == "__main__":
    init_db()

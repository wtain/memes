from config.settings import settings

DATABASE_URL = settings.get('DATABASE_URL')

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")
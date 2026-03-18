from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv('DATABASE_URL')

# Only echo SQL in debug mode to avoid flooding logs during normal operation
_echo = os.getenv('DB_ECHO', 'false').lower() == 'true'
engine = create_engine(DATABASE_URL, echo=_echo, future=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
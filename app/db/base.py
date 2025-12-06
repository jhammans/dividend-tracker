from sqlalchemy.orm import declarative_base

# Create the base class for all models
Base = declarative_base()

# Import all models so they are registered with Base
from app.models import Stock, StockPrice, Dividend, Holding, Transaction, Alert, AIAnalysis, PortfolioMetric

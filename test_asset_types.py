#!/usr/bin/env python3
"""Test yfinance asset type detection for various ETF/fund tickers"""
import yfinance as yf
from pprint import pprint

test_tickers = ["SCHD", "VGT", "GLD", "SPYG", "QQQM", "KO", "AAPL"]

print("=" * 80)
print("Testing yfinance asset type detection")
print("=" * 80)

for ticker in test_tickers:
    print(f"\n{ticker}:")
    print("-" * 40)
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        
        # Extract relevant fields
        quote_type = info.get('quoteType', 'N/A')
        category = info.get('category', 'N/A')
        fund_family = info.get('fundFamily', 'N/A')
        long_name = info.get('longName', 'N/A')
        
        print(f"  quoteType:  {quote_type}")
        print(f"  category:   {category}")
        print(f"  fundFamily: {fund_family}")
        print(f"  longName:   {long_name}")
        
        # Show all available keys (for reference)
        print(f"\n  Available info keys: {list(info.keys())[:10]}... ({len(info.keys())} total)")
        
    except Exception as e:
        print(f"  Error: {e}")

print("\n" + "=" * 80)
print("Summary: Check quoteType and category values above")
print("=" * 80)

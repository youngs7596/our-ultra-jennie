import os
import sys
import logging
import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
from datetime import datetime, timedelta

# Project Root Setup
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)

from shared.database.core import get_db_connection
from shared.db.connection import init_engine, get_session
from dotenv import load_dotenv

# Logger Setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def load_data():
    """Load data from MariaDB"""
    load_dotenv()
    
    # Init DB
    init_engine(None, None, None, None)
    session = get_session()
    engine = session.get_bind()
    
    logger.info("Loading Stock Prices (3Y)...")
    # Using STOCK_DAILY_PRICES_3Y or STOCK_DAILY_PRICES depending on availability
    # Checking models.py, StockDailyPrice maps to STOCK_DAILY_PRICES_3Y
    query_price = """
        SELECT STOCK_CODE, PRICE_DATE, CLOSE_PRICE, OPEN_PRICE 
        FROM STOCK_DAILY_PRICES_3Y
        WHERE PRICE_DATE >= '2022-01-01'
    """
    df_price = pd.read_sql(query_price, engine)
    df_price['PRICE_DATE'] = pd.to_datetime(df_price['PRICE_DATE'])
    
    logger.info(f"Loaded {len(df_price)} price records.")

    logger.info("Loading News Sentiment...")
    # STOCK_NEWS_SENTIMENT (Raw or ORM)
    # Using Raw SQL for wider coverage if ORM was just added
    query_news = """
        SELECT STOCK_CODE, NEWS_DATE, SENTIMENT_SCORE
        FROM STOCK_NEWS_SENTIMENT
        WHERE NEWS_DATE >= '2022-01-01'
    """
    df_news = pd.read_sql(query_news, engine)
    df_news['NEWS_DATE'] = pd.to_datetime(df_news['NEWS_DATE']).dt.normalize()
    
    logger.info(f"Loaded {len(df_news)} news records.")

    logger.info("Loading Investor Trading...")
    query_investor = """
        SELECT STOCK_CODE, TRADE_DATE, FOREIGN_NET_BUY, INSTITUTION_NET_BUY
        FROM STOCK_INVESTOR_TRADING
        WHERE TRADE_DATE >= '2022-01-01'
    """
    df_investor = pd.read_sql(query_investor, engine)
    df_investor['TRADE_DATE'] = pd.to_datetime(df_investor['TRADE_DATE'])
    
    logger.info(f"Loaded {len(df_investor)} investor records.")
    
    return df_price, df_news, df_investor

def calculate_returns(df_price):
    """Calculate forward returns"""
    df = df_price.pivot(index='PRICE_DATE', columns='STOCK_CODE', values='CLOSE_PRICE')
    
    # Forward Returns: (Close_t+n - Close_t) / Close_t
    # We want to know if signal at time t predicts return at t+n
    # Usually: Signal at Close_t (or before Open_t+1) -> Return form Open_t+1 to Close_t+n OR Close_t to Close_t+n
    # Let's use Close-to-Close for simplicity and standard IC definition.
    
    returns = {}
    for days in [1, 3, 5, 10, 20]:
        # Ref: Return from t+1 open to t+days close is more realistic for trading next day
        # But for factor power, Close_t to Close_t+days is standard.
        # Shift -days to align future return to current row
        ret = df.pct_change(days).shift(-days)
        returns[f'RET_{days}D'] = ret.stack().reset_index().rename(columns={0: f'RET_{days}D'})
        
    # Merge all returns
    df_ret = returns['RET_1D']
    for days in [3, 5, 10, 20]:
        df_ret = pd.merge(df_ret, returns[f'RET_{days}D'], on=['PRICE_DATE', 'STOCK_CODE'], how='outer')
        
    return df_ret

def calculate_ic(df_merged, factor_col, return_cols):
    """Calculate Information Coefficient (Spearman Correlation)"""
    ic_results = {}
    
    for ret_col in return_cols:
        # Group by Date then corr, then mean (Daily IC)
        daily_ic = df_merged.groupby('PRICE_DATE')[[factor_col, ret_col]].corr(method='spearman').iloc[0::2, -1]
        
        ic_mean = daily_ic.mean()
        ic_std = daily_ic.std()
        ir = ic_mean / ic_std if ic_std != 0 else 0
        
        ic_results[ret_col] = {
            'IC_Mean': ic_mean,
            'IC_Std': ic_std,
            'IR': ir,
            'Win_Rate': (daily_ic > 0).mean()
        }
    
    return ic_results

def analyze_news_impact(df_price, df_news):
    """Analyze News Sentiment Impact"""
    logger.info("Analyzing News Sentiment Factor...")
    
    # Returns
    df_returns = calculate_returns(df_price)
    
    # Merge News (Aggregate daily sentiment if multiple news)
    # If multiple news, take mean or sum or max? Let's take Mean.
    df_news_daily = df_news.groupby(['STOCK_CODE', 'NEWS_DATE'])['SENTIMENT_SCORE'].mean().reset_index()
    
    # Merge with Returns
    # News Date matches Price Date (assuming news before market close or during day acts on that day/next day)
    # Strictly speaking, News at T (Close) predicts Return T+1 to T+N.
    # Currently df_returns aligns Return(t, t+n) to row t.
    # So if we merge on Date, we are checking if News on Day T predicts price move from T to T+n.
    
    df_merged = pd.merge(df_returns, df_news_daily, left_on=['PRICE_DATE', 'STOCK_CODE'], right_on=['NEWS_DATE', 'STOCK_CODE'], how='inner')
    logger.info(f"News Analysis: Merged {len(df_merged)} records.")
    
    ic_results = calculate_ic(df_merged, 'SENTIMENT_SCORE', ['RET_1D', 'RET_3D', 'RET_5D'])
    
    return ic_results, df_merged

def analyze_investor_impact(df_price, df_investor):
    """Analyze Investor Trading Impact"""
    logger.info("Analyzing Investor Trading Factor...")
    
    df_returns = calculate_returns(df_price)
    
    # Merge Investor Data
    df_merged = pd.merge(df_returns, df_investor, left_on=['PRICE_DATE', 'STOCK_CODE'], right_on=['TRADE_DATE', 'STOCK_CODE'], how='inner')
    
    results = {}
    results['Foreign'] = calculate_ic(df_merged, 'FOREIGN_NET_BUY', ['RET_1D', 'RET_3D', 'RET_5D', 'RET_10D', 'RET_20D'])
    results['Institution'] = calculate_ic(df_merged, 'INSTITUTION_NET_BUY', ['RET_1D', 'RET_3D', 'RET_5D', 'RET_10D', 'RET_20D'])
    
    return results

def generate_report(news_ic, investor_ic):
    """Generate Markdown Report"""
    
    report = f"""# Factor Analysis Report ({datetime.now().strftime('%Y-%m-%d')})

## 1. News Sentiment Factor Analysis
Does 'Good News' (High Sentiment Score) predict price increase?
**Hypothesis Verification**: If IC is positive, Good News -> Buy. If IC is negative, Good News -> Sell (Reverse Signal).

| Horizon | IC Mean | IR | Win Rate |
| :--- | :--- | :--- | :--- |
"""
    for horizon, metrics in news_ic.items():
        report += f"| {horizon} | {metrics['IC_Mean']:.4f} | {metrics['IR']:.4f} | {metrics['Win_Rate']:.2%} |\n"
        
    report += "\n## 2. Investor Trading Factor Analysis\n"
    report += "**Foreign Investor Net Buy**\n\n"
    report += "| Horizon | IC Mean | IR | Win Rate |\n| :--- | :--- | :--- | :--- |\n"
    for horizon, metrics in investor_ic['Foreign'].items():
        report += f"| {horizon} | {metrics['IC_Mean']:.4f} | {metrics['IR']:.4f} | {metrics['Win_Rate']:.2%} |\n"

    report += "\n**Institution Investor Net Buy**\n\n"
    report += "| Horizon | IC Mean | IR | Win Rate |\n| :--- | :--- | :--- | :--- |\n"
    for horizon, metrics in investor_ic['Institution'].items():
        report += f"| {horizon} | {metrics['IC_Mean']:.4f} | {metrics['IR']:.4f} | {metrics['Win_Rate']:.2%} |\n"

    # Interpretation
    report += "\n## 3. Conclusion\n"
    
    news_1d_ic = news_ic.get('RET_1D', {}).get('IC_Mean', 0)
    if news_1d_ic > 0.01:
        report += "- **News Sentiment**: **POSITIVE** correlation. Good news leads to price rise. (Reject Reverse Signal Hypothesis)\n"
    elif news_1d_ic < -0.01:
        report += "- **News Sentiment**: **NEGATIVE** correlation. Good news leads to price drop. (Confirm Reverse Signal Hypothesis)\n"
    else:
        report += "- **News Sentiment**: **NEUTRAL**. No strong directional signal found.\n"
        
    return report

def main():
    try:
        df_price, df_news, df_investor = load_data()
        
        if df_price.empty:
            logger.error("No price data found.")
            return

        # 1. News Analysis
        news_ic, _ = analyze_news_impact(df_price, df_news)
        
        # 2. Investor Analysis
        investor_ic = analyze_investor_impact(df_price, df_investor)
        
        # 3. Report
        report_content = generate_report(news_ic, investor_ic)
        
        with open("analysis/factor_analysis_report.md", "w") as f:
            f.write(report_content)
            
        logger.info("Report generated: analysis/factor_analysis_report.md")
        print(report_content)
        
        # Save to Artifacts for User Review? (Ideally)
        
    except Exception as e:
        logger.error(f"Analysis failed: {e}", exc_info=True)

if __name__ == "__main__":
    main()

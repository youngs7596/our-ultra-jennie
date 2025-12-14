
import os
import sys
import logging
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import random

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)

import shared.database as database
from shared.db.connection import get_session
from shared.news_classifier import get_classifier
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Constants
TARGET_START_DATE = datetime(2022, 1, 1)
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Referer': 'https://finance.naver.com/'
}

def get_top_stocks(limit=100):
    """Get top KOSPI stocks by market cap from STOCK_MASTER"""
    conn = database.get_db_connection()
    cursor = conn.cursor()
    try:
        # Check if IS_KOSPI and MARKET_CAP columns exist and use them
        # Based on previous check, they do exist.
        query = """
            SELECT STOCK_CODE, STOCK_NAME 
            FROM STOCK_MASTER 
            WHERE IS_KOSPI = 1 
            AND MARKET_CAP IS NOT NULL
            ORDER BY MARKET_CAP DESC 
            LIMIT %s
        """
        cursor.execute(query, (limit,))
        return cursor.fetchall()
    except Exception as e:
        logger.error(f"Error fetching top stocks: {e}")
        return []
    finally:
        conn.close()

def parse_naver_date(date_str):
    """Parse Naver news date string (YYYY.MM.DD HH:mm)"""
    try:
        return datetime.strptime(date_str, '%Y.%m.%d %H:%M')
    except ValueError:
        return None

def fetch_qs_news(stock_code, page=1):
    """Fetch news from Naver Finance for a specific stock and page"""
    url = f"https://finance.naver.com/item/news_news.naver?code={stock_code}&page={page}&sm=title_entity_id.basic&clusterId="
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        logger.info(f"[{stock_code}] Page {page} Status: {response.status_code}, Length: {len(response.text)}")
        if response.status_code == 200:
            if page == 1:
                with open("debug_naver.html", "w", encoding="euc-kr") as f:
                     f.write(response.text)
                logger.info(f"Saved debug_naver.html")
            return response.text
    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
    return None

def backfill_stock_news(stock_code, stock_name, classifier, session):
    """Backfill news for a single stock"""
    logger.info(f"Starting backfill for {stock_name} ({stock_code})")
    
    page = 1
    total_added = 0
    stop_scraping = False
    seen_urls = set()
    
    while not stop_scraping:
        html = fetch_qs_news(stock_code, page)
        if not html:
            break
            
        soup = BeautifulSoup(html, 'html.parser')
        
        # Naver Finance News List Selector (Check structure manually or assume standard)
        # Typically tables with class 'type5' or similar in the iframe source
        # But this URL returns a page with specific structure.
        # Let's try to find title links.
        
        # New structure check:
        # The URL https://finance.naver.com/item/news_news.naver?code=... returns a list.
        # It's usually a table.
        
        news_items = soup.select('table.type5 tbody tr')
        
        if not news_items:
            # End of pages or different structure
            break
            
        page_processed_count = 0
        
        for row in news_items:
            # Skip separator rows or empty rows
            if not row.select('.title'):
                continue
                
            title_tag = row.select_one('.title a')
            date_tag = row.select_one('.date')
            source_tag = row.select_one('.info')
            
            if not title_tag or not date_tag:
                continue
                
            title = title_tag.get_text(strip=True)
            link = "https://finance.naver.com" + title_tag['href']
            
            # De-duplicate within session
            if link in seen_urls:
                continue
            seen_urls.add(link)

            date_str = date_tag.get_text(strip=True)
            
            news_date = parse_naver_date(date_str)
            if not news_date:
                continue
                
            # Check date limit
            if news_date < TARGET_START_DATE:
                stop_scraping = True
                break
                
            # Classify
            classification = classifier.classify(title)
            
            # Default values
            score = 50
            reason = "Neutral"
            category = "General"
            
            if classification:
                # Map NewsClassification to score (0-100)
                # base_score is typically -15 to +15.
                # Let's map 0 -> 50, +15 -> ~80, -15 -> ~20
                # Simple mapping: 50 + (base_score * 2)
                score = 50 + (classification.base_score * 2)
                score = max(0, min(100, score)) # Clamp 0-100
                category = classification.category
                reason = f"Category: {category} ({classification.sentiment})"
            
            # Save to DB (Manual Insert to both tables)
            try:
                from shared.db.models import NewsSentiment
                from sqlalchemy import text
                
                # Check DB existence (for idempotency beyond session cache)
                # Check NEWS_SENTIMENT
                existing = session.query(NewsSentiment).filter(NewsSentiment.source_url == link).first()
                if existing:
                    continue

                # 1. NEWS_SENTIMENT (Detailed - ORM)
                # This table matches models.py in this env (validated by successful inserts before)
                new_sentiment = NewsSentiment(
                    stock_code=stock_code,
                    news_title=title,
                    sentiment_score=score,
                    sentiment_reason=reason,
                    source_url=link,
                    published_at=news_date
                )
                session.add(new_sentiment)
                
                # 2. STOCK_NEWS_SENTIMENT (Raw/Legacy - via Raw SQL due to ORM mismatch)
                # Schema: ID, ARTICLE_URL, STOCK_CODE, NEWS_DATE, PRESS, HEADLINE, SUMMARY, SOURCE, CATEGORY, SENTIMENT_SCORE, SCRAPED_AT
                # We use INSERT IGNORE to skip duplicates on ARTICLE_URL
                session.execute(text("""
                    INSERT IGNORE INTO STOCK_NEWS_SENTIMENT 
                    (STOCK_CODE, NEWS_DATE, ARTICLE_URL, HEADLINE, CATEGORY, SENTIMENT_SCORE, SCRAPED_AT, SOURCE)
                    VALUES (:code, :date, :url, :title, :category, :score, NOW(), 'NAVER')
                """), {
                    'code': stock_code,
                    'date': news_date,
                    'url': link,
                    'title': title,
                    'category': category,
                    'score': score
                })
                
                total_added += 1
                page_processed_count += 1
                
            except Exception as e:
                logger.error(f"Error saving {link}: {e}")
                pass
        
        if page_processed_count == 0 and stop_scraping:
             break

        if page_processed_count == 0 and not stop_scraping:
             # Looked through page but found no valid items, maybe end of list or only old items
             break

        logger.info(f"  Processed page {page} for {stock_code}: {page_processed_count} items")
        session.commit() # Commit after every page to see progress
        page += 1
        time.sleep(random.uniform(0.1, 0.3)) # Polite delay
        
        if page > 500: # Safety break to prevent infinite loops
            logger.warning(f"  Reached max pages for {stock_code}")
            break

    logger.info(f"Finished {stock_name}: Added {total_added} news items.")

def main():
    load_dotenv()
    
    # Init DB Engine
    database.init_connection_pool()
    
    # Get Classifier
    classifier = get_classifier()
    
    # Get Top Stocks
    stocks = get_top_stocks(limit=100)
    logger.info(f"Found {len(stocks)} stocks to process.")
    
    session = get_session()
    
    start_total = time.time()
    
    try:
        for i, (code, name) in enumerate(stocks):
            logger.info(f"[{i+1}/{len(stocks)}] Processing {name} ({code})...")
            backfill_stock_news(code, name, classifier, session)
            session.commit() # Commit after each stock
            
    except KeyboardInterrupt:
        logger.warning("Process interrupted by user.")
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
    finally:
        session.close()
        end_total = time.time()
        logger.info(f"Total execution time: {end_total - start_total:.2f} seconds")

if __name__ == "__main__":
    main()

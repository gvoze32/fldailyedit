#!/usr/bin/env python3
"""
Crawler to extract all FotMob Team IDs directly from their XML Sitemaps.
This produces a complete list of ~45,000 teams covered by FotMob.
"""

import csv
import json
import logging
import re
import time
from pathlib import Path

import urllib.request
import urllib.error

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fotmob_crawler")

SITEMAP_INDEX_URL = "https://www.fotmob.com/sitemap/en/teams.xml"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def clean_name(slug: str) -> str:
    """Convert URL slug back to a human-readable name."""
    # Special decoding if needed, but normally URL slugs are mostly dashes
    # decode uri component equivalent
    from urllib.parse import unquote
    name = unquote(slug)
    name = name.replace("-", " ")
    return name.title()

def crawl_sitemaps():
    logger.info(f"Fetching sitemap index: {SITEMAP_INDEX_URL}")
    try:
        req = urllib.request.Request(SITEMAP_INDEX_URL, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as response:
            index_text = response.read().decode('utf-8')
    except Exception as e:
        logger.error(f"Failed to fetch index sitemap: {e}")
        return

    # Extract all child sitemaps e.g. https://www.fotmob.com/sitemap/en/teams/1.xml
    child_sitemaps = re.findall(r"<loc>(https://www\.fotmob\.com/sitemap/en/teams/\d+\.xml)</loc>", index_text)
    
    if not child_sitemaps:
        logger.error("No child sitemaps found in index!")
        return

    logger.info(f"Found {len(child_sitemaps)} child sitemaps.")
    
    teams = []
    seen_ids = set()

    for idx, sitemap_url in enumerate(child_sitemaps, 1):
        logger.info(f"Fetching sitemap {idx}/{len(child_sitemaps)}: {sitemap_url}")
        try:
            req = urllib.request.Request(sitemap_url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=15) as response:
                sitemap_text = response.read().decode('utf-8')
        except Exception as e:
            logger.error(f"Failed to fetch {sitemap_url}: {e}")
            continue
            
        # Example: <loc>https://www.fotmob.com/teams/161812/overview/wealdstone</loc>
        # Pattern handles /teams/ID/overview/slug
        matches = re.finditer(r"<loc>https://www\.fotmob\.com/teams/(\d+)/overview/([^<]+)</loc>", sitemap_text)
        
        count = 0
        for m in matches:
            team_id = int(m.group(1))
            slug = m.group(2)
            
            if team_id not in seen_ids:
                seen_ids.add(team_id)
                teams.append({
                    "fotmob_id": team_id,
                    "name": clean_name(slug),
                    "slug": slug,
                    "url": f"https://www.fotmob.com/teams/{team_id}/overview/{slug}"
                })
                count += 1
                
        logger.info(f"Extracted {count} new teams from sitemap {idx}.")
        time.sleep(0.5) # Be gentle to FotMob
        
    logger.info(f"Finished crawling. Total unique teams extracted: {len(teams)}")
    
    # Sort teams by ID
    teams.sort(key=lambda x: x["fotmob_id"])
    
    # Save files
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    
    json_path = data_dir / "fotmob_teams.json"
    csv_path = data_dir / "fotmob_teams.csv"
    
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(teams, f, indent=2, ensure_ascii=False)
        
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["fotmob_id", "name", "slug", "url"])
        writer.writeheader()
        writer.writerows(teams)
        
    logger.info(f"Saved JSON: {json_path} ({(json_path.stat().st_size / 1024 / 1024):.2f} MB)")
    logger.info(f"Saved CSV: {csv_path} ({(csv_path.stat().st_size / 1024 / 1024):.2f} MB)")
    
if __name__ == "__main__":
    crawl_sitemaps()

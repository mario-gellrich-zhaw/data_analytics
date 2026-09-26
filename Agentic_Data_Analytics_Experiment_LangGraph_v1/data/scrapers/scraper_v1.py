import scraper_kit
import re
import time
from bs4 import BeautifulSoup

def parse_listing(listing_url):
    page = scraper_kit.polite_get(listing_url)
    soup = BeautifulSoup(page.text, 'html.parser')
    title = soup.title.text.strip() if soup.title else ''
    # For initial inspection: just print title and structure.
    print(f'Listing page title: {title}')
    # Later, parse more detail fields here...

# Zurich apartments search page 1
start_url = 'https://www.immoscout24.ch/de/wohnung/mieten/ort-zuerich?pn=1'
page = scraper_kit.polite_get(start_url)

print(f'First page status: {page.status_code}')
if page.status_code != 200:
    print(f'Failed to load: {start_url}')
else:
    soup = BeautifulSoup(page.text, 'html.parser')
    print('HTML title:', soup.title.text.strip() if soup.title else '')
    # Print structure: look for hrefs of listings
    links = [a['href'] for a in soup.find_all('a', href=True) if '/de/angebot/' in a['href']]
    print(f'Found {len(links)} listing links on the first page.')
    if links:
        # Sample parse first listing
        parse_listing('https://www.immoscout24.ch' + links[0])

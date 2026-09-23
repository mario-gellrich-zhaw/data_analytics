import scraper_kit
import json
import time

# Starting with ImmoScout24 Zurich apartments for rent
start_url = "https://www.immoscout24.ch/de/wohnung/mieten/ort-zuerich?pn=1"

results = []
page_num = 1
pages_to_scrape = 2  # Stay low for first run to check structure, see if blocked

try:
    while page_num <= pages_to_scrape:
        url = f"https://www.immoscout24.ch/de/wohnung/mieten/ort-zuerich?pn={page_num}"
        page = scraper_kit.polite_get(url)
        print(f"Fetched page {page_num} status {page.status_code}")
        if page.status_code != 200:
            break
        soup = scraper_kit.make_soup(page.text)
        # Print the html title as structure hint for the Data Engineer
        print('HTML title:', soup.title.string if soup.title else 'No title')
        # Print some classnames from main list for reference
        offers = soup.find_all('article')
        print(f"Found {len(offers)} <article> blocks (listing candidates) on page {page_num}")
        for off in offers:
            title = off.get('aria-label')
            url_elem = off.find('a', href=True)
            href = url_elem['href'] if url_elem else None
            row = { 'source': 'immoscout24', 'listing_id': None, 'title': title, 'rent_gross_chf': None, 'rent_net_chf': None, 'rent_charges_chf': None, 'rooms': None, 'living_space_m2': None, 'floor': None, 'year_built': None, 'street': None, 'zip': None, 'city': 'Zürich', 'lat': None, 'lon': None, 'url': f'https://www.immoscout24.ch{href}' if href else None }
            results.append(row)
        scraper_kit.save_rows(results)
        results = []    # Clear for next page
        page_num += 1
        time.sleep(2)
except scraper_kit.ScrapeBlocked:
    print("Scraping blocked by ImmoScout24 (403/429/bot challenge detected). Try another site.")
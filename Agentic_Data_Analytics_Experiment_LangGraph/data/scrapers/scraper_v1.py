import scraper_kit
from bs4 import BeautifulSoup

# Set the URL for the ImmoScout24 rental listings in Zurich
url = 'https://www.immoscout24.ch/de/wohnung/mieten/ort-zuerich?pn={}'

# Function to scrape listings from a single page
def scrape_listings(page):
    response = scraper_kit.polite_get(url.format(page))
    if response.status_code != 200:
        return []  # return empty list on error
    soup = BeautifulSoup(response.text, 'html.parser')
    listings = []
    for item in soup.select('.result-list__listing'):  # CSS selector for listings
        title = item.select_one('.result-list__title').get_text(strip=True)
        rent_gross_chf = item.select_one('.result-list__price').get_text(strip=True)
        # Extract other necessary details, assuming the HTML structure supports it
        listing_id = item['data-id']
        rooms = item.select_one('.result-list__rooms').get_text(strip=True)
        living_space_m2 = item.select_one('.result-list__space').get_text(strip=True)
        url = item.select_one('.result-list__title')['href']

        listings.append({
            'source': 'ImmoScout24',
            'listing_id': listing_id,
            'title': title,
            'rent_gross_chf': rent_gross_chf,
            'rooms': rooms,
            'living_space_m2': living_space_m2,
            'url': url
        })
    return listings

# Main scraping loop to get multiple pages
all_listings = []
for page in range(1, 6):  # Collect data from the first 5 pages
    all_listings.extend(scrape_listings(page))

# Save results
scraper_kit.save_rows(all_listings)
import scraper_kit
import math
import time

def save_and_print(rows, page_num, dropped_other=0):
    print(f'Saving {len(rows)} rows from page {page_num}, dropped {dropped_other} non-apartments.')
    scraper_kit.save_rows(rows)

# Flatfox API – get count
initial_resp = scraper_kit.polite_get('https://flatfox.ch/api/v1/public-listing/?limit=1')
if initial_resp.status_code != 200:
    print('Could not read Flatfox API.')
else:
    data = initial_resp.json()
    total = data.get('count', 0)
    print('Total Flatfox listings (all types):', total)
    page_size = 100
    # We'll take max 3 pages (newest listings, so offsets near end)
    offsets = [total - page_size * i for i in range(1, 4)]
    for offset in offsets:
        if offset < 0:
            continue
        url = f'https://flatfox.ch/api/v1/public-listing/?limit={page_size}&offset={offset}'
        resp = scraper_kit.polite_get(url)
        if resp.status_code != 200:
            print(f'Page at offset {offset} failed')
            continue
        items = resp.json().get('results', [])
        apartments = []
        dropped = 0
        for item in items:
            if item.get('offer_type') != 'RENT' or item.get('object_category') != 'APARTMENT' or not (8000 <= int(item.get('zipcode', 0)) <= 8999):
                dropped += 1
                continue
            # Only save if price is given
            if item.get('rent_gross') is None and item.get('rent_net') is None:
                dropped += 1
                continue
            apartments.append({
                'source': 'flatfox',
                'listing_id': item['pk'],
                'title': item.get('object_type', ''),
                'rent_gross_chf': item.get('rent_gross'),
                'rent_net_chf': item.get('rent_net'),
                'rent_charges_chf': item.get('rent_charges'),
                'rooms': item.get('number_of_rooms'),
                'living_space_m2': item.get('surface_living'),
                'floor': item.get('floor'),
                'year_built': item.get('year_built'),
                'year_renovated': item.get('year_renovated'),
                'street': item.get('street'),
                'zip': item.get('zipcode'),
                'city': item.get('city'),
                'lat': item.get('latitude'),
                'lon': item.get('longitude'),
                'url': 'https://flatfox.ch' + item.get('url', ''),
                'object_type': item.get('object_type'),
                'description': item.get('description'),
                'attributes': ','.join([a['name'] for a in item.get('attributes', [])]),
                'is_furnished': item.get('is_furnished'),
                'moving_date': item.get('moving_date'),
            })
        save_and_print(apartments, offset, dropped)
        time.sleep(2)

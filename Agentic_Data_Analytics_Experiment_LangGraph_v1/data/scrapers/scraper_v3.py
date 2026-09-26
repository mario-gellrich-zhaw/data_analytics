import scraper_kit
import math
import time

def filter_zurich(listing):
    # Flatfox does not provide canton, only zip/city.
    zipcode = listing.get('zipcode')
    city = listing.get('city', '').lower()
    # Hard filter: only Zurich zip codes
    valid_zip = isinstance(zipcode, int) and 8000 <= zipcode < 9000
    if not valid_zip:
        return False
    return True

def main():
    # First, find out total number of public listings
    count_url = 'https://flatfox.ch/api/v1/public-listing/?limit=1&offset=0'
    response = scraper_kit.polite_get(count_url)
    js = response.json()
    total = js.get('count', 0)
    print(f'Total Flatfox listings: {total}')
    # Start from latest listings, page backwards
    batch = 100
    start_offset = max(0, total - batch)
    saved_rows = 0
    for offset in range(start_offset, -1, -batch):
        url = f'https://flatfox.ch/api/v1/public-listing/?limit={batch}&offset={offset}'
        resp = scraper_kit.polite_get(url)
        data = resp.json()
        results = data.get('results', [])
        filtered = [lst for lst in results if lst.get('offer_type') == 'RENT' and lst.get('object_category') == 'APARTMENT']
        print(f'Page offset {offset}: {len(results)} results, {len(filtered)} after apartment/rent filter')
        zurich = [lst for lst in filtered if filter_zurich(lst)]
        print(f'Zurich postal filter: {len(zurich)} listings')
        out_rows = []
        for x in zurich:
            if x.get('rent_gross') is None and x.get('rent_net') is None:
                continue  # price on request, skip
            out_rows.append({
                'source': 'flatfox',
                'listing_id': x['pk'],
                'title': '',  # not available
                'rent_gross_chf': x.get('rent_gross'),
                'rent_net_chf': x.get('rent_net'),
                'rent_charges_chf': x.get('rent_charges'),
                'rooms': x.get('number_of_rooms'),
                'living_space_m2': x.get('surface_living'),
                'floor': x.get('floor'),
                'year_built': x.get('year_built'),
                'street': x.get('street'),
                'zip': x.get('zipcode'),
                'city': x.get('city'),
                'lat': x.get('latitude'),
                'lon': x.get('longitude'),
                'url': 'https://flatfox.ch' + x.get('url', ''),
                'object_type': x.get('object_type'),
                'description': x.get('description'),
                'attributes': ','.join(a['name'] for a in x.get('attributes', [])),
                'is_furnished': x.get('is_furnished'),
                'year_renovated': x.get('year_renovated'),
                'moving_date': x.get('moving_date'),
            })
        print(f'Saving {len(out_rows)} rows for Zurich')
        scraper_kit.save_rows(out_rows)
        saved_rows += len(out_rows)
        # Cap at max 150 listings per run
        if saved_rows >= 150:
            print('Row cap reached, stopping.')
            break
        time.sleep(2)

if __name__ == '__main__':
    main()

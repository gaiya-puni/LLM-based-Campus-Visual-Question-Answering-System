"""Offline regression checks for the campus recommendation v2 semantic layer."""

import json
from pathlib import Path

import server as server_module

from server import (
    _SCENE_PROFILES,
    _is_unsupported_data_query,
    _looks_like_institution_location_query,
    _matched_scene_profiles,
    direct_configured_poi_matches,
    rank_campus_places,
    search_colleges,
    search_plants,
)


def _rank(query, preferred_campus=None, top_k=2, ranking_location=None):
    plants = search_plants(query)
    colleges = search_colleges(query)
    institution_query = _looks_like_institution_location_query(query)
    return rank_campus_places(
        query,
        plants,
        colleges,
        top_k=top_k,
        institution_location_requested=institution_query,
        preferred_campus=preferred_campus,
        ranking_location=ranking_location,
    )


def _assert(condition, message):
    if not condition:
        raise AssertionError(message)


def test_direct_canteen_query():
    matches = direct_configured_poi_matches('河西食堂在哪里')
    names = [item.get('locationName') for item in matches]
    _assert(names == ['河西食堂'], f'expected only 河西食堂, got {names}')
    ranked = _rank('河西食堂在哪里', preferred_campus='普陀')
    _assert(ranked == [], f'exact canteen query should not produce ranked places, got {ranked}')


def test_putuo_dining_scene():
    places = _rank('普陀校区去哪里吃饭', preferred_campus='普陀')
    names = [item.get('name') for item in places]
    _assert(names == [
        '华东师范大学普陀校区河西食堂',
        '华东师范大学普陀校区河东食堂',
        '华东师范大学普陀校区丽娃西餐厅',
    ], f'expected all Putuo canteens in current order, got {names}')


def test_putuo_dining_prefers_nearest_user_location():
    places = _rank(
        '普陀校区去哪里吃饭',
        preferred_campus='普陀',
        ranking_location={
            'lng': 121.4033,
            'lat': 31.2304,
            'campus': '普陀',
            'source': 'amap',
            'trusted': True,
            'use_for_distance': True,
        },
    )
    _assert(places[0].get('name') == '华东师范大学普陀校区河西食堂', f'expected nearest 河西 first, got {places}')
    _assert('距你当前位置' in places[0].get('reason', ''), f'expected user distance reason, got {places[0]}')


def test_semantic_dining_scene():
    places = _rank('我想找个地方解决一餐', preferred_campus='普陀')
    categories = {item.get('category') for item in places}
    _assert(places, 'expected semantic dining query to return places')
    _assert(categories == {'canteen'}, f'expected canteen only, got {categories}')


def test_semantic_photo_scene_deduped():
    places = _rank('我想找个地方拍点照片')
    names = [item.get('name') for item in places]
    _assert(places, 'expected semantic photo query to return places')
    _assert(len(names) == len(set(names)), f'expected deduped photo places, got {names}')
    _assert(all(item.get('category') == 'plant' for item in places), f'expected plant places, got {places}')


def test_flower_and_quiet_scenes():
    flower_places = _rank('普陀校区哪里适合看花', preferred_campus='普陀')
    quiet_places = _rank('哪里适合安静坐一会儿')
    _assert(flower_places, 'expected flower viewing places')
    _assert(quiet_places, 'expected quiet rest places')
    _assert(all(item.get('category') == 'plant' for item in flower_places), 'flower scene should use plant POIs')
    _assert(all(item.get('category') == 'plant' for item in quiet_places), 'quiet scene should use plant POIs')


def test_minhang_walk_uses_correct_campus_labels():
    query = '闵行校区哪里适合散步'
    scenes = _matched_scene_profiles(query)
    scene_ids = [scene.get('id') for scene in scenes]
    _assert(scene_ids == ['walk'], f'explicit walk intent should suppress semantic scene noise, got {scene_ids}')

    places = _rank(query, preferred_campus='闵行')
    _assert(places, 'expected Minhang walk query to return places')
    _assert(all(place.get('campus') == '闵行' for place in places), f'expected Minhang places only, got {places}')
    _assert(
        all('丽娃河' not in (place.get('name') or '') for place in places),
        f'Minhang recommendations must not use the Putuo river name: {places}',
    )

    minhang_date_places = _rank('闵行校区哪里适合约会', preferred_campus='闵行')
    minhang_date_names = [place.get('name') for place in minhang_date_places]
    _assert('樱桃河周边' in minhang_date_names, f'expected Minhang waterfront to use 樱桃河: {minhang_date_names}')
    _assert('丽娃河周边' not in minhang_date_names, f'Minhang must not use 丽娃河: {minhang_date_names}')

    putuo_date_places = _rank('普陀校区哪里适合约会', preferred_campus='普陀')
    putuo_date_names = [place.get('name') for place in putuo_date_places]
    _assert('丽娃河周边' in putuo_date_names, f'expected Putuo waterfront to use 丽娃河: {putuo_date_names}')


def test_unsupported_category_not_downgraded():
    query = '哪里可以喝咖啡坐一会儿'
    unsupported = _is_unsupported_data_query(
        query,
        search_plants(query),
        search_colleges(query),
        _looks_like_institution_location_query(query),
    )
    _assert(unsupported, 'coffee query should stay unsupported until coffee POIs are added')
    places = _rank(query, preferred_campus='普陀')
    _assert(places == [], f'unsupported coffee query should not rank fallback places, got {places}')


def test_parking_exact_query():
    matches = direct_configured_poi_matches('东门附近停车场在哪里')
    names = [item.get('name') for item in matches]
    _assert(names == ['华东师范大学中山北路门停车场'], f'expected exact parking match, got {names}')
    ranked = _rank('东门附近停车场在哪里', preferred_campus='普陀')
    _assert(ranked == [], f'exact parking query should not produce ranked places, got {ranked}')


def test_parking_numeric_gate_alias_query():
    queries = [
        '普陀校区枣阳路460停车场在哪里',
        '枣阳路460号门停车场怎么走',
        '460停车场的准确地址',
    ]
    for query in queries:
        matches = direct_configured_poi_matches(query)
        ids = [item.get('id') for item in matches]
        _assert(
            ids == ['parking_putuo_zaoyang460_gate'],
            f'expected one normalized 460 gate parking match for {query}, got {ids}',
        )
        ranked = _rank(query, preferred_campus='普陀')
        _assert(ranked == [], f'direct 460 parking query must not produce Top places, got {ranked}')


def test_semantic_corpus_and_evaluation_cases():
    for profile in _SCENE_PROFILES:
        _assert(profile.get('definition'), f"scene {profile.get('id')} missing definition")
        _assert(profile.get('contextTexts'), f"scene {profile.get('id')} missing context texts")
        _assert(profile.get('positiveQueries'), f"scene {profile.get('id')} missing positive queries")
        _assert(profile.get('negativeQueries'), f"scene {profile.get('id')} missing negative queries")

    path = Path(__file__).with_name('semantic_query_cases.json')
    cases = json.loads(path.read_text(encoding='utf-8'))
    _assert(len(cases) >= 50, f'expected at least 50 semantic evaluation cases, got {len(cases)}')
    direct_cases = [case for case in cases if case.get('mode') == 'direct']
    for case in direct_cases:
        matches = direct_configured_poi_matches(case['query'])
        _assert(matches, f"direct evaluation query did not match: {case['query']}")
        if case.get('expectedPoiId'):
            ids = [item.get('id') for item in matches]
            _assert(ids == [case['expectedPoiId']], f"unexpected direct match for {case['query']}: {ids}")


def test_chat_exact_parking_returns_single_map_location():
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {'choices': [{'message': {'content': '该停车场位于460号门附近。'}}]}

    original_post = server_module.requests.post
    server_module.requests.post = lambda *args, **kwargs: FakeResponse()
    try:
        response = server_module.app.test_client().post('/api/chat', json={
            'messages': [
                {'role': 'user', 'content': '普陀校区枣阳路460停车场在哪里'}
            ],
            'userCampus': '普陀',
        })
    finally:
        server_module.requests.post = original_post

    _assert(response.status_code == 200, f'expected chat 200, got {response.status_code}')
    payload = response.get_json()
    locations = payload.get('locations', [])
    _assert(len(locations) == 1, f'expected exactly one map location, got {locations}')
    _assert(
        locations[0].get('name') == '华东师范大学普陀校区枣阳路460号门停车场',
        f'unexpected parking location: {locations}',
    )
    _assert(payload.get('ranked_places') == [], f"direct query must not contain Top-K: {payload.get('ranked_places')}")


def test_direct_plant_query_respects_selected_campus_over_geolocation():
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {'choices': [{'message': {'content': '已显示普陀校区的荷花玉兰位置。'}}]}

    original_post = server_module.requests.post
    server_module.requests.post = lambda *args, **kwargs: FakeResponse()
    try:
        response = server_module.app.test_client().post('/api/chat', json={
            'messages': [{'role': 'user', 'content': '荷花玉兰在哪里'}],
            'userCampus': '普陀',
            'userLocation': {
                'lng': 121.453725,
                'lat': 31.03148,
                'campus': '闵行',
                'campusTrusted': True,
                'trusted': True,
                'useForDistance': True,
                'source': 'amap',
            },
        })
    finally:
        server_module.requests.post = original_post

    _assert(response.status_code == 200, f'expected chat 200, got {response.status_code}')
    payload = response.get_json()
    locations = payload.get('locations', [])
    _assert(locations, 'expected Putuo Magnolia grandiflora locations')
    _assert(
        {item.get('campus') for item in locations} == {'普陀'},
        f'selected Putuo campus must win over Minhang geolocation: {locations}',
    )
    _assert(
        {item.get('name') for item in locations} == {'荷花玉兰'},
        f'incorrect plant alias leaked into Magnolia query: {locations}',
    )


def test_direct_plant_query_only_falls_back_without_explicit_campus():
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {'choices': [{'message': {'content': '位置查询完成。'}}]}

    original_post = server_module.requests.post
    server_module.requests.post = lambda *args, **kwargs: FakeResponse()
    try:
        client = server_module.app.test_client()
        fallback = client.post('/api/chat', json={
            'messages': [{'role': 'user', 'content': '红千层在哪里'}],
            'userCampus': '闵行',
        }).get_json()
        strict = client.post('/api/chat', json={
            'messages': [{'role': 'user', 'content': '闵行校区红千层在哪里'}],
            'userCampus': '普陀',
        }).get_json()
    finally:
        server_module.requests.post = original_post

    _assert(fallback.get('locations'), 'implicit campus query should fall back to the available campus')
    _assert(
        {item.get('campus') for item in fallback['locations']} == {'普陀'},
        f'expected Putuo-only fallback locations: {fallback.get("locations")}',
    )
    _assert(
        strict.get('locations') == [],
        f'explicit Minhang query must not fall back to Putuo: {strict.get("locations")}',
    )


def test_chat_unsupported_query_is_hard_stopped():
    def fail_if_called(*args, **kwargs):
        raise AssertionError('unsupported query must not call the LLM service')

    original_post = server_module.requests.post
    server_module.requests.post = fail_if_called
    try:
        client = server_module.app.test_client()
        for query in ('哪里可以喝咖啡', '附近有充电桩吗', '校医院在哪里'):
            response = client.post('/api/chat', json={
                'messages': [{'role': 'user', 'content': query}],
                'userCampus': '普陀',
            })
            _assert(response.status_code == 200, f'expected unsupported chat 200 for {query}')
            payload = response.get_json()
            _assert(payload.get('unsupported') is True, f'expected unsupported flag for {query}')
            _assert(payload.get('locations') == [], f'unsupported query returned map points: {payload}')
            _assert(payload.get('ranked_places') == [], f'unsupported query returned Top-K: {payload}')
            content = payload.get('choices', [{}])[0].get('message', {}).get('content', '')
            _assert('暂无可靠' in content, f'expected deterministic no-data response for {query}: {content}')
    finally:
        server_module.requests.post = original_post


def test_parking_scene_prefers_nearest():
    places = _rank(
        '普陀校区哪里可以停车',
        preferred_campus='普陀',
        ranking_location={
            'lng': 121.409178,
            'lat': 31.228721,
            'campus': '普陀',
            'source': 'amap',
            'trusted': True,
            'useForDistance': True,
        },
    )
    _assert(places, 'expected parking scene to return places')
    _assert(all(item.get('category') == 'parking' for item in places), f'expected parking only, got {places}')
    _assert(places[0].get('name') == '华东师范大学中山北路门停车场', f'expected nearest parking first, got {places[0]}')


def run_all():
    tests = [
        test_direct_canteen_query,
        test_putuo_dining_scene,
        test_putuo_dining_prefers_nearest_user_location,
        test_semantic_dining_scene,
        test_semantic_photo_scene_deduped,
        test_flower_and_quiet_scenes,
        test_minhang_walk_uses_correct_campus_labels,
        test_unsupported_category_not_downgraded,
        test_parking_exact_query,
        test_parking_numeric_gate_alias_query,
        test_semantic_corpus_and_evaluation_cases,
        test_chat_exact_parking_returns_single_map_location,
        test_direct_plant_query_respects_selected_campus_over_geolocation,
        test_direct_plant_query_only_falls_back_without_explicit_campus,
        test_chat_unsupported_query_is_hard_stopped,
        test_parking_scene_prefers_nearest,
    ]
    for test in tests:
        test()
        print(f'PASS {test.__name__}')


if __name__ == '__main__':
    run_all()

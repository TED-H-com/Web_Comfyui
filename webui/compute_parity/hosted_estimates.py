"""Reviewed, case-specific runtime envelope. No provider quote or actual-cost claim."""
from .contracts import number

VERSION = '20261111-r30'  # Tariff/envelope unchanged; mapping has its own r31 version.
CLOUD_RATE_SOURCE = 'https://support.comfy.org/articles/5846341390-how-credits-work-in-comfy'
CLOUD_LIMIT_SOURCE = 'https://www.comfy.org/pricing'
RH_RATE_SOURCE = 'https://www.runninghub.ai/post/2015763807888478210'
# The public workflow author reports ~60 POINT / 4 minutes. This is an
# envelope reference only: no documented Native/proxy-plus POINT tariff found.
# Enterprise USD/hour and RH coins/second are NOT converted to POINT.
MEASUREMENTS = {}

# Versioned measurements bind exact approved bundle hashes, including runner-only repins.
import json
from pathlib import Path
MEASUREMENTS.update(json.loads(Path(__file__).with_name('local_measurements.json').read_text()))

def measurement_from_journal(path,owner,bundle):
    import sqlite3
    from datetime import datetime, timezone
    path=Path(path)
    if not path.is_file():return None
    with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) as db:
        rows=db.execute("SELECT body FROM jobs WHERE owner=? AND lane='local' AND case_id=? AND state='completed' ORDER BY rowid DESC",(owner,bundle['case_id'])).fetchall()
    from .measurement_mapping import sources,VERSION as MAPPING_VERSION
    accepted=sources(bundle)
    for row in rows:
        j=json.loads(row[0])
        if j.get('bundle_sha256') not in {bundle['bundle_sha256'],*accepted} or not number(j.get('wall_seconds')) or j['wall_seconds']<=0 or not number(j.get('completed_at')):continue
        return {'case_id':j['case_id'],'wall_seconds':j['wall_seconds'],'request_id':j['request_id'],
                'measured_at':datetime.fromtimestamp(j['completed_at'],timezone.utc).isoformat(),
                'evidence':'owner_local_journal','measurement_version':j.get('measurement_version'),
                'source_bundle':j['bundle_sha256'],'mapping_version':MAPPING_VERSION if j['bundle_sha256'] in accepted else 'exact_bundle',
                'limitation':'Local wall 含佇列與輪詢；跨 GPU 耗時為估計'}
    return None

def estimate(bundle, lane, *, cloud_limit_seconds=1800, measurement=None):
    m = measurement or MEASUREMENTS.get(bundle['bundle_sha256'])
    if m is None:
        from .measurement_mapping import sources
        for source,row in sources(bundle).items():
            if source in MEASUREMENTS:
                m={**MEASUREMENTS[source],'source_bundle':source,'mapping_version':row['version']};break
    if not m or m['case_id'] != bundle['case_id']: return {}
    expected = m['wall_seconds'] * 1.5
    if lane == 'comfy_cloud':
        if not number(cloud_limit_seconds) or not 0 < cloud_limit_seconds <= 3600: return {}
        seconds = min(expected, cloud_limit_seconds, 1800)
        rate, unit, source = 0.266, 'credits', CLOUD_RATE_SOURCE
        label = '估計，非官方報價；依 Local wall 與 GPU 秒費率計算，實扣待對帳'
    elif lane == 'runninghub':
        seconds = expected
        rate, unit, source = 60 / (4 * 60), 'POINT', RH_RATE_SOURCE
        label = '估計，非官方報價；公開作者約 60 POINT／4 分鐘作包絡參考，非 Native 48GB 費率'
    else: return {}
    return {'unit': unit, 'amount': round(seconds * rate, 6), 'basis': 'reviewed_local_wall_envelope',
            'label': label, 'estimate_version': VERSION, 'expected_seconds': seconds,
            'uncapped_seconds': expected, 'safety_factor': 1.5, 'rate_per_second': rate,
            'rate_source': source, 'source_checked_at': '2026-11-11', 'measured_at': m.get('measured_at','2026-11-11'), 'measurement': dict(m),
            'runtime_limit_seconds': min(cloud_limit_seconds,1800) if lane == 'comfy_cloud' else None,
            'runtime_limit_basis': '保守採 Standard／Creator 30 分鐘上限；未假定帳號為 Pro' if lane == 'comfy_cloud' else None,
            'runtime_limit_source': CLOUD_LIMIT_SOURCE if lane == 'comfy_cloud' else None,
            'is_estimate': True, 'actual_cost': None}

"""Versioned exact equivalence for runner repins, never a fuzzy hash fallback."""
import json
from pathlib import Path
from .contracts import digest,validate_bundle
VERSION='runner-repin-r31-v1'

def semantic_digest(bundle):
    validate_bundle(bundle)
    return digest({k:v for k,v in bundle.items() if k not in {'bundle_sha256','runner_sources'}})

def link(source,target):
    if semantic_digest(source)!=semantic_digest(target):raise ValueError('Runner repin changed graph, input, prompt or other contract')
    return {'case_id':target['case_id'],'source_bundle':source['bundle_sha256'],
            'target_bundle':target['bundle_sha256'],'semantic_sha256':semantic_digest(target),'version':VERSION}

def sources(bundle):
    mapping=json.loads(Path(__file__).with_name('measurement_mappings.json').read_text())
    try:semantic=semantic_digest(bundle)
    except (ValueError,KeyError,TypeError):return {}
    return {row['source_bundle']:row for row in mapping['links'] if row['target_bundle']==bundle['bundle_sha256']
            and row['case_id']==bundle['case_id'] and row['semantic_sha256']==semantic and row['version']==VERSION}

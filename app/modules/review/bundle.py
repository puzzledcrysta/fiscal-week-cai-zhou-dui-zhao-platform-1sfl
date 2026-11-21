import json
from app.modules.runs.service import get
from app.modules.correction.service import for_origin
from .decisions import timeline


def bundle(run_id):
    history = timeline(run_id)
    run = get(run_id)
    # The export carries source line evidence and the applicable decision version.
    # Corrections keep the signed conclusion traceable without rewriting it.
    return json.dumps({'manifest':run['manifest'],'counts':run['counts'],'results':run['accepted'],
                       'quarantine':run['quarantine'],'suppressed':run['suppressed'],
                       'reconciliation':run['book'],
                       'adjustments':run.get('adjustments') or {'lines':[],'total':'0',
                                                                'settled':run['book']['delta']},
                       'corrections':for_origin(run_id),
                       'review':history},ensure_ascii=False,indent=2)

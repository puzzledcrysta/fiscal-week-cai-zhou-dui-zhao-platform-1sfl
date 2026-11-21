from collections import Counter


def finding(record, code, detail):
    issue = {'line': record['line'], 'key': record.get('key', ''), 'raw': record['raw'],
             'code': code, 'detail': detail}
    if 'revision' in record:
        issue['revision'] = record['revision']
    return issue


def distribution(findings):
    return dict(sorted(Counter(item['code'] for item in findings).items()))

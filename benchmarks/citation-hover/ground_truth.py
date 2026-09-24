"""OCR-only reference lists, plus conservative PDF destination alignment.

The implementation's extracted entry is NEVER used to choose its gold label.
Ambiguous alignments remain unlabelled for review in ground-truth.json.
"""
from collections import Counter
import html
import json
from pathlib import Path
import re
import unicodedata

OUT = Path(__file__).resolve().parent / '.data'
papers = {p['id']: p for p in json.loads((OUT / 'papers.json').read_text())}
inventory = json.loads((OUT / 'inventory.json').read_text())
heading = re.compile(r'(?mi)^(?:#+\s*)?(?:\d+[. ]*\s*)?(?:References(?: and recommended reading)?|Bibliography)\s*$')
number = re.compile(r'^\s*(?:-\s*)?(?:\[(\d{1,3})\]|\((\d{1,3})\)|(\d{1,3})[.])\s+')

def norm(s):
    s = unicodedata.normalize('NFKD', html.unescape(s)).lower()
    s = re.sub(r'(?<=\w)-\s+(?=\w)', '', s)
    return re.sub(r'[^a-z0-9]', '', s)

def grams(s):
    s = norm(s)
    return set(s[i:i+4] for i in range(len(s)-3))

def references(p):
    entries = []
    active = False
    start_page = None
    for page_index, page in enumerate((p['ocr'] or {}).get('pages', [])):
        markdown = page.get('markdown', '')
        m = heading.search(markdown)
        if m:
            active = True
            start_page = start_page or page_index + 1
            markdown = markdown[m.end():]
        # PNAS and supplementary reference lists sometimes have no heading.
        if not active and page_index > len((p['ocr'] or {}).get('pages', [])) * .6 and re.search(r'(?m)^1\.\s.{25}', markdown) and re.search(r'(?m)^2\.\s.{25}', markdown):
            active = True
            start_page = page_index + 1
            markdown = markdown[re.search(r'(?m)^1\.\s', markdown).start():]
        if not active:
            continue
        end = re.search(r'(?m)^#{1,4}\s+(?!References).*', markdown)
        if end:
            markdown = markdown[:end.start()]
            active = False
        parts = re.split(r'\n\s*\n|\n(?=-\s|\[\d+\]|\(?\d+[.)]\s)', markdown)
        for part in parts:
            part = part.strip()
            if not part or len(part) < 25 or re.match(r'^(?:www\.|https?://|Current Opinion|\d+\s*$)', part):
                continue
            part = re.sub(r'^-\s*', '', part)
            n = number.match(part)
            # A continued entry on the next page is not a separate reference.
            if entries and not n and (part[0].islower() or re.match(r'^(?:In |Proceedings|URL |doi:|arXiv|https?://)', part)):
                entries[-1]['text'] += ' ' + part
                entries[-1]['pages'].append(page_index + 1)
                continue
            entries.append({'index': len(entries), 'number': int(next(x for x in n.groups() if x)) if n else None, 'text': part, 'pages': [page_index+1], 'doi': (re.search(r'10\.\d{4,9}/[^\s<>]+',part) or [None])[0]})
    return entries, start_page

result = []
for p in inventory:
    refs, start = references(papers[p['id']])
    targets = {}
    for page in p['pages']:
        for a in page['links']:
            point = a.get('point')
            dest = a.get('dest')
            if not point or not isinstance(dest, str):
                continue
            if not (re.match(r'^(?:cite\.|bib\d|bm_CR\d|link_sbref\d|[LR]pone.*\.ref)', dest) or re.search(r'\.indd:(?:B\d+:|\s*\d+\.\s)', dest.replace('\ufeff',''))):
                continue
            targets[dest] = point
    labels = {}
    for dest, point in targets.items():
        page = p['pages'][point['page']-1]
        x, y = point.get('x'), point.get('y')
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            continue
        # Restrict to destination column BEFORE grouping baselines.
        two = any(q['page']==point['page'] and isinstance(q['x'], (float,int)) and abs(q['x']-x)>page['width']*.3 for q in targets.values())
        maxx = (page['width']/2 if x < page['width']/2 else page['width']) if two else page['width']
        items = [i for i in page['items'] if i['str'].strip() and x-12 <= i['transform'][4] < maxx and y-43 < i['transform'][5] < y+4]
        items.sort(key=lambda i:(-round(i['transform'][5]/3),i['transform'][4]))
        snippet = ' '.join(i['str'] for i in items)[:230]
        ranked = []
        for ref in refs:
            if point['page'] not in ref['pages'] and abs(ref['pages'][0]-point['page'])>1:
                continue
            a, b = grams(snippet[:150]), grams(ref['text'][:180])
            score = len(a & b)/max(1,min(len(a),len(b)))
            snum = number.match(snippet)
            if snum and ref['number'] == int(next(z for z in snum.groups() if z)):
                score += .5
            ranked.append((score, ref['index']))
        ranked.sort(reverse=True)
        explicit = re.search(r'^(?:bib|bm_CR|link_sbref)(\d+)$|\.ref(\d+)$|\.indd:B(\d+):|\.indd:\s*(\d+)\.\s', dest.replace('\ufeff',''))
        if explicit:
            expected = int(next(z for z in explicit.groups() if z))
            eligible = [(s, i) for s,i in ranked if refs[i]['number'] == expected]
            if len(eligible) == 1:
                ranked = [(max(eligible[0][0], 1), eligible[0][1])]
            elif eligible:
                ranked = eligible
            else:
                ranked = []
        score, idx = ranked[0] if ranked else (0,None)
        margin = score-(ranked[1][0] if len(ranked)>1 else 0)
        labels[dest] = {'point':point,'snippet':snippet,'reference':idx if score >= .62 and margin >= .1 else None,'score':round(score,3),'margin':round(margin,3),'candidate':idx}
    # OCR citation occurrences are expanded to individual targets, not clusters.
    # Only known reference numbers / author surnames+year qualify.
    occurrences = []
    numbers = {r['number'] for r in refs if r['number'] is not None}
    for pi, page in enumerate((papers[p['id']]['ocr'] or {}).get('pages',[])):
        if start and pi+1 >= start:
            continue
        text = page.get('markdown','')
        for m in re.finditer(r'\[(\d{1,3}(?:\s*[,–−-]\s*\d{1,3})*)\]|\((\d{1,3}(?:\s*[,–−-]\s*\d{1,3})*)\)|\^\{(\d{1,3}(?:\s*[,–−-]\s*\d{1,3})*)\}',text):
            if m.group(3) and pi == 0 and ('Abstract' not in text[:m.start()] and 'Introduction' not in text[:m.start()]):
                continue  # author affiliations
            group = next(z for z in m.groups() if z)
            ns=[]
            for part in group.split(','):
                ran=re.split('[–−-]',part)
                ns.extend(range(int(ran[0]),int(ran[-1])+1) if len(ran)>1 and int(ran[-1])-int(ran[0])<100 else [int(ran[0])])
            for n in ns:
                if n in numbers: occurrences.append({'page':pi+1,'number':n,'text':m.group()})
        if len(numbers) < len(refs) * .5:
            for m in re.finditer(r'([A-Z][A-Za-zÀ-ž\-]+)(?: et al\.?| and [A-Z][A-Za-zÀ-ž\-]+)?[, ]*\(?((?:19|20)\d{2}[a-z]?)',text):
                if any(norm(m[1]) in norm(r['text'][:200]) and m[2][:4] in r['text'] for r in refs):
                    occurrences.append({'page':pi+1,'text':m.group()})
    result.append({'id':p['id'],'title':p['title'],'references':refs,'referenceStartPage':start,'labels':labels,'ocrCitationOccurrences':occurrences})
    print(p['id'][:8],len(refs),'refs',sum(v['reference'] is not None for v in labels.values()),'/',len(labels),'aligned',len(occurrences),'OCR citations')
(OUT/'ground-truth.json').write_text(json.dumps(result,indent=2,ensure_ascii=False))

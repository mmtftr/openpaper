"""Read-only deployment snapshot. PDFs, OCR and recordings stay git-ignored."""
import concurrent.futures
import json
from pathlib import Path
import subprocess
import urllib.request

OUT = Path(__file__).resolve().parent / '.data'
OUT.mkdir(exist_ok=True)
(OUT / 'pdfs').mkdir(exist_ok=True)
query = "SELECT json_agg(t) FROM (SELECT id,title,authors,abstract,publish_date,doi,s3_object_key,file_url,ocr FROM papers ORDER BY id) t"
papers = json.loads(subprocess.check_output(['docker', 'exec', 'openpaper-postgres-1', 'psql', '-U', 'postgres', '-d', 'openpaper', '-At', '-c', query]))
(OUT / 'papers.json').write_text(json.dumps(papers))

def download(paper):
    path = OUT / 'pdfs' / (paper['id'] + '.pdf')
    if not path.exists():
        url = 'http://127.0.0.1:12010/openpaper-local/' + urllib.parse.quote(paper['s3_object_key'], safe='/')
        with urllib.request.urlopen(url, timeout=90) as response:
            content = response.read()
        if not content.startswith(b'%PDF'):
            raise ValueError('Not a PDF: ' + paper['id'])
        path.write_bytes(content)
    return path.stat().st_size

with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    sizes = list(pool.map(download, papers))
print(f'Exported {len(papers)} PDFs ({sum(sizes) // 1024 // 1024} MiB); database SELECT only.')

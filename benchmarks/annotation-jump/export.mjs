import { execFileSync } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';

export async function exportFixtures(dir, refresh = false) {
  await mkdir(dir, { recursive: true });
  if (!refresh) {
    let cached;
    try { cached = JSON.parse(await readFile(`${dir}/manifest.json`, 'utf8')); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    if (cached) {
      for (const paper of cached.papers) {
        const pdf = await readFile(`${dir}/${paper.id}.pdf`);
        if (createHash('sha256').update(pdf).digest('hex') !== paper.sha256) {
          throw new Error(`Fixture ${paper.id} changed; use --refresh-fixtures to export a complete snapshot`);
        }
      }
      return cached;
    }
  }
  const sql = `SELECT json_build_object('papers', (SELECT json_agg(row_to_json(p)) FROM
    (SELECT p.id, p.title, p.s3_object_key, p.file_url,
      COALESCE((SELECT json_agg(json_build_object('id', h.id, 'raw_text', h.raw_text, 'role', h.role,
        'page_number', h.page_number, 'position', h.position, 'color', h.color) ORDER BY h.id)
        FROM highlights h WHERE h.paper_id = p.id), '[]') AS highlights,
      COALESCE((SELECT json_agg(json_build_object('id', a.id, 'highlight_id', a.highlight_id,
        'paper_id', a.paper_id, 'content', a.content, 'role', a.role, 'created_at', a.created_at) ORDER BY a.id)
        FROM annotations a WHERE a.paper_id = p.id), '[]') AS annotations
      FROM papers p ORDER BY p.id) p));`;
  const manifest = JSON.parse(execFileSync('docker', ['exec', 'openpaper-postgres-1', 'psql', '-U', 'postgres', '-d', 'openpaper', '-At', '-c', sql], { encoding: 'utf8', maxBuffer: 32e6 }));
  for (const paper of manifest.papers) {
    // Same key precedence as S3Service: file_url only for older rows without a key.
    const key = paper.s3_object_key || decodeURIComponent(new URL(paper.file_url).pathname.split('/openpaper-local/')[1] || '');
    if (!key) throw new Error(`No local object key for ${paper.id}`);
    const response = await fetch(`http://127.0.0.1:12010/openpaper-local/${key.split('/').map(encodeURIComponent).join('/')}`);
    if (!response.ok) throw new Error(`PDF ${paper.id}: ${response.status}`);
    const pdf = Buffer.from(await response.arrayBuffer());
    paper.sha256 = createHash('sha256').update(pdf).digest('hex');
    await writeFile(`${dir}/${paper.id}.pdf`, pdf);
    // The server image supplies an independent PDF engine (pymupdf). stdin
    // only, no container files, database writes, or changes to the deployment.
    const oracle = execFileSync('docker', ['exec', '-i', 'openpaper-server-1', 'python', '-c',
      `import sys,json,pymupdf\ndoc=pymupdf.open(stream=sys.stdin.buffer.read(),filetype='pdf')\nprint(json.dumps([{'width':p.rect.width,'height':p.rect.height,'words':p.get_text('words')} for p in doc]))`], { input: pdf, maxBuffer: 128e6 });
    await writeFile(`${dir}/${paper.id}.words.json`, oracle);
    delete paper.s3_object_key;
    delete paper.file_url;
  }
  manifest.exportedAt = new Date().toISOString();
  await writeFile(`${dir}/manifest.json`, JSON.stringify(manifest, null, 2));
  return manifest;
}

# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx"]
# ///
"""End-to-end smoke check against the running compose stack.

    uv run scripts/smoke.py [--paper-id ID] [--skip-chat]

Mints a session token in Postgres, then: opens a paper (detail, PDF bytes,
figures, outline), runs one chat turn, creates and deletes a highlight with a
note, and loads the paper page from the client. Exits non-zero on the first
failure. Everything it creates is deleted again.
"""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
import time

import httpx

API = "http://127.0.0.1:12001"
CLIENT = "http://127.0.0.1:12000"
PG = ["docker", "exec", "openpaper-postgres-1", "psql", "-U", "postgres", "-d", "openpaper", "-tAc"]


def sql(query: str) -> str:
    return subprocess.run([*PG, query], check=True, capture_output=True, text=True).stdout.strip()


def step(name: str):
    print(f"• {name} ... ", end="", flush=True)
    return time.monotonic()


def ok(t0: float, detail: str = "") -> None:
    print(f"ok ({time.monotonic() - t0:.2f}s){' ' + detail if detail else ''}")


def fail(msg: str) -> None:
    print(f"FAIL\n  {msg}")
    sys.exit(1)


def expect(resp: httpx.Response, *codes: int) -> httpx.Response:
    if resp.status_code not in codes:
        fail(f"{resp.request.method} {resp.request.url} -> {resp.status_code}: {resp.text[:400]}")
    return resp


def mint_session() -> str:
    token = "smoke-" + secrets.token_urlsafe(24)
    user_id = sql("select id from users order by created_at limit 1")
    if not user_id:
        fail("no users in the database")
    sql(
        "insert into sessions (id, token, user_id, expires_at, created_at, updated_at) "
        f"values (gen_random_uuid(), '{token}', '{user_id}', now() + interval '1 hour', now(), now())"
    )
    return token


def chat_turn(api: httpx.Client, paper_id: str) -> None:
    t0 = step("chat turn")
    conv = expect(api.post(f"/api/conversation/paper/{paper_id}"), 201).json()
    try:
        body = {
            "trigger": "submit-message",
            "id": conv["id"],
            "messages": [
                {
                    "id": "smoke-user-1",
                    "role": "user",
                    "parts": [{"type": "text", "text": "In one sentence, what is this paper's main contribution? Cite where it says so."}],
                }
            ],
            "paper_id": paper_id,
            "conversation_id": conv["id"],
        }
        text, kinds = [], set()
        with api.stream("POST", "/api/message/chat/paper", json=body, timeout=180) as resp:
            if resp.status_code != 200:
                resp.read()
                expect(resp, 200)
            for line in resp.iter_lines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                part = json.loads(line[6:])
                kinds.add(part.get("type"))
                if part.get("type") == "error":
                    fail(f"stream error part: {part}")
                if part.get("type") == "text-delta":
                    text.append(part.get("delta", ""))
        answer = "".join(text)
        if not answer.strip():
            fail(f"empty answer; parts seen: {sorted(k for k in kinds if k)}")
        if "finish" not in kinds:
            fail(f"stream ended without a finish part; parts seen: {sorted(k for k in kinds if k)}")
        title = None
        for _ in range(20):  # the title is generated after the turn
            title = expect(api.get(f"/api/conversation/{conv['id']}"), 200).json().get("title")
            if title:
                break
            time.sleep(0.5)
        if not title:
            fail("conversation title was not generated")
        ok(t0, f"{len(answer)} chars, title={title!r}")
    finally:
        api.delete(f"/api/conversation/{conv['id']}")


def upload_roundtrip(api: httpx.Client, pdf_path: str) -> None:
    """Upload a PDF, wait for ingest to finish, check the result, delete it."""
    t0 = step("upload + ingest")
    with open(pdf_path, "rb") as fh:
        job = expect(api.post("/api/paper/upload", files={"file": ("smoke.pdf", fh, "application/pdf")}), 200, 201, 202).json()
    deadline = time.monotonic() + 300
    status = {}
    while time.monotonic() < deadline:
        status = expect(api.get(f"/api/paper/upload/status/{job['job_id']}"), 200).json()
        if status.get("status") in ("completed", "failed"):
            break
        time.sleep(1)
    paper_id = status.get("paper_id")
    try:
        if status.get("status") != "completed" or not paper_id:
            fail(f"ingest did not complete: {status}")
        paper = expect(api.get("/api/paper", params={"id": paper_id}), 200).json()
        if not paper.get("title"):
            fail("ingested paper has no title")
        highlights = expect(api.get(f"/api/highlight/{paper_id}"), 200).json()
        figures = expect(api.get(f"/api/paper/{paper_id}/figures"), 200).json()
        ok(t0, f"{paper['title'][:50]!r}, {len(figures)} figures, {len(highlights)} highlights")
    finally:
        if paper_id:
            expect(api.delete("/api/paper", params={"id": paper_id}), 200, 204)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper-id")
    ap.add_argument("--skip-chat", action="store_true")
    ap.add_argument("--upload", metavar="PDF", help="also upload this PDF, wait for ingest, then delete it (spends OCR/LLM credits)")
    args = ap.parse_args()

    token = mint_session()
    cookies = {"session_token": token}
    touched: tuple[str, str] | None = None
    try:
        with httpx.Client(base_url=API, cookies=cookies, timeout=60) as api:
            t0 = step("auth")
            me = expect(api.get("/api/auth/me"), 200).json()
            ok(t0, str(me.get("user", me).get("email", "")))

            paper_id = args.paper_id
            if not paper_id:
                t0 = step("pick paper")
                papers = expect(api.get("/api/paper/all"), 200).json()
                papers = papers.get("papers", papers) if isinstance(papers, dict) else papers
                if not papers:
                    fail("library is empty")
                paper_id = papers[0]["id"]
                ok(t0, paper_id)

            # Opening a paper bumps its last_accessed_at (the library's "recent"
            # order); put it back afterwards so smoke runs don't reorder it.
            touched = (paper_id, sql(f"select coalesce(last_accessed_at::text, '') from papers where id = '{paper_id}'"))

            t0 = step("paper detail")
            paper = expect(api.get("/api/paper", params={"id": paper_id}), 200).json()
            ok(t0, repr((paper.get("title") or "")[:60]))

            t0 = step("PDF bytes")
            pdf = httpx.get(paper["file_url"], timeout=60, follow_redirects=True)
            if pdf.status_code != 200 or not pdf.content.startswith(b"%PDF"):
                fail(f"PDF fetch {paper['file_url']} -> {pdf.status_code}")
            ok(t0, f"{len(pdf.content) // 1024} KB")

            t0 = step("figures")
            figures = expect(api.get(f"/api/paper/{paper_id}/figures"), 200).json()
            available = [f for f in figures if f.get("available")]
            if available:
                img = expect(api.get(f"/api/paper/{paper_id}/figure/{available[0]['id']}"), 200)
                if not img.content.startswith(b"\x89PNG"):
                    fail("figure is not a PNG")
            ok(t0, f"{len(figures)} listed, {len(available)} rendered")

            t0 = step("outline")
            outline = expect(api.get("/api/paper/outline", params={"id": paper_id}, timeout=120), 200).json()
            ok(t0, f"{len(outline)} entries")

            if not args.skip_chat:
                chat_turn(api, paper_id)

            t0 = step("highlight + note")
            position = {
                "boundingRect": {"x1": 50, "y1": 50, "x2": 200, "y2": 70, "width": 612, "height": 792, "pageNumber": 1},
                "rects": [{"x1": 50, "y1": 50, "x2": 200, "y2": 70, "width": 612, "height": 792, "pageNumber": 1}],
            }
            hl = expect(
                api.post("/api/highlight", json={"paper_id": paper_id, "raw_text": "smoke", "position": position, "page_number": 1}),
                201,
            ).json()
            try:
                note = expect(
                    api.post("/api/annotation", json={"paper_id": paper_id, "highlight_id": hl["id"], "content": "smoke note"}),
                    201,
                ).json()
                listed = expect(api.get(f"/api/highlight/{paper_id}"), 200).json()
                if not any(h["id"] == hl["id"] for h in listed):
                    fail("new highlight not listed")
                expect(api.delete(f"/api/annotation/{note['id']}"), 200, 204)
            finally:
                expect(api.delete(f"/api/highlight/{hl['id']}"), 200, 204)
            ok(t0)

            if args.upload:
                upload_roundtrip(api, args.upload)

        t0 = step("client paper page")
        page = httpx.get(f"{CLIENT}/paper/{paper_id}", cookies=cookies, timeout=60, follow_redirects=False)
        if page.status_code != 200:
            fail(f"client /paper/{paper_id} -> {page.status_code}")
        ok(t0)
    finally:
        sql(f"delete from sessions where token = '{token}'")
        if touched:
            pid, ts = touched
            value = f"'{ts}'" if ts else "null"
            sql(f"update papers set last_accessed_at = {value} where id = '{pid}'")

    print("smoke: all checks passed")


if __name__ == "__main__":
    main()

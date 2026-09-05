"""Standalone pydantic-ai agent with exactly one tool: run_python.

Mirrors how server/app/llm/_pai_compat.py builds Azure models (v1 endpoint ->
plain AsyncOpenAI client + OpenAIProvider + OpenAIResponsesModel + a stricter
schema transformer), but imports nothing from the server app.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import openai
from dotenv import load_dotenv
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
from pydantic_ai.profiles.openai import OpenAIJsonSchemaTransformer, openai_model_profile
from pydantic_ai.providers.openai import OpenAIProvider

from helpers import PRELUDE_DOC
from sandbox import RepoSandbox

SERVER_ENV = Path("~/p/tmp/openpaper/server/.env")

_AZURE_UNSUPPORTED_STRICT_KEYWORDS = frozenset({
    "minLength", "maxLength", "pattern", "format",
    "minimum", "maximum", "multipleOf",
    "patternProperties", "unevaluatedProperties", "propertyNames",
    "minProperties", "maxProperties",
    "unevaluatedItems", "contains", "minContains", "maxContains",
    "minItems", "maxItems", "uniqueItems",
})


class AzureStrictJsonSchemaTransformer(OpenAIJsonSchemaTransformer):
    def transform(self, schema):  # type: ignore[override]
        schema = super().transform(schema)
        for kw in _AZURE_UNSUPPORTED_STRICT_KEYWORDS:
            schema.pop(kw, None)
        return schema


def load_env() -> None:
    load_dotenv(SERVER_ENV)


def build_model(model_id: str) -> OpenAIResponsesModel:
    endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
    api_key = os.environ["OPENAI_API_KEY"]  # doubles as the Azure key here
    # The DeepSeek deployment is low-capacity and 429s under even light load.
    # Absorb that at the HTTP layer (the SDK backs off and retries the single
    # failed request) rather than at the run layer, which would throw away all
    # the agent's progress and restart the conversation from scratch.
    client = openai.AsyncOpenAI(
        api_key=api_key, base_url=endpoint, timeout=180.0, max_retries=8
    )
    provider = OpenAIProvider(openai_client=client)
    profile = replace(
        openai_model_profile(model_id),
        json_schema_transformer=AzureStrictJsonSchemaTransformer,
    )
    return OpenAIResponsesModel(model_id, provider=provider, profile=profile)


def build_settings(model_id: str) -> OpenAIResponsesModelSettings | None:
    # DeepSeek-V4-Flash-0731 400s on reasoning_effort (see the server's
    # _KNOWN_MODEL_CAPS); gpt-5.4-mini supports it.
    if model_id.lower().startswith("gpt-"):
        return OpenAIResponsesModelSettings(openai_reasoning_effort="low")
    return None


# --------------------------------------------------------------------------
# System prompts. The sandbox facts here are what probe2/probe4/probe5 actually
# measured -- an inaccurate list is how a weak model burns calls on
# AttributeError.
# --------------------------------------------------------------------------

_CITATION_RULE = """\
CITING CODE (required)
Whenever you quote or point at code, cite it as a path relative to the repo
root plus a line range, on its own line, exactly in this form:

    path/from/repo/root.py lines 120-134

Then show the quoted code. Use the real path (drop the leading `/repo/`) and
real line numbers from the file you actually read. Never guess a line number:
if you are unsure, read the file and count. A citation you did not verify in
the file is worse than no citation.
"""

_SANDBOX_FACTS = """\
THE SANDBOX (read this carefully -- it is NOT full Python)
You run code with the `run_python` tool inside Monty, a restricted Python
interpreter. Session state PERSISTS across run_python calls: variables and
functions you define stay available in later calls.

The repository is mounted READ-ONLY at /repo. You cannot write files, run
shell commands, or use the network.

Modules that exist: json, re, pathlib, os, math, itertools, collections,
dataclasses, datetime, typing, sys.
Modules that DO NOT exist (do not try): functools, os.path, glob, fnmatch,
io, string, textwrap, ast, csv, hashlib, subprocess, shutil, urllib, logging,
enum, copy, operator, random, time -- and NO third-party packages at all
(no numpy, no torch, no requests).

Specific gaps that will bite you if you forget them:
  * os.walk does NOT exist. os.path does NOT exist. os.listdir(dir) does.
  * Path.glob and Path.rglob do NOT exist. Path.iterdir() does.
  * To recurse a directory tree you must hand-roll it, e.g.:
        import os
        from pathlib import Path
        def walk(root):
            out, stack = [], [root]
            while stack:
                d = stack.pop()
                for name in sorted(os.listdir(d)):
                    full = d + '/' + name
                    if Path(full).is_dir():
                        stack.append(full)
                    else:
                        out.append(full)
            return sorted(out)
  * json.load does not exist (json.loads does). itertools.groupby/product do
    not exist. re.subn does not exist. str.format does not exist -- use
    f-strings.
  * dir(), vars(), eval(), exec(), globals(), locals() do not exist.
  * Class inheritance, `yield`/generators, and `match` statements are not
    supported by the parser. Plain classes, functions, comprehensions,
    lambdas, f-strings, dataclasses, try/except and walrus all work fine.

What DOES work well: open(path).read(), Path(p).read_text(), Path(p).is_dir(),
Path(p).iterdir(), os.listdir(), the whole `re` module, string methods,
list/dict/set comprehensions, sorted(key=...), enumerate, zip.

OUTPUT RULES
The tool returns whatever you printed plus the value of the final expression,
TRUNCATED AT 6000 CHARACTERS. Do not dump whole files or whole directory
trees blindly -- print selectively: counts, matched lines with line numbers,
specific line ranges. If output gets truncated you have wasted a call.

HOW TO WORK
Explore iteratively, one focused run_python call at a time:
  1. list the top-level structure,
  2. narrow to the files that matter,
  3. read the specific line ranges you need,
  4. search with `re` when you need to find something across files.
Build up helper functions in early calls and reuse them later -- state
persists. You have at most 25 run_python calls; most questions need far fewer.
Ground every claim in code you actually read in this session.
"""

BARE_PROMPT = f"""\
You are a code-inspection assistant answering questions about a research
paper's companion source repository. You have exactly one tool, `run_python`.

{_SANDBOX_FACTS}
{_CITATION_RULE}
When you are done exploring, give a clear prose answer to the user's question,
with the code citations that support it.
"""

PRELUDE_PROMPT = f"""\
You are a code-inspection assistant answering questions about a research
paper's companion source repository. You have exactly one tool, `run_python`.

{_SANDBOX_FACTS}
HELPER FUNCTIONS (use these first -- they are much cheaper than hand-rolling)
{PRELUDE_DOC}
Because `read()` and `grep()` print real line numbers, use those numbers
directly in your citations.

{_CITATION_RULE}
When you are done exploring, give a clear prose answer to the user's question,
with the code citations that support it.
"""


def build_agent(model_id: str, sandbox: RepoSandbox, *, prelude: bool) -> Agent:
    model = build_model(model_id)
    agent = Agent(
        model,
        instructions=PRELUDE_PROMPT if prelude else BARE_PROMPT,
        model_settings=build_settings(model_id),
        retries=2,
    )

    @agent.tool_plain
    def run_python(code: str) -> str:
        """Execute Python inside the sandbox where the repository is mounted
        read-only at /repo. Session state persists between calls. Returns
        anything printed plus the final expression's value, or the traceback
        if the code raised.

        Args:
            code: The Python snippet to execute.
        """
        return sandbox.run_python(code)

    return agent

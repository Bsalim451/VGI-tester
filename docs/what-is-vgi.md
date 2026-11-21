# What is VGI, and why would you use it?

Short version: VGI lets you add your own functions and tables to DuckDB without
writing a C++ extension. Your code runs in a separate program (a "worker").
DuckDB starts that program, sends it data, and gets results back.

This page explains the idea. The numbers behind the claims are in
[test-report.md](test-report.md).

## 1. The normal way: a native DuckDB extension

A native extension is a C++ library that DuckDB loads into its own process.
It is fast, because there is no copying and no second process. But:

- It is built against one DuckDB version. When DuckDB releases a new version,
  the extension has to be rebuilt, and often changed, because DuckDB's internal
  C++ API is not stable between versions.
- It has to be built for every platform (Linux, macOS, Windows, x86, ARM).
- It runs inside DuckDB. If the extension crashes, DuckDB crashes with it.
- You write it in C++. You can't just use a Python ML library or a Go parser.

So a native extension is not "build once". It is "build, then keep maintaining".

## 2. The VGI way: one bridge extension + your own worker

VGI (Vector Gateway Interface) is made by [Query.Farm](https://query.farm).
It splits the job in two:

```
┌──────────────────────────┐   Arrow IPC   ┌───────────────────────────┐
│ DuckDB                   │  (stdio pipe, │ Worker (separate process) │
│  + vgi extension (C++)   │◄────────────►│  your code, any language  │
│    one native extension, │  Unix socket  │  with a VGI SDK:          │
│    maintained by         │   or HTTP)    │  Python, Rust, Go, C#     │
│    Query.Farm            │               │                           │
└──────────────────────────┘               └───────────────────────────┘
```

1. **The `vgi` extension** is the only native part. Query.Farm builds it for
   each DuckDB version. You install it like any other extension:
   `INSTALL vgi FROM community; LOAD vgi;`
2. **The worker** is your code. You write it with an SDK. For Python that is
   `vgi-python`. A function is a small class:

   ```python
   class UpperTextFunction(ScalarFunction):
       class Meta:
           name = "upper_text"

       @classmethod
       def compute(cls, value: Annotated[pa.StringArray, Param()]) -> Annotated[pa.StringArray, Returns()]:
           return pc.utf8_upper(value)
   ```

3. **You attach the worker as a catalog**, and its functions show up in SQL:

   ```sql
   ATTACH 'tester' (TYPE vgi, LOCATION 'uv run tester_worker.py');
   SELECT tester.main.upper_text('duckdb');   -- 'DUCKDB'
   ```

   `LOCATION` is a command (DuckDB starts the worker itself) or an
   `http://` address (the worker is already running somewhere).

### What happens during one query

1. **Bind** (planning time). DuckDB asks the worker: does this function exist,
   are the argument types right, what type comes back? Wrong types fail here,
   before any data moves.
2. **Process**. DuckDB sends the rows in Arrow record batches (columns, not
   single rows). The worker computes a whole batch and sends a batch back.
3. **Reuse**. The worker process stays alive in a pool, so the next query
   does not pay the start-up cost again.

Because data travels as Apache Arrow (the same column format DuckDB uses inside),
there is no row-by-row conversion. That keeps the overhead per row small.

### What a worker can expose

Scalar functions (one value per row), table functions (`SELECT * FROM f(...)`),
table in-out functions (`LATERAL f(col)`), aggregates, and even whole catalogs
with tables and views. Workers can opt in to filter and projection pushdown.

## 3. Why this is useful

| | Native C++ extension | VGI worker |
|---|---|---|
| Language | C++ only | Python, Rust, Go, C# (any SDK) |
| Libraries | what you can link into C++ | the whole ecosystem of that language (PyTorch, spaCy, ...) |
| New DuckDB version | rebuild, often code changes | worker code unchanged; Query.Farm rebuilds the `vgi` extension |
| Platforms | build per OS/CPU | Python worker runs wherever Python runs |
| Crash in your code | crashes DuckDB | only the worker dies, DuckDB gives an error and keeps running |
| Can run on another machine | no | yes, over HTTP (for example a GPU box) |
| Install for users | `INSTALL x` (if published) | `ATTACH ... LOCATION 'uvx --from git+https://... worker'` |
| Speed for cheap functions | fastest possible | slower: about 3.5 ms extra per query, and 4.5x to 57x slower on our test functions |
| Speed for heavy functions | fast | the overhead disappears next to the real work |

The best fit is work where the function itself is expensive or needs a library
that only exists in another language. Lorenzo's
[vgi-laya](https://github.com/lmangani/vgi-laya) is a good example: it runs a
ModernBERT model on each row. That takes about 245 ms per row on our test
machine. The few milliseconds VGI adds don't matter there, and you could never
easily put that model inside a C++ extension.

The worst fit is a cheap function on millions of rows, like `a + b`. There the
round trip costs more than the work.

## 4. What VGI does NOT solve (from our tests)

VGI moves the version problem. It doesn't remove it.

- **The `vgi` extension is itself a native extension.** It has to exist for your
  DuckDB version. Of the versions I checked on 13 Nov 2026 (1.4.4, 1.5.2,
  1.5.6, 2.0.0), the standard community repository only had it for v2.0.0.
  Stock DuckDB 1.4.4 and 1.5.2 got an HTTP 404. (haybarn 1.5.6 ships its own,
  older build.)
- **The worker SDK and the extension must speak the same protocol version.**
  A worker built on `vgi-python` 0.51.1 (protocol 3.3) failed on the older
  `vgi` extension that ships with haybarn 1.5.6 (protocol 2). The same worker
  *code* worked on both after only changing the SDK pin to 0.47.0. That is much
  less work than porting C++, but it is still a dependency you have to manage.
- **The SDK moves fast.** `vgi-python` had 86 releases between 15 June and
  12 November 2026, going through protocol majors 1, 2 and 3. VGI is young.
- **You depend on Query.Farm.** If they stop building the `vgi` extension for a
  new DuckDB version, every worker stops working on that version.
- **License.** The `vgi` extension and the SDKs use the Query Farm
  Source-Available License 1.0. It is free to use, change and redistribute,
  also in production. You only need a commercial license to build a competing
  VGI product. Each release becomes Apache-2.0 after ten years. Your own worker
  code can have any license (this repo: Apache-2.0).

## 5. Where this leaves the research question

The internship question is whether a VGI worker is a better choice than a
native extension, because native extensions depend on the DuckDB version and
need ongoing maintenance.

What the first tests say:

- **Maintenance:** yes, a worker is easier to keep alive. Same source file,
  no C++, no rebuild per DuckDB release. But "zero maintenance" is not true:
  you still pin an SDK version that matches the `vgi` extension.
- **Robustness:** yes. A crashing worker did not take DuckDB down.
- **Speed:** depends on the function. For cheap functions native wins by a lot.
  For heavy functions (ML, parsing, API calls) the overhead is noise.
- **Reach:** VGI makes things possible that are not realistic as C++
  extensions, like running a 421M-parameter model from SQL.

So the honest claim is not "VGI is always better". It is "VGI is the better
choice when the work per row is heavy or needs another language, and when you
want to avoid C++ maintenance; native is better for cheap hot-path functions".

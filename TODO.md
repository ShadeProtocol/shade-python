# Async HTTP Client Implementation

## Plan

### 1. http.py — Refactor
- [x] Extract shared `_build_request(method, path, payload)` helper used by both `SyncHTTPClient` and `AsyncHTTPClient`
- [x] Rewrite `AsyncHTTPClient` to use `httpx.AsyncClient` instead of `aiohttp`
  - Same constructor args as `SyncHTTPClient`
  - `async def request(...)` with `async with httpx.AsyncClient(...)` lifecycle
  - `aclose()` method for explicit cleanup
  - `__aenter__` / `__aexit__` context manager support
- [ ] Remove aiohttp import/fallback

### 2. client.py — Add AsyncShadeClient
- [ ] Add `AsyncShadeClient` mirroring `ShadeClient` with `async def request(...)`
- [ ] Uses `httpx.AsyncClient`
- [ ] `async with` context manager and `aclose()`

### 3. gateway.py — Update Gateway
- [ ] Instantiate new httpx-based `AsyncHTTPClient`

### 4. __init__.py — Export updates
- [ ] Export any new public classes

### 5. Tests
- [ ] Update `test_rate_limit.py` — Replace aiohttp mocks with httpx mocks
- [ ] Run tests to confirm everything passes

### 6. pyproject.toml
- [ ] Remove aiohttp dev dependency (no longer needed)


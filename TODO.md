# TODO
- [ ] Refactor `src/shade/http.py`:
  - [ ] Add shared `_build_request(...)` helper for URL + headers + JSON body
  - [ ] Add `_AsyncHTTPClient` using `httpx.AsyncClient` with `async def request(...)`
  - [ ] Implement async lifecycle management (async with / awaited `aclose()`)
  - [ ] Keep sync behavior unchanged and ensure both return same response shape
- [x] Update `src/shade/http.py` public `AsyncHTTPClient` to delegate to `_AsyncHTTPClient`
- [x] Update `tests/test_rate_limit.py` to mock `httpx.AsyncClient` instead of `aiohttp`
- [x] Add `httpx` dependency to `pyproject.toml`
- [x] Run test suite: `pytest`
- [x] Verify async tests don’t create event-loop conflicts (pytest-asyncio compatible)



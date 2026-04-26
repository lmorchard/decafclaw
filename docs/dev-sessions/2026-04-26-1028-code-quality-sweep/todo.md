# Todo — code-quality sweep

- [ ] Phase 1: replace `getattr` for declared fields → direct access
- [ ] Phase 2: remove dead `_skill_shutdown_hooks` storage (registered but never invoked)
- [ ] Phase 3: replace `except: pass` with `except Exception as exc: log.debug(...)` across the verified sites
- [ ] Phase 4: extract `PRIORITY_ORDER`/`PRIORITY_GLYPH`/`meets_priority` to `notification_channels/__init__.py`
- [ ] Phase 5: wrap `mail.py` attachment read in `asyncio.to_thread`
- [ ] Phase 6: `make check` + `make test`, fix any failures
- [ ] Phase 7: commit per phase, push, open PR, request Copilot review

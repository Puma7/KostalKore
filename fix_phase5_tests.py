from pathlib import Path

path = Path("Tests/test_phase5_coverage.py")
text = path.read_text(encoding="utf-8")
text = text.replace('AuthenticationException("bad")', 'AuthenticationException("bad", "error")')
text = text.replace('side_effect=[0.0, 10.0]', 'return_value=0.0')
text = text.replace('er.async_get_or_create(', 'entity_registry.async_get_or_create(')
text = text.replace('class DummyClient:\n        async def __aenter__', 'class DummyClient:\n        def __init__(self, *_args, **_kwargs):\n            pass\n\n        async def __aenter__')
path.write_text(text, encoding="utf-8")

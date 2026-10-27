# Bundled pykoplenti

This folder is a copy of [pykoplenti](https://github.com/stegm/pykoplenti)
**1.5.1** by Stefan Meyer (`@stegm`), licensed under the Apache License 2.0
(see `LICENSE`). It ships with KOSTAL KORE so the integration does not depend on
the pykoplenti package from PyPI and its version constraints.

Changes against the PyPI release 1.5.1:

- `api.py`: the session token in the login is encrypted with
  `cryptography`'s AES-GCM (`encrypt_session_token`) instead of
  `pycryptodome`. `cryptography` is a dependency of Home Assistant itself;
  the encrypted bytes are identical. pykoplenti 1.5.0 and 1.5.1 pin
  `pycryptodome~=3.19`, and a broken or mixed `pycryptodome` installation made
  the login fail with `cannot import name 'MessageTooLongError' from 'Crypto'`.
- `api.py`: when several requests find the session expired at the same time,
  they share one re-login behind a per-client lock (`_relogin`), instead of
  starting overlapping logins that invalidate each other's new sessions. Only
  the login runs under the lock; requests are retried after it is released.
- `cli.py` (the command-line tool) is not included.

Everything else is unchanged. Import it relatively inside the integration
(`from .pykoplenti import ApiClient`), never as the top-level `pykoplenti`
package: Home Assistant's built-in `kostal_plenticore` integration installs
that one, possibly in another version.

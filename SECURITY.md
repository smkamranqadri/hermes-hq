# Security

## Reporting a problem

Please report security problems privately through GitHub's
[security advisories](https://github.com/smkamranqadri/hermes-hq/security/advisories/new),
not in a public issue. Expect a first reply within a week.

## Threat model

hermes-hq is built for **one owner** running it on their own server, reached over a **private
network**. It is not a multi-user product and is not hardened for the open internet.

Anyone who logs in can do what the server's user can do:

- run shell commands in the **web terminal**,
- start agents that edit files, run commands and use the tools their Hermes profiles allow,
- read and change every project folder, note and agent profile hermes-hq can see.

Treat the login like SSH access to the machine.

## How to run it safely

- **Keep it private.** Reach it over a VPN (Tailscale, WireGuard) or bind it to localhost with
  `--host 127.0.0.1`. The default bind is `0.0.0.0:9010` so a VPN address works out of the box;
  do not forward that port to the internet.
- **Put HTTPS in front.** The server speaks plain HTTP. Use `tailscale serve` or a reverse proxy
  with a certificate; Web Push and voice capture need HTTPS anyway.
- **Use a long generated password.** The generated one is 16 random URL-safe characters. If you
  set `HERMES_HQ_PASSWORD` yourself, make it long. If a password has ever been written anywhere
  public (a commit, a log you shared), change it.
- **Run it as a normal user,** not root. Agents run with the server's permissions.

## Known limits

- **No login rate limiting.** `/api/login` does not slow down or lock out repeated attempts. A
  long password and a private network are the defence.
- **The generated password is printed to the server log** on first start (journald or the s6 log
  directory). Restrict who can read those logs, or set `HERMES_HQ_PASSWORD`.
- **Session tokens are kept on disk.** They are stored in
  `$HERMES_HQ_HOME/sessions.json` (mode 0600) and last 30 days; logging out ends the session.
- **File permissions are not a boundary on every host.** On some VM setups (for example a
  virtiofs mount shared with the host) file ownership is remapped, so a mode of 0600 may not keep
  an agent from reading a file. The vault does not rely on file modes for that reason.

## Safeguards

- Every `/api/*` route except login and `/api/health` requires a session cookie (`HttpOnly`, `SameSite=Lax`), and
  every state-changing request also needs a CSRF header tied to the session. Paths are normalised
  before the check, so `//api/…` and `/api/./…` cannot slip past it.
- The static file handler only serves files inside the built UI folder.
- The **vault** keeps chosen notes encrypted with AES-GCM under a key derived from its own
  passphrase (scrypt). The key lives only in server memory while unlocked, and vault data sits in
  its own tables, so the librarian and other agents cannot list, search or export it.
- Imports quarantine anything that looks like a private key or cloud credential instead of
  storing it in the database.
- hermes-hq stores no model API keys; providers stay configured in Hermes.

# ADR 0006: Authentication with JWT and two roles

Date: 2026-11-08 · Status: accepted

## Context

The API was open and single-user: `sessions.user_id` existed but was nullable and never filtered,
and the trace endpoint was protected only by `DEBUG_API=true`, which mounted or hid the route.
Plant engineers must not see each other's conversations, and the trace (tool calls, raw results,
tokens, cost) is for the people who debug the service, not for engineers.

## Decision

- **Users and roles.** A `users` table (email unique and lowercase, Argon2 hash via
  `pwdlib`'s `PasswordHash.recommended()`, role `engineer | developer`). `sessions.user_id` is
  `NOT NULL` with a foreign key `ON DELETE CASCADE`. Migration 0003 deletes the ownerless
  sessions created before this change.
- **Stateless access tokens.** `POST /api/auth/login` returns an HS256 JWT signed with
  `JWT_SECRET` (claims `sub`, `role`, `exp`; `JWT_EXPIRE_MINUTES`, default 30). There is no
  refresh token: the client logs in again. The API refuses to start without a `JWT_SECRET` of at
  least 32 characters. `get_current_user` loads the user from the database on every request,
  so the role applied is the stored one (the `role` claim is informational) and a deleted user
  is rejected before the token expires.
- **Protected by default.** Every `/api/*` route except login lives on a router that depends on
  `get_current_user`; `/health` stays public. A test sweeps the OpenAPI paths so a new route
  cannot ship without the dependency.
- **Ownership as 404.** Sessions are filtered by `user_id` in the repository's `WHERE` clauses.
  Another user's session answers `404 session_not_found`, the same as a missing one, so its
  existence is not revealed.
- **The role replaces `DEBUG_API`.** The trace route always exists and requires
  `require_role("developer")` (403 `forbidden` for engineers). A developer can read the trace
  of any user's message. The eval runner logs in with a developer account
  (`EVAL_EMAIL` / `EVAL_PASSWORD`) and logs in again once if its token expires mid-run.
- **Login enumeration.** An unknown email and a wrong password get the same 401
  `invalid_credentials`, and an unknown email is verified against a decoy hash so both take the
  same time.
- **Brute force.** An in-memory sliding window counts login attempts per (client IP, email):
  `LOGIN_MAX_ATTEMPTS` (5) within `LOGIN_WINDOW_MINUTES` (15) answers 429 `too_many_attempts`
  with `Retry-After`, even for the right password. The attempt is counted when it starts, in
  the same step as the check, so a burst of parallel requests cannot get past the limit. A
  successful login clears the count, so in practice it counts failures. Expired keys are swept
  once the table holds 10 000 keys.
- **Argon2 off the event loop.** Password verification runs in a worker thread
  (`run_in_threadpool`), so a login does not stall chat turns.
- **Demo users.** `python -m app.db.seed` creates `engineer1@example.com`,
  `engineer2@example.com` and `developer@example.com` if missing, with passwords from
  `SEED_*_PASSWORD`. It runs in the container after the migrations and the catalog load, and
  never overwrites an existing user. Development defaults live only in `docker-compose.yml`.

## Consequences

- The Android app cannot talk to the API until it gains a login screen and sends the token
  (next task). The response fields of the existing endpoints did not change.
- The rate limiter is per process: it resets on restart, assumes a single uvicorn worker, and
  behind a reverse proxy it sees the proxy's IP. A shared store (Postgres or Redis) would be
  needed for several workers.
- When every client shares one IP (a reverse proxy, or Docker Desktop's port forwarding, which
  often shows the gateway address), the key is effectively per email: anyone can lock an
  account, including the developer account the eval runner uses, by sending 5 wrong passwords
  every 15 minutes. Acceptable for a demo; a deployment would read the client IP from a trusted
  proxy header and add a per-IP limit.
- Argon2 is deliberately expensive (memory and CPU). Logins from rotating emails still cost a
  hash each, decoy included; the thread pool keeps the API responsive, but a sustained flood
  needs a per-IP limit in front.
- A token cannot be revoked before it expires except by deleting the user or rotating
  `JWT_SECRET` (which signs out everyone). Short expiry keeps that window small.
- Rotating a demo password needs a manual update, because the seed only creates missing users.

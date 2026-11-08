# ADR 0007: App session: encrypted storage and one session state

Date: 2026-11-08 · Status: accepted

## Context

The API requires a Bearer token (ADR 0006). The Android app has to sign in, keep the session
across restarts without storing the password, send the token on every call, and return to Login
when the token stops working. `EncryptedSharedPreferences` (androidx security-crypto), the
usual choice, is deprecated; DataStore 1.3.0 ships `datastore-tink`, an `AeadSerializer` that
encrypts a typed DataStore with Tink.

## Decision

- **Storage.** The token, email and role live in a typed DataStore file (`session.json`)
  encrypted with Tink AES-256-GCM through `AeadSerializer`, with the file name as associated
  data. The Tink keyset is in SharedPreferences, encrypted by a master key in the Android
  Keystore. A file that cannot be decrypted (reinstall, lost key) is replaced by an empty
  session through `ReplaceFileCorruptionHandler`: the user signs in again. The password is never
  stored. The session file and the keyset prefs are excluded from backups and device transfer.
  When the Keystore is unavailable, Tink's `AndroidKeysetManager` falls back to a keyset in clear;
  the store checks `isUsingKeystore` and then writes nothing, so the session lasts only while the
  process lives. A store failure (I/O, Keystore) is logged and never crashes the app.
- **One session state.** An app-scoped `SessionManager` holds `Checking`, `SignedOut`,
  `SignedIn(user)` or `VerifyFailed(error)`. The UI root renders one screen per state. Each state
  with screens (signed out, signed in as a given user) gets its own `ViewModelStore`, held by the
  activity and cleared when the state is left: logout or a 401 releases the previous user's
  conversations, chat and back stack, and the login form starts empty. Validation, logout and disk
  writes run in the manager's app scope, so closing a screen cannot leave them half done; token
  and state change together, and a disk write never undoes a newer session.
- **Validation on start.** A stored token is checked with `GET /api/auth/me` before entering.
  Only a 401 discards it; any other failure shows Retry / Sign out instead of entering with an
  unchecked token or forcing a new login for a network blip. The role from `/me` replaces the
  stored one.
- **Token on the wire.** An OkHttp interceptor adds `Authorization: Bearer` to every request
  but the login, and reports any 401 to the `SessionManager` with the token it used. A late 401
  for an older token is ignored, so it cannot sign out a session that just started. A 401 on a call
  that carried no token is reported too. The debug logging interceptor redacts `Authorization`.
- **Debug access** depends only on `user.isDeveloper` (the role from the server): the bug icon,
  the chat's ⋮ → Debug entry and the Debug route are composed only for developers; there is no
  hidden developer mode. The server still enforces it (403 → "No tienes permiso para ver
  trazas", not retried).

## Consequences

- New dependencies: `androidx.datastore:datastore` and `datastore-tink` 1.3.0, plus
  `tink-android` 1.20.0 declared explicitly (the version `datastore-tink` brings) because the app
  uses `AndroidKeysetManager` directly.
- The encrypted store needs the Android Keystore, so it is covered by an instrumented test
  (`TinkSessionStoreTest`); JVM tests use an in-memory `SessionStore`.
- With no refresh token, a session lasts `JWT_EXPIRE_MINUTES` (30 by default); after that the
  next call returns the user to Login.

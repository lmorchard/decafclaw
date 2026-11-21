# DecafClaw Voice Transceiver (Android)

A lightweight, low-latency mobile voice transceiver app for [DecafClaw](https://github.com/lmorchard/decafclaw), built with Expo SDK 58 and React Native. Lives in the DecafClaw monorepo at `contrib/android/`.

Designed for screen-on, tap-to-talk voice interactions on the **Google Pixel 10 Pro**, using on-device Tensor TPU speech recognition and local Android speech synthesis.

---

## Architecture & Flow

```
[ User Speaks ]
      │ (Acoustic audio)
      ▼
[ Pixel Tensor TPU ] ──(expo-speech-recognition / on-device STT)
      │
      ▼ (Text string + punctuation)
[ WebSocket /ws/chat ] ──(Cookie: decafclaw_session=...)──► [ DecafClaw Server ]
                                                                   │
                                                           (Streamed Tokens)
                                                                   ▼
[ Phone Speaker ] ◄──(expo-speech / Native TTS) ◄── [ Sentence Chunk Pipeline ]
                      (0.90x pitch, 1.10x rate)      (Markdown normalizer)
```

1. **On-Device STT (Tensor TPU)**:
   - Uses `expo-speech-recognition` with `requiresOnDeviceRecognition: true`.
   - Runs locally on the phone's Tensor hardware with instant punctuation and zero server audio compute load.
   - Microphone permission is checked at launch and requested on the first tap, not at startup.
2. **WebSocket Gateway**:
   - Streams directly to DecafClaw's `/ws/chat` endpoint with token session auth.
   - Exponential backoff reconnect, foreground wake recovery (`AppState`), a 25s ping keepalive, and a 45s turn watchdog that reconnects and reloads the turn from conversation history.
   - Turns spoken while the socket is down are queued and flushed on reconnect.
   - Server `error` events render as transcript errors instead of hanging on "Thinking...". A `4001` close reports an auth failure and stops reconnecting.
3. **Pipelined Native Speech (TTS)**:
   - Speaks each complete sentence as soon as its boundary arrives, using Android's native Google Speech engine via `expo-speech`.
   - Normalizes Markdown syntax (code fences, wiki links, links, headers, lists, tables, HTML, emphasis) into speakable English.
   - A `■` button in the footer stops playback without opening the mic.
4. **Unified Push-to-Talk & Controls**:
   - Bottom button acts as connection monitor and push-to-talk (`Tap to Speak` / `Listening... Tap to Stop` / `Disconnected · Tap to Connect`).
   - A live status line shows `Thinking...`, `Tool: ...`, `Queued — reconnecting...`, and auth errors.
   - Hamburger menu (`☰`) opens Settings: DecafClaw host/token, TTS toggle, pitch/speed, starred voices, and inline audition `▶` buttons.

---

## Configuration

The Settings sheet stores the DecafClaw connection in `AsyncStorage`:

| Setting | Key | Default |
| --- | --- | --- |
| Host URL | `@decafclaw_host` | `http://decafclaw:18880` |
| Session token | `@decafclaw_token` | `DEFAULT_TOKEN` in `src/config.ts` |

Saving either value reconnects the socket and starts a fresh conversation on the new server. Hosts are normalized to `http://`/`https://` and trailing slashes are stripped.

Android cleartext HTTP is enabled through `expo-build-properties` in `app.json` so the default plain-`http` host also works in release builds. Use `https://` when the server is reachable outside a trusted network.

---

## Project Structure

```
contrib/android/
├── App.tsx                     # Coordinator: transcript, settings, batching, speech tracking
├── app.json                    # Expo config, permissions, build plugins
├── run-android.sh              # Local build launcher with Java 17 & Android SDK env
├── eslint.config.js            # Expo flat ESLint config
├── vitest.config.mts           # Unit test config
├── src/
│   ├── types.ts                # TypeScript contracts (ChatMessage, VoiceItem)
│   ├── config.ts               # Default host/token
│   ├── utils/
│   │   ├── speech.ts           # Markdown normalizer & voice label formatter
│   │   ├── speech.test.ts
│   │   ├── connection.ts       # Host URL normalization
│   │   └── connection.test.ts
│   ├── hooks/
│   │   ├── useDecafClawWebSocket.ts # Socket lifecycle, queues, watchdog, history resync
│   │   └── useVoiceRecognition.ts   # Tensor TPU STT hooks & permissions
│   └── components/
│       ├── Header.tsx          # Clean top bar with safe-area insets
│       ├── TranscriptView.tsx  # Memoized in-memory chat transcript
│       ├── PttFooter.tsx       # Speak/connect button, status line, TTS stop, settings
│       └── VoiceSettingsModal.tsx # Connection, TTS, pitch, speed, starred voices
```

---

## Local Development on Mordecai

### Prerequisites
- **Node.js**: v26+ (Homebrew)
- **Java**: `openjdk@17` (`/opt/homebrew/opt/openjdk@17`)
- **Android SDK**: `~/Library/Android/sdk` (`platform-tools`, `platforms;android-35`, `build-tools;35.0.0`)
- **Device**: Pixel 10 Pro paired over wireless `adb` (`replicant26:37003`)

### Common Commands

```bash
# Type check, lint, and unit tests
npx tsc --noEmit
npm run lint
npm test

# Start the Metro bundler (live fast-refresh over Wi-Fi)
npx expo start

# Regenerate the ignored native folder after changing app.json plugins
npx expo prebuild --platform android --clean --no-install

# Recompile and install the debug APK onto Pixel over wireless adb
./run-android.sh

# Restart app remotely over adb
export PATH=$HOME/Library/Android/sdk/platform-tools:$PATH
adb shell am force-stop com.lmorchard.decafclawvoice
adb shell am start -n com.lmorchard.decafclawvoice/.MainActivity

# Inspect device logs / crashes
adb logcat -d -t 100 | grep -iE 'ReactNativeJS|com.lmorchard.decafclawvoice|FATAL'
```

---

## Key Invariants

- **New Architecture Only**: React Native 0.88 / Expo SDK 58 runs the New Architecture (Bridgeless JSI) by default. Do NOT install legacy C++ modules like `expo-av` (which fails with JSI link errors).
- **Socket Closure Safety**: Always read speech parameters and active options from mutable React refs (`settingsRef.current`) rather than callback closures to prevent WebSocket churn or stale voice profiles.
- **Identity-Guarded Sockets**: Every handler checks `wsRef.current === ws`; a socket is detached before it is closed. This prevents a replaced socket's `close` event from scheduling a reconnect that kills the healthy one.
- **Batched Streaming**: Assistant tokens are flushed into the transcript on a 50 ms timer, never per token. Transcript bubbles are memoized and scrolling is throttled.
- **Exact Pins**: All npm dependencies are exact-pinned. The monorepo's `make check-npm-policy` covers this `package.json` too.

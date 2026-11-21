# Agent Runbook & Engineering Guidelines: decafclaw-voice

This document guides autonomous coding agents (Claude Code, OpenCode, Aider, etc.) operating inside `~/devel/decafclaw/contrib/android` on `mordecai`.

---

## 1. System Topology & Environment

* **Host Machine**: `mordecai` (Apple Silicon Mac, macOS).
* **Target Hardware**: Google Pixel 10 Pro (Android 15 / API 35) connected via **wireless `adb`** (`replicant26:37003`).
* **Framework**: React Native 0.88 + Expo SDK 58 (TypeScript blank template with Continuous Native Generation / prebuild).
* **Backend Server**: Canonical DecafClaw Starlette daemon running on VM 302 at `http://decafclaw:18880` (accessible via Tailscale MagicDNS).

### Build Toolchain Invariants
* **Java**: System default Java on `mordecai` is JDK 27, but **Gradle 9.4 requires JDK 17**. Homebrew's `openjdk@17` is installed at `/opt/homebrew/opt/openjdk@17`.
* **Android SDK**: Located at `$HOME/Library/Android/sdk` (`platform-tools`, `platforms;android-35`, `build-tools;35.0.0`).
* **Build Launcher**: **ALWAYS use `./run-android.sh`** to compile native builds. It automatically exports `JAVA_HOME`, `ANDROID_HOME`, and sets up the correct PATH before calling `npx expo run:android`. Never run bare `gradlew` or `npx expo run:android` without these environment variables.

---

## 2. Hard Invariants & Guardrails

1. **New Architecture Only (Bridgeless JSI)**:
   * React Native 0.88 / Expo SDK 58 has New Architecture (`newArchEnabled=true`) and Bridgeless mode enabled by default.
   * **DO NOT install or use `expo-av`**: It contains legacy C++ JSI bindings that fail on startup with `UnsatisfiedLinkError: dlopen failed: cannot locate symbol "_ZNKR8facebook3jsi5Value8asObjectERNS0_7RuntimeE"`.
   * For speech input: use `expo-speech-recognition`.
   * For speech output: use `expo-speech`.
2. **WebSocket Closure Safety**:
   * DecafClaw's WebSocket (`/ws/chat`) stays open continuously across turns.
   * **Never pass state variables directly into WebSocket message callbacks**. Always read speech parameters (selected voice, pitch, rate, mute state) through a React ref (`settingsRef.current`). This prevents stale closure bugs and avoids closing/re-opening the socket on every setting change.
   * Every socket handler must check `wsRef.current === ws` before touching state. Before replacing or closing a socket, null its handlers; otherwise the stale socket's `close` event schedules a reconnect that kills the healthy replacement.
   * Never reconnect after unmount. The hook uses `stoppedRef` plus a nulled-handler close in the effect cleanup.
   * Turns spoken while the socket is down are queued and flushed on `open`; they are not dropped.
   * Host and token are user-configurable; changing either starts a fresh conversation on the new server.
3. **Pipelined TTS & Markdown Normalization**:
   * Token streams are sentence-buffered and every complete boundary (`[.?!]\n` followed by whitespace or end of buffer) is spoken as soon as it arrives.
   * Never feed raw Markdown to `Speech.speak()`. Always run text through `cleanMarkdownForSpeech()` in `src/utils/speech.ts` to strip code fences, wiki links, asterisks, and list/table syntax.
   * Streaming text is batched into the transcript every 50 ms; do not append per-token in `setMessages`.
4. **Barge-In / Instant Mute**:
   * Whenever speech recognition begins or the user taps the speak button, immediately call `Speech.stop()` to silence ongoing assistant playback.
   * The footer exposes a `■` stop button while TTS utterances are queued; keep the utterance counter in sync via `onDone`/`onStopped`/`onError`.

---

## 3. Architecture & Modular Structure

Keep modules small and single-purpose. Do not dump features into `App.tsx`:

* **`App.tsx`**: Top-level coordinator. Wires hooks (`useDecafClawWebSocket`, `useVoiceRecognition`), manages `messages[]` transcript, maintains `settingsRef`, loads/saves preferences (host, token, voice, pitch, rate, TTS, stars) via `AsyncStorage`, batches streamed text, and tracks TTS utterances.
* **`src/types.ts`**: TypeScript definitions (`ChatMessage`, `VoiceItem`).
* **`src/config.ts`**: Default host (`DEFAULT_HOST`) and token (`DEFAULT_TOKEN`). Runtime overrides live in the settings sheet and `AsyncStorage` (`@decafclaw_host`, `@decafclaw_token`).
* **`src/utils/speech.ts`**: `cleanMarkdownForSpeech()` prose normalizer and `formatVoiceLabel()` Google model formatter.
* **`src/utils/connection.ts`**: `normalizeHost()` for user-entered server addresses.
* **`src/utils/*.test.ts`**: Vitest coverage for the pure utilities.
* **`src/hooks/useDecafClawWebSocket.ts`**: Socket lifecycle, identity-guarded handlers, reconnect backoff, AppState foreground recovery, turn watchdog, queued turns, error surfacing, history resync, and REST conversation creation.
  * **Do not call `WebSocket.ping()` on Android.** RN implements it by sending `ByteString.EMPTY` — an empty *binary* frame, not a protocol ping. DecafClaw's `receive_text()` raises `KeyError: 'text'` and closes the socket, producing a reconnect every ping interval. Rely on the server's protocol ping/pong and the inactivity watchdog.
* **`src/hooks/useVoiceRecognition.ts`**: Android native `SpeechRecognizer` via `expo-speech-recognition` targeting the Pixel's offline Tensor TPU. Exposes one-shot `toggleListening` (tap) and `startLongListening` (long-press, `continuous: true` on Android 13+) modes; long sessions accumulate finalized segments and send once on finish.
  * Continuous sessions restart up to 3 times on an unexpected engine `end`, keeping the accumulated buffer. `stop()` gets a 1.5s fallback that aborts and finalizes if no `end` arrives.
* **`src/components/Header.tsx`**: Insets-safe minimal title bar.
* **`src/components/TranscriptView.tsx`**: Memoized IM-style conversation thread with auto-scroll ref.
* **`src/components/PttFooter.tsx`**: Combined PTT / connection button with tap vs long-press handling, a status glyph (green connected, amber working, red offline/error, `♪` speaking), TTS stop button, and settings hamburger (`☰`).
* **`src/components/VoiceSettingsModal.tsx`**: Slide-up sheet with DecafClaw host/token fields, TTS toggle, pitch/speed controls, starred favorites, and inline audition `▶` buttons.
* **`eslint.config.js`**, **`vitest.config.mts`**: Lint and test configuration.

---

## 4. Verification Runbook for Agents

Before completing any task, execute these verification gates:

1. **Type Check, Lint, and Unit Tests**:
   ```bash
   export PATH=/opt/homebrew/bin:$PATH
   npx tsc --noEmit
   npm run lint
   npm test
   ```
   All three must exit 0. The utility tests cover markdown normalization, voice labels, and host normalization.
2. **Native Config Changes**:
   `app.json` uses config plugins (`expo-build-properties` enables Android cleartext so the default `http://` host works in release builds). After changing plugins, regenerate the ignored native folder:
   ```bash
   npx expo prebuild --platform android --clean --no-install
   ```
   Verify `android/app/src/main/AndroidManifest.xml` contains `android:usesCleartextTraffic="true"`.
3. **Device Connection Verification**:
   ```bash
   export PATH=$HOME/Library/Android/sdk/platform-tools:/opt/homebrew/bin:$PATH
   adb devices
   ```
   Verify `replicant26:37003` is listed as `device`.
4. **App Restart & Logcat Inspection**:
   ```bash
   adb shell am force-stop com.lmorchard.decafclawvoice
   adb shell am start -n com.lmorchard.decafclawvoice/.MainActivity
   sleep 2
   adb logcat -d -t 150 | grep -iE 'ReactNativeJS|FATAL|UnsatisfiedLinkError|com.lmorchard.decafclawvoice'
   ```
   Ensure no fatal crashes or link errors occur on launch.

---

## 5. On-Device Verification Recipes

Use these when the change is visual, gesture-driven, or depends on what the phone actually sends. Static checks cannot cover those.

### Stand up a testable session

* Start Metro in CI mode when you only need it to serve bundles:
  ```bash
  CI=1 npx expo start --port 8081
  ```
  A debug APK launched without Metro dies with `Unable to load script`. Start Metro before launching, and force-stop/start the app after a rebuild.
* Wire the port and rebuild/install without blocking on Metro:
  ```bash
  adb reverse tcp:8081 tcp:8081
  ./run-android.sh --no-bundler
  ```
* Clear logs before a scenario, then read only the current run:
  ```bash
  adb logcat -c
  adb shell am force-stop com.lmorchard.decafclawvoice
  adb shell am start -n com.lmorchard.decafclawvoice/.MainActivity
  sleep 5
  adb logcat -d -t 300 | grep -iE 'ReactNativeJS|FATAL|AndroidRuntime'
  ```

### Screenshots and taps

* Wake and dismiss the keyguard first; a fully black capture usually means the device is dozing:
  ```bash
  adb shell input keyevent KEYCODE_WAKEUP
  adb shell wm dismiss-keyguard
  ```
* Prefer on-device capture for transient states. `adb exec-out screencap` can take seconds and miss the state:
  ```bash
  adb shell screencap -p /sdcard/shot.png
  adb pull /sdcard/shot.png /tmp/shot.png
  ```
* `input tap` takes physical pixels (the PNG dimensions or `wm size`), not the size the image is displayed at.
* Get exact bounds instead of eyeballing:
  ```bash
  adb shell uiautomator dump /sdcard/ui.xml
  adb pull /sdcard/ui.xml /tmp/ui.xml
  python3 - <<'EOF'
  import xml.etree.ElementTree as ET
  for n in ET.parse('/tmp/ui.xml').getroot().iter('node'):
      text = n.get('text') or n.get('content-desc')
      if text:
          print(repr(text), n.get('clickable'), n.get('bounds'))
  EOF
  ```
* Simulate a long-press with a same-point swipe longer than `delayLongPress`:
  ```bash
  adb shell input swipe X Y X Y 800
  ```

### Verify app-owned state

* AsyncStorage lives in `RKStorage` (host/token/voice settings):
  ```bash
  adb shell run-as com.lmorchard.decafclawvoice cat databases/RKStorage > /tmp/rk.db
  sqlite3 /tmp/rk.db \
    "select key, value from catalystLocalStorage where key like '%decafclaw%' order by key;"
  ```
* Prove the running bundle contains an edit:
  ```bash
  curl -s 'http://localhost:8081/index.bundle?platform=android&dev=true&minify=false' \
    | grep -c 'Assistant'
  ```

### Correlate with the server

* WebSocket churn and turn delivery are in the daemon journal. The server logs each line twice; count distinct timestamps:
  ```bash
  ssh decafclaw 'journalctl --user -u decafclaw.service --since "-2 min" --no-pager \
    | grep -E "WebSocket (connected|disconnected|error)"'
  ```
* Live sockets from the phone:
  ```bash
  ssh decafclaw 'ss -tnp | grep 100.89.1.96'   # phone Tailscale IP
  ```
  This is how the ~27s reconnect loop was found: a 25s client ping plus 2s backoff.

### Read the native module when JS is surprising

`node_modules` is the source of truth for platform behavior:
* `react-native/ReactAndroid/.../WebSocketModule.kt` — `ping()` sends `ByteString.EMPTY`, an empty binary frame.
* `expo-speech-recognition/android/.../ExpoSpeechService.kt` — continuous/segmented recognition and `stop()`/`abort()` semantics.

`CI=1` Metro disables Fast Refresh. Use plain `npx expo start` for interactive development; stop a verification-only Metro when done.

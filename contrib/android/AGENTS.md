# Agent Runbook & Engineering Guidelines: decafclaw-voice

This document guides autonomous coding agents (Claude Code, OpenCode, Aider, etc.) operating inside `~/devel/decafclaw-voice` on `mordecai`.

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
3. **Pipelined TTS & Markdown Normalization**:
   * Token streams are sentence-buffered and split at boundaries (`[.?!]\s+|\n+`) so speech plays as sentences arrive.
   * Never feed raw Markdown to `Speech.speak()`. Always run text through `cleanMarkdownForSpeech()` in `src/utils/speech.ts` to strip code fences, wiki links, asterisks, and bullet points.
4. **Barge-In / Instant Mute**:
   * Whenever speech recognition begins or the user taps the speak button, immediately call `Speech.stop()` to silence ongoing assistant playback.

---

## 3. Architecture & Modular Structure

Keep modules small and single-purpose. Do not dump features into `App.tsx`:

* **`App.tsx`**: Top-level coordinator. Wires hooks (`useDecafClawWebSocket`, `useVoiceRecognition`), manages `messages[]` transcript, maintains `settingsRef`, loads/saves preferences via `AsyncStorage`.
* **`src/types.ts`**: TypeScript definitions (`ChatMessage`, `VoiceItem`).
* **`src/config.ts`**: Endpoints (`DEFAULT_HOST`) and auth token (`DEFAULT_TOKEN`).
* **`src/utils/speech.ts`**: `cleanMarkdownForSpeech()` prose normalizer and `formatVoiceLabel()` Google model formatter.
* **`src/hooks/useDecafClawWebSocket.ts`**: Socket lifecycle, auto-reconnect backoff, AppState foreground recovery, turn dispatching via REST conversation creation and WS `send` message.
* **`src/hooks/useVoiceRecognition.ts`**: Android native `SpeechRecognizer` via `expo-speech-recognition` targeting the Pixel's offline Tensor TPU.
* **`src/components/Header.tsx`**: Insets-safe minimal title bar.
* **`src/components/TranscriptView.tsx`**: Scrollable IM-style conversation thread with auto-scroll ref.
* **`src/components/PttFooter.tsx`**: Combined PTT / Connection status button (`Tap to Speak` / `Disconnected · Tap to Connect`) + hamburger menu button (`☰`).
* **`src/components/VoiceSettingsModal.tsx`**: Bounded slide-up sheet with TTS toggle, pitch/speed controls, starred favorites section, and inline audition `▶` buttons.

---

## 4. Verification Runbook for Agents

Before completing any task, execute these verification gates:

1. **Type Check**:
   ```bash
   export PATH=/opt/homebrew/bin:$PATH
   npx tsc --noEmit
   ```
   Must exit with code 0 (zero errors).
2. **Device Connection Verification**:
   ```bash
   export PATH=$HOME/Library/Android/sdk/platform-tools:/opt/homebrew/bin:$PATH
   adb devices
   ```
   Verify `replicant26:37003` is listed as `device`.
3. **App Restart & Logcat Inspection**:
   ```bash
   adb shell am force-stop com.lmorchard.decafclawvoice
   adb shell am start -n com.lmorchard.decafclawvoice/.MainActivity
   sleep 2
   adb logcat -d -t 150 | grep -iE 'ReactNativeJS|FATAL|UnsatisfiedLinkError|com.lmorchard.decafclawvoice'
   ```
   Ensure no fatal crashes or link errors occur on launch.

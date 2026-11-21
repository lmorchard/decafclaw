# DecafClaw Voice Transceiver (Android)

A lightweight, low-latency mobile voice transceiver app for [DecafClaw](https://github.com/lmorchard/decafclaw), built with Expo SDK 58 and React Native.

Designed for hands-free or screen-on voice interactions on the **Google Pixel 10 Pro**, tapping directly into on-device Tensor TPU speech recognition and local Android speech synthesis.

---

## Architecture & Flow

```
[ User Speaks ]
      │ (Acoustic audio)
      ▼
[ Pixel Tensor TPU ] ──(expo-speech-recognition / on-device STT)
      │
      ▼ (Text string + punctuation)
[ WebSocket /ws/chat ] ──(Cookie: decafclaw_session=dfc_...)──► [ DecafClaw Server ]
                                                                      │
                                                              (Streamed Tokens)
                                                                      ▼
[ Phone Speaker ] ◄──(expo-speech / Native TTS) ◄── [ Sentence Chunk Pipeline ]
                      (0.90x pitch, 1.10x rate)      (Markdown normalizer)
```

1. **On-Device STT (Tensor TPU)**:
   - Uses `expo-speech-recognition` with `requiresOnDeviceRecognition: true`.
   - Runs locally on the phone's Tensor hardware with instant punctuation and zero server audio compute load.
2. **WebSocket Gateway**:
   - Streams directly to DecafClaw's `/ws/chat` endpoint over LAN/Tailscale with token session auth.
   - Includes automatic exponential backoff reconnect and foreground wake recovery (`AppState`).
3. **Pipelined Native Speech (TTS)**:
   - Chunks incoming token streams at sentence boundaries (`.`, `!`, `?`, `\n`) and immediately speaks them using Android's native Google Speech engine via `expo-speech`.
   - Normalizes Markdown syntax (code blocks, wiki links, backticks, asterisks) into speakable English.
4. **Unified Push-to-Talk & Controls**:
   - Bottom button acts as both connection monitor and push-to-talk (`Tap to Speak` / `Listening...` / `Disconnected · Tap to Connect`).
   - Bottom hamburger menu (`☰`) opens the Voice Settings sheet.
   - Star/favorite installed voices, customize pitch/speed, and audition voices in place.

---

## Project Structure

```
decafclaw-voice/
├── App.tsx                     # Top-level coordinator & provider wiring
├── app.json                    # Expo config & Android permissions
├── run-android.sh              # Local build launcher with Java 17 & Android SDK env
├── src/
│   ├── types.ts                # TypeScript contracts (ChatMessage, VoiceItem)
│   ├── config.ts               # Host URL (default: http://decafclaw:18880) and token
│   ├── utils/
│   │   └── speech.ts           # cleanMarkdownForSpeech normalizer & voice label formatter
│   ├── hooks/
│   │   ├── useDecafClawWebSocket.ts # Socket lifecycle, reconnect backoff, turn dispatcher
│   │   └── useVoiceRecognition.ts   # Tensor TPU STT hooks & permissions
│   └── components/
│       ├── Header.tsx          # Clean top bar with safe-area insets
│       ├── TranscriptView.tsx  # Persistent IM-style chat transcript
│       ├── PttFooter.tsx       # Unified speak/connect button + settings hamburger
│       └── VoiceSettingsModal.tsx # Slide-up sheet (TTS toggle, pitch, speed, starred voices)
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
# Check TypeScript types
npx tsc --noEmit

# Start the Metro bundler (live fast-refresh over Wi-Fi)
npx expo start

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

import { useState, useCallback, useEffect, useRef } from 'react';
import { Vibration } from 'react-native';
import {
  ExpoSpeechRecognitionModule,
  useSpeechRecognitionEvent,
} from 'expo-speech-recognition';

interface UseVoiceRecognitionOptions {
  onFinalResult: (transcript: string) => void;
  onInterrupted?: () => void;
}

type ListeningMode = 'single' | 'long' | null;

const LONG_SESSION_MAX_RESTARTS = 3;
const RESTART_DELAY_MS = 250;
const FINISH_FALLBACK_MS = 1500;
const SINGLE_SESSION_MAX_MS = 15_000;

function joinTranscript(base: string, next: string): string {
  const head = base.trim();
  const tail = next.trim();
  if (!head) return tail;
  if (!tail) return head;
  return `${head} ${tail}`;
}

function clearTimer(ref: { current: ReturnType<typeof setTimeout> | null }) {
  if (ref.current) {
    clearTimeout(ref.current);
    ref.current = null;
  }
}

export function useVoiceRecognition({
  onFinalResult,
  onInterrupted,
}: UseVoiceRecognitionOptions) {
  const [recognizing, setRecognizing] = useState(false);
  const [isStarting, setIsStarting] = useState(false);
  const [longListening, setLongListening] = useState(false);
  const [transcript, setTranscript] = useState('');
  const hasPermissionRef = useRef(false);

  const modeRef = useRef<ListeningMode>(null);
  const segmentBufferRef = useRef('');
  const stopRequestedRef = useRef(false);
  const failedRef = useRef(false);
  const finishedRef = useRef(false);
  const restartCountRef = useRef(0);
  const restartTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const finishTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const singleTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const callbacksRef = useRef({ onFinalResult, onInterrupted });
  useEffect(() => {
    callbacksRef.current = { onFinalResult, onInterrupted };
  });

  const resetSession = useCallback(() => {
    clearTimer(restartTimerRef);
    clearTimer(finishTimerRef);
    clearTimer(singleTimeoutRef);
    modeRef.current = null;
    segmentBufferRef.current = '';
    stopRequestedRef.current = false;
    failedRef.current = false;
    restartCountRef.current = 0;
    setLongListening(false);
    setIsStarting(false);
    setRecognizing(false);
    setTranscript('');
  }, []);

  const finalizeLongSession = useCallback(() => {
    if (finishedRef.current) {
      return;
    }
    finishedRef.current = true;
    const text = segmentBufferRef.current.trim();
    resetSession();
    if (text) {
      callbacksRef.current.onFinalResult(text);
    }
  }, [resetSession]);

  // Read the current permission state without showing a dialog at launch.
  useEffect(() => {
    let cancelled = false;
    ExpoSpeechRecognitionModule.getPermissionsAsync()
      .then((res) => {
        if (!cancelled) {
          hasPermissionRef.current = res.granted;
        }
      })
      .catch((err) => {
        console.warn('Failed to read speech permissions:', err);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    return () => {
      clearTimer(restartTimerRef);
      clearTimer(finishTimerRef);
      ExpoSpeechRecognitionModule.abort();
    };
  }, []);

  useSpeechRecognitionEvent('start', () => {
    clearTimer(restartTimerRef);
    setIsStarting(false);
    setRecognizing(true);
  });

  useSpeechRecognitionEvent('audiostart', () => {
    clearTimer(restartTimerRef);
    setIsStarting(false);
    setRecognizing(true);
    // Subtle haptic tick confirming mic buffer is actively capturing
    Vibration.vibrate(15);
  });

  useSpeechRecognitionEvent('end', () => {
    if (finishedRef.current) {
      return;
    }
    if (modeRef.current === 'long') {
      if (stopRequestedRef.current || failedRef.current) {
        finalizeLongSession();
        return;
      }
      if (restartCountRef.current >= LONG_SESSION_MAX_RESTARTS) {
        finalizeLongSession();
        return;
      }

      // A segmented session can still end on its own (engine timeout).
      // Keep the session alive and accumulate into the same buffer.
      restartCountRef.current += 1;
      clearTimer(restartTimerRef);
      restartTimerRef.current = setTimeout(() => {
        restartTimerRef.current = null;
        if (modeRef.current !== 'long' || stopRequestedRef.current) {
          return;
        }
        ExpoSpeechRecognitionModule.start({
          lang: 'en-US',
          interimResults: true,
          continuous: true,
          requiresOnDeviceRecognition: true,
          addsPunctuation: true,
        });
      }, RESTART_DELAY_MS);
      return;
    }

    resetSession();
  });

  useSpeechRecognitionEvent('result', (event) => {
    if (finishedRef.current || modeRef.current === null) {
      return;
    }
    const current = event.results[0]?.transcript || '';

    if (modeRef.current === 'long') {
      if (event.isFinal) {
        if (current.trim()) {
          segmentBufferRef.current = joinTranscript(segmentBufferRef.current, current);
          restartCountRef.current = 0;
        }
        setTranscript(segmentBufferRef.current);
      } else {
        setTranscript(joinTranscript(segmentBufferRef.current, current));
      }
      return;
    }

    setTranscript(current);
    if (event.isFinal && current.trim().length > 0) {
      // One-shot: send on the first final segment, then tear down the
      // continuous recognizer so it doesn't keep listening.
      modeRef.current = null;
      callbacksRef.current.onFinalResult(current.trim());
      ExpoSpeechRecognitionModule.abort();
    }
  });

  useSpeechRecognitionEvent('error', (event) => {
    if (finishedRef.current || modeRef.current === null) {
      return;
    }
    if (event.error === 'client' || event.error === 'aborted') {
      // Teardown noise from stopListening()/abort(); the end handler cleans up.
      return;
    }
    if (modeRef.current === 'long') {
      if (stopRequestedRef.current) {
        return;
      }
      if (event.error === 'no-speech' || event.error === 'speech-timeout') {
        // The end handler restarts a long session.
        return;
      }
      failedRef.current = true;
      console.warn('Speech recognition error:', event.error, event.message);
      return;
    }

    if (event.error !== 'no-speech') {
      console.warn('Speech recognition error:', event.error, event.message);
    }
    setIsStarting(false);
    setRecognizing(false);
  });

  const ensurePermission = useCallback(async () => {
    if (hasPermissionRef.current) {
      return true;
    }
    try {
      const { granted } = await ExpoSpeechRecognitionModule.requestPermissionsAsync();
      hasPermissionRef.current = granted;
    } catch (err) {
      console.warn('Failed to request speech permissions:', err);
    }
    return hasPermissionRef.current;
  }, []);

  const startSession = useCallback(
    async (long: boolean) => {
      if (!(await ensurePermission())) {
        setIsStarting(false);
        return;
      }

      modeRef.current = long ? 'long' : 'single';
      segmentBufferRef.current = '';
      stopRequestedRef.current = false;
      failedRef.current = false;
      finishedRef.current = false;
      restartCountRef.current = 0;
      clearTimer(restartTimerRef);
      clearTimer(finishTimerRef);
      clearTimer(singleTimeoutRef);

      setTranscript('');
      setLongListening(long);
      setIsStarting(true);

      ExpoSpeechRecognitionModule.start({
        lang: 'en-US',
        interimResults: true,
        // Continuous mode routes audio through EXTRA_AUDIO_SOURCE on Android
        // 13+, which suppresses the system start/stop beeps. One-shot mode
        // aborts on its first final result to preserve tap-to-talk behavior.
        continuous: true,
        requiresOnDeviceRecognition: true, // Forces Pixel Tensor on-device STT
        addsPunctuation: true,
      });

      if (!long) {
        // Continuous mode no longer auto-stops on silence. Bound the one-shot
        // session so a forgotten tap doesn't leave the mic open forever.
        singleTimeoutRef.current = setTimeout(() => {
          singleTimeoutRef.current = null;
          if (modeRef.current === 'single') {
            modeRef.current = null;
            ExpoSpeechRecognitionModule.abort();
            resetSession();
          }
        }, SINGLE_SESSION_MAX_MS);
      }
    },
    [ensurePermission, resetSession]
  );

  const startLongListening = useCallback(async () => {
    if (recognizing || isStarting) {
      return;
    }
    callbacksRef.current.onInterrupted?.();
    // Distinct tactile confirmation that the monologue session is armed.
    Vibration.vibrate(40);
    await startSession(true);
  }, [recognizing, isStarting, startSession]);

  const toggleListening = useCallback(async () => {
    if (isStarting) {
      if (modeRef.current === 'long') {
        finalizeLongSession();
      }
      resetSession();
      ExpoSpeechRecognitionModule.abort();
      return;
    }

    if (recognizing) {
      if (modeRef.current === 'long') {
        if (restartTimerRef.current) {
          finalizeLongSession();
          return;
        }
        if (stopRequestedRef.current) {
          return;
        }
        stopRequestedRef.current = true;
        ExpoSpeechRecognitionModule.stop();
        finishTimerRef.current = setTimeout(() => {
          finishTimerRef.current = null;
          ExpoSpeechRecognitionModule.abort();
          finalizeLongSession();
        }, FINISH_FALLBACK_MS);
        return;
      }

      // One-shot session: finalize the current utterance and send it.
      setRecognizing(false);
      ExpoSpeechRecognitionModule.stop();
      return;
    }

    callbacksRef.current.onInterrupted?.();
    // Immediate tactile response
    Vibration.vibrate(20);
    await startSession(false);
  }, [finalizeLongSession, isStarting, recognizing, resetSession, startSession]);

  const clearTranscript = useCallback(() => {
    setTranscript('');
  }, []);

  return {
    recognizing: recognizing || isStarting,
    isStarting,
    longListening,
    transcript,
    toggleListening,
    startLongListening,
    clearTranscript,
  };
}

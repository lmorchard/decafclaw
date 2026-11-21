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

export function useVoiceRecognition({
  onFinalResult,
  onInterrupted,
}: UseVoiceRecognitionOptions) {
  const [recognizing, setRecognizing] = useState(false);
  const [isStarting, setIsStarting] = useState(false);
  const [transcript, setTranscript] = useState('');
  const hasPermissionRef = useRef(false);

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

  useSpeechRecognitionEvent('start', () => {
    setIsStarting(false);
    setRecognizing(true);
  });

  useSpeechRecognitionEvent('audiostart', () => {
    setIsStarting(false);
    setRecognizing(true);
    // Subtle haptic tick confirming mic buffer is actively capturing
    Vibration.vibrate(15);
  });

  useSpeechRecognitionEvent('end', () => {
    setIsStarting(false);
    setRecognizing(false);
  });

  useSpeechRecognitionEvent('result', (event) => {
    const current = event.results[0]?.transcript || '';
    setTranscript(current);

    if (event.isFinal && current.trim().length > 0) {
      onFinalResult(current.trim());
    }
  });

  useSpeechRecognitionEvent('error', (event) => {
    if (event.error !== 'no-speech') {
      console.warn('Speech recognition error:', event.error, event.message);
    }
    setIsStarting(false);
    setRecognizing(false);
  });

  const toggleListening = useCallback(async () => {
    if (recognizing) {
      // Finalize the current utterance; the final result dispatches the turn.
      setRecognizing(false);
      ExpoSpeechRecognitionModule.stop();
      return;
    }

    if (isStarting) {
      // The engine has not produced audio yet; cancel instead of finalizing.
      setIsStarting(false);
      ExpoSpeechRecognitionModule.abort();
      return;
    }

    onInterrupted?.();

    // Immediate tactile response
    Vibration.vibrate(20);
    setIsStarting(true);
    setTranscript('');

    if (!hasPermissionRef.current) {
      try {
        const { granted } = await ExpoSpeechRecognitionModule.requestPermissionsAsync();
        hasPermissionRef.current = granted;
      } catch (err) {
        console.warn('Failed to request speech permissions:', err);
      }
      if (!hasPermissionRef.current) {
        setIsStarting(false);
        return;
      }
    }

    ExpoSpeechRecognitionModule.start({
      lang: 'en-US',
      interimResults: true,
      continuous: false,
      requiresOnDeviceRecognition: true, // Forces Pixel Tensor on-device STT
      addsPunctuation: true,
    });
  }, [recognizing, isStarting, onInterrupted]);

  const clearTranscript = useCallback(() => {
    setTranscript('');
  }, []);

  return {
    recognizing: recognizing || isStarting,
    isStarting,
    transcript,
    toggleListening,
    clearTranscript,
  };
}

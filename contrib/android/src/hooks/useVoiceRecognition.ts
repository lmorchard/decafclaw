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

  // Pre-check and request mic permission on mount to avoid latency on button press
  useEffect(() => {
    async function preflightPermissions() {
      try {
        const res = await ExpoSpeechRecognitionModule.requestPermissionsAsync();
        hasPermissionRef.current = res.granted;
      } catch (err) {
        console.warn('Failed to preflight speech permissions:', err);
      }
    }
    preflightPermissions();
  }, []);

  useSpeechRecognitionEvent('start', () => {
    setIsStarting(false);
    setRecognizing(true);
  });

  useSpeechRecognitionEvent('audiostart', () => {
    setIsStarting(false);
    setRecognizing(true);
    // Subtle haptic tick confirming mic buffer is actively capturing
    try {
      Vibration.vibrate(15);
    } catch (e) {}
  });

  useSpeechRecognitionEvent('end', () => {
    setIsStarting(false);
    setRecognizing(false);
  });

  useSpeechRecognitionEvent('result', (event) => {
    const current = event.results[0]?.transcript || '';
    setTranscript(current);

    if (event.isFinal) {
      if (current.trim().length > 0) {
        onFinalResult(current.trim());
      }
    }
  });

  useSpeechRecognitionEvent('error', (event) => {
    console.error('Speech recognition error:', event.error, event.message);
    setIsStarting(false);
    setRecognizing(false);
  });

  const toggleListening = useCallback(async () => {
    if (onInterrupted) {
      onInterrupted();
    }

    if (recognizing || isStarting) {
      setIsStarting(false);
      setRecognizing(false);
      ExpoSpeechRecognitionModule.stop();
    } else {
      // Immediate tactile response
      try {
        Vibration.vibrate(20);
      } catch (e) {}

      setIsStarting(true);
      setTranscript('');

      // If permissions not yet verified, check now
      if (!hasPermissionRef.current) {
        const { granted } = await ExpoSpeechRecognitionModule.requestPermissionsAsync();
        hasPermissionRef.current = granted;
        if (!granted) {
          console.warn('Microphone permission denied');
          setIsStarting(false);
          return;
        }
      }

      // Start recognition immediately without awaiting permission check
      ExpoSpeechRecognitionModule.start({
        lang: 'en-US',
        interimResults: true,
        continuous: false,
        requiresOnDeviceRecognition: true, // Forces Pixel Tensor on-device STT
        addsPunctuation: true,
      });
    }
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

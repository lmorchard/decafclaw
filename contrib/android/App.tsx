import React, { useState, useEffect, useRef, useCallback } from 'react';
import { StyleSheet, View } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Speech from 'expo-speech';

import { ChatMessage, VoiceItem } from './src/types';
import { DEFAULT_HOST, DEFAULT_TOKEN } from './src/config';
import { cleanMarkdownForSpeech } from './src/utils/speech';
import { useDecafClawWebSocket } from './src/hooks/useDecafClawWebSocket';
import { useVoiceRecognition } from './src/hooks/useVoiceRecognition';
import { Header } from './src/components/Header';
import { TranscriptView } from './src/components/TranscriptView';
import { PttFooter } from './src/components/PttFooter';
import { VoiceSettingsModal } from './src/components/VoiceSettingsModal';

const STORAGE_KEYS = {
  VOICE: '@decafclaw_voice_id',
  PITCH: '@decafclaw_voice_pitch',
  RATE: '@decafclaw_voice_rate',
  TTS_ENABLED: '@decafclaw_voice_tts_enabled',
  STARRED: '@decafclaw_starred_voices',
};

function TransceiverScreen() {
  const scrollViewRef = useRef<any>(null);

  // Chat Transcript State (Persists all turns)
  const [messages, setMessages] = useState<ChatMessage[]>([]);

  // TTS Output & Settings State
  const [ttsEnabled, setTtsEnabled] = useState(true);
  const [settingsVisible, setSettingsVisible] = useState(false);
  const [voices, setVoices] = useState<VoiceItem[]>([]);
  const [selectedVoice, setSelectedVoice] = useState<string>('en-us-x-sfg-local');
  const [pitch, setPitch] = useState<number>(0.9); // Grounded workshop tone by default
  const [rate, setRate] = useState<number>(1.1);   // Brisk conversational tempo
  const [starredVoices, setStarredVoices] = useState<string[]>([]);

  const unspokenBufferRef = useRef<string>('');

  // Active settings ref — solves closure staleness across WebSocket events!
  const settingsRef = useRef({
    voice: selectedVoice,
    pitch,
    rate,
    ttsEnabled,
  });

  useEffect(() => {
    settingsRef.current = {
      voice: selectedVoice,
      pitch,
      rate,
      ttsEnabled,
    };
  }, [selectedVoice, pitch, rate, ttsEnabled]);

  // Load saved preferences & available native voices on mount
  useEffect(() => {
    async function loadPreferencesAndVoices() {
      try {
        const [savedVoice, savedPitch, savedRate, savedTts, savedStarred] =
          await Promise.all([
            AsyncStorage.getItem(STORAGE_KEYS.VOICE),
            AsyncStorage.getItem(STORAGE_KEYS.PITCH),
            AsyncStorage.getItem(STORAGE_KEYS.RATE),
            AsyncStorage.getItem(STORAGE_KEYS.TTS_ENABLED),
            AsyncStorage.getItem(STORAGE_KEYS.STARRED),
          ]);

        if (savedPitch) setPitch(parseFloat(savedPitch));
        if (savedRate) setRate(parseFloat(savedRate));
        if (savedTts !== null) setTtsEnabled(savedTts === 'true');
        if (savedStarred) {
          try {
            setStarredVoices(JSON.parse(savedStarred));
          } catch (e) {}
        }

        const available = await Speech.getAvailableVoicesAsync();
        const enVoices = available
          .filter((v) => v.language.startsWith('en'))
          .map((v) => ({
            identifier: v.identifier,
            name: v.name,
            language: v.language,
          }))
          .sort((a, b) => {
            const aLocal = a.identifier.includes('local') ? 0 : 1;
            const bLocal = b.identifier.includes('local') ? 0 : 1;
            return aLocal - bLocal;
          });

        setVoices(enVoices);

        if (savedVoice && enVoices.some((v) => v.identifier === savedVoice)) {
          setSelectedVoice(savedVoice);
        } else {
          const preferred =
            enVoices.find((v) => v.identifier === 'en-us-x-sfg-local') ||
            enVoices.find((v) => v.identifier === 'en-gb-x-rjs-local') ||
            enVoices[0];
          if (preferred) {
            setSelectedVoice(preferred.identifier);
          }
        }
      } catch (err) {
        console.warn('Failed to load preferences/voices:', err);
      }
    }
    loadPreferencesAndVoices();
  }, []);

  // Save changes to persistent storage
  const handleSelectVoice = useCallback((id: string) => {
    setSelectedVoice(id);
    AsyncStorage.setItem(STORAGE_KEYS.VOICE, id).catch(() => {});
  }, []);

  const handleChangePitch = useCallback((p: number) => {
    setPitch(p);
    AsyncStorage.setItem(STORAGE_KEYS.PITCH, p.toString()).catch(() => {});
  }, []);

  const handleChangeRate = useCallback((r: number) => {
    setRate(r);
    AsyncStorage.setItem(STORAGE_KEYS.RATE, r.toString()).catch(() => {});
  }, []);

  const handleToggleTts = useCallback(() => {
    setTtsEnabled((prev) => {
      const next = !prev;
      if (!next) {
        Speech.stop();
      }
      AsyncStorage.setItem(STORAGE_KEYS.TTS_ENABLED, next.toString()).catch(() => {});
      return next;
    });
  }, []);

  const handleToggleStarVoice = useCallback((id: string) => {
    setStarredVoices((prev) => {
      const next = prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id];
      AsyncStorage.setItem(STORAGE_KEYS.STARRED, JSON.stringify(next)).catch(() => {});
      return next;
    });
  }, []);

  // Speak sentence chunk using live settingsRef
  const speakChunk = useCallback((rawText: string) => {
    const { voice, pitch: p, rate: r, ttsEnabled: enabled } = settingsRef.current;
    if (!enabled) return;

    const clean = cleanMarkdownForSpeech(rawText);
    if (clean.length === 0) return;

    Speech.speak(clean, {
      voice,
      pitch: p,
      rate: r,
    });
  }, []);

  const previewVoice = useCallback((voiceId: string) => {
    const { pitch: p, rate: r } = settingsRef.current;
    Speech.stop();
    Speech.speak('Aye, testing this voice on the bench.', {
      voice: voiceId,
      pitch: p,
      rate: r,
    });
  }, []);

  // WebSocket Server Event Handlers
  const handleChunk = useCallback(
    (text: string) => {
      unspokenBufferRef.current += text;

      // Append streamed tokens to current assistant message
      setMessages((prev) => {
        if (prev.length === 0) return prev;
        const last = prev[prev.length - 1];
        if (last.role === 'assistant') {
          return [...prev.slice(0, -1), { ...last, text: last.text + text }];
        }
        return prev;
      });

      // Pipeline speech at sentence boundaries (. ! ? \n)
      const match = unspokenBufferRef.current.match(/^([\s\S]*?[.!?\n])\s+([\s\S]*)$/);
      if (match) {
        const sentence = match[1];
        unspokenBufferRef.current = match[2];
        speakChunk(sentence);
      }

      setTimeout(() => {
        scrollViewRef.current?.scrollToEnd({ animated: true });
      }, 50);
    },
    [speakChunk]
  );

  const handleToolStart = useCallback((_tool: string) => {}, []);
  const handleToolEnd = useCallback((_tool: string) => {}, []);

  const handleTurnComplete = useCallback(() => {
    if (unspokenBufferRef.current.trim().length > 0) {
      speakChunk(unspokenBufferRef.current);
      unspokenBufferRef.current = '';
    }
    setTimeout(() => {
      scrollViewRef.current?.scrollToEnd({ animated: true });
    }, 50);
  }, [speakChunk]);

  // Hook: WebSocket Gateway to DecafClaw
  const { connected, status, manualReconnect, sendTurn } = useDecafClawWebSocket({
    host: DEFAULT_HOST,
    token: DEFAULT_TOKEN,
    onChunk: handleChunk,
    onToolStart: handleToolStart,
    onToolEnd: handleToolEnd,
    onTurnComplete: handleTurnComplete,
  });

  // Handle final speech recognition result
  const handleFinalSpeechResult = useCallback(
    (spokenText: string) => {
      const userEntry: ChatMessage = {
        id: `user-${Date.now()}`,
        role: 'user',
        text: spokenText,
      };
      const agentEntry: ChatMessage = {
        id: `agent-${Date.now()}`,
        role: 'assistant',
        text: '',
      };

      setMessages((prev) => [...prev, userEntry, agentEntry]);
      unspokenBufferRef.current = '';

      setTimeout(() => {
        scrollViewRef.current?.scrollToEnd({ animated: true });
      }, 50);

      sendTurn(spokenText);
    },
    [sendTurn]
  );

  // Hook: On-Device Speech Recognition (Pixel Tensor TPU)
  const { recognizing, isStarting, transcript, toggleListening } = useVoiceRecognition({
    onFinalResult: handleFinalSpeechResult,
    onInterrupted: () => Speech.stop(), // Barge-in interruption
  });

  return (
    <View style={styles.container}>
      <StatusBar style="light" />

      {/* Clean Minimal Header */}
      <Header />

      {/* Persistent IM Transcript */}
      <TranscriptView
        scrollViewRef={scrollViewRef}
        messages={messages}
        transcript={transcript}
        recognizing={recognizing}
      />

      {/* Unified Bottom Control (PTT + Connection + Settings Hamburger) */}
      <PttFooter
        connected={connected}
        status={status}
        recognizing={recognizing}
        isStarting={isStarting}
        onToggleListening={toggleListening}
        onReconnect={manualReconnect}
        onOpenSettings={() => setSettingsVisible(true)}
      />

      {/* Slide-Up Voice Settings Sheet */}
      <VoiceSettingsModal
        visible={settingsVisible}
        ttsEnabled={ttsEnabled}
        voices={voices}
        selectedVoice={selectedVoice}
        pitch={pitch}
        rate={rate}
        starredVoices={starredVoices}
        onToggleTts={handleToggleTts}
        onSelectVoice={handleSelectVoice}
        onPreviewVoice={previewVoice}
        onToggleStarVoice={handleToggleStarVoice}
        onChangePitch={handleChangePitch}
        onChangeRate={handleChangeRate}
        onClose={() => setSettingsVisible(false)}
      />
    </View>
  );
}

export default function App() {
  return (
    <SafeAreaProvider>
      <TransceiverScreen />
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#121214',
  },
});

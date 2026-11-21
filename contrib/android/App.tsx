import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Alert,
  Linking,
  StyleSheet,
  TouchableOpacity,
  Text,
  View,
  type ScrollViewInstance,
} from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Speech from 'expo-speech';

import { ChatMessage, VoiceItem } from './src/types';
import { DEFAULT_HOST, DEFAULT_TOKEN } from './src/config';
import { cleanMarkdownForSpeech } from './src/utils/speech';
import { conversationWebUrl, normalizeHost } from './src/utils/connection';
import { useDecafClawWebSocket } from './src/hooks/useDecafClawWebSocket';
import { useVoiceRecognition } from './src/hooks/useVoiceRecognition';
import { Header } from './src/components/Header';
import { TranscriptView } from './src/components/TranscriptView';
import { PttFooter } from './src/components/PttFooter';
import { VoiceSettingsModal } from './src/components/VoiceSettingsModal';

const STORAGE_KEYS = {
  HOST: '@decafclaw_host',
  TOKEN: '@decafclaw_token',
  VOICE: '@decafclaw_voice_id',
  PITCH: '@decafclaw_voice_pitch',
  RATE: '@decafclaw_voice_rate',
  TTS_ENABLED: '@decafclaw_voice_tts_enabled',
  STARRED: '@decafclaw_starred_voices',
  BARGE_IN: '@decafclaw_barge_in',
  LOBBY_CONV_ID: '@decafclaw_lobby_conv_id',
};

const CHUNK_FLUSH_MS = 50;
const SCROLL_THROTTLE_MS = 80;

function TransceiverScreen() {
  const scrollViewRef = useRef<ScrollViewInstance | null>(null);

  // Connection settings
  const [host, setHost] = useState(DEFAULT_HOST);
  const [token, setToken] = useState(DEFAULT_TOKEN);

  // Chat Transcript State (in memory for this session)
  const [messages, setMessages] = useState<ChatMessage[]>([]);

  // TTS Output & Settings State
  const [ttsEnabled, setTtsEnabled] = useState(true);
  const [bargeInEnabled, setBargeInEnabled] = useState(true);
  const [lobbyConvId, setLobbyConvId] = useState('');
  const [settingsVisible, setSettingsVisible] = useState(false);
  const [voices, setVoices] = useState<VoiceItem[]>([]);
  const [selectedVoice, setSelectedVoice] = useState<string>('en-us-x-sfg-local');
  const [pitch, setPitch] = useState<number>(0.9); // Grounded workshop tone by default
  const [rate, setRate] = useState<number>(1.1);   // Brisk conversational tempo
  const [starredVoices, setStarredVoices] = useState<string[]>([]);
  const [speaking, setSpeaking] = useState(false);

  const unspokenBufferRef = useRef<string>('');
  const pendingTextRef = useRef<string>('');
  const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scrollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const speechPendingRef = useRef(0);
  const suppressSpeechRef = useRef(false);
  const idSeqRef = useRef(0);

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

  const nextId = useCallback((prefix: string) => {
    idSeqRef.current += 1;
    return `${prefix}-${Date.now()}-${idSeqRef.current}`;
  }, []);

  const requestScroll = useCallback(() => {
    if (scrollTimerRef.current) {
      return;
    }
    scrollTimerRef.current = setTimeout(() => {
      scrollTimerRef.current = null;
      scrollViewRef.current?.scrollToEnd({ animated: true });
    }, SCROLL_THROTTLE_MS);
  }, []);

  // Load saved preferences & available native voices on mount
  useEffect(() => {
    async function loadPreferencesAndVoices() {
      try {
        const [
          savedHost,
          savedToken,
          savedVoice,
          savedPitch,
          savedRate,
          savedTts,
          savedStarred,
          savedBargeIn,
          savedLobby,
        ] = await Promise.all([
          AsyncStorage.getItem(STORAGE_KEYS.HOST),
          AsyncStorage.getItem(STORAGE_KEYS.TOKEN),
          AsyncStorage.getItem(STORAGE_KEYS.VOICE),
          AsyncStorage.getItem(STORAGE_KEYS.PITCH),
          AsyncStorage.getItem(STORAGE_KEYS.RATE),
          AsyncStorage.getItem(STORAGE_KEYS.TTS_ENABLED),
          AsyncStorage.getItem(STORAGE_KEYS.STARRED),
          AsyncStorage.getItem(STORAGE_KEYS.BARGE_IN),
          AsyncStorage.getItem(STORAGE_KEYS.LOBBY_CONV_ID),
        ]);

        const normalizedHost = savedHost ? normalizeHost(savedHost) : null;
        if (normalizedHost) setHost(normalizedHost);
        if (savedToken && savedToken.trim()) setToken(savedToken.trim());

        const savedPitchNum = savedPitch ? parseFloat(savedPitch) : NaN;
        const savedRateNum = savedRate ? parseFloat(savedRate) : NaN;
        if (Number.isFinite(savedPitchNum)) setPitch(savedPitchNum);
        if (Number.isFinite(savedRateNum)) setRate(savedRateNum);
        if (savedTts !== null) setTtsEnabled(savedTts === 'true');
        if (savedBargeIn !== null) setBargeInEnabled(savedBargeIn === 'true');
        if (savedLobby && savedLobby.trim()) setLobbyConvId(savedLobby.trim());
        if (savedStarred) {
          try {
            const parsed: unknown = JSON.parse(savedStarred);
            if (Array.isArray(parsed) && parsed.every((item) => typeof item === 'string')) {
              setStarredVoices(parsed);
            }
          } catch {
            // Ignore corrupt favorites; defaults are fine.
          }
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
        speechPendingRef.current = 0;
        setSpeaking(false);
      }
      AsyncStorage.setItem(STORAGE_KEYS.TTS_ENABLED, next.toString()).catch(() => {});
      return next;
    });
  }, []);

  const handleToggleBargeIn = useCallback(() => {
    setBargeInEnabled((prev) => {
      const next = !prev;
      AsyncStorage.setItem(STORAGE_KEYS.BARGE_IN, String(next)).catch(() => {});
      return next;
    });
  }, []);

  const handleSaveLobby = useCallback((val: string) => {
    setLobbyConvId(val);
    AsyncStorage.setItem(STORAGE_KEYS.LOBBY_CONV_ID, val).catch(() => {});
  }, []);

  const handleToggleStarVoice = useCallback((id: string) => {
    setStarredVoices((prev) => {
      const next = prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id];
      AsyncStorage.setItem(STORAGE_KEYS.STARRED, JSON.stringify(next)).catch(() => {});
      return next;
    });
  }, []);

  const finishUtterance = useCallback(() => {
    speechPendingRef.current = Math.max(0, speechPendingRef.current - 1);
    if (speechPendingRef.current === 0) {
      setSpeaking(false);
    }
  }, []);

  const trackUtterance = useCallback(() => {
    speechPendingRef.current += 1;
    setSpeaking(true);
    return {
      onDone: finishUtterance,
      onStopped: finishUtterance,
      onError: finishUtterance,
    };
  }, [finishUtterance]);

  const stopSpeaking = useCallback(() => {
    Speech.stop();
    speechPendingRef.current = 0;
    setSpeaking(false);
  }, []);

  // Speak sentence chunk using live settingsRef
  const speakChunk = useCallback(
    (rawText: string) => {
      const { voice, pitch: p, rate: r, ttsEnabled: enabled } = settingsRef.current;
      if (!enabled || suppressSpeechRef.current) return;

      const clean = cleanMarkdownForSpeech(rawText);
      if (clean.length === 0) return;

      Speech.speak(clean, {
        voice,
        pitch: p,
        rate: r,
        ...trackUtterance(),
      });
    },
    [trackUtterance]
  );

  const previewVoice = useCallback(
    (voiceId: string) => {
      const { pitch: p, rate: r } = settingsRef.current;
      Speech.stop();
      speechPendingRef.current = 0;
      Speech.speak('Aye, testing this voice on the bench.', {
        voice: voiceId,
        pitch: p,
        rate: r,
        ...trackUtterance(),
      });
    },
    [trackUtterance]
  );

  const flushPendingText = useCallback(() => {
    if (flushTimerRef.current) {
      clearTimeout(flushTimerRef.current);
      flushTimerRef.current = null;
    }
    const text = pendingTextRef.current;
    if (!text) return;
    pendingTextRef.current = '';

    setMessages((prev) => {
      const last = prev[prev.length - 1];
      if (last?.role === 'assistant') {
        return [...prev.slice(0, -1), { ...last, text: last.text + text }];
      }
      return [...prev, { id: nextId('agent'), role: 'assistant', text }];
    });
  }, [nextId]);

  // WebSocket Server Event Handlers
  const handleChunk = useCallback(
    (text: string) => {
      unspokenBufferRef.current += text;
      pendingTextRef.current += text;

      if (!flushTimerRef.current) {
        flushTimerRef.current = setTimeout(() => {
          flushTimerRef.current = null;
          flushPendingText();
        }, CHUNK_FLUSH_MS);
      }

      // Pipeline speech at sentence boundaries (. ! ? \n). Speak every complete
      // sentence currently buffered instead of waiting for the next chunk.
      for (;;) {
        const match = unspokenBufferRef.current.match(
          /^([\s\S]*?[.!?\n])(?:\s+|$)([\s\S]*)$/
        );
        if (!match) break;
        unspokenBufferRef.current = match[2];
        speakChunk(match[1]);
        if (!unspokenBufferRef.current) break;
      }

      requestScroll();
    },
    [flushPendingText, requestScroll, speakChunk]
  );

  const handleTurnComplete = useCallback(() => {
    flushPendingText();
    if (!suppressSpeechRef.current && unspokenBufferRef.current.trim().length > 0) {
      speakChunk(unspokenBufferRef.current);
    }
    unspokenBufferRef.current = '';
    requestScroll();
  }, [flushPendingText, requestScroll, speakChunk]);

  const handleError = useCallback(
    (message: string) => {
      flushPendingText();
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        const entry: ChatMessage = { id: nextId('error'), role: 'error', text: message };
        if (last?.role === 'assistant' && last.text.trim().length === 0) {
          return [...prev.slice(0, -1), entry];
        }
        return [...prev, entry];
      });
      requestScroll();
    },
    [flushPendingText, nextId, requestScroll]
  );

  const handleHistoryRecovered = useCallback(
    (text: string) => {
      setMessages((prev) => {
        if (prev.some((m) => m.role === 'assistant' && m.text === text)) {
          return prev;
        }

        let index = -1;
        for (let i = prev.length - 1; i >= 0; i -= 1) {
          if (prev[i].role === 'assistant') {
            index = i;
            break;
          }
        }

        if (index === -1) {
          return [...prev, { id: nextId('agent'), role: 'assistant', text }];
        }

        const target = prev[index];
        if (target.text && !text.startsWith(target.text)) {
          return prev;
        }

        const next = [...prev];
        next[index] = { ...target, text };
        return next;
      });
      requestScroll();
    },
    [nextId, requestScroll]
  );

  const handleConversationSwitched = useCallback(() => {
    suppressSpeechRef.current = true;
    unspokenBufferRef.current = '';
    pendingTextRef.current = '';
    if (flushTimerRef.current) {
      clearTimeout(flushTimerRef.current);
      flushTimerRef.current = null;
    }
    stopSpeaking();
    setMessages([]);
    requestScroll();
  }, [requestScroll, stopSpeaking]);

  // Hook: WebSocket Gateway to DecafClaw
  const {
    connected,
    status,
    convId,
    manualReconnect,
    sendTurn,
    cancelTurn,
    returnToHome,
    clearSavedConversation,
  } = useDecafClawWebSocket({
    host,
    token,
    customLobbyConvId: lobbyConvId,
    onChunk: handleChunk,
    onTurnComplete: handleTurnComplete,
    onError: handleError,
    onHistoryRecovered: handleHistoryRecovered,
    onConversationSwitched: handleConversationSwitched,
  });

  const handleOpenWebUi = useCallback(() => {
    const url = conversationWebUrl(host, convId);
    Linking.openURL(url).catch((err) => {
      Alert.alert(
        'Could not open browser',
        err instanceof Error ? err.message : String(err)
      );
    });
  }, [convId, host]);

  const handleSaveConnection = useCallback(
    (nextHost: string, nextToken: string) => {
      const normalized = normalizeHost(nextHost) ?? host;
      const trimmed = nextToken.trim() || token;
      const changed = normalized !== host || trimmed !== token;

      if (changed) {
        setHost(normalized);
        setToken(trimmed);
        clearSavedConversation();
        handleConversationSwitched();
        AsyncStorage.multiSet([
          [STORAGE_KEYS.HOST, normalized],
          [STORAGE_KEYS.TOKEN, trimmed],
        ]).catch((err) => console.warn('Failed to save connection settings:', err));
      }

      setSettingsVisible(false);
      if (!changed) {
        manualReconnect();
      }
    },
    [clearSavedConversation, handleConversationSwitched, host, manualReconnect, token]
  );

  // Handle final speech recognition result
  const handleFinalSpeechResult = useCallback(
    (spokenText: string) => {
      const userEntry: ChatMessage = {
        id: nextId('user'),
        role: 'user',
        text: spokenText,
      };
      const agentEntry: ChatMessage = {
        id: nextId('agent'),
        role: 'assistant',
        text: '',
      };

      suppressSpeechRef.current = false;
      setMessages((prev) => [...prev, userEntry, agentEntry]);
      unspokenBufferRef.current = '';
      pendingTextRef.current = '';
      if (flushTimerRef.current) {
        clearTimeout(flushTimerRef.current);
        flushTimerRef.current = null;
      }
      requestScroll();

      sendTurn(spokenText);
    },
    [nextId, requestScroll, sendTurn]
  );

  const handleInterruption = useCallback(() => {
    suppressSpeechRef.current = true;
    unspokenBufferRef.current = '';
    flushPendingText();
    stopSpeaking();
    if (bargeInEnabled) {
      cancelTurn();
    }
  }, [bargeInEnabled, cancelTurn, flushPendingText, stopSpeaking]);

  // Hook: On-Device Speech Recognition (Pixel Tensor TPU)
  const { recognizing, isStarting, longListening, transcript, toggleListening, startLongListening } =
    useVoiceRecognition({
      onFinalResult: handleFinalSpeechResult,
      onInterrupted: handleInterruption,
    });

  useEffect(() => {
    return () => {
      if (flushTimerRef.current) clearTimeout(flushTimerRef.current);
      if (scrollTimerRef.current) clearTimeout(scrollTimerRef.current);
      Speech.stop();
    };
  }, []);

  return (
    <View style={styles.container}>
      <StatusBar style="light" />

      {/* Clean Minimal Header */}
      <Header />

      {/* Persistent IM Transcript */}
      <View style={styles.transcriptArea}>
        <TranscriptView
          scrollViewRef={scrollViewRef}
          messages={messages}
          transcript={transcript}
          recognizing={recognizing}
          bottomInset={convId ? 76 : 0}
        />

        {convId ? (
          <>
            <TouchableOpacity
              style={styles.homeFab}
              onPress={returnToHome}
              activeOpacity={0.7}
              accessibilityRole="button"
              accessibilityLabel="Return to home lobby"
              accessibilityHint="Switches active session to the home lobby"
            >
              <Text style={styles.homeFabIcon}>⌂</Text>
            </TouchableOpacity>

            <TouchableOpacity
              style={styles.webFab}
              onPress={handleOpenWebUi}
              activeOpacity={0.7}
              accessibilityRole="button"
              accessibilityLabel="Open conversation in web browser"
              accessibilityHint="Opens this conversation in your browser"
            >
              <Text style={styles.webFabIcon}>↗</Text>
            </TouchableOpacity>
          </>
        ) : null}
      </View>

      {/* Unified Bottom Control (PTT + Connection + Settings Hamburger) */}
      <PttFooter
        connected={connected}
        status={status}
        recognizing={recognizing}
        isStarting={isStarting}
        longListening={longListening}
        speaking={speaking}
        onToggleListening={toggleListening}
        onStartLongListening={startLongListening}
        onReconnect={manualReconnect}
        onStopSpeaking={stopSpeaking}
        onOpenSettings={() => setSettingsVisible(true)}
      />

      {/* Slide-Up Voice Settings Sheet */}
      <VoiceSettingsModal
        key={settingsVisible ? `open:${host}:${token}` : 'closed'}
        visible={settingsVisible}
        host={host}
        token={token}
        lobbyConvId={lobbyConvId}
        ttsEnabled={ttsEnabled}
        bargeInEnabled={bargeInEnabled}
        voices={voices}
        selectedVoice={selectedVoice}
        pitch={pitch}
        rate={rate}
        starredVoices={starredVoices}
        onSaveConnection={handleSaveConnection}
        onSaveLobby={handleSaveLobby}
        onToggleTts={handleToggleTts}
        onToggleBargeIn={handleToggleBargeIn}
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
  transcriptArea: {
    flex: 1,
  },
  homeFab: {
    position: 'absolute',
    left: 20,
    bottom: 14,
    width: 60,
    height: 60,
    borderRadius: 30,
    backgroundColor: '#27272a',
    borderWidth: 1,
    borderColor: '#3f3f46',
    justifyContent: 'center',
    alignItems: 'center',
    elevation: 4,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.3,
    shadowRadius: 3,
  },
  homeFabIcon: {
    color: '#38bdf8',
    fontSize: 26,
    fontWeight: '700',
    lineHeight: 28,
  },
  webFab: {
    position: 'absolute',
    right: 20,
    bottom: 14,
    width: 60,
    height: 60,
    borderRadius: 30,
    backgroundColor: '#27272a',
    borderWidth: 1,
    borderColor: '#3f3f46',
    justifyContent: 'center',
    alignItems: 'center',
    elevation: 4,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.3,
    shadowRadius: 3,
  },
  webFabIcon: {
    color: '#f4f4f5',
    fontSize: 24,
    fontWeight: '700',
    lineHeight: 26,
  },
});

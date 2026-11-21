import React, { useState } from 'react';
import {
  StyleSheet,
  Text,
  View,
  TouchableOpacity,
  ScrollView,
  Modal,
  TextInput,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { VoiceItem } from '../types';
import { formatVoiceLabel } from '../utils/speech';
import { normalizeHost } from '../utils/connection';

interface VoiceSettingsModalProps {
  visible: boolean;
  host: string;
  token: string;
  lobbyConvId: string;
  ttsEnabled: boolean;
  bargeInEnabled: boolean;
  voices: VoiceItem[];
  selectedVoice: string;
  pitch: number;
  rate: number;
  starredVoices: string[];
  onSaveConnection: (host: string, token: string) => void;
  onSaveLobby: (val: string) => void;
  onToggleTts: () => void;
  onToggleBargeIn: () => void;
  onSelectVoice: (id: string) => void;
  onPreviewVoice: (id: string) => void;
  onToggleStarVoice: (id: string) => void;
  onChangePitch: (val: number) => void;
  onChangeRate: (val: number) => void;
  onClose: () => void;
}

export function VoiceSettingsModal({
  visible,
  host,
  token,
  lobbyConvId,
  ttsEnabled,
  bargeInEnabled,
  voices,
  selectedVoice,
  pitch,
  rate,
  starredVoices,
  onSaveConnection,
  onSaveLobby,
  onToggleTts,
  onToggleBargeIn,
  onSelectVoice,
  onPreviewVoice,
  onToggleStarVoice,
  onChangePitch,
  onChangeRate,
  onClose,
}: VoiceSettingsModalProps) {
  const insets = useSafeAreaInsets();

  const [draftHost, setDraftHost] = useState(host);
  const [draftToken, setDraftToken] = useState(token);
  const [draftLobby, setDraftLobby] = useState(lobbyConvId);
  const [showToken, setShowToken] = useState(false);
  const [formError, setFormError] = useState('');

  const starredSet = new Set(starredVoices);
  const favoriteVoices = voices.filter((v) => starredSet.has(v.identifier));
  const otherVoices = voices.filter((v) => !starredSet.has(v.identifier));

  const handleSaveConnection = () => {
    const normalized = normalizeHost(draftHost);
    if (!normalized) {
      setFormError('Enter a valid http:// or https:// host.');
      return;
    }
    if (!draftToken.trim()) {
      setFormError('Enter the DecafClaw session token.');
      return;
    }
    setFormError('');
    onSaveConnection(normalized, draftToken.trim());
    onSaveLobby(draftLobby.trim());
  };

  const renderVoiceItem = (v: VoiceItem) => {
    const isSelected = selectedVoice === v.identifier;
    const isStarred = starredSet.has(v.identifier);
    const isLocal = v.identifier.includes('local');
    const label = formatVoiceLabel(v.identifier);

    return (
      <View
        key={v.identifier}
        style={[styles.voiceItem, isSelected && styles.voiceItemActive]}
      >
        {/* Star Button */}
        <TouchableOpacity
          style={styles.starButton}
          onPress={() => onToggleStarVoice(v.identifier)}
          hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
          activeOpacity={0.7}
        >
          <Text style={[styles.starIcon, isStarred && styles.starIconActive]}>
            {isStarred ? '⭐' : '☆'}
          </Text>
        </TouchableOpacity>

        {/* Voice Info */}
        <TouchableOpacity
          style={styles.voiceItemInfo}
          onPress={() => {
            onSelectVoice(v.identifier);
            onPreviewVoice(v.identifier);
          }}
          activeOpacity={0.7}
        >
          <Text style={[styles.voiceName, isSelected && styles.voiceTextActive]}>
            {label}
          </Text>
          <Text style={styles.voiceSub}>
            {v.identifier} • {isLocal ? 'On-Device' : 'Network'}
          </Text>
        </TouchableOpacity>

        {/* Actions: Selected Checkmark + Inline Audition Play */}
        <View style={styles.voiceItemActions}>
          {isSelected ? <Text style={styles.checkmark}>✓</Text> : null}
          <TouchableOpacity
            style={styles.inlinePlayButton}
            onPress={() => {
              onSelectVoice(v.identifier);
              onPreviewVoice(v.identifier);
            }}
            hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
            activeOpacity={0.7}
          >
            <Text style={styles.inlinePlayText}>▶</Text>
          </TouchableOpacity>
        </View>
      </View>
    );
  };

  return (
    <Modal
      visible={visible}
      animationType="slide"
      transparent={true}
      onRequestClose={onClose}
    >
      <View style={styles.modalOverlay}>
        <View
          style={[
            styles.modalSheet,
            {
              paddingBottom: Math.max(insets.bottom, 20) + 16,
              paddingTop: 20,
            },
          ]}
        >
          {/* Pinned Modal Header */}
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle}>Settings</Text>
            <TouchableOpacity onPress={onClose} style={styles.modalCloseButton}>
              <Text style={styles.modalCloseText}>Done</Text>
            </TouchableOpacity>
          </View>

          {/* Unified Scroll View */}
          <ScrollView
            style={styles.modalScroll}
            contentContainerStyle={{ paddingBottom: 30 }}
            keyboardShouldPersistTaps="handled"
            keyboardDismissMode="on-drag"
          >
            {/* 0. DecafClaw Connection */}
            <View style={styles.cardSection}>
              <Text style={styles.toggleTitle}>DecafClaw Connection</Text>
              <Text style={styles.toggleSubtitle}>
                Server address and session token. Saving reconnects the socket.
              </Text>

              <Text style={styles.inputLabel}>Host URL</Text>
              <TextInput
                style={styles.textInput}
                value={draftHost}
                onChangeText={setDraftHost}
                autoCapitalize="none"
                autoCorrect={false}
                keyboardType="url"
                placeholder="http://decafclaw:18880"
                placeholderTextColor="#71717a"
                returnKeyType="next"
              />

              <Text style={styles.inputLabel}>Session token</Text>
              <View style={styles.tokenRow}>
                <TextInput
                  style={[styles.textInput, styles.tokenInput]}
                  value={draftToken}
                  onChangeText={setDraftToken}
                  autoCapitalize="none"
                  autoCorrect={false}
                  secureTextEntry={!showToken}
                  placeholder="dfc_..."
                  placeholderTextColor="#71717a"
                />
                <TouchableOpacity
                  style={styles.showTokenButton}
                  onPress={() => setShowToken((prev) => !prev)}
                  activeOpacity={0.7}
                >
                  <Text style={styles.showTokenText}>{showToken ? 'Hide' : 'Show'}</Text>
                </TouchableOpacity>
              </View>

              <Text style={styles.inputLabel}>Lobby / Default Session</Text>
              <TextInput
                style={styles.textInput}
                value={draftLobby}
                onChangeText={setDraftLobby}
                autoCapitalize="none"
                autoCorrect={false}
                placeholder="web-username-home (auto)"
                placeholderTextColor="#71717a"
              />
              <Text style={styles.inputHint}>
                Target conversation when launching or returning Home. Leave blank for per-user default.
              </Text>

              {formError ? <Text style={styles.formError}>{formError}</Text> : null}

              <TouchableOpacity
                style={styles.saveButton}
                onPress={handleSaveConnection}
                activeOpacity={0.8}
              >
                <Text style={styles.saveButtonText}>Save & Reconnect</Text>
              </TouchableOpacity>
            </View>

            {/* 1. Spoken Responses Toggle Card */}
            <View style={styles.cardSection}>
              <View style={styles.toggleRow}>
                <View>
                  <Text style={styles.toggleTitle}>Spoken Responses</Text>
                  <Text style={styles.toggleSubtitle}>Speak assistant replies aloud via TTS</Text>
                </View>
                <TouchableOpacity
                  onPress={onToggleTts}
                  style={[styles.ttsTogglePill, ttsEnabled ? styles.ttsPillActive : styles.ttsPillMuted]}
                  activeOpacity={0.7}
                >
                  <Text style={styles.ttsPillText}>
                    {ttsEnabled ? '🔊 Active' : '🔇 Muted'}
                  </Text>
                </TouchableOpacity>
              </View>
            </View>

            {/* 2. Server Barge-In Toggle Card */}
            <View style={styles.cardSection}>
              <View style={styles.toggleRow}>
                <View style={{ flex: 1, paddingRight: 8 }}>
                  <Text style={styles.toggleTitle}>Server Barge-In</Text>
                  <Text style={styles.toggleSubtitle}>
                    Cancel in-flight agent turn and tools when interrupting
                  </Text>
                </View>
                <TouchableOpacity
                  onPress={onToggleBargeIn}
                  style={[
                    styles.ttsTogglePill,
                    bargeInEnabled ? styles.bargeInActive : styles.ttsPillMuted,
                  ]}
                  activeOpacity={0.7}
                >
                  <Text style={styles.ttsPillText}>
                    {bargeInEnabled ? '⚡ Cancel Turn' : '🔇 Mute Only'}
                  </Text>
                </TouchableOpacity>
              </View>
            </View>

            {/* 2. Pitch Controls */}
            <View style={styles.settingSection}>
              <Text style={styles.settingLabel}>
                Pitch: <Text style={styles.settingValue}>{pitch.toFixed(2)}x</Text>
              </Text>
              <View style={styles.pillRow}>
                <TouchableOpacity
                  style={[styles.optionPill, pitch === 0.8 && styles.optionPillActive]}
                  onPress={() => onChangePitch(0.8)}
                >
                  <Text style={styles.optionPillText}>Deep (0.80)</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={[styles.optionPill, pitch === 0.9 && styles.optionPillActive]}
                  onPress={() => onChangePitch(0.9)}
                >
                  <Text style={styles.optionPillText}>Workshop (0.90)</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={[styles.optionPill, pitch === 1.0 && styles.optionPillActive]}
                  onPress={() => onChangePitch(1.0)}
                >
                  <Text style={styles.optionPillText}>Default (1.00)</Text>
                </TouchableOpacity>
              </View>
            </View>

            {/* 3. Speed / Rate Controls */}
            <View style={styles.settingSection}>
              <Text style={styles.settingLabel}>
                Speed: <Text style={styles.settingValue}>{rate.toFixed(2)}x</Text>
              </Text>
              <View style={styles.pillRow}>
                <TouchableOpacity
                  style={[styles.optionPill, rate === 0.95 && styles.optionPillActive]}
                  onPress={() => onChangeRate(0.95)}
                >
                  <Text style={styles.optionPillText}>Measured (0.95)</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={[styles.optionPill, rate === 1.1 && styles.optionPillActive]}
                  onPress={() => onChangeRate(1.1)}
                >
                  <Text style={styles.optionPillText}>Brisk (1.10)</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={[styles.optionPill, rate === 1.25 && styles.optionPillActive]}
                  onPress={() => onChangeRate(1.25)}
                >
                  <Text style={styles.optionPillText}>Fast (1.25)</Text>
                </TouchableOpacity>
              </View>
            </View>

            {/* 4. Starred Favorites Section (if any) */}
            {favoriteVoices.length > 0 ? (
              <View style={styles.settingSection}>
                <Text style={styles.settingLabel}>⭐ Favorite Voices</Text>
                <View style={styles.voiceList}>
                  {favoriteVoices.map(renderVoiceItem)}
                </View>
              </View>
            ) : null}

            {/* 5. All Voices List */}
            <View style={styles.settingSection}>
              <Text style={styles.settingLabel}>
                {favoriteVoices.length > 0 ? 'All Installed Voices' : 'Installed Voices (Tap ☆ to Star)'}
              </Text>
              <View style={styles.voiceList}>
                {otherVoices.map(renderVoiceItem)}
              </View>
            </View>
          </ScrollView>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.75)',
    justifyContent: 'flex-end',
  },
  modalSheet: {
    backgroundColor: '#18181b',
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    height: '82%',
    paddingHorizontal: 20,
  },
  modalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
    paddingBottom: 10,
    borderBottomWidth: 1,
    borderBottomColor: '#27272a',
  },
  modalTitle: {
    color: '#f4f4f5',
    fontSize: 18,
    fontWeight: '700',
  },
  modalCloseButton: {
    paddingHorizontal: 14,
    paddingVertical: 6,
    backgroundColor: '#2563eb',
    borderRadius: 8,
  },
  modalCloseText: {
    color: '#ffffff',
    fontSize: 14,
    fontWeight: '600',
  },
  modalScroll: {
    flex: 1,
  },
  cardSection: {
    backgroundColor: '#27272a',
    borderRadius: 12,
    padding: 14,
    marginBottom: 20,
    borderWidth: 1,
    borderColor: '#3f3f46',
  },
  toggleRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  toggleTitle: {
    color: '#f4f4f5',
    fontSize: 15,
    fontWeight: '700',
  },
  toggleSubtitle: {
    color: '#a1a1aa',
    fontSize: 12,
    marginTop: 2,
  },
  inputLabel: {
    color: '#a1a1aa',
    fontSize: 11,
    fontWeight: '600',
    textTransform: 'uppercase',
    marginTop: 14,
    marginBottom: 6,
  },
  inputHint: {
    color: '#71717a',
    fontSize: 11,
    marginTop: 4,
  },
  textInput: {
    backgroundColor: '#18181b',
    color: '#f4f4f5',
    borderWidth: 1,
    borderColor: '#3f3f46',
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 14,
    fontFamily: 'monospace',
  },
  tokenRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  tokenInput: {
    flex: 1,
  },
  showTokenButton: {
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#3f3f46',
    backgroundColor: '#18181b',
  },
  showTokenText: {
    color: '#38bdf8',
    fontSize: 12,
    fontWeight: '700',
  },
  formError: {
    color: '#fca5a5',
    fontSize: 12,
    marginTop: 10,
  },
  saveButton: {
    marginTop: 14,
    backgroundColor: '#2563eb',
    borderRadius: 8,
    paddingVertical: 12,
    alignItems: 'center',
  },
  saveButtonText: {
    color: '#ffffff',
    fontSize: 14,
    fontWeight: '700',
  },
  ttsTogglePill: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 20,
    borderWidth: 1,
  },
  ttsPillActive: {
    backgroundColor: '#1e293b',
    borderColor: '#38bdf8',
  },
  ttsPillMuted: {
    backgroundColor: '#3f3f46',
    borderColor: '#71717a',
  },
  bargeInActive: {
    backgroundColor: '#431407',
    borderColor: '#f97316',
  },
  ttsPillText: {
    color: '#f4f4f5',
    fontSize: 12,
    fontWeight: '700',
  },
  settingSection: {
    marginBottom: 20,
  },
  settingLabel: {
    color: '#a1a1aa',
    fontSize: 13,
    fontWeight: '600',
    marginBottom: 8,
    textTransform: 'uppercase',
  },
  settingValue: {
    color: '#38bdf8',
  },
  pillRow: {
    flexDirection: 'row',
    gap: 8,
  },
  optionPill: {
    flex: 1,
    backgroundColor: '#27272a',
    paddingVertical: 10,
    borderRadius: 8,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: '#3f3f46',
  },
  optionPillActive: {
    backgroundColor: '#1e293b',
    borderColor: '#38bdf8',
  },
  optionPillText: {
    color: '#f4f4f5',
    fontSize: 12,
    fontWeight: '600',
  },
  voiceList: {
    gap: 8,
  },
  voiceItem: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#27272a',
    padding: 12,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#3f3f46',
    gap: 10,
  },
  voiceItemActive: {
    backgroundColor: '#1e293b',
    borderColor: '#38bdf8',
  },
  starButton: {
    padding: 4,
  },
  starIcon: {
    fontSize: 18,
    color: '#71717a',
  },
  starIconActive: {
    color: '#fbbf24',
  },
  voiceItemInfo: {
    flex: 1,
  },
  voiceName: {
    color: '#f4f4f5',
    fontSize: 15,
    fontWeight: '700',
  },
  voiceTextActive: {
    color: '#38bdf8',
  },
  voiceSub: {
    color: '#a1a1aa',
    fontSize: 11,
    marginTop: 2,
  },
  voiceItemActions: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  inlinePlayButton: {
    backgroundColor: '#059669',
    width: 36,
    height: 36,
    borderRadius: 18,
    justifyContent: 'center',
    alignItems: 'center',
  },
  inlinePlayText: {
    color: '#ffffff',
    fontSize: 13,
    marginLeft: 2,
    fontWeight: '700',
  },
  checkmark: {
    color: '#38bdf8',
    fontSize: 16,
    fontWeight: '700',
  },
});

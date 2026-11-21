import React from 'react';
import {
  StyleSheet,
  Text,
  View,
  TouchableOpacity,
  ScrollView,
  Modal,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { VoiceItem } from '../types';
import { formatVoiceLabel } from '../utils/speech';

interface VoiceSettingsModalProps {
  visible: boolean;
  ttsEnabled: boolean;
  voices: VoiceItem[];
  selectedVoice: string;
  pitch: number;
  rate: number;
  starredVoices: string[];
  onToggleTts: () => void;
  onSelectVoice: (id: string) => void;
  onPreviewVoice: (id: string) => void;
  onToggleStarVoice: (id: string) => void;
  onChangePitch: (val: number) => void;
  onChangeRate: (val: number) => void;
  onClose: () => void;
}

export function VoiceSettingsModal({
  visible,
  ttsEnabled,
  voices,
  selectedVoice,
  pitch,
  rate,
  starredVoices,
  onToggleTts,
  onSelectVoice,
  onPreviewVoice,
  onToggleStarVoice,
  onChangePitch,
  onChangeRate,
  onClose,
}: VoiceSettingsModalProps) {
  const insets = useSafeAreaInsets();

  const starredSet = new Set(starredVoices);
  const favoriteVoices = voices.filter((v) => starredSet.has(v.identifier));
  const otherVoices = voices.filter((v) => !starredSet.has(v.identifier));

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
            <Text style={styles.modalTitle}>Voice & Audio Settings</Text>
            <TouchableOpacity onPress={onClose} style={styles.modalCloseButton}>
              <Text style={styles.modalCloseText}>Done</Text>
            </TouchableOpacity>
          </View>

          {/* Unified Scroll View */}
          <ScrollView
            style={styles.modalScroll}
            contentContainerStyle={{ paddingBottom: 30 }}
          >
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

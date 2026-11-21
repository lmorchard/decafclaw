import React from 'react';
import { StyleSheet, Text, View, TouchableOpacity } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

interface PttFooterProps {
  connected: boolean;
  status: string;
  recognizing: boolean;
  isStarting?: boolean;
  onToggleListening: () => void;
  onReconnect: () => void;
  onOpenSettings: () => void;
}

export function PttFooter({
  connected,
  status,
  recognizing,
  isStarting,
  onToggleListening,
  onReconnect,
  onOpenSettings,
}: PttFooterProps) {
  const insets = useSafeAreaInsets();

  const handleMainButtonPress = () => {
    if (connected) {
      onToggleListening();
    } else {
      onReconnect();
    }
  };

  const getMainButtonText = () => {
    if (!connected) {
      return status.includes('Connect') ? status : `${status} · Tap to Connect`;
    }
    if (isStarting) {
      return 'Opening Mic...';
    }
    if (recognizing) {
      return 'Listening... Tap to Send';
    }
    return 'Tap to Speak';
  };

  const getMainButtonStyle = () => {
    if (!connected) {
      return styles.btnDisconnected;
    }
    if (isStarting) {
      return styles.btnStarting;
    }
    if (recognizing) {
      return styles.btnRecognizing;
    }
    return styles.btnConnected;
  };

  return (
    <View
      style={[
        styles.footer,
        {
          paddingBottom: Math.max(insets.bottom, 20) + 16,
          paddingTop: 16,
        },
      ]}
    >
      <View style={styles.buttonRow}>
        {/* Unified PTT & Connection Button */}
        <TouchableOpacity
          style={[styles.mainButton, getMainButtonStyle()]}
          onPress={handleMainButtonPress}
          activeOpacity={0.8}
        >
          <Text style={styles.mainButtonText}>{getMainButtonText()}</Text>
        </TouchableOpacity>

        {/* Hamburger Menu Button for Settings */}
        <TouchableOpacity
          style={styles.menuButton}
          onPress={onOpenSettings}
          activeOpacity={0.7}
          hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
        >
          <Text style={styles.menuIcon}>☰</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  footer: {
    paddingHorizontal: 20,
    backgroundColor: '#18181b',
    borderTopWidth: 1,
    borderTopColor: '#27272a',
  },
  buttonRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  mainButton: {
    flex: 1,
    height: 60,
    borderRadius: 30,
    justifyContent: 'center',
    alignItems: 'center',
    elevation: 6,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 3 },
    shadowOpacity: 0.3,
    shadowRadius: 4,
  },
  btnConnected: {
    backgroundColor: '#2563eb',
  },
  btnStarting: {
    backgroundColor: '#ea580c', // Warm orange feedback
  },
  btnRecognizing: {
    backgroundColor: '#dc2626',
  },
  btnDisconnected: {
    backgroundColor: '#451a1a',
    borderWidth: 1.5,
    borderColor: '#ef4444',
  },
  mainButtonText: {
    color: '#ffffff',
    fontSize: 16,
    fontWeight: '700',
    paddingHorizontal: 12,
    textAlign: 'center',
  },
  menuButton: {
    width: 60,
    height: 60,
    borderRadius: 30,
    backgroundColor: '#27272a',
    borderWidth: 1,
    borderColor: '#3f3f46',
    justifyContent: 'center',
    alignItems: 'center',
    elevation: 4,
  },
  menuIcon: {
    color: '#f4f4f5',
    fontSize: 22,
    fontWeight: '700',
  },
});

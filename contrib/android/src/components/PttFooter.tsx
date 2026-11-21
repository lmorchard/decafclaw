import React from 'react';
import { StyleSheet, Text, View, TouchableOpacity } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

interface PttFooterProps {
  connected: boolean;
  status: string;
  recognizing: boolean;
  isStarting?: boolean;
  speaking: boolean;
  onToggleListening: () => void;
  onReconnect: () => void;
  onStopSpeaking: () => void;
  onOpenSettings: () => void;
}

interface StatusVisual {
  icon: string;
  color: string;
}

function getStatusVisual(
  connected: boolean,
  status: string,
  recognizing: boolean,
  isStarting: boolean,
  speaking: boolean
): StatusVisual {
  const lower = status.toLowerCase();

  if (lower.includes('queued')) {
    return { icon: '◌', color: '#fbbf24' };
  }

  if (!connected) {
    if (
      status.startsWith('Auth failed') ||
      status.startsWith('Session failed') ||
      status === 'Error'
    ) {
      return { icon: '!', color: '#fecaca' };
    }
    if (lower.includes('reconnect') || lower.includes('connecting')) {
      return { icon: '◌', color: '#fbbf24' };
    }
    return { icon: '○', color: '#fecaca' };
  }

  if (isStarting) {
    return { icon: '◌', color: '#fde68a' };
  }
  if (recognizing) {
    return { icon: '●', color: '#ffffff' };
  }
  if (speaking) {
    return { icon: '♪', color: '#7dd3fc' };
  }
  if (status === 'Error' || status === 'Session failed') {
    return { icon: '!', color: '#fecaca' };
  }
  if (status.startsWith('Tool:')) {
    return { icon: '⚙', color: '#fde68a' };
  }
  if (
    status === 'Thinking...' ||
    status === 'Dispatching turn...' ||
    status === 'Awaiting reply...'
  ) {
    return { icon: '⋯', color: '#fde68a' };
  }
  return { icon: '●', color: '#22c55e' };
}

export function PttFooter({
  connected,
  status,
  recognizing,
  isStarting,
  speaking,
  onToggleListening,
  onReconnect,
  onStopSpeaking,
  onOpenSettings,
}: PttFooterProps) {
  const insets = useSafeAreaInsets();
  const visual = getStatusVisual(
    connected,
    status,
    recognizing,
    Boolean(isStarting),
    speaking
  );

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
      return 'Listening... Tap to Stop';
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

  const buttonText = getMainButtonText();

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
          accessibilityRole="button"
          accessibilityLabel={`${buttonText}. ${status}`}
        >
          <View style={styles.mainButtonContent}>
            <View style={styles.statusBadge}>
              <Text style={[styles.statusIcon, { color: visual.color }]}>
                {visual.icon}
              </Text>
            </View>
            <Text
              style={styles.mainButtonText}
              numberOfLines={1}
              adjustsFontSizeToFit
              minimumFontScale={0.75}
            >
              {buttonText}
            </Text>
          </View>
        </TouchableOpacity>

        {/* Stop TTS without opening the mic */}
        {speaking ? (
          <TouchableOpacity
            style={styles.stopButton}
            onPress={onStopSpeaking}
            activeOpacity={0.7}
            accessibilityRole="button"
            accessibilityLabel="Stop speaking"
          >
            <Text style={styles.stopIcon}>■</Text>
          </TouchableOpacity>
        ) : null}

        {/* Hamburger Menu Button for Settings */}
        <TouchableOpacity
          style={styles.menuButton}
          onPress={onOpenSettings}
          activeOpacity={0.7}
          hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
          accessibilityRole="button"
          accessibilityLabel="Open settings"
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
  mainButtonContent: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 16,
    gap: 8,
  },
  statusBadge: {
    width: 20,
    height: 20,
    borderRadius: 10,
    backgroundColor: 'rgba(0,0,0,0.25)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  statusIcon: {
    fontSize: 12,
    fontWeight: '700',
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
    textAlign: 'center',
    flexShrink: 1,
  },
  stopButton: {
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
  stopIcon: {
    color: '#f4f4f5',
    fontSize: 18,
    fontWeight: '700',
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

import React from 'react';
import { StyleSheet, Text, View, TouchableOpacity } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

interface HeaderProps {
  onOpenWebUi?: () => void;
}

export function Header({ onOpenWebUi }: HeaderProps) {
  const insets = useSafeAreaInsets();

  return (
    <View
      style={[
        styles.header,
        {
          paddingTop: Math.max(insets.top, 24) + 12,
          paddingBottom: 14,
        },
      ]}
    >
      <View style={styles.titleContainer}>
        <Text style={styles.title}>DecafClaw</Text>
        <Text style={styles.subtitle}>Pocket Transceiver • v0.1</Text>
      </View>

      {onOpenWebUi ? (
        <TouchableOpacity
          style={styles.webButton}
          onPress={onOpenWebUi}
          activeOpacity={0.7}
          hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
          accessibilityRole="button"
          accessibilityLabel="Open conversation in web browser"
          accessibilityHint="Opens this conversation in your browser"
        >
          <Text style={styles.webIcon}>↗</Text>
        </TouchableOpacity>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  header: {
    paddingHorizontal: 20,
    backgroundColor: '#18181b',
    borderBottomWidth: 1,
    borderBottomColor: '#27272a',
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  titleContainer: {
    flexDirection: 'row',
    alignItems: 'baseline',
    gap: 8,
  },
  title: {
    color: '#f4f4f5',
    fontSize: 19,
    fontWeight: '800',
    letterSpacing: 0.3,
  },
  subtitle: {
    color: '#a1a1aa',
    fontSize: 12,
    fontWeight: '500',
  },
  webButton: {
    width: 34,
    height: 34,
    borderRadius: 17,
    backgroundColor: '#27272a',
    borderWidth: 1,
    borderColor: '#3f3f46',
    justifyContent: 'center',
    alignItems: 'center',
  },
  webIcon: {
    color: '#a1a1aa',
    fontSize: 20,
    fontWeight: '700',
    lineHeight: 22,
  },
});

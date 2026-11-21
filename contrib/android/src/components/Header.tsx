import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

interface HeaderProps {
  sessionTitle?: string | null;
}

export function Header({ sessionTitle }: HeaderProps) {
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
        <Text style={styles.subtitle} numberOfLines={1} ellipsizeMode="tail">
          {sessionTitle || 'No session'}
        </Text>
      </View>
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
    flex: 1,
    justifyContent: 'center',
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
    marginTop: 2,
  },
});

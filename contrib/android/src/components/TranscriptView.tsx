import React from 'react';
import { StyleSheet, Text, View, ScrollView } from 'react-native';
import { ChatMessage } from '../types';

interface TranscriptViewProps {
  messages: ChatMessage[];
  transcript: string;
  recognizing: boolean;
  scrollViewRef: React.RefObject<any>;
}

export function TranscriptView({
  messages,
  transcript,
  recognizing,
  scrollViewRef,
}: TranscriptViewProps) {
  return (
    <ScrollView
      ref={scrollViewRef}
      style={styles.scrollArea}
      contentContainerStyle={styles.scrollContent}
      keyboardShouldPersistTaps="handled"
    >
      {messages.length === 0 && !transcript ? (
        <View style={styles.emptyContainer}>
          <Text style={styles.emptyTitle}>DecafClaw Transceiver Ready</Text>
          <Text style={styles.emptySubtitle}>Tap the button below to start talking.</Text>
        </View>
      ) : null}

      {messages.map((item) => {
        if (item.role === 'user') {
          return (
            <View key={item.id} style={styles.userBubble}>
              <Text style={styles.bubbleLabel}>You</Text>
              <Text style={styles.userText}>{item.text}</Text>
            </View>
          );
        } else {
          return (
            <View key={item.id} style={styles.agentBubble}>
              <Text style={styles.bubbleLabel}>Caffeina</Text>
              <Text style={styles.agentText}>
                {item.text.length > 0 ? item.text : 'Thinking...'}
              </Text>
            </View>
          );
        }
      })}

      {/* Live speech interim feedback */}
      {transcript && recognizing ? (
        <View style={[styles.userBubble, styles.partialBubble]}>
          <Text style={styles.bubbleLabel}>Hearing...</Text>
          <Text style={styles.partialText}>{transcript}</Text>
        </View>
      ) : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  scrollArea: {
    flex: 1,
  },
  scrollContent: {
    padding: 20,
    gap: 16,
  },
  emptyContainer: {
    paddingVertical: 60,
    alignItems: 'center',
  },
  emptyTitle: {
    color: '#a1a1aa',
    fontSize: 16,
    fontWeight: '600',
    marginBottom: 6,
  },
  emptySubtitle: {
    color: '#71717a',
    fontSize: 13,
  },
  userBubble: {
    backgroundColor: '#27272a',
    padding: 14,
    borderRadius: 12,
    borderLeftWidth: 3,
    borderLeftColor: '#38bdf8',
  },
  partialBubble: {
    borderLeftColor: '#f59e0b',
    opacity: 0.8,
  },
  agentBubble: {
    backgroundColor: '#18181b',
    padding: 14,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#3f3f46',
    borderLeftWidth: 3,
    borderLeftColor: '#10b981',
  },
  bubbleLabel: {
    color: '#a1a1aa',
    fontSize: 11,
    fontWeight: '600',
    marginBottom: 4,
    textTransform: 'uppercase',
  },
  userText: {
    color: '#f4f4f5',
    fontSize: 16,
    lineHeight: 22,
  },
  partialText: {
    color: '#fef08a',
    fontSize: 16,
    lineHeight: 22,
    fontStyle: 'italic',
  },
  agentText: {
    color: '#fafafa',
    fontSize: 16,
    lineHeight: 22,
  },
});

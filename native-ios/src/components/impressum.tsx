import React, { useState } from 'react';
import { Alert, Linking, Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { colors, space } from '@/constants/design';

/** Local content: no session, configured server or network is needed to read it. */
export function ImpressumButton() {
  const [visible, setVisible] = useState(false);

  async function openEmail() {
    try {
      await Linking.openURL('mailto:impressum@zimlab.org');
    } catch {
      Alert.alert('E-Mail nicht geöffnet', 'Bitte schreibe an impressum@zimlab.org.');
    }
  }

  return (
    <>
      <Pressable accessibilityRole="button" accessibilityHint="Ohne Anmeldung und Internetverbindung lesbar."
        onPress={() => setVisible(true)} style={styles.button}>
        <Text style={styles.link}>Impressum</Text>
      </Pressable>
      <Modal visible={visible} animationType="slide" presentationStyle="pageSheet"
        onRequestClose={() => setVisible(false)}>
        <SafeAreaView style={styles.safe}>
          <View style={styles.header}>
            <Text accessibilityRole="header" style={styles.title}>Impressum</Text>
            <Pressable accessibilityRole="button" onPress={() => setVisible(false)} style={styles.button}>
              <Text style={styles.link}>Schließen</Text>
            </Pressable>
          </View>
          <ScrollView contentContainerStyle={styles.content}>
            <Text style={styles.text}>Rezeptregal · Zimlab</Text>
            <Text accessibilityRole="header" style={styles.heading}>Anbieter</Text>
            <Text selectable style={styles.text}>{'Oliver Zimmermann\nc/o COCENTER\nKoppoldstr. 1\n86551 Aichach\nDeutschland'}</Text>
            <Text accessibilityRole="header" style={styles.heading}>Kontakt</Text>
            <Pressable accessibilityRole="link" onPress={() => void openEmail()} style={styles.button}>
              <Text selectable style={styles.link}>impressum@zimlab.org</Text>
            </Pressable>
            <Text style={styles.note}>Dieses Impressum ist auch ohne Internetverbindung verfügbar.</Text>
          </ScrollView>
        </SafeAreaView>
      </Modal>
    </>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.cream },
  header: { paddingHorizontal: space.lg, paddingTop: space.md, flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: space.md },
  title: { fontSize: 26, fontWeight: '800', color: colors.text },
  heading: { fontSize: 21, fontWeight: '700', color: colors.text, marginTop: space.md },
  content: { padding: space.lg, gap: space.md },
  text: { fontSize: 17, lineHeight: 26, color: colors.text },
  note: { fontSize: 14, lineHeight: 21, color: colors.muted },
  button: { minHeight: 44, justifyContent: 'center', paddingHorizontal: 4 },
  link: { fontSize: 14, fontWeight: '700', color: colors.text, textDecorationLine: 'underline' },
});

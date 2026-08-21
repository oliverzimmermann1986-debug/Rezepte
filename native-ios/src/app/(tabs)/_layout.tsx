import { NativeTabs } from 'expo-router/unstable-native-tabs';
import React from 'react';

import { colors } from '@/constants/design';

export default function TabLayout() {
  return (
    <NativeTabs
      backgroundColor={colors.surface}
      iconColor={{ default: colors.muted, selected: colors.text }}
      labelStyle={{ default: { color: colors.muted }, selected: { color: colors.text } }}
      tintColor={colors.text}>
      <NativeTabs.Trigger name="index">
        <NativeTabs.Trigger.Label>Rezepte</NativeTabs.Trigger.Label>
        <NativeTabs.Trigger.Icon sf={{ default: 'books.vertical', selected: 'books.vertical.fill' }} />
      </NativeTabs.Trigger>
      <NativeTabs.Trigger name="plan">
        <NativeTabs.Trigger.Label>Wochenplan</NativeTabs.Trigger.Label>
        <NativeTabs.Trigger.Icon sf={{ default: 'calendar', selected: 'calendar.circle.fill' }} />
      </NativeTabs.Trigger>
      <NativeTabs.Trigger name="cart">
        <NativeTabs.Trigger.Label>Einkauf</NativeTabs.Trigger.Label>
        <NativeTabs.Trigger.Icon sf={{ default: 'cart', selected: 'cart.fill' }} />
      </NativeTabs.Trigger>
      <NativeTabs.Trigger name="admin">
        <NativeTabs.Trigger.Label>Admin</NativeTabs.Trigger.Label>
        <NativeTabs.Trigger.Icon sf={{ default: 'wrench.and.screwdriver', selected: 'wrench.and.screwdriver.fill' }} />
      </NativeTabs.Trigger>
    </NativeTabs>
  );
}

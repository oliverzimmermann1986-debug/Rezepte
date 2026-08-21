import * as ImageManipulator from 'expo-image-manipulator';
import * as ImagePicker from 'expo-image-picker';

export type PickedUploadImage = { uri: string; name: string; mimeType: string };

export async function pickEditedJpeg(prefix = 'rezept'): Promise<PickedUploadImage | null> {
  const result = await ImagePicker.launchImageLibraryAsync({
    mediaTypes: ['images'],
    allowsEditing: true,
    quality: 1,
  });
  if (result.canceled) return null;
  const normalized = await ImageManipulator.manipulateAsync(
    result.assets[0].uri,
    [],
    { compress: 0.9, format: ImageManipulator.SaveFormat.JPEG },
  );
  return {
    uri: normalized.uri,
    name: `${prefix}-${Date.now()}.jpg`,
    mimeType: 'image/jpeg',
  };
}

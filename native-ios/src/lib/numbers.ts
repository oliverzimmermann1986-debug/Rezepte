export function optionalNumber(
  value: number | string | null | undefined,
  label: string,
) {
  if (value === '' || value == null) return null;
  const parsed = Number(String(value).replace(',', '.'));
  if (!Number.isFinite(parsed)) throw new Error(`${label} ist keine gültige Zahl.`);
  return parsed;
}

export function optionalInteger(
  value: number | string | null | undefined,
  label: string,
  min = 1,
  max = 86_400,
) {
  const parsed = optionalNumber(value, label);
  if (parsed == null) return null;
  if (!Number.isInteger(parsed) || parsed < min || parsed > max) {
    throw new Error(`${label} muss eine ganze Zahl zwischen ${min} und ${max} sein.`);
  }
  return parsed;
}

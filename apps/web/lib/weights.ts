export const CATEGORY_KG: Record<string, number> = {
  DOCUMENTS: 0.1,
  CLOTHES: 1,
  PERSONAL_ITEMS: 1,
  ELECTRONICS: 0.5,
  FOOD: 1,
  MEDICINE: 0.2,
  CIGARETTES: 0.5,
  FRAGILE: 1,
  PET: 5,
  OTHER: 1,
};

const MIN_KG = 0.01;
const MAX_KG = 50;

export function suggestedKg(codes: string[]): number | null {
  const total = codes.reduce((sum, code) => sum + (CATEGORY_KG[code] ?? 0), 0);
  if (total <= 0) return null;
  const clamped = Math.min(MAX_KG, Math.max(MIN_KG, total));
  return Math.round(clamped * 100) / 100;
}

export function formatSuggestedKg(amount: number | null): string {
  if (amount == null) return "";
  return String(amount);
}

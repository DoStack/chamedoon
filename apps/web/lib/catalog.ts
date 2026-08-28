import type { Category, City, Country, Locale, Locations } from "@/lib/types";

export function localizedName(
  item: { name_en: string; name_fa: string },
  locale: Locale,
): string {
  return locale === "fa" ? item.name_fa : item.name_en;
}

export function findCountry(locations: Locations | null, code: string): Country | undefined {
  return locations?.countries.find((country) => country.code === code);
}

export function findCity(
  locations: Locations | null,
  countryCode: string,
  slug: string,
): City | undefined {
  return findCountry(locations, countryCode)?.cities.find((city) => city.slug === slug);
}

export function cityLabel(
  locations: Locations | null,
  countryCode: string,
  slug: string,
  locale: Locale,
): string {
  const city = findCity(locations, countryCode, slug);
  return city ? localizedName(city, locale) : slug;
}

export function routeArrow(locale: Locale): string {
  return locale === "fa" ? "←" : "→";
}

export function routeLabel(
  locations: Locations | null,
  originCountry: string,
  originCity: string,
  destinationCountry: string,
  destinationCity: string,
  locale: Locale,
): string {
  return `${cityLabel(locations, originCountry, originCity, locale)} ${routeArrow(locale)} ${cityLabel(locations, destinationCountry, destinationCity, locale)}`;
}

export function categoryLabel(
  categories: Category[],
  code: string,
  locale: Locale,
): string {
  const category = categories.find((item) => item.code === code);
  return category ? localizedName(category, locale) : code;
}

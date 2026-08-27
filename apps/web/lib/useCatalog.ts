"use client";

import { useEffect, useState } from "react";

import { fetchCategories, fetchLocations } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import type { Category, Locations } from "@/lib/types";

export function useCatalog() {
  const { messages } = useI18n();
  const [categories, setCategories] = useState<Category[]>([]);
  const [locations, setLocations] = useState<Locations | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [nextCategories, nextLocations] = await Promise.all([
          fetchCategories(),
          fetchLocations(),
        ]);
        if (!cancelled) {
          setCategories(nextCategories);
          setLocations(nextLocations);
          setError(null);
        }
      } catch {
        if (!cancelled) {
          setError(messages.common.error);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [messages.common.error]);

  return { categories, locations, loading, error };
}

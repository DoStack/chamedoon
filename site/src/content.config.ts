import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';
import { z } from 'astro/zod';

// Content contract: projects/chamedoon/web/PLAN.md §4 (P4 → P6).
const blog = defineCollection({
  loader: glob({ pattern: '**/*.mdx', base: './src/content/blog' }),
  schema: z.object({
    title: z.string(),
    // Optional shorter <title> (≤65 characters) when the H1 is long.
    seoTitle: z.string().optional(),
    description: z.string().max(170),
    slug: z.string().regex(/^[a-z0-9-]+$/).optional(),
    date: z.coerce.date(),
    updated: z.coerce.date().optional(),
    category: z.string(),
    tags: z.array(z.string()).default([]),
    city: z.string().nullish(),
    product: z.enum(['khane', 'barbari', 'both']).default('both'),
    answer: z.string(),
    keyFacts: z.array(z.union([z.string(), z.object({ text: z.string().optional(), label: z.string().optional(), value: z.any().optional(), unit: z.string().optional(), source: z.string().optional(), date: z.any().optional() }).passthrough()])).default([]),
    sources: z.array(z.object({ title: z.string(), url: z.string(), publisher: z.string().optional(), date: z.any().optional() })).default([]),
    faq: z.array(z.object({ q: z.string(), a: z.string() })).default([]),
    charts: z.array(z.object({ id: z.string(), type: z.string().default('bar'), title: z.string(), unit: z.string().optional(), data: z.any(), source: z.string().optional() }).passthrough()).default([]),
    abstract_en: z.string().optional(),
    readingMinutes: z.number().optional(),
    audio: z.any().optional(),
    video: z.any().optional(),
    cover: z.any().optional(),
    coverAlt: z.string().optional(),
    // Optional order on the home strip and at the top of the blog index (1 first).
    featured: z.number().int().positive().optional(),
    draft: z.boolean().default(false),
  }),
});

export const collections = { blog };

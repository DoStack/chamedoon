import { defineConfig, passthroughImageService } from 'astro/config';
import mdx from '@astrojs/mdx';

// One variable moves the whole site to another host (canonicals, sitemaps, JSON-LD, llms.txt).
const SITE_URL = (process.env.SITE_URL || 'https://chamedoon.bigde.li').replace(/\/$/, '');

export default defineConfig({
  site: SITE_URL,
  output: 'static',
  trailingSlash: 'always',
  build: { format: 'directory', assets: '_astro', inlineStylesheets: 'always' },
  image: { service: passthroughImageService() },
  integrations: [mdx()],
  devToolbar: { enabled: false },
  vite: { build: { assetsInlineLimit: 0 } },
});

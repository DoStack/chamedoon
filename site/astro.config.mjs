import { defineConfig, passthroughImageService } from 'astro/config';
import mdx from '@astrojs/mdx';
import { SITE_URL } from './site.config.mjs';

// One variable moves the whole site to another host: site.config.mjs.

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

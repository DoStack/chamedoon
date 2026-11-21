import rss from '@astrojs/rss';
import type { APIRoute } from 'astro';
import { getPosts, postPath } from '../lib/blog';
export const GET: APIRoute = async (ctx) => {
  const posts = await getPosts();
  return rss({
    title: 'وبلاگ چمدون',
    description: 'راهنماهای اجارهٔ خانه در کانادا و آلمان و ارسال بار بین ایران و خارج، از تحریریهٔ چمدون.',
    site: ctx.site!,
    customData: '<language>fa</language>',
    items: posts.map((p) => ({ title: p.data.title, description: p.data.description, link: postPath(p), pubDate: p.data.date, categories: [p.data.category, ...p.data.tags] })),
  });
};
